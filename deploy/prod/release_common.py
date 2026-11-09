r"""Shared plumbing for release_agent.py and release_android.py.

Both scripts sign and publish from the operator's machine, so every key they
need lives in one local folder outside the repo (the release dir) and no
release secret is stored on GitHub. Default location:

    macOS / Linux   ~/.config/mobaide-release/      (chmod 700, files 600)
    Windows         %LOCALAPPDATA%\mobaide-release\ (local, not roaming; only
                                                     your account may have access)

Layout:

    <release dir>/
        agent/manifest-signing-key      base64 Ed25519 private key (32 raw bytes) or PEM
        agent/release-token             same string as the relay's RELEASE_AUTH_TOKEN
        android/upload.keystore
        android/signing.properties      storePassword, keyPassword, keyAlias
        android/google-web-client-id
        android/google-services.json
        android/play-service-account.json
        voice/r2.properties             account_id, bucket, access_key_id, secret_access_key
                                        (upload_voice_model.py)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path

from deploy_central import PROJECT_ROOT, DeployError, dim, http_ssl_context, ok, warn

RELEASE_DIR_ENV = "MOBAIDE_RELEASE_DIR"

# Windows SIDs whose access makes a key readable by other local users, the
# equivalent of the POSIX group/other bits. Checked by SID, not by name,
# because Windows localises group names.
_BROAD_SIDS = {
    "S-1-1-0": "Everyone",
    "S-1-5-7": "ANONYMOUS LOGON",
    "S-1-5-11": "Authenticated Users",
    "S-1-5-32-545": "Users",
    "S-1-5-32-546": "Guests",
}
_ACL_SIDS_PS = (
    "(Get-Acl -LiteralPath $env:MOBAIDE_ACL_PATH).Access"
    " | Where-Object { $_.AccessControlType -eq 'Allow' }"
    " | ForEach-Object { $_.IdentityReference.Translate("
    "[System.Security.Principal.SecurityIdentifier]).Value }"
)


def default_release_dir() -> Path:
    # LOCALAPPDATA, not APPDATA: a roaming profile would copy the keys to
    # every machine the account signs in to.
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "mobaide-release"
    return Path.home() / ".config" / "mobaide-release"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class ReleaseError(DeployError):
    """Release failure; shares DeployError's 'empty message = already printed'."""


# ------------------------------------------------------------ release dir
def resolve_release_dir(cli_value: str | None) -> Path:
    raw = cli_value or os.environ.get(RELEASE_DIR_ENV) or str(default_release_dir())
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        how = "only your account may have access" if os.name == "nt" else "chmod 700"
        raise ReleaseError(
            f"release dir {path} does not exist. Create it ({how}) and put the "
            "release keys in it; see docs/versioning-and-updates.md."
        )
    try:
        path.relative_to(PROJECT_ROOT)
    except ValueError:
        pass
    else:
        raise ReleaseError(f"release dir {path} is inside the repository; keep keys outside it")
    _require_private(path)
    return path


def _require_private(path: Path) -> None:
    """Refuse a key file or folder that other local users can read."""
    if os.name == "nt":
        _require_private_windows(path)
        return
    if os.name != "posix":
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        wanted = "700" if path.is_dir() else "600"
        raise ReleaseError(
            f"{path} is accessible to other users (mode {stat.S_IMODE(mode):o}). "
            f"Run: chmod {wanted} '{path}'"
        )


def _require_private_windows(path: Path) -> None:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _ACL_SIDS_PS],
        capture_output=True, text=True, env=dict(os.environ, MOBAIDE_ACL_PATH=str(path)),
    )
    if r.returncode != 0:
        raise ReleaseError(f"cannot read the access list of {path}:\n{r.stderr.strip()}")
    broad = sorted({_BROAD_SIDS[sid] for sid in r.stdout.split() if sid in _BROAD_SIDS})
    if broad:
        user = os.environ.get("USERNAME", "<your user>")
        fix = (
            f'icacls "{path}" /inheritance:r /grant:r "{user}:(OI)(CI)F"'
            if path.is_dir()
            else f'icacls "{path}" /reset'
        )
        raise ReleaseError(f"{path} is accessible to {', '.join(broad)}. Run: {fix}")


def secret_file(release_dir: Path, rel: str) -> Path:
    path = release_dir / rel
    if not path.is_file():
        raise ReleaseError(f"missing {path}")
    _require_private(path.parent)
    _require_private(path)
    return path


def read_secret(release_dir: Path, rel: str) -> str:
    value = secret_file(release_dir, rel).read_text(encoding="utf-8").strip()
    if not value:
        raise ReleaseError(f"{release_dir / rel} is empty")
    return value


def read_properties(path: Path) -> dict[str, str]:
    """Minimal Java .properties reader: `key=value` or `key: value` lines,
    `#`/`!` comments. No escapes or line continuations."""
    props: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line[0] in "#!":
            continue
        m = re.match(r"([^=:\s]+)\s*[=:]\s*(.*)$", line)
        if m:
            props[m.group(1)] = m.group(2)
    return props


# ------------------------------------------------------------ local tools
def require_tools(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise ReleaseError(f"not found in PATH: {', '.join(missing)}")
    ok(f"Local tools present: {', '.join(tools)}")


def git(*args: str, cwd: Path = PROJECT_ROOT, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if check and r.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


# ------------------------------------------------------------ tags + worktree
def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def resolve_release_tag(prefix: str, version: str | None) -> tuple[str, str, str]:
    """Return (tag, version, commit) after checking the tag matches origin.

    The check stops a tag that exists only locally, or that was moved locally,
    from being built and signed as if it were the published release commit.
    """
    git("fetch", "--quiet", "origin", "--tags")
    if version is None:
        tags = [
            t for t in git("tag", "--list", f"{prefix}*").splitlines()
            if _SEMVER_RE.match(t[len(prefix):])
        ]
        if not tags:
            raise ReleaseError(f"no {prefix}X.Y.Z tag found")
        version = max((t[len(prefix):] for t in tags), key=_version_key)
    elif not _SEMVER_RE.match(version):
        raise ReleaseError(f"version must be X.Y.Z, got {version!r}")
    tag = f"{prefix}{version}"

    local = git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}", check=False)
    if not local:
        raise ReleaseError(f"tag {tag} does not exist locally or on origin")
    remote_lines = git("ls-remote", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}").splitlines()
    remote: dict[str, str] = {}
    for line in remote_lines:
        sha, ref = line.split("\t", 1)
        remote[ref] = sha
    remote_commit = remote.get(f"refs/tags/{tag}^{{}}") or remote.get(f"refs/tags/{tag}")
    if remote_commit is None:
        raise ReleaseError(f"tag {tag} is not on origin. Release only tags that release-please pushed.")
    if remote_commit != local:
        raise ReleaseError(f"local tag {tag} ({local[:12]}) differs from origin ({remote_commit[:12]})")
    ok(f"{tag} -> {local[:12]} (matches origin)")
    return tag, version, local


@contextlib.contextmanager
def temporary_worktree(commit: str) -> Generator[Path]:
    """A detached worktree at `commit`, removed on exit."""
    parent = Path(tempfile.mkdtemp(prefix="mobaide-release-"))
    path = parent / "src"
    # LF checkout on every OS: with core.autocrlf=true (the Windows default)
    # the shipped .py files would get CRLF and the build would differ from
    # the one made on macOS / Linux.
    git("-c", "core.autocrlf=false", "-c", "core.eol=lf",
        "worktree", "add", "--quiet", "--detach", str(path), commit)
    dim(f"worktree: {path}")
    try:
        yield path
    finally:
        r = subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        if r.returncode != 0:
            warn(f"could not remove worktree {path}: {r.stderr.strip()}")
        shutil.rmtree(parent, ignore_errors=True)


# ------------------------------------------------------------ hashing + HTTP
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def http_request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60,
) -> tuple[int, bytes]:
    """One HTTP call through the shared TLS context. Raises HTTPError on 4xx/5xx."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    req.add_header("User-Agent", "mobaide-release-script")
    context = http_ssl_context()
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.status, resp.read()


def http_json(url: str, **kwargs) -> dict:
    _, body = http_request(url, **kwargs)
    return json.loads(body)


def with_retries(what: str, fn, *, attempts: int = 5, delay: float = 10, retry_4xx: bool = False):
    """Retry `fn` on transport errors and 5xx (and 4xx when asked)."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code < 500 and not retry_4xx:
                detail = e.read().decode("utf-8", "replace")[:500]
                raise ReleaseError(f"{what}: HTTP {e.code} {detail}") from e
            error = f"HTTP {e.code}"
        except (urllib.error.URLError, OSError) as e:
            error = str(e)
        if attempt == attempts:
            raise ReleaseError(f"{what}: failed after {attempts} attempts ({error})")
        dim(f"{what}: {error}; retry {attempt}/{attempts - 1} in {delay:.0f}s")
        time.sleep(delay)
    raise AssertionError("unreachable")
