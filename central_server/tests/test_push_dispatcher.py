"""Tests for the APNs push package.

Covers the attention content map (string parity with the iOS/Android
notifiers), the dispatcher's fan-out over registered push targets,
per-device environment selection, dead-token cleanup on 410/Unregistered,
and the disabled (unconfigured) no-op path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from central_server.push.content import attention_content
from central_server.push.dispatcher import PushDispatcher
from central_server.push.sender import PushResult


class TestAttentionContent:
    @pytest.mark.parametrize(
        "reason,expected_title,expected_body",
        [
            (
                "awaiting_permission",
                "Permission needed",
                "My project is waiting for your approval",
            ),
            (
                "awaiting_os_permission",
                "Action needed on your Mac",
                "My project is paused — your Mac is asking to allow file "
                "access. Approve it on the Mac to continue.",
            ),
            (
                "turn_finished",
                "Your move",
                "My project finished its turn — tap to continue",
            ),
        ],
    )
    def test_reason_strings_match_client_notifiers(
        self, reason, expected_title, expected_body
    ):
        content = attention_content(reason, "My project", None, None)
        assert content == (expected_title, expected_body)

    def test_ended_includes_exit_reason_suffix(self):
        content = attention_content("ended", "My project", None, "exit code 1")
        assert content == ("Session ended (exit code 1)", "My project")

    def test_ended_blank_exit_reason_has_no_suffix(self):
        content = attention_content("ended", "My project", None, "   ")
        assert content == ("Session ended", "My project")

    def test_label_falls_back_title_then_project_then_default(self):
        _, body = attention_content("turn_finished", "  ", "proj", None)
        assert body.startswith("proj ")
        _, body = attention_content("turn_finished", None, None, None)
        assert body.startswith("Claude session ")

    @pytest.mark.parametrize("reason", [None, "", "something_new"])
    def test_unknown_or_absent_reason_returns_none(self, reason):
        assert attention_content(reason, "t", "p", None) is None


@dataclass
class FakeSender:
    results: dict[str, PushResult] = field(default_factory=dict)
    sent: list[dict] = field(default_factory=list)
    closed: bool = False

    async def send(self, *, token, title, body, session_id, agent_id,
                   environment) -> PushResult:
        self.sent.append(
            {
                "token": token,
                "title": title,
                "body": body,
                "session_id": session_id,
                "agent_id": agent_id,
                "environment": environment,
            }
        )
        return self.results.get(
            token, PushResult(ok=True, should_unregister=False, status="200")
        )

    def close(self) -> None:
        self.closed = True


def _attention_payload(**overrides) -> dict:
    payload = {
        "session_id": "s1",
        "reason": "awaiting_permission",
        "title": "Fix the bug",
        "agent_id": "agent-1",
    }
    payload.update(overrides)
    return payload


async def _register_device(db, user_id, device_id, token=None, env="sandbox"):
    await db.upsert_mobile_device(
        device_id=device_id, user_id=user_id, name="iPhone", platform="ios"
    )
    if token is not None:
        assert await db.set_push_token(device_id, user_id, "ios", token, env)


@pytest.fixture
async def user_id(db):
    return await db.create_user(
        email="push-user@test.local", password_hash="hash"
    )


class TestPushDispatcher:
    async def test_fans_out_to_all_registered_targets(self, db, user_id):
        await _register_device(db, user_id, "dev-1", token="tok-1")
        await _register_device(db, user_id, "dev-2", token="tok-2")
        await _register_device(db, user_id, "dev-no-token")
        sender = FakeSender()
        dispatcher = PushDispatcher(db, sender)

        await dispatcher._send_attention(user_id, _attention_payload())

        assert {s["token"] for s in sender.sent} == {"tok-1", "tok-2"}
        first = sender.sent[0]
        assert first["title"] == "Permission needed"
        assert first["body"] == "Fix the bug is waiting for your approval"
        assert first["session_id"] == "s1"
        assert first["agent_id"] == "agent-1"

    async def test_device_environment_wins_over_default(self, db, user_id):
        await _register_device(
            db, user_id, "dev-1", token="tok-1", env="production"
        )
        sender = FakeSender()
        dispatcher = PushDispatcher(db, sender, default_environment="sandbox")

        await dispatcher._send_attention(user_id, _attention_payload())

        assert sender.sent[0]["environment"] == "production"

    async def test_unregistered_result_clears_token(self, db, user_id):
        await _register_device(db, user_id, "dev-1", token="tok-dead")
        await _register_device(db, user_id, "dev-2", token="tok-live")
        sender = FakeSender(
            results={
                "tok-dead": PushResult(
                    ok=False,
                    should_unregister=True,
                    status="410",
                    description="Unregistered",
                )
            }
        )
        dispatcher = PushDispatcher(db, sender)

        await dispatcher._send_attention(user_id, _attention_payload())

        remaining = await db.list_push_targets(user_id)
        assert {row["push_token"] for row in remaining} == {"tok-live"}

    async def test_transient_failure_keeps_token(self, db, user_id):
        await _register_device(db, user_id, "dev-1", token="tok-1")
        sender = FakeSender(
            results={
                "tok-1": PushResult(
                    ok=False,
                    should_unregister=False,
                    status="500",
                    description="InternalServerError",
                )
            }
        )
        dispatcher = PushDispatcher(db, sender)

        await dispatcher._send_attention(user_id, _attention_payload())

        assert len(await db.list_push_targets(user_id)) == 1

    async def test_unknown_reason_sends_nothing(self, db, user_id):
        await _register_device(db, user_id, "dev-1", token="tok-1")
        sender = FakeSender()
        dispatcher = PushDispatcher(db, sender)

        await dispatcher._send_attention(
            user_id, _attention_payload(reason="something_new")
        )

        assert sender.sent == []

    async def test_disabled_dispatcher_schedules_nothing(self, db, user_id):
        dispatcher = PushDispatcher(db, sender=None)
        assert not dispatcher.enabled

        dispatcher.schedule_attention(user_id, _attention_payload())

        assert dispatcher._tasks == set()

    async def test_schedule_attention_completes_in_background(
        self, db, user_id
    ):
        await _register_device(db, user_id, "dev-1", token="tok-1")
        sender = FakeSender()
        dispatcher = PushDispatcher(db, sender)

        dispatcher.schedule_attention(user_id, _attention_payload())
        assert len(dispatcher._tasks) == 1
        await asyncio.gather(*dispatcher._tasks)

        assert len(sender.sent) == 1
        assert dispatcher._tasks == set()

    async def test_aclose_cancels_tasks_and_closes_sender(self, db, user_id):
        sender = FakeSender()
        dispatcher = PushDispatcher(db, sender)

        await dispatcher.aclose()

        assert sender.closed


class TestFromConfig:
    async def test_unconfigured_returns_disabled_dispatcher(
        self, db, config, caplog
    ):
        import logging

        with caplog.at_level(logging.WARNING):
            dispatcher = PushDispatcher.from_config(db, config)

        assert not dispatcher.enabled
        # AC: unconfigured server no-ops with zero errors logged.
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

        dispatcher.schedule_attention("u1", _attention_payload())
        assert dispatcher._tasks == set()

    async def test_configured_builds_enabled_dispatcher(
        self, db, config, tmp_path
    ):
        from dataclasses import replace

        key_file = tmp_path / "AuthKey_TEST.p8"
        key_file.write_text("-----BEGIN PRIVATE KEY-----\nfake\n")
        apns_config = replace(
            config,
            apns_key_path=str(key_file),
            apns_key_id="KEYID12345",
            apns_team_id="TEAM123456",
            apns_topic="com.mobaide.app",
            apns_use_sandbox=True,
        )

        dispatcher = PushDispatcher.from_config(db, apns_config)

        assert dispatcher.enabled
        assert dispatcher._default_environment == "sandbox"
        await dispatcher.aclose()
