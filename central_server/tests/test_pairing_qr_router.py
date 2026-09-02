"""End-to-end tests for ``POST /api/pairing/initiate`` and
``POST /api/pairing/claim``.

The router uses three pieces of app state that the default
``conftest.py`` ``app`` fixture does not configure:

* ``app.state.config`` must carry the manifest signing keypair for the
  503 fail-closed pattern; we substitute a per-test config.
* ``app.state.pairing_ws_registry`` is a ``PairingWsRegistry`` —
  required because the claim handler dispatches the signed push into it.

The WS endpoint itself (``WS /ws/pairing/{nonce}``) is exercised end-to-
end in the integration suite; here we focus on the REST flow's
correctness invariants (state machine, replay, expiry, fp mismatch,
re-pair branching).
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from central_server.config import CentralServerConfig
from central_server.pairing.qr_signer import compute_trust_key_fp, hash_pairing_nonce
from central_server.pairing.ws_handlers import PairingWsRegistry

_CLAIM_DEV_UUID = "11111111-1111-4111-8111-111111111111"
_NEW_DEV_UUID = "22222222-2222-4222-8222-222222222222"
_SHARED_DEV_UUID = "33333333-3333-4333-8333-333333333333"


def _ed25519_keypair_b64() -> tuple[str, str]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    return (
        base64.b64encode(private.private_bytes_raw()).decode(),
        base64.b64encode(public.public_bytes_raw()).decode(),
    )


def _override_config(app, **changes) -> None:
    """Replace app.state.config with a copy carrying the given overrides.

    Mirrors the pattern in ``test_trust_key_endpoint.py``: the
    ``CentralServerConfig`` dataclass is frozen, so we rebuild it by
    splatting ``__dict__`` and applying changes.
    """
    base = app.state.config.__dict__
    app.state.config = CentralServerConfig(**{**base, **changes})


@pytest.fixture
async def qr_app(app):
    """Wrap the conftest ``app`` with QR-pairing-specific state."""
    priv_b64, pub_b64 = _ed25519_keypair_b64()
    _override_config(
        app,
        manifest_signing_public_key_b64=pub_b64,
        manifest_signing_private_key_b64=priv_b64,
        public_base_url="https://relay.test",
    )
    app.state.pairing_ws_registry = PairingWsRegistry()
    # Stash the pub/priv so tests can verify or sign payloads.
    app.state._test_priv_b64 = priv_b64
    app.state._test_pub_b64 = pub_b64
    yield app


class TestInitiate:
    async def test_returns_payload_when_keys_configured(
        self, client, qr_app
    ):
        resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "alpha", "os": "Linux"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "nonce" in body
        assert "expires_at" in body
        assert "qr_payload" in body
        # The pairing WS URL is no longer returned by the server — the
        # agent derives it from RELAY_SERVER_URL + nonce. Pin the absence
        # so a regression that re-introduces a server-side ws_url field
        # gets caught here.
        assert "ws_url" not in body
        # trust_key_fp matches the configured public key fingerprint.
        assert body["trust_key_fp"] == compute_trust_key_fp(
            qr_app.state._test_pub_b64
        )
        # The QR payload format is pinned to MOBA1|relay|nonce|fp|exp.
        parts = body["qr_payload"].split("|")
        assert parts[0] == "MOBA1"
        assert len(parts) == 5
        assert parts[2] == body["nonce"]
        assert parts[3] == body["trust_key_fp"]
        # exp_unix is integer-parseable.
        assert int(parts[4]) > 0

    async def test_returns_503_when_private_key_missing(
        self, client, qr_app
    ):
        _override_config(qr_app, manifest_signing_private_key_b64=None)

        resp = await client.post(
            "/api/pairing/initiate", json={"hostname": "alpha"},
        )

        assert resp.status_code == 503
        assert "MANIFEST_SIGNING" in resp.json()["detail"]

    async def test_returns_503_when_public_key_missing(
        self, client, qr_app
    ):
        _override_config(qr_app, manifest_signing_public_key_b64=None)

        resp = await client.post(
            "/api/pairing/initiate", json={"hostname": "alpha"},
        )

        assert resp.status_code == 503

    async def test_metadata_persisted_for_claim_handler(
        self, client, qr_app
    ):
        # The claim handler reads agent_metadata.current_agent_id off the
        # row to distinguish first-pair from re-pair.
        resp = await client.post(
            "/api/pairing/initiate",
            json={
                "hostname": "alpha",
                "os": "Linux",
                "current_agent_id": "agent-old-id",
            },
        )
        assert resp.status_code == 200
        nonce = resp.json()["nonce"]

        meta = await qr_app.state.db.lookup_pairing_token_metadata(nonce)
        assert meta is not None
        decoded = json.loads(meta["agent_metadata"])
        assert decoded["current_agent_id"] == "agent-old-id"
        assert decoded["hostname"] == "alpha"

    async def test_relay_url_persisted_for_recovery_handler(
        self, client, qr_app
    ):
        # Regression guard: ``pairing_tokens.relay_url`` is written at
        # initiate time and read back by both the claim handler and the
        # WS recovery handler. Without this column, the recovery branch
        # recomputed relay_url from ``websocket.url.scheme`` (wss → https)
        # while the initiate branch recomputed it from ``request.base_url``
        # (often http behind a reverse proxy that doesn't forward
        # X-Forwarded-Proto to uvicorn). The agent's strict-equality
        # check then rejected its own credentials on recovery — the
        # exact failure mode of the "RELAY_AGENT_* stays empty after
        # pairing" bug.
        resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "alpha", "os": "Linux"},
        )
        assert resp.status_code == 200
        nonce = resp.json()["nonce"]

        meta = await qr_app.state.db.lookup_pairing_token_metadata(nonce)
        assert meta is not None
        # ``public_base_url`` set in the qr_app fixture: "https://relay.test".
        assert meta["relay_url"] == "https://relay.test", (
            f"expected relay_url to be persisted at initiate; got "
            f"{meta.get('relay_url')!r}. The QR payload also carries it: "
            f"{resp.json()['qr_payload']!r}"
        )


class TestClaim:
    async def _initiate_nonce(self, client) -> str:
        resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "alpha", "os": "Linux"},
        )
        assert resp.status_code == 200
        return resp.json()["nonce"]

    async def _initiate_repair_nonce(
        self, client, current_agent_id: str
    ) -> str:
        resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "alpha", "current_agent_id": current_agent_id},
        )
        assert resp.status_code == 200
        return resp.json()["nonce"]

    async def test_first_pair_creates_agent(
        self, client, qr_app, api_auth_headers
    ):
        nonce = await self._initiate_nonce(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
                "friendly_name": "lab-host",
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["agent_friendly_name"] == "lab-host"
        # New agent row exists in the DB and is owned by the viewer.
        agent = await qr_app.state.db.get_agent(body["agent_id"])
        assert agent is not None
        owner = await qr_app.state.db.get_user_by_email("viewer@test.local")
        assert agent["owner_user_id"] == owner["id"]

    async def test_replay_returns_410(
        self, client, qr_app, api_auth_headers
    ):
        nonce = await self._initiate_nonce(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)
        body = {
            "nonce": nonce,
            "trust_key_fp": fp,
            "mobile_device_id": _CLAIM_DEV_UUID,
        }
        first = await client.post(
            "/api/pairing/claim", json=body, headers=api_auth_headers,
        )
        assert first.status_code == 200

        second = await client.post(
            "/api/pairing/claim", json=body, headers=api_auth_headers,
        )

        assert second.status_code == 410

    async def test_unknown_nonce_returns_410(
        self, client, qr_app, api_auth_headers
    ):
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": "totally-unknown-nonce",
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 410

    async def test_expired_nonce_returns_410(
        self, client, qr_app, api_auth_headers
    ):
        from datetime import datetime, timezone

        from central_server.db.database import _hash_token

        nonce = await self._initiate_nonce(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)
        # Force-expire the row.
        past = datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat()
        await qr_app.state.db.conn.execute(
            "UPDATE pairing_tokens SET expires_at = ? WHERE token_hash = ?",
            (past, _hash_token(nonce)),
        )
        await qr_app.state.db.conn.commit()

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 410

    async def test_trust_key_fp_mismatch_returns_400(
        self, client, qr_app, api_auth_headers
    ):
        nonce = await self._initiate_nonce(client)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": "0" * 64,  # well-formed but wrong
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 400
        assert "trust_key_fp" in resp.json()["detail"]

    async def test_503_when_signing_keys_missing(
        self, client, qr_app, api_auth_headers
    ):
        nonce = await self._initiate_nonce(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)
        _override_config(qr_app, manifest_signing_private_key_b64=None)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 503

    async def test_response_does_not_leak_token(
        self, client, qr_app, api_auth_headers
    ):
        # A non-leak invariant: PairingClaimResponse intentionally has no
        # token field. The token reaches the agent only over the
        # pairing WS push.
        nonce = await self._initiate_nonce(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "agent_token" not in body
        assert "token" not in body

    async def test_response_carries_ws_delivered_flag(
        self, client, qr_app, api_auth_headers
    ):
        # The mobile uses ``ws_delivered`` to refine the timeout-error copy
        # when the just-paired agent never comes online. With no WS waiter
        # registered for this nonce (no agent is listening on the pairing
        # WS in this test setup), the dispatch attempt returns False and
        # the credentials are staged in claimed_token_raw for the agent's
        # next reconnect to recover. The response must surface this state.
        nonce = await self._initiate_nonce(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "ws_delivered" in body, (
            "PairingClaimResponse must include the ws_delivered field — "
            "the mobile uses it to distinguish 'dispatched to agent WS' "
            "from 'staged for agent recovery' when building the "
            "pairing-failed diagnostic message"
        )
        # No waiter registered → dispatch returns False → ws_delivered=False.
        assert body["ws_delivered"] is False


class TestClaimRepair:
    async def test_same_owner_rotates_token(
        self, client, qr_app, api_auth_headers, registered_agent
    ):
        # registered_agent is owned by the viewer per conftest.
        agent_id, original_raw = registered_agent
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        init_resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "h", "current_agent_id": agent_id},
        )
        nonce = init_resp.json()["nonce"]

        claim_resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert claim_resp.status_code == 200
        assert claim_resp.json()["agent_id"] == agent_id
        # The original token must NOT verify after rotation.
        assert (
            await qr_app.state.db.verify_agent_token(agent_id, original_raw)
            is False
        )

    async def test_different_owner_claim_is_rejected(
        self, client, qr_app, api_auth_headers, registered_agent
    ):
        # registered_agent is owned by viewer@test.local. A *different*
        # user scanning the re-pair QR must NOT be able to take the agent
        # over — knowing the agent_id is not authority. The claim is
        # rejected with 409 and ownership + the prior owner's tokens are
        # left untouched (no transfer, no cascade).
        agent_id, _ = registered_agent

        prior_owner = await qr_app.state.db.get_user_by_email(
            "viewer@test.local"
        )
        await qr_app.state.db.store_refresh_token(
            raw_token="prior-refresh-raw",
            mobile_device_id="prior-dev",
            agent_id=agent_id,
            expire_minutes=60,
        )

        # Create a NEW user account and authenticate as them.
        await qr_app.state.auth_service.register_user(
            "transfer@test.local", "transferpw123"
        )
        new_user = await qr_app.state.db.get_user_by_email(
            "transfer@test.local"
        )
        _uid, _role, new_token, _scheduled = await qr_app.state.auth_service.login_user(
            "transfer@test.local", "transferpw123"
        )
        new_headers = {"Authorization": f"Bearer {new_token}"}
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        # Initiate carrying the victim's agent_id (anonymous, forgeable),
        # then claim as the different user.
        init_resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "h", "current_agent_id": agent_id},
        )
        nonce = init_resp.json()["nonce"]

        claim_resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _NEW_DEV_UUID,
            },
            headers=new_headers,
        )

        assert claim_resp.status_code == 409
        # Ownership did NOT move.
        assert (
            await qr_app.state.db.get_agent_owner(agent_id)
            == prior_owner["id"]
        )
        # No cascade ran: the prior owner's refresh token still validates.
        assert (
            await qr_app.state.db.validate_and_consume_refresh(
                "prior-refresh-raw"
            )
            is not None
        )
        assert prior_owner["id"] != new_user["id"]


class TestClaimWsDispatch:
    async def test_claim_dispatches_signed_payload_when_waiter_registered(
        self, client, qr_app, api_auth_headers
    ):
        # Pre-register a waiter, then run the claim. The signed payload
        # must land on the queue and verify under the configured public key.
        nonce = await TestClaim._initiate_nonce(self, client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)
        queue = await qr_app.state.pairing_ws_registry.register_waiter(
            hash_pairing_nonce(nonce)
        )

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200
        # When a waiter is registered the dispatch succeeds and the
        # response must surface that — the mobile uses ``ws_delivered=True``
        # to refine its diagnostic copy (no need to mention the "credentials
        # were staged for recovery" remediation when the agent already
        # received the push at claim time).
        assert resp.json()["ws_delivered"] is True
        # Payload is queued.
        raw = await queue.get()
        push = json.loads(raw.decode("utf-8"))
        assert push["type"] == "claim_completed"
        assert push["agent_id"] == resp.json()["agent_id"]
        assert push["agent_token"]
        assert push["signature_b64"]
        # Verify the signature with the configured public key.
        pub = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(qr_app.state._test_pub_b64)
        )
        unsigned = {k: v for k, v in push.items() if k != "signature_b64"}
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        # Does not raise = signature valid.
        pub.verify(base64.b64decode(push["signature_b64"]), canonical)

    async def test_claim_succeeds_even_when_no_waiter(
        self, client, qr_app, api_auth_headers
    ):
        # No waiter registered → dispatch returns False but the claim
        # itself still returns 200 (the agent will retry on next QR).
        nonce = await TestClaim._initiate_nonce(self, client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200

    async def test_claim_relay_url_matches_persisted_value(
        self, client, qr_app, api_auth_headers
    ):
        # The relay_url echoed in the dispatch push MUST equal the
        # value persisted at initiate time. Before this fix the claim
        # handler recomputed it from ``request.base_url`` per request,
        # which can disagree with what initiate stored when the proxy
        # configuration differs between requests (or doesn't forward
        # X-Forwarded-Proto consistently). After the fix the column
        # is the single source of truth across initiate / claim /
        # WS recovery.
        nonce = await TestClaim._initiate_nonce(self, client)
        persisted = await qr_app.state.db.lookup_pairing_token_metadata(nonce)
        assert persisted is not None
        persisted_relay = persisted["relay_url"]
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        queue = await qr_app.state.pairing_ws_registry.register_waiter(
            hash_pairing_nonce(nonce)
        )
        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )
        assert resp.status_code == 200

        raw = await queue.get()
        push = json.loads(raw.decode("utf-8"))
        assert push["relay_url"] == persisted_relay, (
            f"dispatch path's relay_url ({push['relay_url']!r}) "
            f"diverged from the persisted value ({persisted_relay!r}); "
            f"the WS recovery branch reads the same column, so any "
            f"divergence here means dispatch and recovery deliver "
            f"different payloads to the agent — the original "
            f"strict-equality failure mode."
        )

    async def test_recovery_uses_persisted_relay_url(
        self, client, qr_app, api_auth_headers
    ):
        # WS-recovery branch regression: when ``read_and_clear_claimed_credentials``
        # returns a row that carries a persisted ``relay_url``, the
        # router MUST echo that value (not recompute it from the WS
        # request context).
        nonce = await TestClaim._initiate_nonce(self, client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)
        # Claim without registering a waiter so ``claimed_token_raw``
        # stays in the row for the recovery path to read.
        claim_resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=api_auth_headers,
        )
        assert claim_resp.status_code == 200

        consumed = await qr_app.state.db.read_and_clear_claimed_credentials(
            nonce
        )
        assert consumed is not None
        # Pin: the row carries the persisted relay_url. Without this
        # the recovery branch falls through to
        # ``_resolve_public_base_url_from_app(websocket)`` which is
        # what caused the cross-path mismatch.
        assert consumed["relay_url"] == "https://relay.test", (
            f"expected persisted relay_url to flow through "
            f"read_and_clear_claimed_credentials; got "
            f"{consumed.get('relay_url')!r}"
        )


class TestClaimDeviceReassignment:
    """End-to-end coverage of the last-login-wins ``mobile_device_id``
    handover through ``POST /api/pairing/claim``.

    The DB-layer handover is unit-tested in ``test_database.py``; these
    tests pin the router path (``upsert_mobile_device`` always succeeds,
    the claim proceeds to completion) at the endpoint, not just the
    helper. Both use a first-pair nonce (no ``current_agent_id``) so the
    agent-ownership guard — which still 409s and is covered by
    ``test_different_owner_claim_is_rejected`` — stays out of the way.
    """

    async def _other_user_id(self, qr_app) -> str:
        await qr_app.state.auth_service.register_user(
            "prior@test.local", "priorpw123"
        )
        prior = await qr_app.state.db.get_user_by_email("prior@test.local")
        return prior["id"]

    async def _initiate_first_pair(self, client) -> str:
        resp = await client.post(
            "/api/pairing/initiate",
            json={"hostname": "alpha", "os": "Linux"},
        )
        assert resp.status_code == 200
        return resp.json()["nonce"]

    async def test_claim_reassigns_idle_cross_tenant_device(
        self, client, qr_app, api_auth_headers, viewer_user
    ):
        # A prior user owns the device but holds no live refresh token
        # (idle — its agent was deleted / it was revoked). The viewer
        # first-pairs the *same* physical device id: a clean handover
        # with nothing to revoke. 200, and the device moves to the viewer.
        prior_id = await self._other_user_id(qr_app)
        await qr_app.state.db.upsert_mobile_device(
            device_id=_SHARED_DEV_UUID, user_id=prior_id, name="prior phone"
        )
        await qr_app.state.db.revoke_mobile_device(_SHARED_DEV_UUID, prior_id)

        nonce = await self._initiate_first_pair(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _SHARED_DEV_UUID,
                "mobile_device_name": "viewer phone",
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200, resp.text
        # The device left the prior owner and now belongs to the viewer.
        assert await qr_app.state.db.list_user_devices(prior_id) == []
        viewer_devices = await qr_app.state.db.list_user_devices(
            viewer_user["id"]
        )
        assert [d["device_id"] for d in viewer_devices] == [_SHARED_DEV_UUID]
        # A real agent was created for the claimer.
        agent = await qr_app.state.db.get_agent(resp.json()["agent_id"])
        assert agent is not None
        assert agent["owner_user_id"] == viewer_user["id"]

    async def test_claim_hands_over_device_with_live_session(
        self, client, qr_app, api_auth_headers, viewer_user
    ):
        # Last-login-wins at the HTTP boundary: even a device pinned to a
        # prior user by a live refresh token is handed over on claim. The
        # claim succeeds end-to-end — the device moves, the prior owner's
        # refresh token dies, the nonce is consumed, an agent is created.
        prior_id = await self._other_user_id(qr_app)
        prior_agent_id, _ = await qr_app.state.db.register_agent(
            "prior-agent", prior_id
        )
        await qr_app.state.db.upsert_mobile_device(
            device_id=_SHARED_DEV_UUID, user_id=prior_id, name="prior phone"
        )
        await qr_app.state.db.store_refresh_token(
            raw_token="prior-live-refresh",
            mobile_device_id=_SHARED_DEV_UUID,
            agent_id=prior_agent_id,
            expire_minutes=60,
        )

        nonce = await self._initiate_first_pair(client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": _SHARED_DEV_UUID,
                "mobile_device_name": "viewer phone",
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200, resp.text
        # The device moved to the viewer and left the prior owner.
        assert await qr_app.state.db.list_user_devices(prior_id) == []
        viewer_devices = await qr_app.state.db.list_user_devices(
            viewer_user["id"]
        )
        assert [d["device_id"] for d in viewer_devices] == [_SHARED_DEV_UUID]
        # The prior owner's live session died with the handover.
        assert await qr_app.state.db.validate_and_consume_refresh(
            "prior-live-refresh"
        ) is None
        # The pairing ran to completion: nonce consumed, agent created
        # for the viewer.
        meta = await qr_app.state.db.lookup_pairing_token_metadata(nonce)
        assert meta is not None
        assert meta["consumed_at"] is not None
        agent = await qr_app.state.db.get_agent(resp.json()["agent_id"])
        assert agent is not None
        assert agent["owner_user_id"] == viewer_user["id"]


class TestFirstPairingConversion:
    """``_complete_pairing`` stamps ``users.first_paired_at`` exactly once per
    account and hands the id to the ads uploader — independent of the trust
    pin, which resets when an account drops to zero agents."""

    async def _claim(self, client, qr_app, headers):
        resp = await client.post(
            "/api/pairing/initiate", json={"hostname": "alpha", "os": "Linux"}
        )
        assert resp.status_code == 200
        return await client.post(
            "/api/pairing/claim",
            json={
                "nonce": resp.json()["nonce"],
                "trust_key_fp": compute_trust_key_fp(qr_app.state._test_pub_b64),
                "mobile_device_id": _CLAIM_DEV_UUID,
            },
            headers=headers,
        )

    async def test_first_pair_marks_the_account_and_schedules_the_conversion(
        self, client, qr_app, api_auth_headers
    ):
        uploader = MagicMock()
        qr_app.state.ads_uploader = uploader
        db = qr_app.state.db
        owner = await db.get_user_by_email("viewer@test.local")

        first = await self._claim(client, qr_app, api_auth_headers)
        assert first.status_code == 200, first.text
        marker = (await db.get_user_by_id(owner["id"]))["first_paired_at"]
        assert marker is not None
        uploader.schedule_first_pairing.assert_called_once_with(owner["id"])

        second = await self._claim(client, qr_app, api_auth_headers)
        assert second.status_code == 200, second.text
        assert (await db.get_user_by_id(owner["id"]))["first_paired_at"] == marker
        uploader.schedule_first_pairing.assert_called_once()

    async def test_pairing_again_after_removing_every_agent_is_not_a_first_pairing(
        self, client, qr_app, api_auth_headers
    ):
        uploader = MagicMock()
        qr_app.state.ads_uploader = uploader
        db = qr_app.state.db
        owner = await db.get_user_by_email("viewer@test.local")

        first = await self._claim(client, qr_app, api_auth_headers)
        assert first.status_code == 200, first.text
        marker = (await db.get_user_by_id(owner["id"]))["first_paired_at"]
        await db.delete_agent(first.json()["agent_id"])
        assert await db.count_agents_for_owner(owner["id"]) == 0

        again = await self._claim(client, qr_app, api_auth_headers)
        assert again.status_code == 200, again.text
        row = await db.get_user_by_id(owner["id"])
        # The pin was reset and re-taken; the marker never moved.
        assert row["trust_key_fp"] is not None
        assert row["first_paired_at"] == marker
        uploader.schedule_first_pairing.assert_called_once()

    async def test_scheduling_failure_does_not_fail_the_pairing(
        self, client, qr_app, api_auth_headers
    ):
        uploader = MagicMock()
        uploader.schedule_first_pairing.side_effect = RuntimeError("boom")
        qr_app.state.ads_uploader = uploader

        resp = await self._claim(client, qr_app, api_auth_headers)

        assert resp.status_code == 200, resp.text
        uploader.schedule_first_pairing.assert_called_once()
