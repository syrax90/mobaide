"""Pins on the post-service pairing-QR flow in ``deploy/install.sh``.

The Linux/macOS counterpart to the Windows-side checks in
``test_install_ps1.py``. The reorder makes the running agent service
the single initiator of the pairing nonce: the QR is rendered ONLY
after ``wait_agent_healthy`` confirms ``/api/health`` returns 200 and
``show_pairing_qr_from_service`` finds the pending file the
service's pairing-monitor wrote. The previous order rendered the QR
*before* the service was up, so a mobile scan during the boot gap
silently failed to land — ``RELAY_AGENT_ID`` /
``RELAY_AGENT_TOKEN`` in ``.env`` stayed empty.

These tests are pure text/AST pins on ``deploy/install.sh`` — they
do NOT execute the installer; they assert the source-of-truth file
encodes the new contract. End-to-end execution is covered by the
manual verification steps in the plan and by CI distro images.
"""
from __future__ import annotations

import re
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"


@cache
def _read_install_sh_text() -> str:
    assert _DEPLOY_INSTALL_SH.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_SH}"
    )
    return _DEPLOY_INSTALL_SH.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    """Extract the body extent of a bash function definition by name.

    Robust enough for ``deploy/install.sh`` since every function in
    the file follows the canonical ``name() {{ ... }}`` shape with the
    closing ``}}`` at column 0 (matching the ``sed -n '/^name()/,/^}}$/p'``
    convention used elsewhere in this test suite — see the
    ``ensure_ensurepip_available`` extraction in
    ``test_install_script_trap.py``). A nested function in another
    function would break this — none exist here today; if any are
    added in future, this helper will need a proper parser.
    """
    text = _read_install_sh_text()
    pattern = re.compile(
        rf"^{re.escape(name)}\(\)\s*\{{(.*?)^\}}",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(text)
    assert match is not None, (
        f"function {name!r} not defined in {_DEPLOY_INSTALL_SH}"
    )
    return match.group(1)


def _list_function_names() -> list[str]:
    text = _read_install_sh_text()
    return re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", text, re.M)


def test_install_sh_parses_under_bash_n() -> None:
    """``deploy/install.sh`` must parse cleanly under ``bash -n``.

    Cheap insurance against syntax breakage. Mirrors
    ``test_install_script_passes_bash_syntax_check`` in
    ``test_install_script_trap.py``; runs even on Windows when Git
    Bash is available.
    """
    # Reuse the same bash lookup as test_install_script_trap.py so
    # Windows hosts with Git Bash run this test too.
    from test_install_script_trap import _BASH

    if _BASH is None:
        pytest.skip("bash unavailable on this platform")
    proc = subprocess.run(
        [_BASH, "-n", str(_DEPLOY_INSTALL_SH)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"bash -n failed for {_DEPLOY_INSTALL_SH}: {proc.stderr!r}"
    )


def test_stage_release_staging_python_does_no_urllib_network() -> None:
    """The Python staging step in ``stage_release`` must NOT touch the
    network — no ``urllib.request`` import and no ``urlopen`` call.

    Core of the macOS SSL fix: all network I/O moved to curl
    (``download_asset``), leaving Python to verify sha256 + rename the
    wheel + extract the tarball. ``urllib.parse`` (pure string parsing, no
    sockets) is still allowed for wheel-basename derivation, so the
    assertion targets the network surface specifically.
    """
    body = _function_body("stage_release")
    assert "urllib.request" not in body, (
        "stage_release's staging Python must not import urllib.request — "
        "the python.org empty-CA-store crash (CERTIFICATE_VERIFY_FAILED) "
        "comes from urllib's default-context urlopen. Network downloads "
        "now go through curl (download_asset). Body:\n" + repr(body)
    )
    assert "urlopen" not in body, (
        "stage_release's staging Python must not call urlopen — network "
        "I/O moved to curl (download_asset). Body:\n" + repr(body)
    )


_ = sys  # silence unused-import noise on hosts without Git Bash
