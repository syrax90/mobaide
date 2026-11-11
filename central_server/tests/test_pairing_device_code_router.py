"""End-to-end tests for the reversed (device-code) pairing endpoints.

The browser mints a short code, an anonymous agent redeems it into an
ordinary pairing nonce, and the account holder confirms — rejoining the QR
flow's signed-credential push at the same WebSocket registry.

Like ``test_pairing_qr_router.py`` this suite wires its own app fixture:
the shared ``conftest.py`` configures neither the manifest signing keypair
nor ``app.state.pairing_ws_registry``, and both are required here. The
three device-code rate limiters are deliberately left unwired — the
dependencies fall open in that state, matching ``_require_reset_ip_quota``.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from central_server.auth.rate_limit import ResendRateLimiter
from central_server.config import CentralServerConfig
from central_server.pairing.qr_signer import (
    compute_trust_key_fp,
    hash_pairing_nonce,
)
from central_server.pairing.ws_handlers import PairingWsRegistry

from tests.test_user_events import _make_user_client

_DEVICE_UUID = "11111111-1111-4111-8111-111111111111"
_STALE_FP = "aa" * 32


def _ed25519_keypair_b64() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    return (
        base64.b64encode(private.private_bytes_raw()).decode(),
        base64.b64encode(private.public_key().public_bytes_raw()).decode(),
    )


def _override_config(app, **changes) -> None:
    """Rebuild the frozen config dataclass with overrides applied."""
    app.state.config = CentralServerConfig(**{**app.state.config.__dict__, **changes})


@pytest.fixture
async def dc_app(app):
    """Wrap the conftest ``app`` with device-code-pairing state."""
    priv_b64, pub_b64 = _ed25519_keypair_b64()
    _override_config(
        app,
        manifest_signing_public_key_b64=pub_b64,
        manifest_signing_private_key_b64=priv_b64,
        public_base_url="https://relay.test",
    )
    app.state.pairing_ws_registry = PairingWsRegistry()
    app.state._test_pub_b64 = pub_b64
    yield app


async def _mint(client, headers) -> dict:
    resp = await client.post("/api/pairing/device-code", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _redeem(client, code: str, redeemer_id: str, **extra):
    return await client.post(
        "/api/pairing/device-code/redeem",
        json={"code": code, "redeemer_id": redeemer_id, **extra},
    )


async def _accept(client, code: str, redeemer_id: str):
    return await client.post(
        "/api/pairing/device-code/accept",
        json={"code": code, "redeemer_id": redeemer_id},
    )


async def _redeem_and_accept(client, code: str, redeemer_id: str, **extra):
    """Drive a code to the state the browser is allowed to confirm.

    Two calls because the relay will not offer a confirm until the machine
    that entered the code says its operator approved — the terminal prompt
    is the reversed direction's primary control, so nothing may be
    registered or rotated while it is still unanswered.
    """
    redeemed = await _redeem(client, code, redeemer_id, **extra)
    assert redeemed.status_code == 200, redeemed.text
    accepted = await _accept(client, code, redeemer_id)
    assert accepted.status_code == 204, accepted.text
    return redeemed


async def _confirm(client, headers, request_id: str):
    return await client.post(
        f"/api/pairing/device-code/{request_id}/confirm",
        headers=headers,
        json={"mobile_device_id": _DEVICE_UUID, "platform": "web"},
    )


async def _expire_nonce(db, nonce: str) -> None:
    """Backdate the pairing token minted at redeem, leaving the code row alone."""
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await db.conn.execute(
        "UPDATE pairing_tokens SET expires_at = ? WHERE token_hash = ?",
        (past, hash_pairing_nonce(nonce)),
    )
    await db.conn.commit()


class TestMintAndRedeem:
    async def test_redeem_accepts_the_code_as_the_operator_retypes_it(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        typed = minted["code"].lower().replace("-", " ") + "\n"

        resp = await _redeem(client, typed, "redeemer-lowercase")

        assert resp.status_code == 200, resp.text
        assert resp.json()["nonce"]

    async def test_redeem_hands_back_the_relay_fingerprint_and_masked_account(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)

        body = (await _redeem(client, minted["code"], "redeemer-hint")).json()

        # The agent fail-closes on this fingerprint, and the human at the
        # terminal decides on the hint — both are load-bearing.
        assert body["trust_key_fp"] == compute_trust_key_fp(
            dc_app.state._test_pub_b64
        )
        assert "***" in body["account_hint"]
        assert "viewer@test.local" not in body["account_hint"]
        assert body["replayed"] is False
        assert body["is_repair"] is False

    async def test_malformed_code_is_rejected_before_any_lookup(
        self, client, dc_app
    ):
        resp = await _redeem(client, "nope", "redeemer-malformed")

        assert resp.status_code == 400

    async def test_well_formed_but_unknown_code_is_gone(self, client, dc_app):
        # Same shape as a real code, so this exercises the hash lookup miss
        # rather than the normaliser.
        resp = await _redeem(client, "WDJB-MJHT", "redeemer-unknown")

        assert resp.status_code == 410

    async def test_minting_again_supersedes_the_previous_code(
        self, client, dc_app, api_auth_headers
    ):
        first = await _mint(client, api_auth_headers)
        await _mint(client, api_auth_headers)

        resp = await _redeem(client, first["code"], "redeemer-stale")

        # One live code per account is a guessing-surface control, not just
        # tidiness: the ~34.6-bit space only holds while the valid set is 1.
        assert resp.status_code == 410

    async def test_redeem_fails_closed_when_the_relay_has_no_signing_keys(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        _override_config(dc_app, manifest_signing_private_key_b64=None)

        resp = await _redeem(client, minted["code"], "redeemer-keyless")

        # Must refuse before spending the code — a redeem that succeeded
        # here would burn it and then have no fingerprint to return.
        assert resp.status_code == 503


class TestConfirm:
    async def test_confirm_reaches_the_agent_waiting_on_the_nonce(
        self, client, dc_app, api_auth_headers
    ):
        # The registry is keyed by the nonce *hash*. A raw-vs-hash mismatch
        # fails soft — ws_delivered=False and the agent recovers a TTL late
        # via read_and_clear_claimed_credentials — so this assertion is the
        # only thing that catches it on the device-code path.
        minted = await _mint(client, api_auth_headers)
        redeemed = (await _redeem_and_accept(client, minted["code"], "redeemer-ws")).json()
        queue = await dc_app.state.pairing_ws_registry.register_waiter(
            hash_pairing_nonce(redeemed["nonce"])
        )

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 200, resp.text
        assert resp.json()["ws_delivered"] is True, resp.text
        assert not queue.empty(), "signed credential push never reached the waiter"

    async def test_confirm_pins_the_relay_key_to_the_account(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-pin")

        await _confirm(client, api_auth_headers, minted["request_id"])

        pin = await client.get("/api/pairing/trust-pin", headers=api_auth_headers)
        assert pin.json()["account_trust_key_fp"] == compute_trust_key_fp(
            dc_app.state._test_pub_b64
        )

    async def test_confirm_is_single_use(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-replay")
        await _confirm(client, api_auth_headers, minted["request_id"])

        again = await _confirm(client, api_auth_headers, minted["request_id"])

        assert again.status_code == 410

    async def test_confirm_before_any_machine_redeemed_is_a_conflict(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 409

    async def test_confirm_after_the_nonce_expired_is_gone(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        redeemed = (await _redeem_and_accept(client, minted["code"], "redeemer-expiry")).json()
        await _expire_nonce(dc_app.state.db, redeemed["nonce"])

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        # The confirm CAS tests the *nonce's* deadline, which is shorter
        # than the code's; the code row still looks redeemable here.
        assert resp.status_code == 410

    async def test_a_refused_confirm_leaves_the_request_open_for_a_retry(
        self, client, dc_app, api_auth_headers, db, viewer_user, registered_agent
    ):
        # The reservation is taken before the pairing runs, so the refusal
        # path has to hand it back — otherwise one 409 burns a code that
        # the operator can still legitimately confirm.
        await db.pin_user_trust_key_fp(viewer_user["id"], _STALE_FP)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-reopen")
        refused = await _confirm(client, api_auth_headers, minted["request_id"])
        assert refused.status_code == 409, refused.text

        _override_config(dc_app, trust_key_pin_enforced=False)
        again = await _confirm(client, api_auth_headers, minted["request_id"])

        assert again.status_code == 200, again.text


class TestTerminalApproval:
    """Nothing may be created or rotated until the machine agrees.

    The browser mints the code, so a confirmation it shows is answered by
    whoever minted it — the prompt on the agent's own terminal is the only
    party that can refuse a code it did not ask for. That prompt is only a
    control if the relay waits for its answer.
    """

    async def test_a_redeemed_code_polls_as_pending_until_the_host_accepts(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem(client, minted["code"], "redeemer-unapproved")

        before = (
            await client.get(
                f"/api/pairing/device-code/{minted['request_id']}",
                headers=api_auth_headers,
            )
        ).json()
        await _accept(client, minted["code"], "redeemer-unapproved")
        after = (
            await client.get(
                f"/api/pairing/device-code/{minted['request_id']}",
                headers=api_auth_headers,
            )
        ).json()

        assert before["status"] == "pending"
        assert after["status"] == "redeemed"
        assert after["hostname"] == before["hostname"]

    async def test_confirming_before_the_host_accepts_changes_nothing(
        self, client, dc_app, api_auth_headers, db, viewer_user
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem(client, minted["code"], "redeemer-early")

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 409, resp.text
        # The damage the guard exists to prevent: a declined prompt used to
        # leave a registered agent behind, and a declined re-pair left the
        # working host's token already rotated.
        assert await db.list_agents_for_user(viewer_user["id"]) == []

    async def test_accepting_twice_is_still_no_content(
        self, client, dc_app, api_auth_headers
    ):
        # Idempotent on purpose: the agent re-sends on a resumed run rather
        # than persisting whether the first attempt was acknowledged.
        minted = await _mint(client, api_auth_headers)
        await _redeem(client, minted["code"], "redeemer-twice")

        first = await _accept(client, minted["code"], "redeemer-twice")
        second = await _accept(client, minted["code"], "redeemer-twice")

        assert (first.status_code, second.status_code) == (204, 204)

    async def test_another_machine_cannot_accept_the_redemption(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem(client, minted["code"], "redeemer-owner")

        resp = await _accept(client, minted["code"], "redeemer-impostor")

        assert resp.status_code == 410

    async def test_accepting_a_canceled_request_is_refused(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem(client, minted["code"], "redeemer-canceled")
        await client.delete(
            f"/api/pairing/device-code/{minted['request_id']}",
            headers=api_auth_headers,
        )

        resp = await _accept(client, minted["code"], "redeemer-canceled")

        assert resp.status_code == 410


class TestRepairDisconnect:
    async def test_rotating_an_online_agent_drops_its_tunnel(
        self, client, dc_app, api_auth_headers, registered_agent
    ):
        # The rotated token stops authenticating, so leaving the socket up
        # left an agent connected on revoked credentials — and left every
        # client's "waiting for it to come online" watcher reading a `true`
        # that predates the pairing it is waiting for.
        agent_id, _ = registered_agent
        agent_ws = MagicMock()
        agent_ws.send_text = AsyncMock()
        agent_ws.close = AsyncMock()
        dc_app.state.routing.register_agent(agent_id, agent_ws)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(
            client,
            minted["code"],
            "redeemer-repair-online",
            current_agent_id=agent_id,
        )

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 200, resp.text
        assert dc_app.state.routing.is_agent_online(agent_id) is False
        # 4012, not 4010: the agent is expected back, and the paired mobile
        # sockets closed with it must keep reconnecting rather than treat
        # this as a destructive revocation.
        agent_ws.close.assert_awaited_once_with(
            code=4012, reason="credentials_rotated"
        )


class TestRedeemIdempotency:
    async def test_same_redeemer_replays_without_reissuing_the_nonce(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        first = (await _redeem(client, minted["code"], "redeemer-stable")).json()

        second = await _redeem(client, minted["code"], "redeemer-stable")

        # A response lost in transit must not leave the operator holding a
        # burnt code. The nonce itself is never stored, so the replay says
        # so rather than inventing a second one.
        assert second.status_code == 200, second.text
        assert second.json()["replayed"] is True
        assert second.json()["nonce"] is None
        assert second.json()["expires_at"] == first["expires_at"]

    async def test_a_second_machine_cannot_take_a_redeemed_code(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem(client, minted["code"], "redeemer-first")

        resp = await _redeem(client, minted["code"], "redeemer-second")

        assert resp.status_code == 410

    async def test_replay_stops_once_the_request_is_confirmed(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-settled")
        await _confirm(client, api_auth_headers, minted["request_id"])

        resp = await _redeem(client, minted["code"], "redeemer-settled")

        assert resp.status_code == 410


class TestStatus:
    async def test_reports_the_redeeming_host_once_a_machine_answers(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        before = await client.get(
            f"/api/pairing/device-code/{minted['request_id']}",
            headers=api_auth_headers,
        )
        assert before.json()["status"] == "pending"

        await _redeem_and_accept(
            client,
            minted["code"],
            "redeemer-status",
            hostname="pavel-mbp",
            os="Darwin",
        )

        body = (
            await client.get(
                f"/api/pairing/device-code/{minted['request_id']}",
                headers=api_auth_headers,
            )
        ).json()
        assert body["status"] == "redeemed"
        assert body["hostname"] == "pavel-mbp"
        assert body["os"] == "Darwin"
        assert body["blocked_reason"] is None

    async def test_reports_expired_once_the_nonce_deadline_passed(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        redeemed = (
            await _redeem(client, minted["code"], "redeemer-status-expiry")
        ).json()
        await _expire_nonce(dc_app.state.db, redeemed["nonce"])

        body = (
            await client.get(
                f"/api/pairing/device-code/{minted['request_id']}",
                headers=api_auth_headers,
            )
        ).json()

        # Computed from the pairing_tokens row, not the code row: reading
        # "redeemed" here would offer a confirm button guaranteed to 410.
        assert body["status"] == "expired"

    async def test_flags_a_repair_of_an_agent_owned_by_someone_else(
        self, client, dc_app, api_auth_headers, db
    ):
        other = await db.create_user(
            email="other@test.local", password_hash="h"
        )
        foreign_agent_id, _token = await db.register_agent("theirs", other)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(
            client,
            minted["code"],
            "redeemer-foreign",
            current_agent_id=foreign_agent_id,
        )

        body = (
            await client.get(
                f"/api/pairing/device-code/{minted['request_id']}",
                headers=api_auth_headers,
            )
        ).json()

        # Surfaced before the click so the confirm does not burn the code
        # and the nonce on a refusal that was knowable in advance.
        assert body["is_repair"] is True
        assert body["blocked_reason"] == "owned_by_other"


class TestCancel:
    async def test_cancel_blocks_a_later_confirm(
        self, client, dc_app, api_auth_headers
    ):
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-cancel")

        deleted = await client.delete(
            f"/api/pairing/device-code/{minted['request_id']}",
            headers=api_auth_headers,
        )

        assert deleted.status_code == 204
        resp = await _confirm(client, api_auth_headers, minted["request_id"])
        assert resp.status_code == 410

    async def test_a_cancel_landing_mid_confirm_cannot_also_win(
        self, client, dc_app, api_auth_headers, monkeypatch
    ):
        # The dangerous ordering: the DELETE arrives after the confirm has
        # been admitted but before the credentials exist. Cancel used to
        # answer 204 there while the confirm went on to hand the machine an
        # agent, so both clients were told they had won.
        from central_server.pairing import router as router_mod

        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-race")
        original = router_mod._complete_pairing
        cancel_codes: list[int] = []

        async def cancel_then_complete(**kwargs):
            resp = await client.delete(
                f"/api/pairing/device-code/{minted['request_id']}",
                headers=api_auth_headers,
            )
            cancel_codes.append(resp.status_code)
            return await original(**kwargs)

        monkeypatch.setattr(
            router_mod, "_complete_pairing", cancel_then_complete
        )

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 200, resp.text
        assert cancel_codes == [404]


class TestAccountTrustPin:
    async def test_refuses_a_rotated_key_for_an_account_that_owns_agents(
        self, client, dc_app, api_auth_headers, db, viewer_user, registered_agent
    ):
        await db.pin_user_trust_key_fp(viewer_user["id"], _STALE_FP)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-pinned")

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 409, resp.text
        # Clients discriminate on the code, not the prose — 409 already
        # means "agent belongs to another account" on this path.
        assert resp.json()["detail"]["error"] == "trust_key_pin_mismatch"

    async def test_a_refused_confirm_leaves_the_stale_pin_in_place(
        self, client, dc_app, api_auth_headers, db, viewer_user, registered_agent
    ):
        await db.pin_user_trust_key_fp(viewer_user["id"], _STALE_FP)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-pin-intact")

        await _confirm(client, api_auth_headers, minted["request_id"])

        assert await db.get_user_trust_key_fp(viewer_user["id"]) == _STALE_FP

    async def test_repins_an_account_whose_pin_protects_no_agents(
        self, client, dc_app, api_auth_headers, db, viewer_user
    ):
        # Self-service recovery: "remove your agents, pair again". The
        # account owns nothing, so the stale pin is guarding nothing.
        await db.pin_user_trust_key_fp(viewer_user["id"], _STALE_FP)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-repin")

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 200, resp.text
        assert await db.get_user_trust_key_fp(
            viewer_user["id"]
        ) == compute_trust_key_fp(dc_app.state._test_pub_b64)

    async def test_disabling_enforcement_clears_a_stale_pin(
        self, client, dc_app, api_auth_headers, db, viewer_user, registered_agent
    ):
        _override_config(dc_app, trust_key_pin_enforced=False)
        await db.pin_user_trust_key_fp(viewer_user["id"], _STALE_FP)
        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-unenforced")

        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 200, resp.text
        # The escape hatch has to be self-healing: accounts re-pin as they
        # pair, so the flag can be turned back on after a rotation.
        assert await db.get_user_trust_key_fp(
            viewer_user["id"]
        ) == compute_trust_key_fp(dc_app.state._test_pub_b64)


class TestRateLimiting:
    """The three device-code limiters, wired for real.

    ``dc_app`` deliberately leaves them unset (see its docstring) so the
    rest of this module exercises the falls-open branch. Each test below
    tightens only the one limiter it means to trip; the other two stay at
    a cap generous enough not to interfere, since ``getattr`` reads
    whatever is on ``app.state`` at request time.
    """

    @pytest.fixture
    async def limited_app(self, dc_app):
        dc_app.state.device_code_ip_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=1000
        )
        dc_app.state.device_code_global_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=1000
        )
        dc_app.state.device_code_mint_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=0
        )
        yield dc_app

    async def test_the_per_ip_breaker_returns_429_with_retry_after(
        self, client, limited_app
    ):
        limited_app.state.device_code_ip_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=2
        )
        # Charged on every redeem regardless of outcome, so an unknown code
        # is enough to fill it — and ASGITransport reports one synthetic
        # ``request.client.host``, so every call here shares one IP key.
        for _ in range(2):
            resp = await _redeem(client, "ZZZZZZZZ", "redeemer-noise")
            assert resp.status_code == 410, resp.text

        resp = await _redeem(client, "ZZZZZZZZ", "redeemer-noise")

        assert resp.status_code == 429, resp.text
        assert resp.headers.get("Retry-After")
        detail = resp.json()["detail"]
        assert detail["error"] == "device_code_rate_limited"
        assert detail["retry_after_seconds"] > 0

    async def test_the_global_breaker_is_not_charged_by_successful_redeems(
        self, client, limited_app, api_auth_headers
    ):
        limited_app.state.device_code_global_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=2
        )
        # More redemptions than the breaker's cap: charging every redeem
        # the way a failure is charged would 429 well before the fifth —
        # this is the whole reason ``peek`` exists instead of reusing
        # ``_require_device_code_redeem_quota``'s ``try_acquire``.
        for i in range(5):
            minted = await _mint(client, api_auth_headers)
            resp = await _redeem(client, minted["code"], f"redeemer-{i}")
            assert resp.status_code == 200, resp.text

    async def test_the_global_breaker_trips_after_failed_redeems(
        self, client, limited_app
    ):
        limited_app.state.device_code_global_limiter = ResendRateLimiter(
            cooldown_seconds=0, hourly_cap=2
        )
        for _ in range(2):
            resp = await _redeem(client, "ZZZZZZZZ", "redeemer-noise")
            assert resp.status_code == 410, resp.text

        # The third redeem never reaches the handler: ``peek`` refuses it
        # at the dependency, before a fourth failure could be charged.
        resp = await _redeem(client, "ZZZZZZZZ", "redeemer-noise")

        assert resp.status_code == 429, resp.text
        assert resp.headers.get("Retry-After")
        detail = resp.json()["detail"]
        assert detail["error"] == "device_code_rate_limited"
        assert detail["retry_after_seconds"] > 0

    async def test_the_mint_cooldown_returns_429(
        self, client, limited_app, api_auth_headers
    ):
        limited_app.state.device_code_mint_limiter = ResendRateLimiter(
            cooldown_seconds=100, hourly_cap=0
        )
        await _mint(client, api_auth_headers)

        resp = await client.post(
            "/api/pairing/device-code", headers=api_auth_headers
        )

        assert resp.status_code == 429, resp.text
        assert resp.headers.get("Retry-After")
        detail = resp.json()["detail"]
        assert detail["error"] == "device_code_rate_limited"
        assert detail["retry_after_seconds"] > 0


class TestFirstPairingConversion:
    async def test_confirm_marks_the_first_pairing_once_and_schedules_the_conversion(
        self, client, dc_app, api_auth_headers
    ):
        uploader = MagicMock()
        dc_app.state.ads_uploader = uploader
        db = dc_app.state.db
        owner = await db.get_user_by_email("viewer@test.local")

        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-first")
        resp = await _confirm(client, api_auth_headers, minted["request_id"])
        assert resp.status_code == 200, resp.text
        marker = (await db.get_user_by_id(owner["id"]))["first_paired_at"]
        assert marker is not None
        uploader.schedule_first_pairing.assert_called_once_with(owner["id"])

        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-second")
        resp = await _confirm(client, api_auth_headers, minted["request_id"])
        assert resp.status_code == 200, resp.text
        assert (await db.get_user_by_id(owner["id"]))["first_paired_at"] == marker
        uploader.schedule_first_pairing.assert_called_once()

    async def test_confirm_broadcasts_the_account_event_once(
        self, client, dc_app, api_auth_headers
    ):
        dc_app.state.ads_uploader = MagicMock()
        owner = await dc_app.state.db.get_user_by_email("viewer@test.local")
        listener = _make_user_client(owner["id"])
        dc_app.state.routing.register_user_client(owner["id"], listener)

        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-first")
        resp = await _confirm(client, api_auth_headers, minted["request_id"])
        assert resp.status_code == 200, resp.text
        assert json.loads(listener.send_queue.get_nowait()) == {
            "type": "account.first_agent_paired",
            "payload": {"agent_id": resp.json()["agent_id"]},
        }

        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-second")
        resp = await _confirm(client, api_auth_headers, minted["request_id"])
        assert resp.status_code == 200, resp.text
        assert listener.send_queue.empty()

    async def test_broadcast_failure_does_not_reopen_the_code(
        self, client, dc_app, api_auth_headers, monkeypatch
    ):
        uploader = MagicMock()
        dc_app.state.ads_uploader = uploader
        monkeypatch.setattr(
            "central_server.pairing.router.broadcast_user_event",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        minted = await _mint(client, api_auth_headers)
        await _redeem_and_accept(client, minted["code"], "redeemer-first")
        resp = await _confirm(client, api_auth_headers, minted["request_id"])

        assert resp.status_code == 200, resp.text
        uploader.schedule_first_pairing.assert_called_once()
        again = await _confirm(client, api_auth_headers, minted["request_id"])
        assert again.status_code == 410, again.text
