"""``KeepAwake`` state machine and ``AgentSettingsService`` persistence, with a
fake backend."""
from __future__ import annotations

import asyncio
import json

import pytest

from mobaide.agent.settings.keep_awake import KeepAwake, KeepAwakeError
from mobaide.agent.settings.service import (
    EVENT_TYPE,
    AgentSettingsError,
    AgentSettingsService,
)

from .keep_awake_support import FakeBackend


def _wire(enabled: bool, state: str, message: str | None = None) -> dict:
    return {"keep_awake": {"enabled": enabled, "state": state, "message": message}}


class TestKeepAwake:
    async def test_on_takes_the_lock_and_off_lets_it_go(self):
        backend = FakeBackend()
        keep_awake = KeepAwake(backend)

        await keep_awake.set(True)
        assert keep_awake.snapshot() == ("active", None)
        assert backend.held

        await keep_awake.set(False)
        assert keep_awake.snapshot() == ("off", None)
        assert not backend.held

    @pytest.mark.parametrize(
        "error",
        [KeepAwakeError("Access denied"), RuntimeError("Access denied")],
        ids=["refused", "crashed"],
    )
    async def test_a_lock_the_os_refuses_reads_failed_until_turned_off(self, error):
        backend = FakeBackend()
        backend.refuse = error
        keep_awake = KeepAwake(backend)

        await keep_awake.set(True)

        state, message = keep_awake.snapshot()
        assert (keep_awake.enabled, state) == (True, "failed")
        assert "Access denied" in message

        await keep_awake.set(False)
        assert keep_awake.snapshot() == ("off", None)

    async def test_a_lock_lost_and_regained_by_itself_is_reported_each_time(self):
        backend = FakeBackend()
        keep_awake = KeepAwake(backend)
        changes = []
        keep_awake.on_change = lambda: changes.append(keep_awake.snapshot())
        await keep_awake.set(True)

        backend.lose("The logind lock ended (exit code 1).")
        backend.regain()

        assert changes == [("failed", "The logind lock ended (exit code 1)."), ("active", None)]

    async def test_a_refusal_is_forgotten_once_a_retry_gets_the_lock(self):
        backend = FakeBackend()
        backend.refuse = KeepAwakeError("systemd-inhibit: Access denied")
        keep_awake = KeepAwake(backend)
        await keep_awake.set(True)

        backend.regain()
        backend.lose("The GNOME session lock ended (exit code 0).")

        assert keep_awake.snapshot() == ("failed", "The GNOME session lock ended (exit code 0).")

    async def test_no_backend_is_unsupported_whatever_the_choice(self):
        keep_awake = KeepAwake(None, "This agent runs inside WSL.")

        await keep_awake.set(True)

        assert keep_awake.enabled
        assert keep_awake.snapshot() == ("unsupported", "This agent runs inside WSL.")


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def published() -> list[tuple[str, dict]]:
    return []


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "state" / "agent_settings.json"


def _service(settings_file, backend, published) -> AgentSettingsService:
    return AgentSettingsService(
        settings_file,
        publish=lambda event, payload: published.append((event, payload)),
        keep_awake=KeepAwake(backend),
    )


class TestAgentSettingsService:
    async def test_a_toggle_is_stored_beside_unknown_keys_applied_and_announced(
        self, settings_file, backend, published
    ):
        settings_file.parent.mkdir()
        settings_file.write_text(json.dumps({"from_a_newer_agent": {"x": 1}}), encoding="utf-8")
        service = _service(settings_file, backend, published)

        on = await service.set_keep_awake(True)

        assert on.model_dump(mode="json") == _wire(True, "active")
        assert backend.held
        assert json.loads(settings_file.read_text(encoding="utf-8")) == {
            "from_a_newer_agent": {"x": 1},
            "keep_awake": True,
        }

        off = await service.set_keep_awake(False)

        assert off.model_dump(mode="json") == _wire(False, "off")
        assert not backend.held
        assert json.loads(settings_file.read_text(encoding="utf-8"))["keep_awake"] is False
        assert published == [(EVENT_TYPE, _wire(True, "active")), (EVENT_TYPE, _wire(False, "off"))]

    async def test_a_stored_on_is_applied_again_after_a_restart(self, settings_file, backend, published):
        await _service(settings_file, FakeBackend(), []).set_keep_awake(True)
        restarted = _service(settings_file, backend, published)

        await restarted.start()

        assert backend.held
        assert restarted.status().model_dump(mode="json") == _wire(True, "active")
        assert published == [(EVENT_TYPE, _wire(True, "active"))]

        await restarted.shutdown()

        assert not backend.held
        assert json.loads(settings_file.read_text(encoding="utf-8"))["keep_awake"] is True

    @pytest.mark.parametrize(
        "stored",
        [None, '{"keep_awake": tru', '{"keep_awake": "true"}', '["keep_awake"]'],
        ids=["missing", "corrupt", "not_a_bool", "not_an_object"],
    )
    async def test_start_leaves_the_computer_alone_without_a_stored_on(
        self, settings_file, backend, published, stored
    ):
        if stored is not None:
            settings_file.parent.mkdir()
            settings_file.write_text(stored, encoding="utf-8")
        service = _service(settings_file, backend, published)

        await service.start()

        assert backend.acquires == 0
        assert service.status().model_dump(mode="json") == _wire(False, "off")
        assert published == []

    async def test_a_write_failure_is_a_storage_error_and_changes_nothing(self, tmp_path, backend, published):
        (tmp_path / "not-a-dir").write_text("", encoding="utf-8")
        service = _service(tmp_path / "not-a-dir" / "agent_settings.json", backend, published)

        with pytest.raises(AgentSettingsError) as caught:
            await service.set_keep_awake(True)

        assert (caught.value.status, caught.value.code) == (500, "storage_error")
        assert backend.acquires == 0
        assert service.status().model_dump(mode="json") == _wire(False, "off")
        assert published == []

    async def test_a_lock_lost_by_itself_is_announced(self, settings_file, backend, published):
        service = _service(settings_file, backend, published)
        await service.set_keep_awake(True)

        backend.lose("The logind lock ended (exit code 1).")

        assert published[-1] == (
            EVENT_TYPE, _wire(True, "failed", "The logind lock ended (exit code 1)."),
        )

    async def test_crossing_toggles_end_on_the_last_one(self, settings_file, backend, published):
        backend.gate = asyncio.Event()
        service = _service(settings_file, backend, published)

        turning_on = asyncio.create_task(service.set_keep_awake(True))
        await asyncio.wait_for(_until(lambda: backend.acquires == 1), 5)
        turning_off = asyncio.create_task(service.set_keep_awake(False))
        await asyncio.sleep(0)
        backend.gate.set()
        await asyncio.wait_for(asyncio.gather(turning_on, turning_off), 5)

        assert not backend.held
        assert service.status().model_dump(mode="json") == _wire(False, "off")
        assert json.loads(settings_file.read_text(encoding="utf-8"))["keep_awake"] is False
        assert published[-1] == (EVENT_TYPE, _wire(False, "off"))

    async def test_a_start_waiting_behind_toggles_does_not_undo_the_last_one(
        self, settings_file, backend, published
    ):
        """The boot re-apply runs in the background, so a toggle can take the
        lock before it. The start must read the choice when its turn comes,
        not the "on" it would have read while it waited."""
        backend.gate = asyncio.Event()
        service = _service(settings_file, backend, published)
        turning_on = asyncio.create_task(service.set_keep_awake(True))
        await asyncio.wait_for(_until(lambda: backend.acquires == 1), 5)
        turning_off = asyncio.create_task(service.set_keep_awake(False))
        await asyncio.sleep(0)
        starting = asyncio.create_task(service.start())
        await asyncio.sleep(0)

        backend.gate.set()
        await asyncio.wait_for(asyncio.gather(turning_on, turning_off, starting), 5)

        assert backend.acquires == 1
        assert not backend.held
        assert service.status().model_dump(mode="json") == _wire(False, "off")
        assert published[-1] == (EVENT_TYPE, _wire(False, "off"))

    async def test_a_shutdown_during_a_slow_start_cancels_it_and_holds_nothing(
        self, settings_file, backend, published
    ):
        await _service(settings_file, FakeBackend(), []).set_keep_awake(True)
        backend.gate = asyncio.Event()
        service = _service(settings_file, backend, published)
        service.start_soon()
        await asyncio.wait_for(_until(lambda: backend.acquires == 1), 5)

        await asyncio.wait_for(service.shutdown(restarting=True), 5)

        assert backend.cancelled == 1
        assert not backend.held
        assert published == []

    @pytest.mark.parametrize(
        ("refuse", "restarting", "bridges"),
        [(None, True, 1), (None, False, 0), (KeepAwakeError("Access denied"), True, 0)],
        ids=["restart_while_awake", "stop", "restart_while_failed"],
    )
    async def test_shutdown_bridges_only_a_restart_while_the_lock_holds(
        self, settings_file, backend, published, refuse, restarting, bridges
    ):
        backend.refuse = refuse
        service = _service(settings_file, backend, published)
        await service.set_keep_awake(True)

        await service.shutdown(restarting=restarting)

        assert backend.bridges == bridges
        assert not backend.held


async def _until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0)
