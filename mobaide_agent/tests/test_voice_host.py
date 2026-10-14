"""Host assessment behind the low-spec warning: every threshold boundary,
the blocking reasons that make voice ``unsupported`` and the unknown-probe
fallbacks."""
from __future__ import annotations

from collections import namedtuple
from pathlib import Path

import pytest

from mobaide.voice import host
from mobaide.voice.host import (
    DISK_MARGIN_BYTES,
    GIB,
    OK_CPU_COUNT,
    OK_FREE_DISK_BYTES,
    OK_RAM_BYTES,
    assess,
    disk_shortfall,
)

_PLENTY_DISK = 100 * GIB
_REMAINING = 700 * 1024 * 1024


def _pin(monkeypatch, *, ram=16 * GIB, disk=_PLENTY_DISK, cpus=8) -> None:
    monkeypatch.setattr(host, "total_ram_bytes", lambda: ram)
    monkeypatch.setattr(host, "free_disk_bytes", lambda _path: disk)
    monkeypatch.setattr(host.os, "cpu_count", lambda: cpus)


def _codes(assessment) -> list[str]:
    return [r.code for r in assessment.reasons]


def test_capable_host_is_ok_with_no_reasons(monkeypatch, tmp_path):
    _pin(monkeypatch, ram=OK_RAM_BYTES, cpus=OK_CPU_COUNT, disk=OK_FREE_DISK_BYTES + _REMAINING)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "ok"
    assert result.reasons == ()
    assert result.ram_total_bytes == OK_RAM_BYTES
    assert result.cpu_count == OK_CPU_COUNT


def test_twelve_gb_machine_reporting_under_12_gib_still_passes(monkeypatch, tmp_path):
    _pin(monkeypatch, ram=12 * GIB - 400 * 1024 * 1024)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "ok"


def test_ram_just_below_threshold_warns_low_ram(monkeypatch, tmp_path):
    _pin(monkeypatch, ram=OK_RAM_BYTES - 1)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "low_resources"
    assert _codes(result) == ["low_ram"]


def test_too_few_cpus_warns_low_cpu(monkeypatch, tmp_path):
    _pin(monkeypatch, cpus=OK_CPU_COUNT - 1)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "low_resources"
    assert _codes(result) == ["low_cpu"]


def test_disk_that_fits_the_install_but_is_low_warns_low_disk(monkeypatch, tmp_path):
    _pin(monkeypatch, disk=OK_FREE_DISK_BYTES - 1)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "low_resources"
    assert _codes(result) == ["low_disk"]


def test_disk_that_cannot_fit_the_remaining_install_is_unsupported(monkeypatch, tmp_path):
    _pin(monkeypatch, disk=_REMAINING + DISK_MARGIN_BYTES - 1)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "unsupported"
    # low_disk would only repeat the blocking reason.
    assert _codes(result) == ["disk_insufficient"]


@pytest.mark.parametrize(
    ("free", "short"),
    [(_REMAINING + DISK_MARGIN_BYTES, False), (_REMAINING + DISK_MARGIN_BYTES - 1, True)],
    ids=["exactly_enough", "one_byte_short"],
)
def test_disk_shortfall_keeps_the_margin_on_top_of_the_remaining_install(free, short):
    reason = disk_shortfall(_REMAINING, free)

    assert (reason is not None) is short
    if short:
        assert reason.code == "disk_insufficient"


def test_installed_model_is_never_blocked_by_disk_space(monkeypatch, tmp_path):
    _pin(monkeypatch, disk=1024)

    result = assess(tmp_path, remaining_bytes=0, runtime_reason_code=None)

    assert result.recommendation == "low_resources"
    assert _codes(result) == ["low_disk"]


def test_missing_runtime_is_unsupported_and_listed_before_warnings(monkeypatch, tmp_path):
    _pin(monkeypatch, ram=4 * GIB)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code="python_unsupported")

    assert result.recommendation == "unsupported"
    assert _codes(result) == ["python_unsupported", "low_ram"]
    assert "Python" in result.reasons[0].message


def test_unknown_probes_do_not_warn(monkeypatch, tmp_path):
    _pin(monkeypatch, ram=None, disk=None, cpus=None)

    result = assess(tmp_path, remaining_bytes=_REMAINING, runtime_reason_code=None)

    assert result.recommendation == "ok"
    assert result.reasons == ()
    assert (result.ram_total_bytes, result.disk_free_bytes, result.cpu_count) == (None, None, None)


@pytest.mark.parametrize("code", ["python_unsupported", "platform_unsupported", "libc_unsupported", "new_code"])
def test_every_runtime_reason_has_a_message(code):
    reason = host.runtime_reason(code)

    assert reason.code == code
    assert reason.message


def test_free_disk_walks_up_to_the_nearest_existing_ancestor(monkeypatch, tmp_path):
    """The voice root does not exist before the first install."""
    usage = namedtuple("usage", "total used free")
    probed: list[Path] = []

    def disk_usage(path):
        probed.append(Path(path))
        if not Path(path).exists():
            raise FileNotFoundError(path)
        return usage(10, 3, 7)

    monkeypatch.setattr(host.shutil, "disk_usage", disk_usage)

    free = host.free_disk_bytes(tmp_path / "voice" / "models")

    assert free == 7
    assert probed == [tmp_path / "voice" / "models", tmp_path / "voice", tmp_path]
