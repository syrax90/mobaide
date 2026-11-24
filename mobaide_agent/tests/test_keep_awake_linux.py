"""``LinuxBackend`` against fake ``systemd-inhibit``, ``gnome-session-inhibit``,
``busctl`` and ``systemd-run``, so no test takes a real sleep lock."""
from __future__ import annotations

import asyncio
import os
import signal

import pytest

from mobaide.agent.settings import keep_awake as keep_awake_module
from mobaide.agent.settings.keep_awake import BRIDGE_SECONDS, REASON, KeepAwake, LinuxBackend

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
echo "$what" >> "@TRIED@"
case " @REFUSED@ " in
  *" $what "*) echo "Failed to inhibit: Access denied" >&2; exit 1 ;;
esac
echo "$what" > "@TAKEN@"
echo $$ >> "@PID@"
@STALL@
exec "$@"
"""


@pytest.fixture(autouse=True)
def _no_desktop_login(tmp_path_factory, monkeypatch):
    """Keeps ``_display_session()`` off the host's real /run/systemd/users."""
    monkeypatch.setattr(keep_awake_module, "_LOGIND_USERS", tmp_path_factory.mktemp("logind_users"))


@pytest.fixture
def inhibitor(tmp_path):
    """Writes the fake refusing the given ``--what=`` values; returns its path.
    Every run, refused or not, adds its ``--what=`` to ``tried``. With
    ``stall`` it waits for its stdin to close before it runs the holder,
    like an inhibitor still waiting for logind to answer."""

    def make(*refused: str, stall: bool = False) -> str:
        script = tmp_path / "systemd-inhibit"
        script.write_text(
            _FAKE_SYSTEMD_INHIBIT.replace("@REFUSED@", " ".join(refused))
            .replace("@TAKEN@", str(tmp_path / "taken"))
            .replace("@TRIED@", str(tmp_path / "tried"))
            .replace("@PID@", str(tmp_path / "pid"))
            .replace("@STALL@", "read line" if stall else ""),
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(script)

    return make


async def test_takes_the_idle_lock_when_sleep_is_refused_and_release_ends_the_holder(inhibitor, tmp_path):
    backend = LinuxBackend(inhibitor("sleep"), None, runtime_dir=tmp_path)

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
    keep_awake = KeepAwake(LinuxBackend(inhibitor("sleep", "idle"), None, runtime_dir=tmp_path))

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
        assert (tmp_path / "taken").read_text().strip() == "sleep"

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
    backend = LinuxBackend(inhibitor("sleep", "idle"), None, runtime_dir=tmp_path)
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


async def test_an_idle_lock_from_before_login_becomes_sleep_once_per_desktop_login(
    inhibitor, tmp_path, monkeypatch
):
    """Before any desktop login polkit refuses a system service ``sleep``, and
    KDE's auto-suspend ignores ``idle``. A lingering user's bus is there long
    before the login, so only logind's ``DISPLAY=`` marks one. Each graphical
    session gets one ``sleep`` try; a granted one must end the idle holder,
    and the fills after it must start nothing."""
    users = tmp_path / "users"
    users.mkdir()
    monkeypatch.setattr(keep_awake_module, "_LOGIND_USERS", users)
    user_state = users / str(os.getuid())
    user_state.write_text("STATE=lingering\n", encoding="utf-8")
    (tmp_path / "bus").write_text("", encoding="utf-8")

    def log_in(session: str) -> None:
        user_state.write_text(f"STATE=active\nDISPLAY={session}\n", encoding="utf-8")

    tried = tmp_path / "tried"
    backend = LinuxBackend(inhibitor("sleep"), None, runtime_dir=tmp_path)
    await backend.acquire()
    try:
        await backend.acquire()
        assert tried.read_text().split() == ["sleep", "idle"]

        # polkit refuses this session, and is not asked again in it.
        log_in("c2")
        await backend.acquire()
        inhibitor()
        await backend.acquire()
        assert tried.read_text().split() == ["sleep", "idle", "sleep"]

        log_in("c3")
        await backend.acquire()
        log_in("c4")
        await backend.acquire()

        assert tried.read_text().split() == ["sleep", "idle", "sleep", "sleep"]
        idle_holder, sleep_holder = (int(pid) for pid in (tmp_path / "pid").read_text().split())
        assert not _alive(idle_holder)
        assert _alive(sleep_holder)
        assert backend.held
        assert backend.error is None
    finally:
        await backend.release()

    assert not _alive(sleep_holder)


_FAKE_GNOME_SESSION_INHIBIT = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    --*) shift 2 ;;
    *) break ;;
  esac
done
echo $$ >> "@PID@"
exec "$@"
"""

# One argument per line, a blank line after each call.
_FAKE_SYSTEMD_RUN = """#!/bin/sh
printf '%s\\n' "$@" '' >> "@CALLS@"
"""


def _executable(path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class _GnomeSession:
    """A fake signed-in GNOME session: a bus socket, an inhibitor, and a
    ``busctl`` that names pid 4242 as the session manager, ``manager``
    being its comm. ``starts`` stands in for ``/proc``, which a macOS run
    does not have."""

    PID = 4242

    def __init__(self, tmp_path, monkeypatch) -> None:
        self.bus = tmp_path / "bus"
        self.bus.write_text("", encoding="utf-8")
        self._holders = tmp_path / "gnome-pid"
        self.inhibit = _executable(
            tmp_path / "gnome-session-inhibit",
            _FAKE_GNOME_SESSION_INHIBIT.replace("@PID@", str(self._holders)),
        )
        self.busctl = _executable(tmp_path / "busctl", f'#!/bin/sh\necho "u {self.PID}"\n')
        self.manager = "gnome-session-b"
        self.starts = {self.PID: "1000"}
        monkeypatch.setattr(keep_awake_module, "_comm", lambda pid: self.manager if pid in self.starts else "")
        monkeypatch.setattr(keep_awake_module, "_start_time", self.starts.get)

    def holders(self) -> list[int]:
        return [int(pid) for pid in self._holders.read_text().split()]

    def stay(self) -> None:
        pass

    def restart_session_manager(self) -> None:
        self.starts[self.PID] = "2000"

    def replace_bus(self) -> None:
        fresh = self.bus.with_name("bus.new")
        fresh.write_text("", encoding="utf-8")
        os.replace(fresh, self.bus)

    def log_out(self) -> None:
        self.bus.unlink()


@pytest.fixture
def gnome_session(tmp_path, monkeypatch) -> _GnomeSession:
    return _GnomeSession(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("manager", "change", "started", "alive"),
    [
        ("gnome-session-b", "stay", 1, [0]),
        ("gnome-session-b", "restart_session_manager", 2, [1]),
        ("cinnamon-sessio", "restart_session_manager", 2, [1]),
        ("gnome-session-b", "replace_bus", 2, [1]),
        ("gnome-session-b", "log_out", 1, []),
    ],
    ids=[
        "same_session",
        "session_manager_restarted",
        "cinnamon_session_manager_restarted",
        "bus_replaced",
        "logged_out",
    ],
)
async def test_the_gnome_lock_follows_the_session_it_was_taken_in(
    inhibitor, gnome_session, tmp_path, manager, change, started, alive
):
    """gnome-session-inhibit outlives a log-out while its lock ended with the
    session. The next fill (the minute retry runs the same one as an acquire)
    must end that holder and take the lock in the new session, and must
    leave a live session's holder and the logind lock alone. Cinnamon's
    session manager owns the same bus name."""
    gnome_session.manager = manager
    backend = LinuxBackend(
        inhibitor(), gnome_session.inhibit, runtime_dir=tmp_path, busctl=gnome_session.busctl
    )
    await backend.acquire()
    try:
        assert len(gnome_session.holders()) == 1

        getattr(gnome_session, change)()
        await backend.acquire()

        holders = gnome_session.holders()
        assert len(holders) == started
        assert [i for i, pid in enumerate(holders) if _alive(pid)] == alive
        assert _alive(int((tmp_path / "pid").read_text()))
        assert backend.error is None
    finally:
        await backend.release()


async def test_a_restart_bridge_runs_each_held_lock_under_the_users_service_manager(
    inhibitor, gnome_session, tmp_path
):
    """systemd stops every process of the agent's unit before it starts the
    next one, so each bridge must run in the user's manager, with the
    inhibitor command that got the lock, and end by itself."""
    calls = tmp_path / "systemd-run-calls"
    systemd_run = _executable(tmp_path / "systemd-run", _FAKE_SYSTEMD_RUN.replace("@CALLS@", str(calls)))
    systemd_inhibit = inhibitor("sleep")
    backend = LinuxBackend(
        systemd_inhibit,
        gnome_session.inhibit,
        runtime_dir=tmp_path,
        busctl=gnome_session.busctl,
        systemd_run=systemd_run,
    )
    keep_awake = KeepAwake(backend)
    await keep_awake.set(True)

    await keep_awake.close(bridge=True)

    user_unit = [
        "--user", "--collect", "--quiet",
        f"--setenv=DBUS_SESSION_BUS_ADDRESS=unix:path={gnome_session.bus}", "--",
    ]
    logind = [systemd_inhibit, "--what=idle", "--who=Mobaide", f"--why={REASON}", "--mode=block"]
    gnome = [gnome_session.inhibit, "--app-id", "mobaide", "--reason", REASON, "--inhibit", "suspend"]
    until = ["sleep", str(BRIDGE_SECONDS)]
    assert [run.split("\n") for run in calls.read_text().split("\n\n") if run] == [
        [*user_unit, *logind, *until],
        [*user_unit, *gnome, *until],
    ]
    assert not backend.held
