"""``mobaide-agent voice status | install | remove``: the voice root the CLI
resolves (the same one the agent uses, refusing where it could not write
safely) and the verbs end to end in fake mode against a temp root."""
from __future__ import annotations

import argparse
import io
import json
import sys

import pytest

from mobaide import __main__ as _main
from mobaide.voice import cli
from mobaide.voice import service as service_module
from mobaide.voice.locking import FileLock
from mobaide.voice.store import VoicePaths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("MOBAIDE_VOICE_DIR", "MOBAIDE_VOICE_FAKE", "MOBAIDE_AGENT_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    # Never read the .env of an agent installed on this machine.
    monkeypatch.setattr("mobaide.agent.app._resolve_env_file", lambda: None)


def _voice(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        _main.main(argv=["voice", *argv])
    return excinfo.value.code


def _status(voice_dir, capsys) -> dict:
    capsys.readouterr()
    assert _voice(["status", "--json", "--voice-dir", str(voice_dir)]) == 0
    return json.loads(capsys.readouterr().out)


def _ns(**fields) -> argparse.Namespace:
    return argparse.Namespace(**{"voice_dir": None, "prefix": None, **fields})


def _write_agent_env(prefix, text: str) -> None:
    env_file = prefix / "mobaide_agent" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(text, encoding="utf-8")


class TestVoiceRoot:
    def test_explicit_voice_dir_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOBAIDE_VOICE_DIR", str(tmp_path / "env"))

        root = cli._voice_root(_ns(voice_dir=str(tmp_path / "flag")), write=True)

        assert root == tmp_path / "flag"

    def test_env_voice_dir_is_next(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MOBAIDE_VOICE_DIR", str(tmp_path / "env"))

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) == tmp_path / "env"

    def test_voice_dir_set_only_in_the_agent_env_under_the_prefix(self, tmp_path):
        _write_agent_env(tmp_path, f"MOBAIDE_VOICE_DIR={tmp_path / 'configured'}\n")

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) == tmp_path / "configured"

    @pytest.mark.parametrize("source", ["process_env", "agent_env"])
    def test_relative_voice_dir_is_a_leaf_under_the_prefix(self, tmp_path, monkeypatch, source):
        if source == "process_env":
            monkeypatch.setenv("MOBAIDE_VOICE_DIR", "my-voice")
        else:
            _write_agent_env(tmp_path, "MOBAIDE_VOICE_DIR=my-voice\n")

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) == tmp_path / "my-voice"

    def test_relative_voice_dir_of_a_source_run_is_under_the_agent_state_prefix(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("MOBAIDE_VOICE_DIR", "my-voice")
        monkeypatch.setattr(cli, "default_prefix", lambda: tmp_path / "not-installed")
        monkeypatch.setattr(cli, "state_prefix", lambda: tmp_path / "state")

        assert cli._voice_root(_ns(), write=True) == tmp_path / "state" / "my-voice"

    def test_fake_voice_set_only_in_the_agent_env_under_the_prefix(self, tmp_path):
        _write_agent_env(tmp_path, "MOBAIDE_VOICE_FAKE=1\n")

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) == tmp_path / "voice-fake"

    def test_process_env_beats_the_agent_env(self, tmp_path, monkeypatch):
        _write_agent_env(tmp_path, "MOBAIDE_VOICE_FAKE=1\n")
        monkeypatch.setenv("MOBAIDE_VOICE_FAKE", "0")

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) == tmp_path / "voice"

    def test_without_a_prefix_the_env_file_the_agent_resolves_is_read(self, tmp_path, monkeypatch):
        env_file = tmp_path / "checkout.env"
        env_file.write_text(f"MOBAIDE_VOICE_DIR={tmp_path / 'dev-voice'}\n", encoding="utf-8")
        monkeypatch.setattr("mobaide.agent.app._resolve_env_file", lambda: env_file)

        assert cli._voice_root(_ns(), write=True) == tmp_path / "dev-voice"

    @pytest.mark.parametrize(("fake", "name"), [("", "voice"), ("1", "voice-fake")])
    def test_prefix_holds_the_voice_root(self, tmp_path, monkeypatch, fake, name):
        monkeypatch.setenv("MOBAIDE_VOICE_FAKE", fake)

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) == tmp_path / name

    def test_source_run_without_an_install_uses_the_agent_state_prefix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "default_prefix", lambda: tmp_path / "not-installed")
        monkeypatch.setattr(cli, "state_prefix", lambda: tmp_path / "state")

        assert cli._voice_root(_ns(), write=True) == tmp_path / "state" / "voice"

    def test_installed_agent_prefix_is_used_even_without_flags(self, tmp_path, monkeypatch):
        prefix = tmp_path / "mobaide"
        prefix.mkdir()
        (prefix / "current_version").write_text("0.3.14\n", encoding="utf-8")
        monkeypatch.setattr(cli, "default_prefix", lambda: prefix)
        monkeypatch.setattr(cli, "state_prefix", lambda: tmp_path / "elsewhere")

        assert cli._voice_root(_ns(), write=True) == prefix / "voice"

    def test_unwritable_installed_prefix_is_refused_for_writes_only(self, tmp_path, monkeypatch, capsys):
        prefix = tmp_path / "mobaide"
        prefix.mkdir()
        (prefix / "current_version").write_text("0.3.14\n", encoding="utf-8")
        monkeypatch.setattr(cli, "default_prefix", lambda: prefix)
        monkeypatch.setattr(cli, "_is_writable_dir", lambda _path: False)

        assert cli._voice_root(_ns(), write=False) == prefix / "voice"
        assert cli._voice_root(_ns(), write=True) is None
        assert "not writable" in capsys.readouterr().err

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership check")
    def test_root_is_refused_on_a_prefix_owned_by_the_agent_user(self, tmp_path, monkeypatch, capsys):
        if (tmp_path.stat().st_uid) == 0:
            pytest.skip("needs a prefix owned by a non-root user")
        monkeypatch.setattr(cli.os, "geteuid", lambda: 0)

        assert cli._voice_root(_ns(prefix=str(tmp_path)), write=True) is None
        assert "not as root" in capsys.readouterr().err

    def test_refused_root_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_is_writable_dir", lambda _path: False)

        assert _voice(["install", "--prefix", str(tmp_path)]) == 2


class TestVerbs:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch):
        monkeypatch.setenv("MOBAIDE_VOICE_FAKE", "1")

    def test_status_reads_without_creating_the_root(self, tmp_path, capsys):
        voice_dir = tmp_path / "voice"

        status = _status(voice_dir, capsys)

        assert status["state"] == "not_installed"
        assert status["model"]["id"] == "fake-stt"
        assert not voice_dir.exists()

    def test_install_then_remove(self, tmp_path, capsys):
        voice_dir = tmp_path / "voice"

        assert _voice(["install", "--voice-dir", str(voice_dir)]) == 0
        installed = _status(voice_dir, capsys)
        assert installed["state"] == "ready"
        assert installed["speed_factor"] is not None

        assert _voice(["install", "--voice-dir", str(voice_dir)]) == 0

        assert _voice(["remove", "--yes", "--voice-dir", str(voice_dir)]) == 0
        assert _status(voice_dir, capsys)["state"] == "not_installed"
        assert not VoicePaths(voice_dir).models_dir.exists()

    def test_install_refuses_while_another_process_installs(self, tmp_path, capsys):
        voice_dir = tmp_path / "voice"
        other = FileLock(VoicePaths(voice_dir).lock_file)
        assert other.try_acquire()
        try:
            code = _voice(["install", "--voice-dir", str(voice_dir)])
        finally:
            other.release()

        assert code == 1
        assert "Another Mobaide process" in capsys.readouterr().err
        assert not VoicePaths(voice_dir).models_dir.exists()

    def test_remove_without_yes_off_a_terminal_is_refused(self, tmp_path, monkeypatch, capsys):
        voice_dir = tmp_path / "voice"
        assert _voice(["install", "--voice-dir", str(voice_dir)]) == 0
        monkeypatch.setattr(cli.sys, "stdin", io.StringIO())

        assert _voice(["remove", "--voice-dir", str(voice_dir)]) == 2
        assert "--yes" in capsys.readouterr().err
        assert _status(voice_dir, capsys)["state"] == "ready"

    def test_remove_of_a_missing_root_is_a_no_op(self, tmp_path):
        assert _voice(["remove", "--yes", "--voice-dir", str(tmp_path / "absent")]) == 0
        assert not (tmp_path / "absent").exists()


def test_install_on_an_unsupported_host_exits_1_without_downloading(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(service_module, "resolve_runtime", lambda: (None, "platform_unsupported"))
    voice_dir = tmp_path / "voice"

    assert _voice(["install", "--voice-dir", str(voice_dir)]) == 1
    assert "unsupported" in capsys.readouterr().out
    assert not voice_dir.exists()


def test_status_runs_fake_when_only_the_agent_env_says_so(tmp_path, capsys):
    _write_agent_env(tmp_path, "MOBAIDE_VOICE_FAKE=1\n")

    assert _voice(["status", "--json", "--prefix", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["model"]["id"] == "fake-stt"
    assert not (tmp_path / "voice-fake").exists()
