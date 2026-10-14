"""``SttHelper`` against real worker processes: the fake engine for the
happy paths, and tiny stand-in workers (POSIX shell wrappers) for a worker
that crashes or hangs mid-request. Timeouts are shortened, never slept
through."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mobaide.voice import helper as helper_module
from mobaide.voice.helper import HelperError, LoadSpec, SttHelper

from .conftest import skip_if_not_linux

RATE = 16000
ONE_SECOND = b"\x00\x00" * RATE
TENTH_SECOND = b"\x00\x00" * (RATE // 10)

_STUB_WORKER = """
import os, sys, time
from mobaide.voice.ipc import encode_frame, read_frame

mode, mark = sys.argv[1], sys.argv[2]
first_run = not os.path.exists(mark)
inp = os.fdopen(0, "rb", buffering=0)
out = os.fdopen(1, "wb")


def send(header):
    out.write(encode_frame(header))
    out.flush()


while True:
    frame = read_frame(inp)
    if frame is None:
        sys.exit(0)
    header, _ = frame
    op = header.get("op")
    if op == "shutdown":
        sys.exit(0)
    if op == "load":
        send({"id": header["id"], "ok": True, "load_ms": 1, "pid": os.getpid()})
    elif op == "transcribe":
        if first_run:
            open(mark, "w").close()
            if mode == "crash":
                os._exit(3)
            if mode == "hang":
                time.sleep(3600)
        send({"id": header["id"], "ok": True, "text": "recovered", "decode_ms": 1})
"""


def _spec(tmp_path: Path, key: str = "fake:1", engine: str = "fake") -> LoadSpec:
    return LoadSpec(key=key, engine=engine, model_dir=tmp_path, runtime_dir=None)


def _stub_python(tmp_path: Path, mode: str) -> str:
    """An executable that ignores ``-m mobaide.voice.worker`` and runs a stub
    worker which misbehaves (``crash`` | ``hang``) on its first run only."""
    stub = tmp_path / "stub_worker.py"
    stub.write_text(_STUB_WORKER, encoding="utf-8")
    wrapper = tmp_path / f"python-{mode}"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{stub}" {mode} "{tmp_path / "first-run-done"}"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def _process_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _fill_pipe(fd: int) -> None:
    """Write into ``fd`` until the pipe buffer is full, so the next blocking
    write to it waits until the reader goes away."""
    os.set_blocking(fd, False)
    try:
        while True:
            os.write(fd, b"\x00" * 65536)
    except BlockingIOError:
        pass
    finally:
        os.set_blocking(fd, True)


@pytest.fixture
async def make_helper():
    helpers: list[SttHelper] = []

    def make(**kwargs) -> SttHelper:
        stt = SttHelper(**kwargs)
        helpers.append(stt)
        return stt

    yield make
    for stt in helpers:
        await stt.stop()


async def test_first_request_spawns_and_loads_then_the_worker_is_reused(make_helper, tmp_path):
    stt = make_helper()
    assert not stt.running

    first = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    pid = stt.pid
    second = await stt.transcribe(_spec(tmp_path), TENTH_SECOND, RATE)

    assert first.text == "fake transcript 1000 ms"
    assert isinstance(first.load_ms, int)
    assert second.load_ms is None
    assert stt.pid == pid
    assert stt.running
    assert not stt.busy


async def test_preload_loads_once(make_helper, tmp_path):
    stt = make_helper()

    load_ms = await stt.preload(_spec(tmp_path))
    result = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert isinstance(load_ms, int)
    assert result.load_ms is None


async def test_unknown_engine_fails_the_load_and_stops_the_worker(make_helper, tmp_path):
    stt = make_helper()

    with pytest.raises(HelperError) as excinfo:
        await stt.transcribe(_spec(tmp_path, engine="nope"), ONE_SECOND, RATE)

    assert excinfo.value.code == "load_failed"
    assert not stt.running


async def test_idle_worker_exits(make_helper, tmp_path):
    stt = make_helper(idle_seconds=0.1)
    await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    pid = stt.pid

    await _wait_for(lambda: not stt.running)

    assert stt.pid is None
    await _wait_for(lambda: _process_gone(pid))


async def test_worker_is_recycled_after_max_requests(make_helper, tmp_path):
    stt = make_helper(max_requests=1)
    await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    first_pid = stt.pid

    result = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert stt.pid != first_pid
    assert isinstance(result.load_ms, int)


async def test_worker_is_recycled_after_max_lifetime(make_helper, tmp_path):
    stt = make_helper(max_lifetime_seconds=0)
    await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    first_pid = stt.pid

    await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert stt.pid != first_pid


@pytest.mark.parametrize(
    "limits",
    [{"max_requests": 1}, {"max_lifetime_seconds": 0}],
    ids=["max_requests", "max_lifetime"],
)
async def test_worker_past_its_limit_is_retired_once_idle_without_another_request(
    make_helper, tmp_path, limits
):
    stt = make_helper(**limits)

    result = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    pid = stt.pid

    assert result.text == "fake transcript 1000 ms"
    assert pid is not None
    await _wait_for(lambda: not stt.running)
    assert stt.pid is None
    await _wait_for(lambda: _process_gone(pid))


async def test_a_planted_mobaide_package_in_the_cwd_never_runs_as_the_worker(
    make_helper, tmp_path, monkeypatch
):
    cwd = tmp_path / "cwd"
    planted = cwd / "mobaide"
    planted.mkdir(parents=True)
    (planted / "__init__.py").write_text("import sys\nsys.exit(97)\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    stt = make_helper()

    result = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert result.text == "fake transcript 1000 ms"


@skip_if_not_linux
def test_graceful_shutdown_of_a_worker_that_never_reads_stdin_still_kills_it(monkeypatch):
    """The polite ``shutdown`` frame cannot be written into a full pipe; the
    stop must still kill the worker after the grace period instead of
    blocking on that write forever."""
    monkeypatch.setattr(helper_module, "_STOP_GRACE_SECONDS", 0.2)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert proc.stdin is not None
        _fill_pipe(proc.stdin.fileno())
        stopper = threading.Thread(
            target=helper_module._shutdown_process, args=(proc, True), daemon=True
        )

        stopper.start()
        stopper.join(timeout=helper_module._STOP_GRACE_SECONDS + helper_module._REAP_SECONDS + 1)

        assert not stopper.is_alive()
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


async def test_a_different_model_key_restarts_the_worker(make_helper, tmp_path):
    stt = make_helper()
    await stt.transcribe(_spec(tmp_path, key="a"), ONE_SECOND, RATE)
    first_pid = stt.pid

    result = await stt.transcribe(_spec(tmp_path, key="b"), ONE_SECOND, RATE)

    assert stt.pid != first_pid
    assert isinstance(result.load_ms, int)


@skip_if_not_linux
async def test_worker_killed_while_idle_is_respawned_on_the_next_request(make_helper, tmp_path):
    stt = make_helper()
    await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    pid = stt.pid
    os.kill(pid, signal.SIGKILL)
    await _wait_for(lambda: not stt.running)

    result = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert result.text == "fake transcript 1000 ms"
    assert stt.pid not in (None, pid)


@skip_if_not_linux
async def test_crash_mid_request_is_helper_crashed_and_the_next_request_respawns(
    make_helper, tmp_path
):
    stt = make_helper(python=_stub_python(tmp_path, "crash"))

    with pytest.raises(HelperError) as excinfo:
        await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert excinfo.value.code == "helper_crashed"
    assert not stt.busy
    result = await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    assert result.text == "recovered"


@skip_if_not_linux
async def test_hung_request_times_out_kills_the_worker_and_the_next_request_respawns(
    make_helper, tmp_path, monkeypatch
):
    monkeypatch.setattr(helper_module, "MIN_TRANSCRIBE_TIMEOUT_SECONDS", 0.5)
    stt = make_helper(python=_stub_python(tmp_path, "hang"))

    request = asyncio.ensure_future(stt.transcribe(_spec(tmp_path), TENTH_SECOND, RATE))
    await _wait_for(lambda: stt.pid is not None)
    hung_pid = stt.pid
    assert stt.busy
    with pytest.raises(HelperError) as excinfo:
        await request

    assert excinfo.value.code == "helper_timeout"
    assert not stt.busy
    assert _process_gone(hung_pid)
    result = await stt.transcribe(_spec(tmp_path), TENTH_SECOND, RATE)
    assert result.text == "recovered"
    assert stt.pid != hung_pid


async def test_stop_ends_the_worker(make_helper, tmp_path):
    stt = make_helper()
    await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)
    pid = stt.pid

    await stt.stop()

    assert not stt.running
    assert _process_gone(pid)


async def test_missing_interpreter_is_helper_unavailable(make_helper, tmp_path):
    stt = make_helper(python=str(tmp_path / "no-such-python"))

    with pytest.raises(HelperError) as excinfo:
        await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert excinfo.value.code == "helper_unavailable"


async def test_worker_gone_right_after_the_load_is_a_retryable_crash(make_helper, tmp_path, monkeypatch):
    stt = make_helper()

    async def loaded_but_exited(_spec):
        return 5

    monkeypatch.setattr(stt, "_ensure_loaded", loaded_but_exited)

    with pytest.raises(HelperError) as excinfo:
        await stt.transcribe(_spec(tmp_path), ONE_SECOND, RATE)

    assert excinfo.value.code == "helper_crashed"
    assert not stt.busy
