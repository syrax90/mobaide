"""Cookie-credentialed ``/refresh`` and the two sign-out endpoints.

The cookie path is the only one a third-party page could ever ride, so it is
gated on a custom header the browser cannot attach cross-origin; the body path
stays exactly as the mobile clients found it.
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD

REFRESH = "__Host-mobaide_refresh"
PROOF = {"X-Mobaide-Session": "1"}


def _named(cookies: list[str], name: str) -> str:
    matches = [c for c in cookies if c.startswith(f"{name}=")]
    assert len(matches) == 1, f"expected exactly one {name}, got {cookies}"
    return matches[0]


def _value(cookie: str) -> str:
    return cookie.split(";")[0].split("=", 1)[1]


async def _login_with_cookies(client, *, transport: str) -> dict:
    resp = await client.post(
        "/api/auth/login",
        json={
            "email": TEST_VIEWER_EMAIL,
            "password": TEST_VIEWER_PASSWORD,
            "refreshable": True,
            "session_transport": transport,
        },
    )
    assert resp.status_code == 200
    return {"body": resp.json(), "cookies": resp.headers.get_list("set-cookie")}


class TestCookieRefresh:
    async def test_rotates_off_the_cookie_with_no_body_at_all(
        self, client, viewer_user
    ) -> None:
        first = _value(_named((await _login_with_cookies(client, transport="cookie"))["cookies"], REFRESH))

        resp = await client.post("/api/auth/refresh", headers=PROOF)

        payload = resp.json()
        assert resp.status_code == 200
        assert payload["access_token"]
        assert payload["refresh_token"] is None
        assert payload["session_transport"] == "cookie"
        assert payload["user_id"] == viewer_user["id"]
        rotated = _value(_named(resp.headers.get_list("set-cookie"), REFRESH))
        assert rotated != first

    async def test_the_rotated_off_value_is_dead(self, client, viewer_user) -> None:
        del viewer_user
        stale = _value(_named((await _login_with_cookies(client, transport="cookie"))["cookies"], REFRESH))
        await client.post("/api/auth/refresh", headers=PROOF)

        replay = await client.post(
            "/api/auth/refresh", json={"refresh_token": stale}
        )

        assert replay.status_code == 401

    async def test_a_losing_rotation_never_touches_the_jar(
        self, client, viewer_user
    ) -> None:
        """One browser holds one shared cookie value, so a loser in a
        concurrent rotation must be incapable of clobbering the winner's jar —
        not with a fresh value, and not with a deletion."""
        del viewer_user
        stale = _value(_named((await _login_with_cookies(client, transport="cookie"))["cookies"], REFRESH))
        # Spend the jar's value out of band, exactly as a peer tab would.
        winner = await client.post(
            "/api/auth/refresh", json={"refresh_token": stale}
        )
        assert winner.status_code == 200

        resp = await client.post("/api/auth/refresh", headers=PROOF)

        assert resp.status_code == 401
        assert resp.headers.get_list("set-cookie") == []
        assert client.cookies.get(REFRESH) == stale


class TestOriginProof:
    async def test_the_cookie_path_without_the_header_is_403(
        self, client, viewer_user
    ) -> None:
        del viewer_user
        await _login_with_cookies(client, transport="cookie")

        resp = await client.post("/api/auth/refresh")

        assert resp.status_code == 403

    async def test_a_body_token_needs_no_header(self, client, viewer_user) -> None:
        """Native clients cannot be asked for custom headers, and a request
        that had to read its token out of storage cannot have been forged."""
        del viewer_user
        session = await _login_with_cookies(client, transport="body")

        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": session["body"]["refresh_token"]},
        )

        assert resp.status_code == 200

    @pytest.mark.parametrize("fetch_site", ["cross-site", "same-site"])
    async def test_a_foreign_fetch_site_is_rejected_despite_the_header(
        self, client, viewer_user, fetch_site
    ) -> None:
        """``SameSite=Strict`` is scoped to the registrable domain, so a
        sibling host's forged POST arrives ``same-site`` with the cookie on."""
        del viewer_user
        await _login_with_cookies(client, transport="cookie")

        resp = await client.post(
            "/api/auth/refresh",
            headers={**PROOF, "Sec-Fetch-Site": fetch_site},
        )

        assert resp.status_code == 403

    async def test_our_own_page_passes(self, client, viewer_user) -> None:
        del viewer_user
        await _login_with_cookies(client, transport="cookie")

        resp = await client.post(
            "/api/auth/refresh",
            headers={**PROOF, "Sec-Fetch-Site": "same-origin"},
        )

        assert resp.status_code == 200


class TestLogout:
    async def test_revokes_the_cookie_credential_and_clears_the_jar(
        self, client, viewer_user
    ) -> None:
        del viewer_user
        credential = _value(
            _named((await _login_with_cookies(client, transport="cookie"))["cookies"], REFRESH)
        )

        resp = await client.post("/api/auth/logout", headers=PROOF)

        assert resp.status_code == 204
        # The handler returns a Response, so FastAPI merges nothing for it —
        # the deletion has to be set on the object it returns.
        assert "Max-Age=0" in _named(resp.headers.get_list("set-cookie"), REFRESH)
        assert client.cookies.get(REFRESH) is None
        replay = await client.post(
            "/api/auth/refresh", json={"refresh_token": credential}
        )
        assert replay.status_code == 401

    async def test_without_the_origin_proof_header_is_403(self, client) -> None:
        resp = await client.post("/api/auth/logout")

        assert resp.status_code == 403

    async def test_is_idempotent_with_nothing_left_to_revoke(self, client) -> None:
        resp = await client.post("/api/auth/logout", headers=PROOF)

        assert resp.status_code == 204


class TestLogoutAll:
    async def test_revokes_a_second_browsers_session_too(
        self, client, viewer_user, api_auth_headers
    ) -> None:
        del viewer_user
        first = await _login_with_cookies(client, transport="body")
        second = await _login_with_cookies(client, transport="body")

        resp = await client.post("/api/auth/logout/all", headers=api_auth_headers)

        assert resp.status_code == 204
        for session in (first, second):
            replay = await client.post(
                "/api/auth/refresh",
                json={"refresh_token": session["body"]["refresh_token"]},
            )
            assert replay.status_code == 401

    async def test_requires_a_bearer(self, client) -> None:
        resp = await client.post("/api/auth/logout/all")

        assert resp.status_code == 401


class TestSupersededCredential:
    async def test_signing_in_again_retires_the_cookie_it_arrived_with(
        self, client, viewer_user
    ) -> None:
        """Re-login must not orphan the previous 30-day row.

        The new cookie overwrites the old one in the jar either way, so a row
        left live would be a credential nobody holds and nobody can revoke.
        """
        first = _value(
            _named((await _login_with_cookies(client, transport="cookie"))["cookies"], REFRESH)
        )
        second = _value(
            _named((await _login_with_cookies(client, transport="cookie"))["cookies"], REFRESH)
        )
        assert first != second

        replay = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": first},
        )
        assert replay.status_code == 401

        client.cookies.set(REFRESH, second)
        current = await client.post(
            "/api/auth/refresh", headers=PROOF
        )
        assert current.status_code == 200


class TestCorsAbsence:
    async def test_preflight_for_the_origin_proof_header_is_not_answered(
        self, client
    ) -> None:
        """The CSRF control is that this preflight has no answer.

        `X-Mobaide-Session` works as a CSRF defence only because a custom header
        makes the request non-simple: the browser must preflight, and with no
        `CORSMiddleware` installed the OPTIONS is unroutable, so the real
        request is never sent. Installing CORS for an unrelated reason would
        dismantle the control while every header-presence test kept passing.
        """
        resp = await client.options(
            "/api/auth/logout",
            headers={
                "Origin": "https://get.mobaide.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-mobaide-session",
            },
        )
        assert resp.status_code == 405
        assert not [
            name for name in resp.headers if name.lower().startswith("access-control-allow")
        ]


class TestCookieNameIsNotNegotiable:
    """The credential is read under exactly one name, chosen by the config.

    `__Host-` buys precisely one thing: the prefix forbids a `Domain`
    attribute, so a sibling host on the registrable domain cannot write a
    cookie this origin will send. Accepting the bare name as a "dev
    friendliness" fallback hands that back — `get.mobaide.com` (GitHub Pages)
    could then set `mobaide_refresh=<its own token>; Domain=mobaide.com` and
    fixate an attacker-chosen session on a victim. These two cases exist
    because that fallback is a natural thing to re-add and would otherwise
    leave the whole suite green.
    """

    async def test_a_tossed_unprefixed_cookie_is_rejected(
        self, client, viewer_user
    ) -> None:
        token = _value(
            _named(
                (await _login_with_cookies(client, transport="cookie"))["cookies"],
                REFRESH,
            )
        )
        client.cookies.clear()
        client.cookies.set("mobaide_refresh", token)

        resp = await client.post("/api/auth/refresh", headers=PROOF)

        assert resp.status_code == 401

    async def test_the_prefixed_cookie_still_refreshes(
        self, client, viewer_user
    ) -> None:
        token = _value(
            _named(
                (await _login_with_cookies(client, transport="cookie"))["cookies"],
                REFRESH,
            )
        )
        client.cookies.clear()
        client.cookies.set(REFRESH, token)

        resp = await client.post("/api/auth/refresh", headers=PROOF)

        assert resp.status_code == 200
