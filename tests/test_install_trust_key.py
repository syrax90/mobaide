"""The install scripts carry the release public key.

A new install trusts the key inside the script it runs. It does not ask the
server that serves the manifest which key to trust. ``MOBAIDE_AGENT_TRUST_KEY_B64``
still selects the key of a self-hosted release pipeline.

The checks on ``deploy/install.ps1`` read the script text, so they run
without PowerShell.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from test_install_script_trap import _BASH, _LOG_FUNCS, _sh_function

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"
_INSTALL_PS1 = _REPO_ROOT / "deploy" / "install.ps1"

_SH_KEY_LINE_RE = re.compile(r'^MOBAIDE_RELEASE_PUBLIC_KEY_B64="([^"]+)"$', re.M)
_PS1_KEY_LINE_RE = re.compile(r'^\$Script:ReleasePublicKeyB64 = "([^"]+)"$', re.M)

_needs_bash = pytest.mark.skipif(
    _BASH is None or sys.platform == "win32",
    reason="runs install.sh functions under a POSIX bash",
)


def _carried_key(script: Path, line_re: re.Pattern[str]) -> str:
    keys = line_re.findall(script.read_text(encoding="utf-8"))
    assert len(keys) == 1, f"{script.name} must define the release key once: {keys}"
    return keys[0]


def test_both_install_scripts_carry_the_same_ed25519_public_key() -> None:
    sh_key = _carried_key(_INSTALL_SH, _SH_KEY_LINE_RE)
    ps1_key = _carried_key(_INSTALL_PS1, _PS1_KEY_LINE_RE)

    assert sh_key == ps1_key
    Ed25519PublicKey.from_public_bytes(base64.b64decode(sh_key, validate=True))


@pytest.mark.parametrize("script", [_INSTALL_SH, _INSTALL_PS1], ids=lambda p: p.name)
def test_install_script_does_not_read_the_trust_key_endpoint(script: Path) -> None:
    assert "agent/trust_key" not in script.read_text(encoding="utf-8")


def _bootstrap_trust_key(operator_key: str) -> subprocess.CompletedProcess[str]:
    """Run ``bootstrap_trust_key`` and print the key it selected. Every
    ``curl`` call fails the run: the key must not come from the network."""
    assert _BASH is not None
    key_line = _SH_KEY_LINE_RE.search(_INSTALL_SH.read_text(encoding="utf-8"))
    assert key_line is not None
    harness = (
        "set -euo pipefail\n"
        + _LOG_FUNCS
        + "curl() { echo 'curl must not run' >&2; exit 97; }\n"
        + f"VERIFIER_PYTHON={shlex.quote(sys.executable)}\n"
        + key_line.group(0)
        + "\n"
        + f"MOBAIDE_AGENT_TRUST_KEY_B64={shlex.quote(operator_key)}\n"
        + _sh_function("_validate_trust_key_or_fail")
        + _sh_function("bootstrap_trust_key")
        + "bootstrap_trust_key\n"
        + "printf 'KEY=%s\\n' \"$MOBAIDE_AGENT_TRUST_KEY_B64\"\n"
    )
    return subprocess.run([_BASH, "-c", harness], capture_output=True, text=True)


@_needs_bash
def test_install_sh_trusts_the_carried_key_by_default() -> None:
    proc = _bootstrap_trust_key("")

    assert proc.returncode == 0, proc.stderr
    carried = _carried_key(_INSTALL_SH, _SH_KEY_LINE_RE)
    assert f"KEY={carried}" in proc.stdout.splitlines()


@_needs_bash
def test_install_sh_operator_key_replaces_the_carried_key() -> None:
    operator_key = base64.b64encode(
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    ).decode()

    proc = _bootstrap_trust_key(operator_key)

    assert proc.returncode == 0, proc.stderr
    assert f"KEY={operator_key}" in proc.stdout.splitlines()


@_needs_bash
@pytest.mark.parametrize(
    "operator_key",
    ["not-base64-!!", base64.b64encode(b"\x01" * 16).decode()],
    ids=["not-base64", "wrong-length"],
)
def test_install_sh_stops_on_a_malformed_operator_key(operator_key: str) -> None:
    proc = _bootstrap_trust_key(operator_key)

    assert proc.returncode != 0
    assert "KEY=" not in proc.stdout
    assert "not a valid base64 Ed25519 public key" in proc.stderr


def _signed_manifest_file(directory: Path, signer: Ed25519PrivateKey) -> Path:
    body = {"version": "1.2.3"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "body": body,
                "signature_b64": base64.b64encode(signer.sign(canonical)).decode(),
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _public_key_b64(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes_raw()).decode()


def _run_main_until_staging(
    tmp_path: Path, *, manifest: Path, operator_key: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run ``main`` with the real key selection, signature check and key
    write. The manifest comes from a local file and the run stops where the
    release would be staged, after printing the key file it finds there."""
    assert _BASH is not None
    prefix = tmp_path / "prefix"
    key_file = prefix / "trust" / "central_server.pub"
    key_line = _SH_KEY_LINE_RE.search(_INSTALL_SH.read_text(encoding="utf-8"))
    assert key_line is not None
    no_ops = (
        "cleanup_on_exit detect_platform check_macos_version "
        "require_root_when_writing_system_dir "
        "check_command check_python ensure_ensurepip_available "
        "resolve_verifier_python"
    )
    harness = (
        "set -euo pipefail\n"
        + _LOG_FUNCS
        + "curl() { echo 'curl must not run' >&2; exit 97; }\n"
        + "".join(f"{name}() {{ :; }}\n" for name in no_ops.split())
        + f"VERIFIER_PYTHON={shlex.quote(sys.executable)}\n"
        + f"MOBAIDE_AGENT_PREFIX={shlex.quote(str(prefix))}\n"
        + key_line.group(0)
        + "\n"
        + f"MOBAIDE_AGENT_TRUST_KEY_B64={shlex.quote(operator_key)}\n"
        + f'fetch_manifest() {{ cp {shlex.quote(str(manifest))} "$1"; }}\n'
        + "stage_release() {\n"
        + f"    printf 'STAGED_WITH_KEY=%s\\n' \"$(cat {shlex.quote(str(key_file))})\"\n"
        + "    exit 0\n"
        + "}\n"
        + _sh_function("_validate_trust_key_or_fail")
        + _sh_function("bootstrap_trust_key")
        + _sh_function("verify_manifest_signature")
        + _sh_function("write_trust_key")
        + _sh_function("main")
        + "main\n"
    )
    proc = subprocess.run(
        [_BASH, "-c", harness],
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(tmp_path)},
    )
    return proc, key_file


@_needs_bash
@pytest.mark.parametrize("install", ["new", "existing"])
def test_install_sh_does_not_store_a_key_that_does_not_verify_the_manifest(
    tmp_path: Path, install: str
) -> None:
    """The key file is what the agent trusts from then on. A run that stops at
    the signature check must leave that file as it was."""
    manifest = _signed_manifest_file(tmp_path, Ed25519PrivateKey.generate())
    key_of_the_install = _public_key_b64(Ed25519PrivateKey.generate())
    key_file = tmp_path / "prefix" / "trust" / "central_server.pub"
    if install == "existing":
        key_file.parent.mkdir(parents=True)
        key_file.write_text(key_of_the_install + "\n", encoding="utf-8")

    proc, _ = _run_main_until_staging(
        tmp_path,
        manifest=manifest,
        operator_key=_public_key_b64(Ed25519PrivateKey.generate()),
    )

    assert proc.returncode != 0
    assert "manifest signature does not verify" in proc.stderr
    assert "STAGED_WITH_KEY=" not in proc.stdout
    if install == "existing":
        assert key_file.read_text(encoding="utf-8") == key_of_the_install + "\n"
    else:
        assert not key_file.exists()


@_needs_bash
def test_install_sh_stores_the_key_that_verified_the_manifest_before_staging(
    tmp_path: Path,
) -> None:
    signer = Ed25519PrivateKey.generate()
    manifest = _signed_manifest_file(tmp_path, signer)

    proc, key_file = _run_main_until_staging(
        tmp_path, manifest=manifest, operator_key=_public_key_b64(signer),
    )

    assert proc.returncode == 0, proc.stderr
    assert f"STAGED_WITH_KEY={_public_key_b64(signer)}" in proc.stdout.splitlines()
    assert key_file.read_text(encoding="utf-8") == _public_key_b64(signer) + "\n"


def test_install_ps1_stores_the_key_after_the_signature_check_and_before_staging() -> None:
    main = re.search(
        r"^function Main \{\n(.*?)^\}", _INSTALL_PS1.read_text(encoding="utf-8"), re.S | re.M,
    )
    assert main is not None
    body = "\n".join(
        line for line in main.group(1).splitlines() if not line.lstrip().startswith("#")
    )

    assert body.count("Write-TrustKey") == 1, body
    assert (
        -1
        < body.find("Test-ManifestSignature")
        < body.find("Write-TrustKey")
        < body.find("Install-Release")
    ), body
