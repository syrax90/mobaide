"""The three account-creating auth paths store the web sign-up's click
attribution on the new row — and nowhere else."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from central_server.ads.consent import network_fingerprint, sign_attribution
from central_server.auth.models import AdAttribution
from central_server.auth.service import AuthService
from tests.conftest import TEST_JWT_SECRET

GCLID = "Cj0KCQjw_abc-123"
CLICKED_AT = 1_756_800_000
CLICKED_AT_ISO = datetime.fromtimestamp(CLICKED_AT, tz=timezone.utc).isoformat()
# The ASGI transport's default client address — the network the banner was
# accepted from, as far as the server can tell.
HOME_NET = network_fingerprint(TEST_JWT_SECRET, "127.0.0.1")


def _signed(click_id, click_id_kind, clicked_at, network=HOME_NET) -> dict:
    """An object the way the SPA forwards it: the server-minted fields plus
    the signature that proves the banner on mobaide.com issued them."""
    return {
        "click_id": click_id,
        "click_id_kind": click_id_kind,
        "clicked_at": clicked_at,
        "network": network,
        "signature": sign_attribution(
            TEST_JWT_SECRET,
            click_id=click_id,
            click_id_kind=click_id_kind,
            clicked_at=clicked_at,
            network=network,
        ),
    }


ATTRIBUTION = _signed(GCLID, "gclid", CLICKED_AT)
CONSENT_ONLY = _signed(None, None, CLICKED_AT)


async def _register(client, email: str, **extra):
    return await client.post(
        "/api/auth/register",
        json={"email": email, "password": "strongpass123", **extra},
    )


class TestRegisterEndpoint:
    async def test_register_stores_ad_attribution_on_new_account(self, client, app):
        resp = await _register(client, "ads@example.com", ad_attribution=ATTRIBUTION)
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("ads@example.com")
        assert row["ad_click_id"] == GCLID
        assert row["ad_click_kind"] == "gclid"
        assert row["ad_click_at"] == CLICKED_AT_ISO
        assert row["ads_consent"] == 1
        assert row["first_paired_at"] is None

    async def test_register_consent_only_object_sets_consent_without_click(
        self, client, app
    ):
        resp = await _register(client, "consent@example.com", ad_attribution=CONSENT_ONLY)
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("consent@example.com")
        assert row["ads_consent"] == 1
        assert (row["ad_click_id"], row["ad_click_kind"]) == (None, None)
        assert row["ad_click_at"] == CLICKED_AT_ISO

    @pytest.mark.parametrize(
        "forged",
        [
            {},
            {"clicked_at": CLICKED_AT},
            {**ATTRIBUTION, "signature": None},
            {**ATTRIBUTION, "signature": "not-the-real-one"},
            {**ATTRIBUTION, "click_id": "Cj0KCQjw_abc-124"},
            {**ATTRIBUTION, "click_id_kind": "wbraid"},
            {**ATTRIBUTION, "clicked_at": CLICKED_AT + 1},
            {**ATTRIBUTION, "network": network_fingerprint(TEST_JWT_SECRET, "10.0.0.9")},
            {**ATTRIBUTION, "network": None},
            {**CONSENT_ONLY, "signature": ATTRIBUTION["signature"]},
        ],
    )
    async def test_register_ignores_unsigned_or_tampered_attribution(
        self, client, app, forged
    ):
        """A planted domain cookie must neither count as consent nor cost
        anyone the account: the sign-up succeeds, nothing is stored."""
        resp = await _register(client, "forged@example.com", ad_attribution=forged)
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("forged@example.com")
        assert row["ads_consent"] == 0
        assert row["ad_click_id"] is None

    async def test_consent_minted_elsewhere_keeps_the_click_but_not_the_consent(
        self, app
    ):
        """A validly signed value that reaches sign-up from a different
        network than it was minted on — a visitor whose address changed, or
        an attacker's client planting its own cookie for a victim — keeps
        the click id (no personal data) but never the hashed-email consent."""
        elsewhere = AsyncClient(
            transport=ASGITransport(app=app, client=("10.0.0.9", 4242)),
            base_url="https://test",
        )
        async with elsewhere:
            with_click = await _register(elsewhere, "moved@example.com", ad_attribution=ATTRIBUTION)
            assert with_click.status_code == 200, with_click.text
            consent_only = await _register(elsewhere, "planted@example.com", ad_attribution=CONSENT_ONLY)
            assert consent_only.status_code == 200, consent_only.text
        moved = await app.state.db.get_user_by_email("moved@example.com")
        assert moved["ad_click_id"] == GCLID and moved["ads_consent"] == 0
        planted = await app.state.db.get_user_by_email("planted@example.com")
        assert planted["ad_click_id"] is None and planted["ads_consent"] == 0

    async def test_register_without_ad_attribution_leaves_consent_off(self, client, app):
        resp = await _register(client, "plain@example.com")
        assert resp.status_code == 200
        row = await app.state.db.get_user_by_email("plain@example.com")
        assert row["ads_consent"] == 0 and row["ad_click_id"] is None

    @pytest.mark.parametrize(
        "bad",
        [
            {"click_id": GCLID},
            {"click_id_kind": "gclid"},
            {"click_id": "has space", "click_id_kind": "gclid"},
            {"click_id": "a" * 201, "click_id_kind": "gclid"},
            {"click_id": GCLID, "click_id_kind": "fbclid"},
            {"click_id": GCLID, "click_id_kind": "gclid", "clicked_at": -1},
            {"click_id": GCLID, "click_id_kind": "gclid", "clicked_at": 4_102_444_801},
        ],
    )
    async def test_register_rejects_malformed_attribution(self, client, app, bad):
        resp = await _register(client, "bad@example.com", ad_attribution=bad)
        assert resp.status_code == 422, resp.text
        assert await app.state.db.get_user_by_email("bad@example.com") is None

    async def test_register_accepts_odd_but_parseable_clicked_at(self, client, app):
        """The timestamp comes from the visitor's clock; skew must not cost
        them the account."""
        odd = _signed(GCLID, "gclid", 4_102_444_800)
        resp = await _register(client, "skew@example.com", ad_attribution=odd)
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("skew@example.com")
        assert row["ad_click_at"].startswith("2100-01-01")

    async def test_login_body_with_ad_attribution_is_ignored(self, client, app):
        await _register(client, "login@example.com")
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": "login@example.com",
                "password": "strongpass123",
                "ad_attribution": ATTRIBUTION,
            },
        )
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("login@example.com")
        assert row["ads_consent"] == 0


def _patch_google(monkeypatch, *, sub: str, email: str) -> None:
    async def _verify(self, token_str):
        return {"sub": sub, "email": email, "email_verified": True, "iat": int(time.time())}

    monkeypatch.setattr(AuthService, "_verify_google_id_token", _verify)
    monkeypatch.setattr(
        AuthService, "_verify_google_token_claims", lambda self, *a, **k: None
    )


def _patch_apple(monkeypatch, *, sub: str, email: str) -> None:
    async def _verify(self, token_str):
        return {"sub": sub, "email": email, "email_verified": True, "nonce": "n" * 43}

    monkeypatch.setattr(AuthService, "_verify_apple_id_token", _verify)
    monkeypatch.setattr(
        AuthService, "_verify_apple_token_claims", lambda self, *a, **k: None
    )


class TestSocialSignIn:
    async def test_google_new_user_stores_ad_attribution(self, client, app, monkeypatch):
        _patch_google(monkeypatch, sub="g-new", email="gnew@example.com")
        resp = await client.post(
            "/api/auth/google", json={"id_token": "tok", "ad_attribution": ATTRIBUTION}
        )
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("gnew@example.com")
        assert row["ad_click_id"] == GCLID and row["ads_consent"] == 1

    async def test_google_existing_user_ignores_ad_attribution(self, app, monkeypatch):
        _patch_google(monkeypatch, sub="g-old", email="gold@example.com")
        svc = app.state.auth_service
        user_id, _, _, is_new = await svc.google_authenticate("tok")
        assert is_new
        user_id2, _, _, is_new2 = await svc.google_authenticate(
            "tok", ad_attribution=AdAttribution(**ATTRIBUTION)
        )
        assert (user_id2, is_new2) == (user_id, False)
        row = await app.state.db.get_user_by_id(user_id)
        assert row["ads_consent"] == 0 and row["ad_click_id"] is None

    async def test_apple_new_user_stores_ad_attribution(self, client, app, monkeypatch):
        _patch_apple(monkeypatch, sub="a-new", email="anew@example.com")
        resp = await client.post(
            "/api/auth/apple",
            json={"id_token": "tok", "nonce": "n" * 43, "ad_attribution": ATTRIBUTION},
        )
        assert resp.status_code == 200, resp.text
        row = await app.state.db.get_user_by_email("anew@example.com")
        assert row["ad_click_id"] == GCLID and row["ads_consent"] == 1

    async def test_apple_existing_user_ignores_ad_attribution(self, app, monkeypatch):
        _patch_apple(monkeypatch, sub="a-old", email="aold@example.com")
        svc = app.state.auth_service
        user_id, _, _, is_new = await svc.apple_authenticate("tok", nonce="n" * 43)
        assert is_new
        user_id2, _, _, is_new2 = await svc.apple_authenticate(
            "tok", nonce="n" * 43, ad_attribution=AdAttribution(**ATTRIBUTION)
        )
        assert (user_id2, is_new2) == (user_id, False)
        row = await app.state.db.get_user_by_id(user_id)
        assert row["ads_consent"] == 0 and row["ad_click_id"] is None
