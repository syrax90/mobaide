"""Tests for the prompt-free macOS Full Disk Access probe.

``full_disk_access`` must never pop a TCC dialog: it infers the grant purely
from whether opening the user's ``TCC.db`` is silently denied. The probe is
gated to macOS, so the behaviour is driven here by pinning ``sys.platform`` and
stubbing the file open — no real darwin host or TCC database required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mobaide.core import macos_access
from mobaide.core.macos_access import full_disk_access


@pytest.fixture
def on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_access.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/test")))


def test_returns_none_off_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_access.sys, "platform", "linux")
    assert full_disk_access() is None


def test_returns_false_on_permission_error(
    on_darwin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # FDA-gated files are denied silently with PermissionError when the grant
    # is absent — the signal we map to "no Full Disk Access".
    def _denied(self: Path, *_a: object, **_kw: object) -> object:
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(Path, "open", _denied)
    assert full_disk_access() is False


def test_returns_true_on_successful_open(
    on_darwin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _DummyHandle:
        def __enter__(self) -> _DummyHandle:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(Path, "open", lambda self, *_a, **_kw: _DummyHandle())
    assert full_disk_access() is True


def test_returns_true_on_file_not_found(
    on_darwin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A missing TCC.db can only be observed once the caller can already
    # traverse the protected directory — i.e. it is not FDA-blocked.
    def _missing(self: Path, *_a: object, **_kw: object) -> object:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(Path, "open", _missing)
    assert full_disk_access() is True


def test_returns_true_on_other_oserror(
    on_darwin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Any non-permission OSError is treated as "not FDA-blocked" rather than a
    # false alarm — PermissionError is the only denial signal.
    def _io_error(self: Path, *_a: object, **_kw: object) -> object:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(Path, "open", _io_error)
    assert full_disk_access() is True


def test_returns_none_when_home_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``Path.home()`` raises RuntimeError when no home dir can be resolved; the
    # probe degrades to None rather than crashing the health check.
    monkeypatch.setattr(macos_access.sys, "platform", "darwin")

    def _no_home(cls: type[Path]) -> Path:
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(Path, "home", classmethod(_no_home))
    assert full_disk_access() is None
