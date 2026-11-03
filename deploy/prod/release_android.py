#!/usr/bin/env python3
"""release_android.py — Build, sign and upload an Android release from this machine.

Usage:
    python deploy/prod/release_android.py                 # newest android-vX.Y.Z tag
    python deploy/prod/release_android.py 3.6.15          # a specific version
    python deploy/prod/release_android.py --skip-upload   # build + sign only, upload nothing
    python deploy/prod/release_android.py --force         # allow a dirty working tree

What it does:
  1) Preflight: tools, JDK 17+, Android SDK, clean tree, release dir keys,
     the tag matches origin
  2) Detached worktree at the tag: check versionName, read the in-app update
     priority from android/CHANGELOG.md (BREAKING 5, Bug Fixes 3, else 1)
  3) ./gradlew bundleRelease with the local upload keystore
  4) Check that the bundle certificate is the upload-key certificate, copy
     the bundle to android/app/release/ and print its SHA-256
  5) Upload to the Play internal track through the Play Developer API

Keys come from the release dir (see release_common.py); nothing is stored on
GitHub. Needs the `cryptography` package for the Play API service-account
token (not for --skip-upload).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
    read_properties,
    read_secret,
    require_tools,
    resolve_release_dir,
    resolve_release_tag,
    secret_file,
    sha256_file,
    temporary_worktree,
)

PACKAGE_NAME = "com.mobaide.app"
TAG_PREFIX = "android-v"
TRACK = "internal"
MIN_JDK = 17
OUTPUT_DIR = PROJECT_ROOT / "android" / "app" / "release"  # git-ignored

KEYSTORE_FILE = "android/upload.keystore"
SIGNING_PROPS_FILE = "android/signing.properties"
WEB_CLIENT_ID_FILE = "android/google-web-client-id"
GOOGLE_SERVICES_FILE = "android/google-services.json"
PLAY_ACCOUNT_FILE = "android/play-service-account.json"

# build.gradle.kts reads these before local.properties. Dropping them makes a
# developer shell (e.g. a LAN CENTRAL_SERVER_URL) unable to leak into a store
# build; the Gradle defaults are the production values.
SCRUBBED_ENV = ("CENTRAL_SERVER_URL", "PRIVACY_POLICY_URL", "SUPPORT_GITHUB_URL", "SUPPORT_EMAIL")

PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
PLAY_API = f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{PACKAGE_NAME}"
PLAY_UPLOAD_API = f"https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/{PACKAGE_NAME}"


# ------------------------------------------------------------ toolchain
def find_java_home() -> Path | None:
    env = os.environ.get("JAVA_HOME")
    if env and (Path(env) / "bin").is_dir():
        return Path(env)
    if platform.system() == "Darwin":
        jbr = Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home")
        if jbr.is_dir():
            return jbr
    return None


def java_tool(java_home: Path | None, name: str) -> str:
    if java_home is not None:
        exe = java_home / "bin" / (f"{name}.exe" if os.name == "nt" else name)
        if exe.exists():
            return str(exe)
    found = shutil.which(name)
    if found is None:
        raise ReleaseError(f"{name} not found: set JAVA_HOME to a JDK {MIN_JDK}+")
    return found


def check_jdk(java_home: Path | None) -> None:
    r = subprocess.run([java_tool(java_home, "java"), "-version"], capture_output=True, text=True)
    m = re.search(r'version "(\d+)(?:\.(\d+))?', r.stderr + r.stdout)
    if not m:
        raise ReleaseError(f"cannot read the JDK version:\n{r.stderr.strip()}")
    major = int(m.group(1))
    if major == 1 and m.group(2):
        major = int(m.group(2))
    if major < MIN_JDK:
        raise ReleaseError(f"JDK {major} found; JDK {MIN_JDK}+ is required (set JAVA_HOME)")
    ok(f"JDK {major} ({java_home or 'from PATH'})")


def find_android_sdk() -> Path:
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            sdk = Path(os.environ[var])
            break
    else:
        system = platform.system()
        if system == "Darwin":
            sdk = Path.home() / "Library" / "Android" / "sdk"
        elif system == "Windows":
            sdk = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk"
        else:
            sdk = Path.home() / "Android" / "Sdk"
    if not sdk.is_dir():
        raise ReleaseError(f"Android SDK not found at {sdk}; set ANDROID_HOME")
    ok(f"Android SDK: {sdk}")
    return sdk


# ------------------------------------------------------------ version + priority
def check_version_name(src: Path, version: str) -> None:
    text = (src / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    m = re.search(r'appVersionName\s*=\s*"([^"]+)"', text)
    if not m or m.group(1) != version:
        found = m.group(1) if m else "<none>"
        raise ReleaseError(f"build.gradle.kts has versionName {found}, the tag says {version}")


def expected_version_code(version: str) -> int:
    major, minor, patch = (int(p) for p in version.split("."))
    return major * 10000 + minor * 100 + patch


def update_priority(changelog: str, version: str) -> int:
    """In-app update priority from this version's CHANGELOG section:
    a breaking change 5, else a bug fix 3, else 1."""
    section: list[str] = []
    found = False
    for line in changelog.splitlines():
        if line.startswith(f"## [{version}]"):
            found = True
            continue
        if found and line.startswith("## ["):
            break
        if found:
            section.append(line)
    if not found:
        warn(f"no CHANGELOG section for {version}; using priority 1")
        return 1
    if any(re.match(r"### .*BREAKING", line, re.IGNORECASE) for line in section):
        return 5
    if any(line.startswith("### Bug Fixes") for line in section):
        return 3
    return 1


# ------------------------------------------------------------ build + verify
def gradle_env(
    java_home: Path | None, sdk: Path, keystore: Path, props: dict[str, str], web_client_id: str
) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    if java_home is not None:
        env["JAVA_HOME"] = str(java_home)
    env["ANDROID_HOME"] = env["ANDROID_SDK_ROOT"] = str(sdk)
    env["ANDROID_KEYSTORE_PATH"] = str(keystore)
    env["ANDROID_KEYSTORE_PASSWORD"] = props["storePassword"]
    env["ANDROID_KEY_ALIAS"] = props["keyAlias"]
    env["ANDROID_KEY_PASSWORD"] = props["keyPassword"]
    env["GOOGLE_WEB_CLIENT_ID"] = web_client_id
    return env


def build_bundle(src: Path, env: dict[str, str]) -> Path:
    android_dir = src / "android"
    gradlew = android_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    r = subprocess.run(
        [str(gradlew), "bundleRelease", "--no-daemon", "--console=plain"],
        cwd=android_dir, env=env,
    )
    if r.returncode != 0:
        raise ReleaseError("gradle bundleRelease failed")
    aab = android_dir / "app" / "build" / "outputs" / "bundle" / "release" / "app-release.aab"
    if not aab.is_file():
        raise ReleaseError(f"bundle not found at {aab}")
    return aab


def _sha256_fingerprints(output: str) -> set[str]:
    return {m.upper() for m in re.findall(r"SHA256:\s*([0-9A-Fa-f:]{95})", output)}


def check_bundle_certificate(java_home: Path | None, aab: Path, keystore: Path, props: dict[str, str]) -> None:
    """build.gradle.kts falls back to debug signing when the release config is
    incomplete; Play rejects that bundle only at upload. Catch it here."""
    keytool = java_tool(java_home, "keytool")
    env = dict(os.environ, MOBAIDE_STOREPASS=props["storePassword"])
    r = subprocess.run(
        [keytool, "-J-Duser.language=en", "-list", "-v", "-keystore", str(keystore),
         "-alias", props["keyAlias"], "-storepass:env", "MOBAIDE_STOREPASS"],
        capture_output=True, text=True, env=env,
    )
    expected = _sha256_fingerprints(r.stdout)
    if r.returncode != 0 or not expected:
        raise ReleaseError(f"cannot read the upload certificate from the keystore:\n{r.stderr.strip() or r.stdout.strip()}")
    r = subprocess.run(
        [keytool, "-J-Duser.language=en", "-printcert", "-jarfile", str(aab)],
        capture_output=True, text=True,
    )
    actual = _sha256_fingerprints(r.stdout)
    if r.returncode != 0 or not actual:
        raise ReleaseError(f"cannot read the bundle certificate:\n{r.stderr.strip() or r.stdout.strip()}")
    if actual != expected:
        raise ReleaseError(
            "the bundle is not signed with the upload key (debug-signing fallback?)\n"
            f"  bundle:   {', '.join(sorted(actual))}\n  keystore: {', '.join(sorted(expected))}"
        )
    ok("Bundle is signed with the upload key")


# ------------------------------------------------------------ Play Developer API
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def play_access_token(account: dict) -> str:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:
        raise ReleaseError(
            "the 'cryptography' package is missing. Run: python3 -m pip install cryptography"
        ) from e
    token_uri = account.get("token_uri", "https://oauth2.googleapis.com/token")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": account["client_email"], "scope": PLAY_SCOPE, "aud": token_uri,
        "iat": now, "exp": now + 3600,
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[call-arg]
    assertion = f"{signing_input}.{_b64url(signature)}"
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    resp = _google("token", lambda: http_json(
        token_uri, method="POST", data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ))
    return resp["access_token"]


def _google(what: str, fn):
    try:
        return fn()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:2000]
        raise ReleaseError(f"Play API {what}: HTTP {e.code}\n{detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise ReleaseError(f"Play API {what}: {e}") from e


def upload_to_play(aab: Path, aab_sha: str, version: str, priority: int, account: dict) -> None:
    auth = {"Authorization": f"Bearer {play_access_token(account)}"}
    ok(f"Play API token for {account['client_email']}")

    edit_id = _google("edits.insert", lambda: http_json(
        f"{PLAY_API}/edits", method="POST", data=b"{}",
        headers={**auth, "Content-Type": "application/json"},
    ))["id"]
    committed = False
    try:
        dim(f"edit {edit_id}: uploading {aab.stat().st_size / 1e6:.1f} MB")
        bundle = _google("bundles.upload", lambda: http_json(
            f"{PLAY_UPLOAD_API}/edits/{edit_id}/bundles?uploadType=media",
            method="POST", data=aab.read_bytes(), timeout=900,
            headers={**auth, "Content-Type": "application/octet-stream"},
        ))
        version_code = int(bundle["versionCode"])
        if bundle.get("sha256", "").lower() != aab_sha:
            raise ReleaseError(f"Play stored sha256 {bundle.get('sha256')}, local is {aab_sha}")
        if version_code != expected_version_code(version):
            raise ReleaseError(f"Play read versionCode {version_code}, expected {expected_version_code(version)}")
        ok(f"Bundle uploaded (versionCode {version_code})")

        track = {
            "track": TRACK,
            "releases": [{
                "versionCodes": [str(version_code)],
                "status": "completed",
                "inAppUpdatePriority": priority,
            }],
        }
        _google("tracks.update", lambda: http_json(
            f"{PLAY_API}/edits/{edit_id}/tracks/{TRACK}", method="PUT",
            data=json.dumps(track).encode(),
            headers={**auth, "Content-Type": "application/json"},
        ))
        _google("edits.commit", lambda: http_json(
            f"{PLAY_API}/edits/{edit_id}:commit", method="POST", data=b"",
            headers=auth,
        ))
        committed = True
        ok(f"Committed to the {TRACK} track (inAppUpdatePriority {priority})")
    finally:
        if not committed:
            try:
                http_request(f"{PLAY_API}/edits/{edit_id}", method="DELETE", headers=auth)
                dim(f"edit {edit_id} deleted")
            except Exception:
                pass


# ------------------------------------------------------------ main flow
def run(args: argparse.Namespace) -> int:
    step("Preflight")
    require_tools("git")
    java_home = find_java_home()
    check_jdk(java_home)
    sdk = find_android_sdk()
    if args.force:
        warn("--force: skipping clean-tree check")
    else:
        check_clean_tree()

    release_dir = resolve_release_dir(args.release_dir)
    keystore = secret_file(release_dir, KEYSTORE_FILE)
    props = read_properties(secret_file(release_dir, SIGNING_PROPS_FILE))
    missing = [k for k in ("storePassword", "keyPassword", "keyAlias") if not props.get(k)]
    if missing:
        raise ReleaseError(f"{release_dir / SIGNING_PROPS_FILE} lacks: {', '.join(missing)}")
    web_client_id = read_secret(release_dir, WEB_CLIENT_ID_FILE)
    google_services = secret_file(release_dir, GOOGLE_SERVICES_FILE)
    account = None
    if not args.skip_upload:
        account = json.loads(read_secret(release_dir, PLAY_ACCOUNT_FILE))
        if account.get("type") != "service_account":
            raise ReleaseError(f"{release_dir / PLAY_ACCOUNT_FILE} is not a service-account key")
    ok(f"Keys loaded from {release_dir}")

    tag, version, commit = resolve_release_tag(TAG_PREFIX, args.version)

    step(f"Build {tag}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUTPUT_DIR / f"mobaide-{version}.aab"
    with temporary_worktree(commit) as src:
        check_version_name(src, version)
        priority = update_priority(
            (src / "android" / "CHANGELOG.md").read_text(encoding="utf-8"), version
        )
        ok(f"versionName {version}, in-app update priority {priority}")
        shutil.copyfile(google_services, src / "android" / "app" / "google-services.json")
        aab = build_bundle(src, gradle_env(java_home, sdk, keystore, props, web_client_id))
        check_bundle_certificate(java_home, aab, keystore, props)
        shutil.copyfile(aab, out)
    aab_sha = sha256_file(out)
    ok(f"Bundle: {out}")
    dim(f"sha256 {aab_sha}  {out.name}")

    if args.skip_upload:
        info("")
        info("--skip-upload: nothing was uploaded.")
        return 0

    assert account is not None
    step(f"Upload to Play ({TRACK} track)")
    upload_to_play(out, aab_sha, version, priority, account)

    info("")
    info(f"Released {tag} to the Play {TRACK} track. Bundle sha256 {aab_sha}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build, sign and upload an Android release")
    p.add_argument("version", nargs="?", help="X.Y.Z (default: the newest android-v tag)")
    p.add_argument("--skip-upload", action="store_true", help="build and sign only")
    p.add_argument("--force", action="store_true", help="allow a dirty working tree")
    p.add_argument("--release-dir", help="folder with the release keys (default: $MOBAIDE_RELEASE_DIR or ~/.config/mobaide-release)")
    return p


def main(argv: list[str] | None = None) -> int:
    if not require_min_python("release_android.py"):
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
