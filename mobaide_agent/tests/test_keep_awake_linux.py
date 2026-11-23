"""``LinuxBackend`` against a fake ``systemd-inhibit``, so no test takes a real
sleep lock."""
from __future__ import annotations

import asyncio
import os
import signal

import pytest

from mobaide.agent.settings import keep_awake as keep_awake_module
from mobaide.agent.settings.keep_awake import KeepAwake, LinuxBackend

from .conftest import skip_if_not_linux

pytestmark = skip_if_not_linux

_FAKE_SYSTEMD_INHIBIT = """#!/bin/sh
what=""
while [ $# -gt 0 ]; do
  case "$1" in
    --what=*) what="${1#--what=}"; shift ;;
    --*) shift ;;
    *) break ;;
  esac
done
case " @REFUSED@ " in
  *" $what "*) echo "Failed to inhibit: Access denied" >&2; exit 1 ;;
esac
echo "$what" > "@TAKEN@"
echo $$ >> "@PID@"
@STALL@
exec "$@"
"""


@pytest.fixture
def inhibitor(tmp_path):
    """Writes the fake refusing the given ``--what=`` values; returns its path.
    With ``stall`` it waits for its stdin to close before it runs the holder,
    like an inhibitor still waiting for logind to answer."""

    def make(*refused: str, stall: bool = False) -> str:
        script = tmp_path / "systemd-inhibit"
        script.write_text(
            _FAKE_SYSTEMD_INHIBIT.replace("@REFUSED@", " ".join(refused))
            .replace("@TAKEN@", str(tmp_path / "taken"))
            .replace("@PID@", str(tmp_path / "pid"))
            .replace("@STALL@", "read line" if stall else ""),
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(script)

    return make


async def test_takes_the_idle_lock_when_sleep_is_refused_and_release_ends_the_holder(inhibitor, tmp_path):
    backend = LinuxBackend(inhibitor("sleep:idle"), None, runtime_dir=tmp_path)

    await backend.acquire()
    try:
        assert backend.held
        assert backend.error is None
        assert (tmp_path / "taken").read_text().strip() == "idle"
        holder = int((tmp_path / "pid").read_text())
    finally:
        await backend.release()

    assert not backend.held
    with pytest.raises(ProcessLookupError):
        os.kill(holder, 0)


async def test_every_lock_refused_reads_failed_with_the_inhibitors_words(inhibitor, tmp_path):
    keep_awake = KeepAwake(LinuxBackend(inhibitor("sleep:idle", "idle"), None, runtime_dir=tmp_path))

    await keep_awake.set(True)
    try:
        assert keep_awake.snapshot() == ("failed", "systemd-inhibit: Failed to inhibit: Access denied")
    finally:
        await keep_awake.set(False)

    assert keep_awake.snapshot() == ("off", None)


async def test_a_lock_that_ends_by_itself_reads_failed_through_on_change(inhibitor, tmp_path):
    keep_awake = KeepAwake(LinuxBackend(inhibitor(), None, runtime_dir=tmp_path))
    changed = asyncio.Event()
    keep_awake.on_change = changed.set

    await keep_awake.set(True)
    try:
        assert keep_awake.snapshot() == ("active", None)
        assert (tmp_path / "taken").read_text().strip() == "sleep:idle"

        os.kill(int((tmp_path / "pid").read_text()), signal.SIGTERM)
        await asyncio.wait_for(changed.wait(), 5)

        state, message = keep_awake.snapshot()
        assert state == "failed"
        assert "logind" in message
    finally:
        await keep_awake.set(False)


async def test_overlapping_fills_start_one_holder(inhibitor, tmp_path):
    """The retry fills the missing locks the same way an acquire does. The
    later fill must find the lock the first one took, not start a second
    holder that nothing would ever stop."""
    backend = LinuxBackend(inhibitor(), None, runtime_dir=tmp_path)

    await asyncio.gather(backend.acquire(), backend.acquire())
    try:
        assert backend.held
        assert len((tmp_path / "pid").read_text().split()) == 1
    finally:
        await backend.release()


async def test_turning_off_while_the_retry_starts_a_holder_ends_it(inhibitor, tmp_path, monkeypatch):
    """Turning the setting off cancels a retry still waiting for the
    inhibitor's answer. The holder it started must not outlive it, or it
    keeps the computer awake until the agent exits."""
    monkeypatch.setattr(keep_awake_module, "_RETRY_SECONDS", 0)
    backend = LinuxBackend(inhibitor("sleep:idle", "idle"), None, runtime_dir=tmp_path)
    keep_awake = KeepAwake(backend)
    await keep_awake.set(True)
    assert keep_awake.snapshot()[0] == "failed"

    # logind now grants the lock, but slowly; the retry runs at the next await.
    inhibitor(stall=True)
    started: asyncio.Future[asyncio.subprocess.Process] = asyncio.get_running_loop().create_future()
    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*argv, **kwargs):
        proc = await real_spawn(*argv, **kwargs)
        started.set_result(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    holder = await asyncio.wait_for(started, 5)

    await keep_awake.set(False)

    await asyncio.wait_for(holder.wait(), 5)
    assert not backend.held
