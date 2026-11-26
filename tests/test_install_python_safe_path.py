"""The install scripts keep Python away from files in the current directory.

Both scripts run Python for their checks (trust key, manifest signature,
manifest fields) from the directory the user is in. ``PYTHONSAFEPATH`` stops
Python from importing a file there in place of a standard module.

The check on ``deploy/install.ps1`` reads the script text, so it runs
without PowerShell.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from test_install_script_trap import _BASH, _LOG_FUNCS, _sh_function
from test_install_trust_key import _public_key_b64, _signed_manifest_file

_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALL_SH = _REPO_ROOT / "deploy" / "install.sh"
_INSTALL_PS1 = _REPO_ROOT / "deploy" / "install.ps1"

_SH_FIRST_FUNCTION_RE = re.compile(r"^\w+\(\)\s*\{")
_PS1_FIRST_FUNCTION_RE = re.compile(r"^function\s")


def _lines_before_the_first_function(
    script: Path, function_re: re.Pattern[str]
) -> list[str]:
    lines = script.read_text(encoding="utf-8").splitlines()
    first = next(i for i, line in enumerate(lines) if function_re.match(line))
    return lines[:first]


@pytest.mark.parametrize(
    "script, function_re, setting",
    [
        (_INSTALL_SH, _SH_FIRST_FUNCTION_RE, "export PYTHONSAFEPATH=1"),
        (_INSTALL_PS1, _PS1_FIRST_FUNCTION_RE, '$env:PYTHONSAFEPATH = "1"'),
    ],
    ids=lambda value: value.name if isinstance(value, Path) else None,
)
def test_install_script_sets_safe_path_before_any_python_runs(
    script: Path, function_re: re.Pattern[str], setting: str
) -> None:
    """Every Python call is in a function. The setting is a top-level line
    above the first function, and no line above the setting runs Python."""
    top = _lines_before_the_first_function(script, function_re)

    assert top.count(setting) == 1, f"{script.name} must set it once, unindented"
    code_above = [
        line
        for line in top[: top.index(setting)]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not [line for line in code_above if "python" in line.lower()], code_above


def test_install_ps1_removes_the_setting_when_main_ends() -> None:
    """Under ``irm | iex`` the script runs in the PowerShell session of the
    user, so the setting must not stay there, also when ``Main`` stops with
    an error. The script ends with ``try { Main } finally { <remove> }`` and
    calls ``Main`` nowhere else."""
    code = [
        line.strip()
        for line in _INSTALL_PS1.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert code.count("Main") == 1
    assert code[-5:-2] == ["try {", "Main", "} finally {"]
    assert re.fullmatch(r"Remove-Item\s+Env:\\?PYTHONSAFEPATH\b.*", code[-2])
    assert code[-1] == "}"


@pytest.mark.skipif(
    _BASH is None or sys.platform == "win32",
    reason="runs install.sh functions under a POSIX bash",
)
def test_install_sh_signature_check_ignores_a_module_in_the_current_directory(
    tmp_path: Path,
) -> None:
    """Runs the real top of the script and the real signature check from a
    directory that holds a ``json.py``. The check must use the standard
    ``json`` and must not run that file."""
    assert _BASH is not None
    signer = Ed25519PrivateKey.generate()
    manifest = _signed_manifest_file(tmp_path, signer)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    marker = tmp_path / "local-json-was-imported"
    (workdir / "json.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8",
    )
    harness = (
        "\n".join(_lines_before_the_first_function(_INSTALL_SH, _SH_FIRST_FUNCTION_RE))
        + "\n"
        + _LOG_FUNCS
        + f"VERIFIER_PYTHON={shlex.quote(sys.executable)}\n"
        + f"MOBAIDE_AGENT_TRUST_KEY_B64={shlex.quote(_public_key_b64(signer))}\n"
        + _sh_function("verify_manifest_signature")
        + f"verify_manifest_signature {shlex.quote(str(manifest))}\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONSAFEPATH"}

    proc = subprocess.run(
        [_BASH, "-c", harness], capture_output=True, text=True, cwd=workdir, env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert not marker.exists()
