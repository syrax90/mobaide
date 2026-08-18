"""Pins on the browser-pairing banner in ``deploy/install.ps1``.

Separate from ``test_install_ps1.py`` because that module skips wholesale
when no PowerShell host is on PATH — its assertions go through
``Parser::ParseFile``. These are plain file-text pins, so they run on every
platform, which is the point: the contract they guard is the Windows twin
of the one ``test_install_sh.py`` guards for bash, and it would otherwise be
unchecked on the Linux and macOS hosts where this suite is actually run.

The contract: the completion banner names the web-app pairing URL, but only
when a QR and its pairing hints were really printed. On an already-paired
re-install nothing is rendered, and "use the link or MOBA1 code above" would
then point at nothing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEPLOY_INSTALL_PS1 = _REPO_ROOT / "deploy" / "install.ps1"


def _text() -> str:
    assert _DEPLOY_INSTALL_PS1.is_file(), (
        f"missing install script: {_DEPLOY_INSTALL_PS1}"
    )
    return _DEPLOY_INSTALL_PS1.read_text(encoding="utf-8")


def test_pairing_web_origin_default_matches_the_agent() -> None:
    """One hostname, three copies, no drift.

    The banner sends the user to the same host the pairing link rendered
    above it points at, and that link is built by
    ``pairing_qr.format_pairing_link``. A shell script cannot import Python,
    so this is the guard that keeps the copies equal.
    """
    sys.path.insert(0, str(_REPO_ROOT / "mobaide_agent"))
    try:
        from mobaide.agent.pairing_qr import DEFAULT_PAIRING_WEB_ORIGIN
    except ImportError:  # pragma: no cover - agent deps absent
        pytest.skip("mobaide_agent not importable on this host")
    finally:
        sys.path.pop(0)

    assert (
        f'$Script:DefaultPairWebOrigin = "{DEFAULT_PAIRING_WEB_ORIGIN}"'
        in _text()
    )


def test_uac_relaunch_preserves_the_pairing_web_origin() -> None:
    """The elevated session rebuilds its environment from ``$passthrough``.

    A name missing from that list is silently dropped across the UAC
    boundary, so a self-hosted origin would revert to the production
    default in the elevated half of the install.
    """
    assert "'MOBAIDE_AGENT_PAIR_WEB_ORIGIN'," in _text()


def test_qr_rendered_flag_is_script_scoped_and_set_after_the_failure_guard(
) -> None:
    text = _text()
    assert re.search(r"^\$Script:QrRendered = \$false$", text, re.M), (
        "the flag must be initialised at script scope, beside the other "
        "$Script: defaults"
    )
    guard = text.index("show-qr --require-pending failed")
    setter = text.index("$Script:QrRendered = $true")
    assert setter > guard, (
        "the flag must be set after the $LASTEXITCODE guard, so a failed "
        "render never advertises a link the user never saw"
    )
    assert "$QrRendered = $true" not in text.replace(
        "$Script:QrRendered = $true", ""
    ), "a bare $QrRendered would create a function-local and never fire"


def test_completion_banner_points_at_the_web_app_only_when_rendered() -> None:
    text = _text()
    banner = text[text.index("function Write-Completion"):]
    assert "if ($Script:QrRendered)" in banner
    assert "Get-PairWebOrigin" in banner
    assert "MOBA1" in banner
