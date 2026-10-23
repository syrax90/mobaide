"""Tests for ``mobaide.agent.self_uninstall``.

Every test runs against a fake installed prefix under ``tmp_path``. The
platform branch is chosen through ``uninstall(platform=...)`` and the
external commands go through a recording stand-in for ``_run``, so nothing
here touches a real service manager, home directory or install.
"""
from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobaide.agent import self_uninstall
from mobaide.agent._env_writer import parse_env_file
from mobaide.agent.self_uninstall import (
    PREFIX_ENTRIES,
    disarm,
    handle_uninstall_request,
    remove_prefix_entries,
    remove_voice_files,
    uninstall,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_TEXT = """\
JWT_SECRET_KEY=secret
RELAY_SERVER_URL=wss://relay.example
RELAY_AGENT_ID=agent-42
RELAY_AGENT_TOKEN=tok-secret
"""

_FOREIGN = ("notes.txt", "backups")


def _make_voice_root(root: Path) -> Path:
    """The voice layout: settings, lock, one model and one runtime."""
    (root / "models" / "parakeet").mkdir(parents=True)
    (root / "models" / "parakeet" / "encoder.onnx").write_bytes(b"m")
    (root / "runtime" / "1.12-cp312-linux").mkdir(parents=True)
    (root / "runtime" / "1.12-cp312-linux" / "sherpa.so").write_bytes(b"r")
    (root / "settings.json").write_text('{"enabled": true}', encoding="utf-8")
    (root / ".lock").write_text("", encoding="utf-8")
    return root


def _make_install(prefix: Path) -> Path:
    """Lay out the entries a real install has, plus files the user put in
    the same directory that uninstall must leave alone."""
    venv_python = prefix / "releases" / "0.9.0" / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    (prefix / "trust").mkdir()
    (prefix / "trust" / "central_server.pub").write_text("k", encoding="utf-8")
    (prefix / "mobaide_agent").mkdir()
    (prefix / "mobaide_agent" / ".env").write_text(_ENV_TEXT, encoding="utf-8")
    (prefix / "run").mkdir()
    (prefix / "run" / "pending_pairing.json").write_text("{}", encoding="utf-8")
    (prefix / "state").mkdir()
    (prefix / "logs").mkdir()
    (prefix / "logs" / "agent.log").write_text("x", encoding="utf-8")
    (prefix / "bin").mkdir()
    (prefix / "bin" / "mobaide-agent-launcher.sh").write_text(
        "#!/bin/sh\nexec python -m mobaide\n", encoding="utf-8"
    )
    (prefix / "current_version").write_text("0.9.0", encoding="utf-8")
    (prefix / ".boot_journal").write_text("", encoding="utf-8")
    (prefix / "users.json").write_text("[]", encoding="utf-8")
    _make_voice_root(prefix / "voice")
    (prefix / "notes.txt").write_text("mine", encoding="utf-8")
    (prefix / "backups").mkdir()
    (prefix / "backups" / "keep.tar").write_text("mine", encoding="utf-8")
    return prefix


@pytest.fixture
def prefix(tmp_path) -> Path:
    return _make_install(tmp_path / "mobaide")


@pytest.fixture
def commands(monkeypatch) -> list[list[str]]:
    """Record every external command instead of running it; rc 0."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str]) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(self_uninstall, "_run", fake_run)
    return calls


@pytest.fixture
def no_systemd_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(
        self_uninstall, "SYSTEMD_HELPER_PATH_UNIT", tmp_path / "absent.path"
    )


def _remaining(prefix: Path) -> set[str]:
    return {p.name for p in prefix.iterdir()}


class TestDisarm:
    def test_blanks_relay_credentials_and_keeps_other_keys(self, prefix):
        disarm(prefix)

        env = parse_env_file(prefix / "mobaide_agent" / ".env")
        assert env["RELAY_AGENT_ID"] == ""
        assert env["RELAY_AGENT_TOKEN"] == ""
        assert env["RELAY_SERVER_URL"] == "wss://relay.example"
        assert env["JWT_SECRET_KEY"] == "secret"

    def test_clears_pending_pairing(self, prefix):
        disarm(prefix)

        assert not (prefix / "run" / "pending_pairing.json").exists()

    def test_missing_env_file_is_tolerated(self, tmp_path):
        prefix = tmp_path / "bare"
        prefix.mkdir()

        disarm(prefix)

        assert not (prefix / "mobaide_agent" / ".env").exists()


class TestRemovePrefixEntries:
    def test_removes_known_entries_and_keeps_foreign_ones(self, prefix):
        remove_prefix_entries(prefix)

        assert _remaining(prefix) == set(_FOREIGN)
        assert (prefix / "backups" / "keep.tar").read_text() == "mine"

    def test_removes_the_prefix_when_only_known_entries_were_there(
        self, prefix
    ):
        for name in _FOREIGN:
            target = prefix / name
            if target.is_dir():
                (target / "keep.tar").unlink()
                target.rmdir()
            else:
                target.unlink()

        remove_prefix_entries(prefix)

        assert not prefix.exists()

    def test_keep_spares_the_entry_and_the_prefix(self, prefix):
        for name in _FOREIGN:
            target = prefix / name
            if target.is_dir():
                (target / "keep.tar").unlink()
                target.rmdir()
            else:
                target.unlink()

        remove_prefix_entries(prefix, keep=frozenset({"bin"}))

        assert _remaining(prefix) == {"bin"}

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_symlinked_entry_is_unlinked_without_following_it(
        self, tmp_path
    ):
        prefix = tmp_path / "mobaide"
        prefix.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "precious").write_text("x", encoding="utf-8")
        (prefix / "releases").symlink_to(elsewhere, target_is_directory=True)

        remove_prefix_entries(prefix)

        assert not prefix.exists()
        assert (elsewhere / "precious").read_text() == "x"


class TestRemoveVoiceFiles:
    def test_removes_the_voice_dirs_under_the_prefix(self, prefix):
        _make_voice_root(prefix / "voice-fake")

        remove_voice_files(prefix)

        assert not (prefix / "voice").exists()
        assert not (prefix / "voice-fake").exists()
        assert (prefix / "releases" / "0.9.0").is_dir()
        assert (prefix / "notes.txt").exists()

    def test_custom_root_loses_only_the_voice_layout(self, prefix, tmp_path):
        root = _make_voice_root(tmp_path / "models-disk" / "mobaide-voice")
        (root / "README").write_text("mine", encoding="utf-8")

        remove_voice_files(prefix, root)

        assert {p.name for p in root.iterdir()} == {"README"}
        assert not (prefix / "voice").exists()

    def test_custom_root_left_empty_is_removed(self, prefix, tmp_path):
        root = _make_voice_root(tmp_path / "mobaide-voice")

        remove_voice_files(prefix, root)

        assert not root.exists()

    def test_default_root_is_not_cleared_twice(self, prefix, monkeypatch):
        cleared: list[Path] = []
        real_remove_tree = self_uninstall.remove_tree

        def recording_remove_tree(path: Path) -> bool:
            cleared.append(path)
            return real_remove_tree(path)

        monkeypatch.setattr(self_uninstall, "remove_tree", recording_remove_tree)

        remove_voice_files(prefix, prefix / "voice")

        assert cleared == [prefix / "voice", prefix / "voice-fake"]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_symlinked_voice_dir_clears_its_target_and_keeps_the_rest(
        self, prefix, tmp_path
    ):
        import shutil

        shutil.rmtree(prefix / "voice")
        target = _make_voice_root(tmp_path / "big-disk" / "voice")
        (target / "README").write_text("mine", encoding="utf-8")
        (prefix / "voice").symlink_to(target, target_is_directory=True)

        remove_voice_files(prefix)

        assert {p.name for p in target.iterdir()} == {"README"}
        # The link itself goes with the other prefix entries.
        assert (prefix / "voice").is_symlink()
        remove_prefix_entries(prefix)
        assert not (prefix / "voice").exists()
        assert (target / "README").read_text() == "mine"

    def test_failure_does_not_stop_the_uninstall(
        self, prefix, no_systemd_helper, monkeypatch, commands
    ):
        def boom(_prefix: Path, _root: Path | None = None) -> None:
            raise RuntimeError("voice gone wrong")

        monkeypatch.setattr(self_uninstall, "remove_voice_files", boom)

        uninstall(prefix, platform="linux")

        # The prefix entries still list the voice dir.
        assert _remaining(prefix) == {"bin", *_FOREIGN}


class TestInstallShHelper:
    def test_helper_deletes_exactly_the_prefix_entries(self):
        """The root helper ``install.sh`` writes repeats PREFIX_ENTRIES."""
        text = (_REPO_ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
        start = text.index("rm -rf --one-file-system -- ")
        end = text.index("\n", text.index("projects.json", start))
        words = text[start:end].replace("\\\\", " ").split()

        assert words[:3] == ["rm", "-rf", "--one-file-system"]
        assert tuple(words[4:]) == PREFIX_ENTRIES


class TestUninstallLinux:
    def test_helper_unit_present_only_drops_the_marker(
        self, prefix, tmp_path, monkeypatch, commands
    ):
        helper = tmp_path / "mobaide-agent-uninstall.path"
        helper.write_text("", encoding="utf-8")
        monkeypatch.setattr(self_uninstall, "SYSTEMD_HELPER_PATH_UNIT", helper)

        uninstall(prefix, platform="linux")

        assert (prefix / "run" / "uninstall-requested").is_file()
        # The root helper deletes the install; the agent leaves it intact
        # apart from disarming and the voice files, which a helper written
        # by an older install.sh would not delete.
        assert (prefix / "releases" / "0.9.0").is_dir()
        assert not (prefix / "voice").exists()
        assert (prefix / "current_version").exists()
        assert parse_env_file(prefix / "mobaide_agent" / ".env")[
            "RELAY_AGENT_TOKEN"
        ] == ""
        assert commands == []

    def test_fallback_stubs_the_launcher_and_removes_the_rest(
        self, prefix, no_systemd_helper, commands
    ):
        uninstall(prefix, platform="linux")

        assert _remaining(prefix) == {"bin", *_FOREIGN}
        launcher = prefix / "bin" / "mobaide-agent-launcher.sh"
        body = launcher.read_text(encoding="utf-8")
        assert body.rstrip().splitlines()[-1] == "exit 0"
        assert "exec python" not in body
        if sys.platform != "win32":
            assert launcher.stat().st_mode & 0o111
        assert commands == []

    @pytest.mark.skipif(sys.platform == "win32", reason="runs the POSIX stub")
    def test_fallback_stub_exits_zero(self, prefix, no_systemd_helper, commands):
        uninstall(prefix, platform="linux")

        result = subprocess.run(
            ["/bin/sh", str(prefix / "bin" / "mobaide-agent-launcher.sh")],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_unwritable_marker_falls_back_to_stub_and_removal(
        self, prefix, tmp_path, monkeypatch, commands
    ):
        helper = tmp_path / "mobaide-agent-uninstall.path"
        helper.write_text("", encoding="utf-8")
        monkeypatch.setattr(self_uninstall, "SYSTEMD_HELPER_PATH_UNIT", helper)
        marker = prefix / "run" / "uninstall-requested"
        real_touch = Path.touch

        def refuse_marker(self, *args, **kwargs):
            if self == marker:
                raise PermissionError("run/ is read-only")
            return real_touch(self, *args, **kwargs)

        monkeypatch.setattr(Path, "touch", refuse_marker)

        uninstall(prefix, platform="linux")

        assert _remaining(prefix) == {"bin", *_FOREIGN}
        body = (prefix / "bin" / "mobaide-agent-launcher.sh").read_text(
            encoding="utf-8"
        )
        assert body.rstrip().splitlines()[-1] == "exit 0"
        assert commands == []

    def test_unwritable_launcher_still_removes_the_rest(
        self, prefix, no_systemd_helper, monkeypatch, commands
    ):
        launcher = prefix / "bin" / "mobaide-agent-launcher.sh"
        real_write_text = Path.write_text

        def refuse_launcher(self, *args, **kwargs):
            if self == launcher:
                raise PermissionError("launcher is read-only")
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", refuse_launcher)

        uninstall(prefix, platform="linux")

        assert _remaining(prefix) == {"bin", *_FOREIGN}
        assert "exec python" in launcher.read_text(encoding="utf-8")

    def test_fallback_without_launcher_creates_no_stub(
        self, prefix, no_systemd_helper, commands
    ):
        (prefix / "bin" / "mobaide-agent-launcher.sh").unlink()

        uninstall(prefix, platform="linux")

        assert list((prefix / "bin").iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="needs the pwd module")
class TestUninstallMacos:
    @pytest.fixture
    def home(self, tmp_path, monkeypatch) -> Path:
        import pwd

        home = tmp_path / "home"
        agents_dir = home / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "com.mobaide.agent.plist").write_text("<plist/>")
        (agents_dir / "com.example.other.plist").write_text("<plist/>")
        monkeypatch.setattr(
            pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(home))
        )
        return home

    def test_removes_plist_and_install_then_boots_out_last(
        self, prefix, home, monkeypatch
    ):
        import os

        agents_dir = home / "Library" / "LaunchAgents"
        seen: list[tuple[list[str], bool, set[str]]] = []

        def fake_run(cmd: list[str]) -> int:
            seen.append((
                cmd,
                (agents_dir / "com.mobaide.agent.plist").exists(),
                _remaining(prefix),
            ))
            return 0

        monkeypatch.setattr(self_uninstall, "_run", fake_run)

        uninstall(prefix, platform="darwin")

        assert len(seen) == 1
        cmd, plist_present, remaining = seen[0]
        assert cmd == ["launchctl", "bootout", f"gui/{os.getuid()}/com.mobaide.agent"]
        # bootout kills this process, so everything else is already gone.
        assert plist_present is False
        assert remaining == set(_FOREIGN)
        assert (agents_dir / "com.example.other.plist").exists()

    def test_failed_bootout_falls_back_to_launchctl_remove(
        self, prefix, home, monkeypatch
    ):
        import os

        calls: list[list[str]] = []

        def fake_run(cmd: list[str]) -> int:
            calls.append(cmd)
            return 5 if cmd[1] == "bootout" else 0

        monkeypatch.setattr(self_uninstall, "_run", fake_run)

        uninstall(prefix, platform="darwin")

        assert calls == [
            ["launchctl", "bootout", f"gui/{os.getuid()}/com.mobaide.agent"],
            ["launchctl", "remove", "com.mobaide.agent"],
        ]

    def test_failed_disarm_still_removes_the_install_and_boots_out(
        self, prefix, home, monkeypatch, commands
    ):
        def broken_disarm(_prefix: Path) -> None:
            raise PermissionError(".env is read-only")

        monkeypatch.setattr(self_uninstall, "disarm", broken_disarm)

        uninstall(prefix, platform="darwin")

        assert [c[:2] for c in commands] == [["launchctl", "bootout"]]
        assert _remaining(prefix) == set(_FOREIGN)
        assert not (
            home / "Library" / "LaunchAgents" / "com.mobaide.agent.plist"
        ).exists()

    def test_unknown_uid_skips_the_plist_but_still_boots_out(
        self, prefix, monkeypatch, commands
    ):
        import pwd

        def no_such_user(uid: int):
            raise KeyError(uid)

        monkeypatch.setattr(pwd, "getpwuid", no_such_user)

        uninstall(prefix, platform="darwin")

        assert [c[:2] for c in commands] == [["launchctl", "bootout"]]
        assert _remaining(prefix) == set(_FOREIGN)

    def test_failed_file_removal_still_boots_out(
        self, prefix, home, monkeypatch, commands
    ):
        def broken_remove(_prefix: Path, **_kwargs) -> None:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(self_uninstall, "remove_prefix_entries", broken_remove)

        uninstall(prefix, platform="darwin")

        assert [c[:2] for c in commands] == [["launchctl", "bootout"]]


class TestUninstallWindows:
    @pytest.fixture
    def base_python(self, tmp_path, monkeypatch) -> Path:
        python = tmp_path / "Python312" / "pythonw.exe"
        monkeypatch.setattr(self_uninstall, "_base_pythonw", lambda _p: python)
        return python

    def test_registers_runs_cleanup_task_then_deletes_agent_task(
        self, prefix, base_python, commands
    ):
        uninstall(prefix, platform="win32")

        script = prefix / "uninstall_cleanup.py"
        assert script.is_file()
        create, run, delete = commands
        assert create[:3] == ["schtasks.exe", "/Create", "/TN"]
        assert create[3] == "MobaideAgentCleanup"
        # Elevated task: the script sits in the admin-only prefix, never
        # %TEMP%, and runs isolated so no planted module beside it loads.
        assert create[create.index("/TR") + 1] == f'"{base_python}" -I "{script}"'
        assert create[create.index("/RL") + 1] == "HIGHEST"
        assert run == ["schtasks.exe", "/Run", "/TN", "MobaideAgentCleanup"]
        assert delete == ["schtasks.exe", "/Delete", "/TN", "MobaideAgent", "/F"]
        # The running venv is locked; the cleanup task deletes it later.
        assert (prefix / "releases" / "0.9.0").is_dir()
        assert parse_env_file(prefix / "mobaide_agent" / ".env")[
            "RELAY_AGENT_TOKEN"
        ] == ""

    def test_cleanup_script_is_valid_python_targeting_the_prefix(
        self, prefix, base_python, commands
    ):
        uninstall(prefix, platform="win32")

        source = (prefix / "uninstall_cleanup.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        literals = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"prefix", "entries"}
        }
        assert literals == {"prefix": str(prefix), "entries": PREFIX_ENTRIES}
        assert "'MobaideAgentCleanup'" in source

    def test_cleanup_script_deletes_itself_and_then_the_emptied_prefix(
        self, prefix, base_python, commands, monkeypatch
    ):
        for name in _FOREIGN:
            target = prefix / name
            if target.is_dir():
                (target / "keep.tar").unlink()
                target.rmdir()
            else:
                target.unlink()
        uninstall(prefix, platform="win32")
        script = prefix / "uninstall_cleanup.py"
        task_commands: list[list[str]] = []
        monkeypatch.setattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **kwargs: task_commands.append(cmd),
        )

        def no_sleep(_seconds: float) -> None:
            raise AssertionError("cleanup retried although nothing was locked")

        monkeypatch.setattr(time, "sleep", no_sleep)

        exec(
            compile(script.read_text(encoding="utf-8"), str(script), "exec"),
            {"__file__": str(script), "__name__": "__main__"},
        )

        assert task_commands == [
            ["schtasks.exe", "/Delete", "/TN", "MobaideAgentCleanup", "/F"]
        ]
        assert not prefix.exists()

    def test_cleanup_script_leaves_foreign_files_and_the_prefix(
        self, prefix, base_python, commands, monkeypatch
    ):
        uninstall(prefix, platform="win32")
        script = prefix / "uninstall_cleanup.py"
        monkeypatch.setattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
        )
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: None)

        exec(
            compile(script.read_text(encoding="utf-8"), str(script), "exec"),
            {"__file__": str(script), "__name__": "__main__"},
        )

        assert _remaining(prefix) == set(_FOREIGN)

    def test_failed_task_registration_skips_run_but_still_deletes_agent_task(
        self, prefix, base_python, monkeypatch
    ):
        calls: list[list[str]] = []

        def fake_run(cmd: list[str]) -> int:
            calls.append(cmd)
            return 1 if "/Create" in cmd else 0

        monkeypatch.setattr(self_uninstall, "_run", fake_run)

        uninstall(prefix, platform="win32")

        assert [c[1] for c in calls] == ["/Create", "/Delete"]
        assert calls[-1][3] == "MobaideAgent"

    def test_unwritable_cleanup_script_skips_the_task_but_deletes_agent_task(
        self, prefix, base_python, monkeypatch, commands
    ):
        script = prefix / "uninstall_cleanup.py"
        real_write_text = Path.write_text

        def refuse_script(self, *args, **kwargs):
            if self == script:
                raise PermissionError("prefix is read-only")
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", refuse_script)

        uninstall(prefix, platform="win32")

        assert commands == [
            ["schtasks.exe", "/Delete", "/TN", "MobaideAgent", "/F"]
        ]
        assert not script.exists()

    def test_no_interpreter_outside_prefix_only_deletes_agent_task(
        self, prefix, monkeypatch, commands
    ):
        monkeypatch.setattr(self_uninstall, "_base_pythonw", lambda _p: None)

        uninstall(prefix, platform="win32")

        assert commands == [
            ["schtasks.exe", "/Delete", "/TN", "MobaideAgent", "/F"]
        ]
        assert not (prefix / "uninstall_cleanup.py").exists()


class TestBasePythonw:
    def test_prefers_pythonw_next_to_the_base_interpreter(
        self, tmp_path, monkeypatch
    ):
        base = tmp_path / "Python312"
        base.mkdir()
        (base / "python.exe").write_text("")
        (base / "pythonw.exe").write_text("")
        monkeypatch.setattr(
            sys, "_base_executable", str(base / "python.exe"), raising=False
        )

        assert self_uninstall._base_pythonw(tmp_path / "mobaide") == (
            base / "pythonw.exe"
        )

    def test_interpreter_inside_the_prefix_is_rejected(
        self, tmp_path, monkeypatch
    ):
        prefix = tmp_path / "mobaide"
        venv = prefix / "releases" / "0.9.0" / "venv" / "Scripts"
        venv.mkdir(parents=True)
        (venv / "python.exe").write_text("")
        (venv / "pythonw.exe").write_text("")
        monkeypatch.setattr(
            sys, "_base_executable", str(venv / "python.exe"), raising=False
        )

        assert self_uninstall._base_pythonw(prefix) is None


class TestRun:
    def test_timeout_is_reported_not_raised(self, monkeypatch):
        def fake_subprocess_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr(self_uninstall.subprocess, "run", fake_subprocess_run)

        assert self_uninstall._run(["launchctl", "bootout", "x"]) == -1

    def test_missing_binary_is_reported_not_raised(self, monkeypatch):
        def fake_subprocess_run(cmd, **kwargs):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(self_uninstall.subprocess, "run", fake_subprocess_run)

        assert self_uninstall._run(["schtasks.exe", "/Run"]) == -1

    def test_returns_the_exit_code(self, monkeypatch):
        monkeypatch.setattr(
            self_uninstall.subprocess,
            "run",
            lambda cmd, **kwargs: SimpleNamespace(returncode=3, stderr="no\n"),
        )

        assert self_uninstall._run(["schtasks.exe", "/Run"]) == 3


class TestHandleUninstallRequest:
    @pytest.fixture(autouse=True)
    def _immediate_exit(self, monkeypatch):
        monkeypatch.setattr(self_uninstall, "_EXIT_DELAY_S", 0)

    @staticmethod
    async def _drain_loop() -> None:
        for _ in range(3):
            await asyncio.sleep(0)

    async def test_not_installed_touches_nothing_and_does_not_exit(
        self, prefix
    ):
        exits: list[int] = []

        await handle_uninstall_request(
            prefix, installed=False, exit_func=exits.append
        )
        await self._drain_loop()

        assert exits == []
        env = parse_env_file(prefix / "mobaide_agent" / ".env")
        assert env["RELAY_AGENT_TOKEN"] == "tok-secret"
        assert (prefix / "releases" / "0.9.0").is_dir()

    async def test_installed_uninstalls_then_exits_zero(
        self, prefix, monkeypatch
    ):
        removed: list[tuple[Path, Path | None]] = []
        monkeypatch.setattr(
            self_uninstall,
            "uninstall",
            lambda p, voice_root=None: removed.append((p, voice_root)),
        )
        exits: list[int] = []

        await handle_uninstall_request(
            prefix, installed=True, exit_func=exits.append
        )
        await self._drain_loop()

        assert removed == [(prefix, None)]
        assert exits == [0]

    async def test_voice_is_shut_down_before_its_files_are_deleted(
        self, prefix, tmp_path, monkeypatch
    ):
        events: list[object] = []
        voice_root = tmp_path / "voice-elsewhere"

        class FakeVoice:
            paths = SimpleNamespace(root=voice_root)

            async def shutdown(self) -> None:
                events.append("voice.shutdown")

        monkeypatch.setattr(
            self_uninstall,
            "uninstall",
            lambda p, voice_root=None: events.append(("uninstall", p, voice_root)),
        )
        exits: list[int] = []

        await handle_uninstall_request(
            prefix, installed=True, voice=FakeVoice(), exit_func=exits.append
        )
        await self._drain_loop()

        assert events == ["voice.shutdown", ("uninstall", prefix, voice_root)]
        assert exits == [0]

    async def test_real_voice_service_is_stopped_and_its_files_deleted(
        self, prefix, monkeypatch
    ):
        from mobaide.voice.service import VoiceService

        class RecordingHelper:
            busy = False
            stops = 0

            async def stop(self) -> None:
                RecordingHelper.stops += 1

        voice = VoiceService(
            voice_dir=prefix / "voice", fake=True, helper=RecordingHelper()
        )
        real_uninstall = self_uninstall.uninstall
        # No service manager here: the generic branch only deletes files.
        monkeypatch.setattr(
            self_uninstall,
            "uninstall",
            lambda p, voice_root=None: real_uninstall(
                p, platform="freebsd", voice_root=voice_root
            ),
        )

        await handle_uninstall_request(
            prefix, installed=True, voice=voice, exit_func=lambda _: None
        )

        assert RecordingHelper.stops == 1
        assert _remaining(prefix) == set(_FOREIGN)

    async def test_not_installed_leaves_voice_running(self, prefix):
        class FakeVoice:
            paths = SimpleNamespace(root=prefix / "voice")

            async def shutdown(self) -> None:
                raise AssertionError("a source run must not stop voice")

        await handle_uninstall_request(
            prefix, installed=False, voice=FakeVoice(), exit_func=lambda _: None
        )

        assert (prefix / "voice" / "models").is_dir()

    async def test_failed_uninstall_still_exits_zero(self, prefix, monkeypatch):
        def boom(_prefix: Path, voice_root: Path | None = None) -> None:
            raise OSError("disk gone")

        monkeypatch.setattr(self_uninstall, "uninstall", boom)
        exits: list[int] = []

        await handle_uninstall_request(
            prefix, installed=True, exit_func=exits.append
        )
        await self._drain_loop()

        assert exits == [0]
