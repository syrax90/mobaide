"""Tests for ``core.host_path``: picking up PATH folders saved after start.

The registry read is replaced by a fake saved PATH and only ``host_path``'s
view of the platform is switched, so the real ``shutil.which`` runs natively
and no test touches the registry or leaks a PATH change.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from mobaide.core import host_path
from mobaide.core.host_path import refresh_process_path, which_first


@pytest.fixture
def windows_host(monkeypatch):
    monkeypatch.setattr(host_path, "sys", SimpleNamespace(platform="win32"))


@pytest.fixture
def posix_host(monkeypatch):
    monkeypatch.setattr(host_path, "sys", SimpleNamespace(platform="linux"))


def _saved_path(monkeypatch, *entries: Path | str) -> None:
    monkeypatch.setattr(host_path, "_saved_windows_path", lambda: [str(e) for e in entries])


def _set_path(monkeypatch, *entries: Path | str) -> str:
    value = os.pathsep.join(str(e) for e in entries)
    monkeypatch.setenv("PATH", value)
    return value


def _folder(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _install(folder: Path, name: str) -> str:
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class TestRefreshProcessPath:

    def test_appends_the_saved_folders_missing_from_path(
        self, windows_host, monkeypatch, tmp_path
    ):
        old = _folder(tmp_path, "old")
        new_machine = _folder(tmp_path, "machine")
        new_user = _folder(tmp_path, "user")
        _set_path(monkeypatch, old)
        _saved_path(monkeypatch, old, new_machine, new_user)

        assert refresh_process_path() is True
        assert os.environ["PATH"] == os.pathsep.join(
            [str(old), str(new_machine), str(new_user)]
        )

    def test_unchanged_when_the_saved_folders_are_already_on_path_spelled_differently(
        self, windows_host, monkeypatch, tmp_path
    ):
        folder = _folder(tmp_path, "tools")
        before = _set_path(monkeypatch, folder)
        _saved_path(
            monkeypatch,
            str(folder) + os.sep,
            os.path.join(str(folder), "sub", os.pardir),
        )

        assert refresh_process_path() is False
        assert os.environ["PATH"] == before

    def test_unreadable_saved_path_leaves_path_alone(self, windows_host, monkeypatch, tmp_path):
        before = _set_path(monkeypatch, tmp_path)

        def _unreadable() -> list[str]:
            raise OSError("registry unavailable")

        monkeypatch.setattr(host_path, "_saved_windows_path", _unreadable)

        assert refresh_process_path() is False
        assert os.environ["PATH"] == before

class TestWhichFirst:

    def test_finds_a_cli_installed_after_start(self, windows_host, monkeypatch, tmp_path):
        _set_path(monkeypatch, _folder(tmp_path, "empty"))
        later = _folder(tmp_path, "installed-later")
        agy = _install(later, "agy.exe")
        _saved_path(monkeypatch, later)

        assert which_first(("agy.exe",)) == os.path.abspath(agy)
        assert str(later) in os.environ["PATH"].split(os.pathsep)

    def test_a_hit_on_the_current_path_leaves_path_alone(
        self, windows_host, monkeypatch, tmp_path
    ):
        folder = _folder(tmp_path, "bin")
        tool = _install(folder, "tool.exe")
        before = _set_path(monkeypatch, folder)
        _saved_path(monkeypatch, _folder(tmp_path, "new"))

        assert which_first(("tool.exe",)) == os.path.abspath(tool)
        assert os.environ["PATH"] == before

    def test_none_outside_windows_even_if_a_saved_folder_has_it(
        self, posix_host, monkeypatch, tmp_path
    ):
        before = _set_path(monkeypatch, _folder(tmp_path, "empty"))
        later = _folder(tmp_path, "installed-later")
        _install(later, "agy.exe")
        _saved_path(monkeypatch, later)

        assert which_first(("agy.exe",)) is None
        assert os.environ["PATH"] == before

    def test_a_path_too_long_to_extend_is_a_miss_not_an_error(
        self, windows_host, monkeypatch, tmp_path
    ):
        before = _set_path(monkeypatch, _folder(tmp_path, "empty"))
        later = _folder(tmp_path, "installed-later")
        _install(later, "agy.exe")
        _saved_path(monkeypatch, later)

        class _CappedEnviron(dict):
            def __setitem__(self, key, value):
                raise ValueError("the environment variable is longer than 32767 characters")

        monkeypatch.setattr(
            host_path,
            "os",
            SimpleNamespace(
                environ=_CappedEnviron(os.environ), pathsep=os.pathsep, path=os.path
            ),
        )

        assert which_first(("agy.exe",)) is None
        assert os.environ["PATH"] == before
