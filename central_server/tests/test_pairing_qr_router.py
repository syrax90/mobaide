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

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from central_server.config import CentralServerConfig
from central_server.pairing.qr_signer import compute_trust_key_fp
from central_server.pairing.ws_handlers import PairingWsRegistry


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
                "mobile_device_id": "dev-1",
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
            "mobile_device_id": "dev-1",
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
                "mobile_device_id": "dev-1",
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
                "mobile_device_id": "dev-1",
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
                "mobile_device_id": "dev-1",
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
                "mobile_device_id": "dev-1",
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
                "mobile_device_id": "dev-1",
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert "agent_token" not in body
        assert "token" not in body


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
                "mobile_device_id": "dev-1",
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

    async def test_different_owner_transfers_and_cascades(
        self, client, qr_app, api_auth_headers, registered_agent
    ):
        # registered_agent is owned by viewer@test.local. Re-pair scan is
        # done by a *different* user; the agent must transfer ownership
        # AND drop the prior owner's mobile_refresh_tokens / pairings.
        agent_id, _ = registered_agent

        # Arrange: prior owner has a pairing + refresh token tied to this agent.
        prior_owner = await qr_app.state.db.get_user_by_email(
            "viewer@test.local"
        )
        await qr_app.state.db.create_pairing(
            agent_id, mobile_device_id="prior-dev", device_name="OldPhone",
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
        _uid, _role, new_token = await qr_app.state.auth_service.login_user(
            "transfer@test.local", "transferpw123"
        )
        new_headers = {"Authorization": f"Bearer {new_token}"}
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)

        # Initiate as the agent (anonymous), then claim as the new user.
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
                "mobile_device_id": "new-dev",
            },
            headers=new_headers,
        )

        assert claim_resp.status_code == 200
        # Ownership moved to the new user.
        assert (
            await qr_app.state.db.get_agent_owner(agent_id) == new_user["id"]
        )
        # Cascade: prior owner's pairings dropped.
        pairings = await qr_app.state.db.list_pairings_for_agent(agent_id)
        assert pairings == []
        # Cascade: prior refresh token cannot be consumed any more.
        assert (
            await qr_app.state.db.validate_and_consume_refresh(
                "prior-refresh-raw"
            )
            is None
        )
        # Sanity: prior_owner is the old viewer
        assert prior_owner["id"] != new_user["id"]


class TestClaimWsDispatch:
    async def test_claim_dispatches_signed_payload_when_waiter_registered(
        self, client, qr_app, api_auth_headers
    ):
        # Pre-register a waiter, then run the claim. The signed payload
        # must land on the queue and verify under the configured public key.
        nonce = await TestClaim._initiate_nonce(self, client)
        fp = compute_trust_key_fp(qr_app.state._test_pub_b64)
        queue = await qr_app.state.pairing_ws_registry.register_waiter(nonce)

        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": "dev-1",
            },
            headers=api_auth_headers,
        )

        assert resp.status_code == 200
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
                "mobile_device_id": "dev-1",
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

        queue = await qr_app.state.pairing_ws_registry.register_waiter(nonce)
        resp = await client.post(
            "/api/pairing/claim",
            json={
                "nonce": nonce,
                "trust_key_fp": fp,
                "mobile_device_id": "dev-1",
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
                "mobile_device_id": "dev-1",
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
