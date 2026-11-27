#!/usr/bin/env python3
"""release_agent.py — Build, sign and publish an mobaide_agent release from this machine.

Usage:
    python deploy/prod/release_agent.py                   # newest agent-vX.Y.Z tag
    python deploy/prod/release_agent.py 0.3.21            # a specific version
    python deploy/prod/release_agent.py --skip-publish    # build + sign only, publish nothing
    python deploy/prod/release_agent.py --force           # allow a dirty working tree
    python deploy/prod/release_agent.py --new-relay-pairing-key   # print a new key pair, do nothing else
    python deploy/prod/release_agent.py --update-lock     # write mobaide_agent/requirements.lock for a pull request
    python deploy/prod/release_agent.py --upgrade-lock    # the same, with the newest version of every dependency

What it does:
  1) Preflight: tools (git, uv), clean tree, release dir keys, the tag
     matches origin, the local signing key's public half equals the
     trust key the relay serves, and the relay pairing key in the release
     dir equals the one the relay holds
  2) Detached worktree at the tag: the install scripts must carry the
     signing key's public half; uv build (wheel + sdist); the committed
     hash-pinned dependency lock (mobaide_agent/requirements.lock) must fit
     pyproject.toml and goes into the source tarball; uv confirms a binary
     wheel for every supported platform and a trial install proves that pip
     accepts the lock; SHA-256 of all three. A release never picks new
     dependency versions: --update-lock does, for a pull request
  3) Sign the manifest with the local Ed25519 key. The signed body names
     the lock by SHA-256 and lists the keys agents may trust
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
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from deploy_central import (
    PROJECT_ROOT,
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
# Optional public keys (base64 of the 32 raw bytes) that the signed manifest
# tells agents to trust: the relay's pairing key, and a second release key
# that will sign later manifests.
RELAY_PAIRING_PUBLIC_KEY_FILE = "agent/relay-pairing-public-key"
NEXT_SIGNING_PUBLIC_KEY_FILE = "agent/next-manifest-public-key"

DEPENDENCY_LOCK_NAME = "requirements.lock"
# Where an agent can run. The agent installs with --only-binary, so every
# locked dependency needs a binary wheel on each of these platforms, for
# each Python version.
LOCK_TARGET_PLATFORMS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-musl",
    "aarch64-unknown-linux-musl",
)
LOCK_PYTHON_VERSIONS = ("3.12", "3.13", "3.14", "3.15")
# Python 3.15 on glibc Linux: one dependency has wheels only for
# glibc 2.39 or newer. install.sh refuses an older glibc with that Python,
# so the check uses the platform that can install.
LOCK_PLATFORM_FOR_PYTHON = {
    ("x86_64-unknown-linux-gnu", "3.15"): "x86_64-manylinux_2_39",
    ("aarch64-unknown-linux-gnu", "3.15"): "aarch64-manylinux_2_39",
}
# The oldest macOS the agent installs on; deploy/install.sh refuses older
# ones. Without it uv checks macOS wheels for macOS 13 and passes a lock that
# macOS 11 and 12 cannot install.
LOCK_MACOS_MINIMUM = "11.0"
PYPI_INDEX = "https://pypi.org/simple"
INSTALL_SCRIPTS = ("deploy/install.sh", "deploy/install.ps1")


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


def read_optional_public_key(release_dir: Path, rel: str) -> str | None:
    if not (release_dir / rel).exists():
        return None
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    value = read_secret(release_dir, rel)
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(value, validate=True))
    except ValueError as e:
        raise ReleaseError(f"{release_dir / rel} is not a base64 Ed25519 public key: {e}") from e
    return value


def check_relay_pairing_key(local: str | None, server_url: str) -> None:
    """The manifest tells agents which key signs the pairing push, so it must
    name the key the relay holds. A wrong or missing key would break pairing
    on the day the relay starts to sign with it."""
    try:
        served = with_retries(
            "GET pairing_key",
            lambda: http_json(f"{server_url}/api/v1/agent/pairing_key", timeout=30),
            attempts=3, delay=5,
        ).get("public_key_b64")
    except ReleaseError as e:
        # 404: a relay from before the pairing key, which holds none.
        if "HTTP 404" not in str(e):
            raise
        served = None
    if served == local:
        if local:
            ok("Relay pairing key matches the relay")
        else:
            warn("No relay pairing key: the manifest lists none")
        return
    if served and not local:
        raise ReleaseError(
            f"the relay holds a pairing key but {RELAY_PAIRING_PUBLIC_KEY_FILE} is missing "
            f"from the release dir. Put this public key there: {served}"
        )
    if local and not served:
        raise ReleaseError(
            f"{RELAY_PAIRING_PUBLIC_KEY_FILE} exists but the relay serves no pairing key. "
            "Set RELAY_PAIRING_PRIVATE_KEY on the relay and deploy central_server first."
        )
    raise ReleaseError(
        f"{RELAY_PAIRING_PUBLIC_KEY_FILE} does not match the relay pairing key "
        f"(local {local}, relay {served})"
    )


def print_new_relay_pairing_key() -> int:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    info("New relay pairing key. Nothing was saved.")
    info(f"  Relay .env:   RELAY_PAIRING_PRIVATE_KEY={base64.b64encode(private).decode()}")
    info(f"  Release dir:  {RELAY_PAIRING_PUBLIC_KEY_FILE} = {public_key_b64(key)}")
    return 0


# ------------------------------------------------------------ build
def check_install_scripts(src: Path, key) -> None:
    """New installs trust the key inside the install script, so a script
    with another key would install an agent that refuses this release."""
    expected = public_key_b64(key)
    for rel in INSTALL_SCRIPTS:
        if expected not in (src / rel).read_text(encoding="utf-8"):
            raise ReleaseError(f"{rel} at this tag does not carry the signing key's public half ({expected})")
    ok("Install scripts carry the signing key's public half")


def minimum_python(requires_python: str) -> str:
    m = re.fullmatch(r"\s*>=\s*(\d+\.\d+)\s*", requires_python)
    if not m:
        raise ReleaseError(f"requires-python must be '>=X.Y', found {requires_python!r}")
    return m.group(1)


# uv settings that may come from this machine without a change of what is resolved.
_UV_ENV_KEPT = ("UV_CACHE_DIR", "UV_NATIVE_TLS", "UV_SYSTEM_CERTS", "UV_HTTP_TIMEOUT")


def _uv_env() -> dict[str, str]:
    """uv must resolve from the public index and from pyproject.toml only,
    whatever this machine's own uv settings say (indexes, constraints,
    overrides)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("UV_") or k in _UV_ENV_KEPT}


def compile_lock(agent_dir: Path, lock: Path, python_floor: str, *, upgrade: bool = False) -> None:
    """Resolve every dependency for all platforms and every Python from
    ``python_floor`` up, with the SHA-256 of each file, into ``lock``. Without
    the floor uv starts at the interpreter it finds, and an agent on an
    older Python would miss a dependency. uv keeps the versions that ``lock``
    already holds when they still fit; ``upgrade`` takes the newest instead."""
    r = subprocess.run(
        [
            "uv", "--no-config", "pip", "compile", "pyproject.toml",
            "--universal", "--python-version", python_floor,
            "--default-index", PYPI_INDEX,
            "--generate-hashes", "--no-header", "--no-annotate",
            *(["--upgrade"] if upgrade else []),
            "--quiet", "--output-file", str(lock),
        ],
        cwd=agent_dir, env=_uv_env(),
    )
    if r.returncode != 0 or not lock.is_file():
        raise ReleaseError("uv pip compile failed")
    # LF on every OS, so the lock has the same SHA-256 wherever it was made.
    lock.write_bytes(lock.read_bytes().replace(b"\r\n", b"\n"))


_LOCK_PIN_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*([^\\]*?))?\s*\\?\s*$")
_LOCK_HASH_RE = re.compile(r"--hash=(sha256:[0-9a-f]{64})")


def lock_entries(text: str) -> dict[tuple[str, str, str], frozenset[str]]:
    """(name, version, marker) -> the hashes the lock accepts for it."""
    entries: dict[tuple[str, str, str], set[str]] = {}
    current: set[str] | None = None
    for line in text.splitlines():
        m = _LOCK_PIN_RE.match(line)
        if m:
            current = entries.setdefault((m.group(1).lower(), m.group(2), (m.group(3) or "").strip()), set())
        elif current is not None:
            current.update(_LOCK_HASH_RE.findall(line))
    return {key: frozenset(hashes) for key, hashes in entries.items()}


_LOCK_PIN_LINE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*==[^\s;\\]+( ; [^\\]+)? \\")
_LOCK_HASH_LINE_RE = re.compile(r"    --hash=sha256:[0-9a-f]{64}( \\)?")


def check_lock_grammar(text: str) -> None:
    """The lock may hold only what the comparison with the index reads: a
    pinned version with an optional marker, then its sha256 lines. pip
    understands more (other hash forms, URLs, index options), so any other
    line stops the release."""
    in_entry = False
    number = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not line:
            valid = not in_entry
        elif _LOCK_PIN_LINE_RE.fullmatch(line):
            valid = not in_entry and " --" not in line
            in_entry = True
        elif _LOCK_HASH_LINE_RE.fullmatch(line):
            valid = in_entry
            in_entry = line.endswith("\\")
        else:
            valid = False
        if not valid:
            break
    else:
        if not in_entry:
            return
    raise ReleaseError(
        f"mobaide_agent/{DEPENDENCY_LOCK_NAME} line {number} is not a complete pinned version "
        "with its sha256 lines. Run 'python deploy/prod/release_agent.py --update-lock' "
        "and commit its output unchanged."
    )


def lock_pins(text: str) -> dict[str, set[str]]:
    """Package name -> pinned versions (more than one when platforms differ)."""
    pins: dict[str, set[str]] = {}
    for name, version, _marker in lock_entries(text):
        pins.setdefault(name, set()).add(version)
    return pins


def lock_difference(old_text: str, new_text: str) -> list[str]:
    """One line per dependency whose versions, markers or hashes differ."""
    old, new = lock_entries(old_text), lock_entries(new_text)
    old_pins, new_pins = lock_pins(old_text), lock_pins(new_text)
    lines = []
    for name in sorted({key[0] for key in old.keys() | new.keys()}):
        before, after = old_pins.get(name, set()), new_pins.get(name, set())
        if before != after:
            lines.append(f"{name}: {', '.join(sorted(before)) or 'new'} -> {', '.join(sorted(after)) or 'removed'}")
        elif {k: v for k, v in old.items() if k[0] == name} != {k: v for k, v in new.items() if k[0] == name}:
            lines.append(f"{name}: hashes or markers changed")
    return lines


def recompile_from_pins(agent_dir: Path, text: str, target: Path, python_floor: str, *, upgrade: bool = False) -> None:
    """Resolve again with the versions of ``text`` as the starting point, but
    without its hashes: uv copies hashes from the file it starts from, and
    here they must come from the index."""
    seed = [
        f"{name}=={version}" + (f" ; {marker}" if marker else "")
        for name, version, marker in lock_entries(text)
    ]
    target.write_text("\n".join(seed) + "\n", encoding="utf-8")
    compile_lock(agent_dir, target, python_floor, upgrade=upgrade)


def load_dependency_lock(agent_dir: Path, out_dir: Path, python_floor: str) -> Path:
    """Take the lock that is committed with the source. Its versions were
    reviewed in a pull request; a release never picks new ones. The lock
    must be what the index gives for those versions and for pyproject.toml:
    a changed dependency, marker or hash stops the release here."""
    committed = agent_dir / DEPENDENCY_LOCK_NAME
    if not committed.is_file():
        raise ReleaseError(f"mobaide_agent/{DEPENDENCY_LOCK_NAME} is missing at this tag")
    lock = out_dir / DEPENDENCY_LOCK_NAME
    lock.write_bytes(committed.read_bytes().replace(b"\r\n", b"\n"))
    text = lock.read_text(encoding="utf-8")
    check_lock_grammar(text)
    fresh = out_dir / "lock-fresh.txt"
    recompile_from_pins(agent_dir, text, fresh, python_floor)
    difference = lock_difference(text, fresh.read_text(encoding="utf-8"))
    fresh.unlink(missing_ok=True)
    if difference:
        raise ReleaseError(
            f"mobaide_agent/{DEPENDENCY_LOCK_NAME} is not what the package index gives for "
            "mobaide_agent/pyproject.toml (lock -> index):\n  " + "\n  ".join(difference) + "\n"
            "Run 'python deploy/prod/release_agent.py --update-lock', review the change in a "
            "pull request, and release from a tag that contains it."
        )
    ok(f"Dependency lock: {len(lock_entries(text))} pinned versions, as committed")
    return lock


def update_lock(*, upgrade: bool) -> int:
    """Write mobaide_agent/requirements.lock in this checkout, for a pull request."""
    require_tools("uv")
    agent_dir = PROJECT_ROOT / "mobaide_agent"
    with (agent_dir / "pyproject.toml").open("rb") as fh:
        python_floor = minimum_python(tomllib.load(fh)["project"]["requires-python"])
    lock = agent_dir / DEPENDENCY_LOCK_NAME
    old_text = lock.read_text(encoding="utf-8") if lock.is_file() else ""
    step("Upgrade dependency lock to the newest versions" if upgrade else "Update dependency lock")
    with tempfile.TemporaryDirectory(prefix="mobaide-lock-") as scratch:
        candidate = Path(scratch) / DEPENDENCY_LOCK_NAME
        recompile_from_pins(agent_dir, old_text, candidate, python_floor, upgrade=upgrade)
        check_lock_has_wheels(candidate, Path(scratch), python_floor)
        new_text = candidate.read_text(encoding="utf-8")
        check_lock_grammar(new_text)
    if new_text == old_text:
        info(f"  {lock} is already current. Nothing was written.")
        return 0
    for line in lock_difference(old_text, new_text) or ["only the layout of the file changed"]:
        info(f"  {line}")
    lock.write_text(new_text, encoding="utf-8", newline="\n")
    info("")
    info(f"Wrote {lock}. Commit it in a pull request.")
    return 0


def check_lock_has_wheels(lock: Path, out_dir: Path, python_floor: str) -> None:
    """The trial install proves the lock only on this machine. This check
    asks uv for every supported platform and Python version, so a release
    that some agents could never install stops here."""
    floor = tuple(int(part) for part in python_floor.split("."))
    versions = [v for v in LOCK_PYTHON_VERSIONS if tuple(int(p) for p in v.split(".")) >= floor]
    if not versions:
        raise ReleaseError(
            f"LOCK_PYTHON_VERSIONS has no version at or above requires-python >={python_floor}"
        )
    scratch = out_dir / "lock-check.txt"
    missing: list[str] = []
    for platform in LOCK_TARGET_PLATFORMS:
        env = _uv_env()
        macos = ""
        if platform.endswith("-apple-darwin"):
            env["MACOSX_DEPLOYMENT_TARGET"] = LOCK_MACOS_MINIMUM
            macos = f" on macOS {LOCK_MACOS_MINIMUM}"
        for version in versions:
            r = subprocess.run(
                [
                    "uv", "--no-config", "pip", "compile", str(lock),
                    "--python-platform", LOCK_PLATFORM_FOR_PYTHON.get((platform, version), platform),
                    "--python-version", version, "--default-index", PYPI_INDEX,
                    "--no-build", "--no-deps", "--quiet", "--output-file", str(scratch),
                ],
                capture_output=True, text=True, env=env,
            )
            if r.returncode != 0:
                cause = next(
                    (line.split("cause:", 1)[1].strip() for line in r.stderr.splitlines() if "cause:" in line),
                    r.stderr.strip()[-300:],
                )
                missing.append(f"  {platform}, Python {version}{macos}: {cause}")
    scratch.unlink(missing_ok=True)
    if missing:
        raise ReleaseError(
            "the dependency lock cannot be installed everywhere:\n" + "\n".join(missing) + "\n"
            "Give the dependency a version with a wheel for that platform (a requirement with "
            "a platform marker in mobaide_agent/pyproject.toml), or remove the platform from "
            "LOCK_TARGET_PLATFORMS when agents no longer run there."
        )
    ok(
        f"The lock has wheels for {len(LOCK_TARGET_PLATFORMS)} platforms x Python "
        f"{', '.join(versions)} (macOS {LOCK_MACOS_MINIMUM} and newer)"
    )


def trial_install(lock: Path, wheel: Path, wheel_sha: str, out_dir: Path) -> None:
    """Install the way the agent's updater does. A lock that pip refuses here
    would stop every agent from taking the release."""
    venv = out_dir / "trial-venv"
    r = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ReleaseError(f"could not create the trial venv:\n{r.stderr.strip()}")
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    requirements = out_dir / "trial-requirements.txt"
    requirements.write_text(
        lock.read_text(encoding="utf-8")
        + f"\nmobaide-agent @ {wheel.resolve().as_uri()} --hash=sha256:{wheel_sha}\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            str(python), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
            "--no-input", "--require-hashes", "--only-binary", ":all:", "-r", str(requirements),
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise ReleaseError(f"trial install with the dependency lock failed:\n{(r.stdout + r.stderr).strip()[-3000:]}")
    shutil.rmtree(venv, ignore_errors=True)
    requirements.unlink(missing_ok=True)
    ok("Trial install with the dependency lock works")


def build_artifacts(src: Path, version: str, out_dir: Path) -> tuple[Path, Path, Path]:
    agent_dir = src / "mobaide_agent"
    with (agent_dir / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]
    py_version = project["version"]
    if py_version != version:
        raise ReleaseError(f"mobaide_agent/pyproject.toml has version {py_version}, the tag says {version}")

    dist = out_dir / "dist"
    r = subprocess.run(
        [
            "uv", "--no-config", "build", "--wheel", "--sdist",
            "--default-index", PYPI_INDEX, "--out-dir", str(dist),
        ],
        cwd=agent_dir, env=_uv_env(),
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

    python_floor = minimum_python(project["requires-python"])
    lock = load_dependency_lock(agent_dir, out_dir, python_floor)
    check_lock_has_wheels(lock, out_dir, python_floor)
    trial_install(lock, wheel, sha256_file(wheel), out_dir)

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
        tar.add(lock, arcname=DEPENDENCY_LOCK_NAME, filter=_filter)
    ok(f"Built {tarball.name}")
    return wheel, tarball, lock


def release_file_url(server_url: str, sha256: str, filename: str) -> str:
    return f"{server_url}/api/v1/agent/releases/{sha256}/{filename}"


def sign_manifest(key, version: str, server_url: str, tarball: Path, tarball_sha: str,
                  wheel: Path, wheel_sha: str, lock_sha: str,
                  relay_pairing_key: str | None = None, next_signing_key: str | None = None) -> dict:
    # Must stay byte-compatible with the canonical form the relay and the
    # agent verify: sorted keys, no whitespace.
    body = {
        "version": version,
        "tarball_url": release_file_url(server_url, tarball_sha, tarball.name),
        "tarball_sha256": tarball_sha,
        "wheel_url": release_file_url(server_url, wheel_sha, wheel.name),
        "wheel_sha256": wheel_sha,
        # SHA-256 of requirements.lock inside the tarball. The agent installs
        # dependencies only at the versions and hashes that file lists.
        "dependency_lock_sha256": lock_sha,
        "released_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_central_server_version": MIN_CENTRAL_SERVER_VERSION,
        # Keys the agent trusts because this signed body lists them: the keys
        # that may sign the pairing credential push (the full list), and more
        # keys that may sign a manifest.
        "trust": {
            "relay_pairing_public_keys": [relay_pairing_key] if relay_pairing_key else [],
            "release_public_keys": [next_signing_key] if next_signing_key else [],
        },
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
    relay_pairing_key = read_optional_public_key(release_dir, RELAY_PAIRING_PUBLIC_KEY_FILE)
    next_signing_key = read_optional_public_key(release_dir, NEXT_SIGNING_PUBLIC_KEY_FILE)
    ok(f"Keys loaded from {release_dir}")
    check_trust_key(key, server_url)
    check_relay_pairing_key(relay_pairing_key, server_url)
    if next_signing_key:
        info(f"The manifest adds a second trusted release key: {next_signing_key}")
    tag, version, commit = resolve_release_tag(TAG_PREFIX, args.version)

    out_dir = Path(tempfile.mkdtemp(prefix="mobaide-agent-out-"))
    step(f"Build {tag}")
    with temporary_worktree(commit) as src:
        check_install_scripts(src, key)
        wheel, tarball, lock = build_artifacts(src, version, out_dir)
    tarball_sha, wheel_sha, lock_sha = sha256_file(tarball), sha256_file(wheel), sha256_file(lock)
    dim(f"sha256 {tarball_sha}  {tarball.name}")
    dim(f"sha256 {wheel_sha}  {wheel.name}")
    dim(f"sha256 {lock_sha}  {lock.name}")

    step("Sign manifest")
    record = sign_manifest(
        key, version, server_url, tarball, tarball_sha, wheel, wheel_sha, lock_sha,
        relay_pairing_key=relay_pairing_key, next_signing_key=next_signing_key,
    )
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
    p.add_argument("--new-relay-pairing-key", action="store_true",
                   help="print a new relay pairing key pair and exit; nothing is saved or published")
    p.add_argument("--update-lock", action="store_true",
                   help="write mobaide_agent/requirements.lock for the current pyproject.toml and exit; "
                        "versions already in the lock stay when they still fit")
    p.add_argument("--upgrade-lock", action="store_true",
                   help="like --update-lock, but take the newest version of every dependency")
    p.add_argument("--release-dir", help="folder with the release keys (default: $MOBAIDE_RELEASE_DIR, else ~/.config/mobaide-release, on Windows %%LOCALAPPDATA%%\\mobaide-release)")
    p.add_argument("--server-url", default=CENTRAL_SERVER_URL, help=f"relay base URL (default: {CENTRAL_SERVER_URL})")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("release_agent.py"):
        return 1
    args = build_parser().parse_args(argv)
    enable_ansi()
    try:
        if args.new_relay_pairing_key:
            return print_new_relay_pairing_key()
        if args.update_lock or args.upgrade_lock:
            return update_lock(upgrade=args.upgrade_lock)
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
