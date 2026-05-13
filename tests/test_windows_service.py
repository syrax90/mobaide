"""Tests for ``deploy/windows_service.py``.

The module hosts the Windows service that replaces the pre-fix
``cmd.exe /c launcher.cmd`` design (real root cause of the
``Start-Service : Cannot start service MobaideAgent on computer '.'``
failure). It imports ``pywin32`` unconditionally, so this file
auto-skips on hosts where the import is not satisfiable (any
non-Windows CI, or a Windows CI without pywin32 in the env).

Lives under ``deploy/`` rather than inside the mobaide package because
it is deploy infrastructure, not agent runtime — nothing in mobaide.*
imports it. install.ps1 fetches it from get.mobaide.com (gh-pages)
alongside install.ps1 itself, so the two cannot desync.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip(
    "win32serviceutil",
    reason="pywin32 not available — windows_service module is Windows-only",
)

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "deploy"),
)

import windows_service  # noqa: E402


def test_default_prefix_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``MOBAIDE_AGENT_PREFIX`` env var must override the default."""
    monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", r"D:\custom\mobaide")
    assert windows_service._default_prefix() == Path(r"D:\custom\mobaide")


def test_default_prefix_uses_program_files_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var, the default prefix is
    ``%ProgramFiles%\\MobaideAgent``.

    Mirrors the resolution in install.ps1's ``$Script:Prefix`` default
    and ``mobaide.launcher._default_prefix``. Drift between the three
    silently breaks the service start (it would read the wrong
    ``current_version`` file).
    """
    monkeypatch.delenv("MOBAIDE_AGENT_PREFIX", raising=False)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    assert (
        windows_service._default_prefix()
        == Path(r"C:\Program Files") / "MobaideAgent"
    )


def test_resolve_release_python_returns_release_interpreter(tmp_path: Path) -> None:
    """Happy path: ``current_version`` -> ``releases/<v>/venv/Scripts/python.exe``."""
    (tmp_path / "current_version").write_text("1.2.3\n", encoding="utf-8")
    release_scripts = tmp_path / "releases" / "1.2.3" / "venv" / "Scripts"
    release_scripts.mkdir(parents=True)
    (release_scripts / "python.exe").write_bytes(b"\x4d\x5a")
    resolved = windows_service._resolve_release_python(tmp_path)
    assert resolved == release_scripts / "python.exe"


def test_resolve_release_python_raises_on_missing_current_version(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="cannot read"):
        windows_service._resolve_release_python(tmp_path)


def test_resolve_release_python_raises_on_empty_current_version(
    tmp_path: Path,
) -> None:
    (tmp_path / "current_version").write_text("   \n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="empty"):
        windows_service._resolve_release_python(tmp_path)


def test_resolve_release_python_raises_on_missing_interpreter(
    tmp_path: Path,
) -> None:
    (tmp_path / "current_version").write_text("9.9.9", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing release interpreter"):
        windows_service._resolve_release_python(tmp_path)


def test_main_install_verb_calls_install_service() -> None:
    """``mobaide_service.py install`` must route to ``_install_service``."""
    with mock.patch.object(windows_service, "_install_service") as install_mock:
        rc = windows_service.main(["mobaide_service.py", "install"])
    assert rc == 0
    install_mock.assert_called_once_with()


def test_main_remove_verb_calls_remove_service() -> None:
    with mock.patch.object(windows_service, "_remove_service") as remove_mock:
        rc = windows_service.main(["mobaide_service.py", "remove"])
    assert rc == 0
    remove_mock.assert_called_once_with()


def test_main_unknown_verb_returns_nonzero() -> None:
    """An unrecognised verb prints a usage message and returns non-zero."""
    rc = windows_service.main(["mobaide_service.py", "frobnicate"])
    assert rc != 0


def test_main_no_args_starts_service_ctrl_dispatcher() -> None:
    """When invoked by SCM (no argv beyond argv[0]), the module must enter
    the SCM-dispatch path.

    This is the path SCM takes after spawning ``python.exe -u
    mobaide_service.py``. The dispatch chain
    (``Initialize`` -> ``PrepareToHostSingle`` ->
    ``StartServiceCtrlDispatcher``) hands control to the SCM
    framework which then calls ``SvcDoRun``.
    """
    with (
        mock.patch.object(windows_service, "servicemanager") as svcman,
        mock.patch.object(windows_service, "win32serviceutil"),
    ):
        rc = windows_service.main(["mobaide_service.py"])
    assert rc == 0
    svcman.Initialize.assert_called_once_with()
    svcman.PrepareToHostSingle.assert_called_once_with(
        windows_service.MobaideAgentService
    )
    svcman.StartServiceCtrlDispatcher.assert_called_once_with()


def test_install_service_uses_python_exe_not_pythonservice_exe() -> None:
    """``_install_service`` must register with ``exeName=sys.executable``
    and ``exeArgs='-u <module path>'``, NOT pywin32's default
    ``pythonservice.exe``.

    Rationale (pywin32 #1450, #2032): pythonservice.exe links against
    python<N>.dll and uses standard DLL search, which does not find
    the DLL inside a venv (it's in the base interpreter's install
    dir). The venv's python.exe is venv-aware (reads pyvenv.cfg) and
    finds the DLL correctly. Registering with python.exe sidesteps
    the venv-DLL-search problem.
    """
    with mock.patch.object(
        windows_service.win32serviceutil, "InstallService"
    ) as install:
        windows_service._install_service()
    assert install.call_count == 1
    kwargs = install.call_args.kwargs
    assert kwargs["exeName"] == sys.executable
    assert windows_service.__file__ is not None
    assert os.path.abspath(windows_service.__file__) in kwargs["exeArgs"]
    assert kwargs["serviceName"] == "MobaideAgent"
    assert kwargs["startType"] == windows_service.win32service.SERVICE_AUTO_START


def test_install_service_pythonClassString_targets_service_class() -> None:
    """The registered ``pythonClassString`` must resolve to
    ``MobaideAgentService`` so SCM dispatch reaches our handler.

    pywin32 reads the PythonClass key from the registry on service
    start and instantiates that class. If the key points to the
    wrong module:class pair, the service starts pythonservice.exe,
    fails to find the class, and exits.
    """
    with mock.patch.object(
        windows_service.win32serviceutil, "InstallService"
    ) as install:
        windows_service._install_service()
    class_string = install.call_args.kwargs["pythonClassString"]
    assert class_string.endswith(".MobaideAgentService")


def test_service_class_constants() -> None:
    """The service-name constants must match what install.ps1 expects.

    install.ps1's ``$Script:ServiceName = "MobaideAgent"`` is the SCM
    key the rest of the installer uses (``sc.exe failure $ServiceName
    ...``, ``Start-Service -Name $ServiceName``). If the Python side
    registers under a different name, ``Start-Service`` cannot find
    the service.
    """
    assert windows_service.MobaideAgentService._svc_name_ == "MobaideAgent"
    assert windows_service.MobaideAgentService._svc_display_name_ == "Mobaide Agent"
