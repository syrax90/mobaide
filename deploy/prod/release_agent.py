#!/usr/bin/env python3
"""release_agent.py — Build, sign and publish an mobaide_agent release from this machine.

Usage:
    python deploy/prod/release_agent.py                   # newest agent-vX.Y.Z tag
    python deploy/prod/release_agent.py 0.3.21            # a specific version
    python deploy/prod/release_agent.py --skip-publish    # build + sign only, publish nothing
    python deploy/prod/release_agent.py --force           # allow a dirty working tree

What it does:
  1) Preflight: tools (git, uv), clean tree, release dir keys, the tag
     matches origin, and the local signing key's public half equals the
     trust key the relay serves to agents
  2) Detached worktree at the tag: uv build (wheel + sdist), source tarball,
     SHA-256 of both
  3) Sign the manifest with the local Ed25519 key
  4) PUT the tarball and wheel to the relay, which stores them by SHA-256
  5) Download each file anonymously from the manifest URL and check its SHA-256
  6) POST the manifest to the relay, then read it back and compare the version

The relay hosts the release files itself, at content-addressed URLs: anonymous
GitHub release downloads proved too unreliable for the update path. Re-running
a release is safe; a rebuilt file gets a new URL.

Keys come from the release dir (see release_common.py). Needs the
`cryptography` package in the interpreter that runs this.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from deploy_central import (
    DeployError,
    check_clean_tree,
    dim,
    enable_ansi,
    fail,
    info,
    ok,
    require_min_python,
    step,
    warn,
)
from release_common import (
    ReleaseError,
    http_json,
    http_request,
    read_secret,
    require_tools,
    resolve_release_dir,
    resolve_release_tag,
    sha256_file,
    temporary_worktree,
    with_retries,
)

CENTRAL_SERVER_URL = "https://api.mobaide.com"
TAG_PREFIX = "agent-v"
MIN_CENTRAL_SERVER_VERSION = "0.1.0"

SIGNING_KEY_FILE = "agent/manifest-signing-key"
RELEASE_TOKEN_FILE = "agent/release-token"


# ------------------------------------------------------------ signing key
def load_signing_key(release_dir: Path):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as e:
        raise ReleaseError(
            "the 'cryptography' package is missing. Run: python3 -m pip install cryptography"
        ) from e
    raw = read_secret(release_dir, SIGNING_KEY_FILE)
    try:
        if raw.startswith("-----BEGIN"):
            key = serialization.load_pem_private_key(raw.encode(), password=None)
        else:
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
    except Exception as e:
        raise ReleaseError(f"{release_dir / SIGNING_KEY_FILE} is not a valid Ed25519 key: {e}") from e
    if not isinstance(key, Ed25519PrivateKey):
        raise ReleaseError(f"{release_dir / SIGNING_KEY_FILE} is not an Ed25519 key")
    return key


def public_key_b64(key) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode()


def check_trust_key(key, server_url: str) -> None:
    """Agents verify every manifest against the relay's trust key, so a key
    that does not match would publish an update no agent accepts."""
    served = with_retries(
        "GET trust_key",
        lambda: http_json(f"{server_url}/api/v1/agent/trust_key", timeout=30),
        attempts=3, delay=5,
    )["public_key_b64"]
    local = public_key_b64(key)
    if served != local:
        raise ReleaseError(
            f"local signing key does not match the relay trust key "
            f"(local {local}, relay {served})"
        )
    ok("Signing key matches the relay trust key")


# ------------------------------------------------------------ build
def build_artifacts(src: Path, version: str, out_dir: Path) -> tuple[Path, Path]:
    agent_dir = src / "mobaide_agent"
    with (agent_dir / "pyproject.toml").open("rb") as fh:
        py_version = tomllib.load(fh)["project"]["version"]
    if py_version != version:
        raise ReleaseError(f"mobaide_agent/pyproject.toml has version {py_version}, the tag says {version}")

    dist = out_dir / "dist"
    r = subprocess.run(
        ["uv", "build", "--wheel", "--sdist", "--out-dir", str(dist)], cwd=agent_dir
    )
    if r.returncode != 0:
        raise ReleaseError("uv build failed")
    wheel = dist / f"mobaide_agent-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise ReleaseError(f"expected wheel {wheel.name} was not built: {sorted(p.name for p in dist.iterdir())}")
    extra = [p.name for p in dist.glob("*.whl") if p != wheel]
    if extra:
        raise ReleaseError(f"unexpected extra wheels: {extra}")
    ok(f"Built {wheel.name}")

    tarball = out_dir / f"mobaide-agent-{version}.tar.gz"

    def _filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = ti.name.rsplit("/", 1)[-1]
        if name == "__pycache__" or name.endswith(".pyc"):
            return None
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        # Windows reports 0o666/0o777 for every entry; fix the modes so the
        # tarball is the same on every OS.
        ti.mode = 0o755 if ti.isdir() else 0o644
        return ti

    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(agent_dir / "mobaide", arcname="mobaide", filter=_filter)
    ok(f"Built {tarball.name}")
    return wheel, tarball


def release_file_url(server_url: str, sha256: str, filename: str) -> str:
    return f"{server_url}/api/v1/agent/releases/{sha256}/{filename}"


def sign_manifest(key, version: str, server_url: str, tarball: Path, tarball_sha: str,
                  wheel: Path, wheel_sha: str) -> dict:
    # Must stay byte-compatible with the canonical form the relay and the
    # agent verify: sorted keys, no whitespace.
    body = {
        "version": version,
        "tarball_url": release_file_url(server_url, tarball_sha, tarball.name),
        "tarball_sha256": tarball_sha,
        "wheel_url": release_file_url(server_url, wheel_sha, wheel.name),
        "wheel_sha256": wheel_sha,
        "released_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_central_server_version": MIN_CENTRAL_SERVER_VERSION,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    signature = key.sign(canonical)
    key.public_key().verify(signature, canonical)
    return {"body": body, "signature_b64": base64.b64encode(signature).decode()}


# ------------------------------------------------------------ publish
def upload_release_file(path: Path, sha256: str, token: str, server_url: str) -> None:
    data = path.read_bytes()
    try:
        with_retries(
            f"PUT {path.name}",
            lambda: http_request(
                f"{server_url}/api/internal/agent_release/{sha256}/{path.name}",
                method="PUT",
                data=data,
                headers={"X-Release-Token": token, "Content-Type": "application/octet-stream"},
                timeout=120,
            ),
        )
    except ReleaseError as e:
        if "HTTP 404" in str(e) or "HTTP 405" in str(e):
            raise ReleaseError(
                f"{e}\nThe relay has no release-file upload endpoint yet. Deploy central_server first."
            ) from e
        raise
    ok(f"Uploaded {path.name} to the relay")


def check_anonymous_download(url: str, expected_sha: str) -> None:
    """Fetch the file the way an agent does: no credentials, the exact URL."""
    _, body = with_retries(f"GET {url}", lambda: http_request(url, timeout=120))
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected_sha:
        raise ReleaseError(f"{url} serves sha256 {actual}, expected {expected_sha}")
    ok(f"Anonymous download works: {url.rsplit('/', 1)[-1]}")


def publish_manifest(record: dict, token: str, server_url: str) -> None:
    payload = json.dumps(record).encode()
    with_retries(
        "POST agent_manifest",
        lambda: http_request(
            f"{server_url}/api/internal/agent_manifest",
            method="POST",
            data=payload,
            headers={"X-Release-Token": token, "Content-Type": "application/json"},
        ),
    )
    ok("Manifest posted to the relay")
    published = with_retries(
        "GET manifest.json",
        lambda: http_json(f"{server_url}/api/v1/agent/manifest.json", timeout=30),
        attempts=3, delay=5,
    )["body"]["version"]
    if published != record["body"]["version"]:
        raise ReleaseError(f"the relay serves version {published}, expected {record['body']['version']}")
    ok(f"Relay serves manifest version {published}")


# ------------------------------------------------------------ main flow
def run(args: argparse.Namespace) -> int:
    server_url = args.server_url.rstrip("/")

    step("Preflight")
    require_tools("git", "uv")
    if args.force:
        warn("--force: skipping clean-tree check")
    else:
        check_clean_tree()
    release_dir = resolve_release_dir(args.release_dir)
    key = load_signing_key(release_dir)
    token = None if args.skip_publish else read_secret(release_dir, RELEASE_TOKEN_FILE)
    ok(f"Keys loaded from {release_dir}")
    check_trust_key(key, server_url)
    tag, version, commit = resolve_release_tag(TAG_PREFIX, args.version)

    out_dir = Path(tempfile.mkdtemp(prefix="mobaide-agent-out-"))
    step(f"Build {tag}")
    with temporary_worktree(commit) as src:
        wheel, tarball = build_artifacts(src, version, out_dir)
    tarball_sha, wheel_sha = sha256_file(tarball), sha256_file(wheel)
    dim(f"sha256 {tarball_sha}  {tarball.name}")
    dim(f"sha256 {wheel_sha}  {wheel.name}")

    step("Sign manifest")
    record = sign_manifest(key, version, server_url, tarball, tarball_sha, wheel, wheel_sha)
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ok(f"Signed {manifest}")

    if args.skip_publish:
        info("")
        info(f"--skip-publish: artifacts are in {out_dir}; nothing was published.")
        return 0

    assert token is not None
    step(f"Upload release files to {server_url}")
    upload_release_file(tarball, tarball_sha, token, server_url)
    upload_release_file(wheel, wheel_sha, token, server_url)
    check_anonymous_download(record["body"]["tarball_url"], tarball_sha)
    check_anonymous_download(record["body"]["wheel_url"], wheel_sha)

    step(f"Publish manifest to {server_url}")
    publish_manifest(record, token, server_url)

    info("")
    info(f"Released {tag}. Local copies: {out_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build, sign and publish an mobaide_agent release")
    p.add_argument("version", nargs="?", help="X.Y.Z (default: the newest agent-v tag)")
    p.add_argument("--skip-publish", action="store_true", help="build and sign only")
    p.add_argument("--force", action="store_true", help="allow a dirty working tree")
    p.add_argument("--release-dir", help="folder with the release keys (default: $MOBAIDE_RELEASE_DIR, else ~/.config/mobaide-release, on Windows %%LOCALAPPDATA%%\\mobaide-release)")
    p.add_argument("--server-url", default=CENTRAL_SERVER_URL, help=f"relay base URL (default: {CENTRAL_SERVER_URL})")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("release_agent.py"):
        return 1
    args = build_parser().parse_args(argv)
    enable_ansi()
    try:
        return run(args)
    except DeployError as e:
        if str(e):
            fail(str(e))
        return 1
    except KeyboardInterrupt:
        fail("Interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
