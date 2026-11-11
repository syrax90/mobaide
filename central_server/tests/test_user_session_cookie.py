"""The raw ``Set-Cookie`` contract for the browser session cookies.

``httpx`` implements neither cookie prefixes nor the attribute policy a browser
enforces, so a round-trip assertion would pass happily against a cookie every
real browser drops on the floor. Everything here reads the header string the
server actually emits.
"""
from __future__ import annotations

from starlette.responses import Response

from central_server.auth.session_cookie import (
    clear_session_cookies,
    set_session_cookies,
)
from central_server.config import CentralServerConfig
from tests.conftest import (
    TEST_JWT_SECRET,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)

REFRESH = "__Host-mobaide_refresh"
MARKER = "__Host-mobaide_signed_in"


def _named(cookies: list[str], name: str) -> str:
    matches = [c for c in cookies if c.startswith(f"{name}=")]
    assert len(matches) == 1, f"expected exactly one {name}, got {cookies}"
    return matches[0]


def _attributes(cookie: str) -> set[str]:
    return {part.strip() for part in cookie.split(";")[1:]}


def _value(cookie: str) -> str:
    return cookie.split(";")[0].split("=", 1)[1]


def _config(tmp_path, **overrides) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        db_path=str(tmp_path / "cookie.db"),
        manifest_store_path=str(tmp_path / "manifest.json"),
        **overrides,
    )


async def _cookie_login(client) -> list[str]:
    resp = await client.post(
        "/api/auth/login",
        json={
            "email": TEST_VIEWER_EMAIL,
            "password": TEST_VIEWER_PASSWORD,
            "refreshable": True,
            "session_transport": "cookie",
        },
    )
    assert resp.status_code == 200
    return resp.headers.get_list("set-cookie")


class TestRefreshCookieAttributes:
    async def test_carries_every_attribute_the_host_prefix_requires(
        self, client, config, viewer_user
    ) -> None:
        del viewer_user
        sent = await _cookie_login(client)

        attributes = _attributes(_named(sent, REFRESH))
        assert "HttpOnly" in attributes
        assert "Secure" in attributes
        assert "SameSite=strict" in attributes
        assert "Path=/" in attributes
        # A `Domain=` would widen the cookie to sibling hosts and is itself a
        # `__Host-` violation, so the whole cookie would be dropped.
        assert not any(a.startswith("Domain=") for a in attributes)
        assert (
            f"Max-Age={config.user_session_refresh_expire_minutes * 60}"
            in attributes
        )

    async def test_the_marker_is_readable_and_shares_the_credentials_lifetime(
        self, client, viewer_user
    ) -> None:
        """The SPA reads this bit to tell "signed out" from "signed in, needs a
        round trip". ``HttpOnly`` would hide it, and any other attribute
        differing would let the marker outlive or under-live the credential."""
        del viewer_user
        sent = await _cookie_login(client)

        marker = _named(sent, MARKER)
        assert _value(marker) == "1"
        assert _attributes(_named(sent, REFRESH)) - {"HttpOnly"} == _attributes(
            marker
        )


class TestNamesFollowTheSecureFlag:
    def test_an_insecure_config_drops_the_prefix_that_requires_secure(
        self, tmp_path
    ) -> None:
        """A browser silently drops a whole ``__Host-`` cookie that arrives
        without ``Secure``, so the two have to move together."""
        response = Response()

        set_session_cookies(
            response,
            "raw-token",
            _config(tmp_path, user_session_cookie_secure=False),
        )

        sent = response.headers.getlist("set-cookie")
        assert {c.split("=")[0] for c in sent} == {
            "mobaide_refresh",
            "mobaide_signed_in",
        }
        assert all("Secure" not in _attributes(c) for c in sent)


class TestClearing:
    def test_deletes_both_names_under_the_attributes_they_were_set_with(
        self, tmp_path
    ) -> None:
        """Starlette's ``delete_cookie`` defaults to ``secure=False``, which a
        browser rejects as a prefix violation — leaving the cookie alive
        through a sign-out."""
        response = Response()

        clear_session_cookies(response, _config(tmp_path))

        by_name = {
            c.split("=")[0]: _attributes(c)
            for c in response.headers.getlist("set-cookie")
        }
        assert set(by_name) == {
            REFRESH,
            "mobaide_refresh",
            MARKER,
            "mobaide_signed_in",
        }
        for name, attributes in by_name.items():
            assert "Max-Age=0" in attributes
            assert "Path=/" in attributes
            assert "SameSite=strict" in attributes
            # Only the prefixed names may carry Secure; repeating it on the
            # bare ones would be harmless but the prefixed ones need it.
            assert ("Secure" in attributes) == name.startswith("__Host-")
        assert "HttpOnly" in by_name[REFRESH]
        assert "HttpOnly" not in by_name[MARKER]
