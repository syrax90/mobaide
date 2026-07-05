"""Text/AST pins on the venv "bad interpreter" fix in ``deploy/install.sh``.

Sibling to ``test_install_sh.py`` (post-service pairing-QR pins) and
``test_install_ps1.py``'s ``Install-Release`` checks. Two contracts the
fix added to ``stage_release`` are pinned here, both of which are
load-bearing for the cross-platform recovery semantics introduced
alongside the updater rewrite:

1. **Completion sentinel.** ``stage_release`` must write
   ``releases/<v>/.installed`` after pip + the wheel are installed.
   ``mobaide.agent.install_paths.is_release_complete`` (and the inlined
   copies in ``mobaide.launcher``, ``deploy/windows_launcher.py``, and
   this script's own self-heal heredoc) treat a release as runnable only
   when it carries that sentinel OR the legacy ``mobaide-agent`` console
   script. A new shell-installed release has the console script, but the
   sentinel is the canonical signal — dropping its write would leave new
   installs depending solely on the legacy fallback and would silently
   diverge from the updater, which writes the sentinel as its final step.

2. **Base-interpreter hardening (R1).** ``stage_release`` must base the
   release venv off a *stable* interpreter (``resolve_base_python`` —
   ``sys._base_executable``) and clear ``VIRTUAL_ENV`` / ``PYTHONHOME``
   for the ``python -m venv`` call. The original "bad interpreter" report
   came from a venv whose interpreter symlinked into a transient venv;
   once that venv moved/vanished, ``pip`` / ``mobaide-agent`` shebangs
   pointed at a swept path. Resolving the base interpreter and dropping
   ``VIRTUAL_ENV`` makes the release venv symlink the real system
   interpreter, which never moves.

These are pure text/AST pins on the source-of-truth file — they do NOT
execute the installer. The *behaviour* of ``resolve_base_python`` (that a
venv created with a dummy ``VIRTUAL_ENV`` set still bases off the stable
interpreter) is covered by the runtime sibling
``test_install_sh_stage_release_runtime.py`` pattern; this module pins the
shape so a refactor that drops the sentinel write or reverts to a bare
``python3 -m venv`` is caught at parse time.
"""
from __future__ import annotations

import re
import subprocess
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

    Same canonical ``name() {{ ... }}`` extraction the sibling
    ``test_install_sh.py::_function_body`` uses: every function in
    ``deploy/install.sh`` has its closing ``}}`` at column 0, so the
    non-greedy match up to ``^}}`` slices the body without a full parser.
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

    Cheap insurance against syntax breakage introduced by the new helper
    and the heredoc edits. Mirrors the same ``_BASH`` lookup the sibling
    install-sh tests use so Windows hosts with Git Bash run it too;
    auto-skips where bash is unavailable.
    """
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
