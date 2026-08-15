"""Tests for what is left of the end-user web portal.

Sign-in, sign-up, verification, password reset and the account page moved to
the browser client; ``/portal/*`` now serves only two anonymous pages —
``/portal/delete-account-info`` (the URL registered in Google Play Console's
Data Safety form) and ``/portal/cancel-deletion`` (the deletion email's
cancel link) — plus CSRF enforcement on the latter's state-changing POST.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


# ---- Helpers -------------------------------------------------------------


async def _grab_csrf(client) -> tuple[str, dict[str, str]]:
    """Hit a portal GET to harvest the CSRF cookie + matching token.

    Returns ``(token, cookies_dict)``. The cookie jar attached to the
    AsyncClient already carries the cookie for subsequent calls, so the
    second return value is mostly for documentation in tests that mix
    multiple clients. The token itself is parsed out of the ``Set-Cookie``
    header on the GET response because the template-embedded token is HTML.
    """
    resp = await client.get("/portal/cancel-deletion")
    assert resp.status_code == 200
    cookie_header = resp.headers.get("set-cookie", "")
    # ``portal_csrf=<nonce>.<digest>; Path=/; ...`` — extract the value.
    token = ""
    for piece in cookie_header.split(","):
        piece = piece.strip()
        if piece.startswith("portal_csrf="):
            token = piece.split(";", 1)[0].removeprefix("portal_csrf=")
            break
    assert token, f"portal_csrf cookie not set; saw: {cookie_header!r}"
    return token, {"portal_csrf": token}


# ---- Public, no-auth pages ----------------------------------------------


class TestPublicPortalPages:
    async def test_delete_account_info_page_reachable_without_auth(
        self, client
    ) -> None:
        """The URL Google Play Console points to MUST work without
        authentication — that is the whole point of the public
        ``delete-account-info`` route (plan + Data Safety form)."""
        resp = await client.get("/portal/delete-account-info")
        assert resp.status_code == 200
        # No redirect to login.
        assert resp.url.path == "/portal/delete-account-info"

    async def test_cancel_deletion_get_with_no_token_renders_missing_token(
        self, client
    ) -> None:
        resp = await client.get("/portal/cancel-deletion")
        # The GET path is also public — it renders a confirmation form
        # before consuming the token (avoids accidental link-scanner hits).
        assert resp.status_code == 200


# ---- CSRF protection on state-changing routes ---------------------------


class TestPortalCsrf:
    async def test_cancel_deletion_post_without_csrf_returns_403(
        self, client
    ) -> None:
        """``/portal/cancel-deletion`` requires the double-submit CSRF
        pair. With no cookie+header, the dependency raises 403."""
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything"},
        )
        assert resp.status_code == 403

    async def test_cancel_deletion_post_with_matching_pair_accepted(
        self, client
    ) -> None:
        """Cookie + matching header → CSRF check passes. The token itself
        is bogus (no DB row) so the route renders the "invalid_token"
        outcome with HTTP 200 — but that's downstream of CSRF: the only
        thing under test here is that we didn't 403 out.
        """
        token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "bogus-cancel-token", "csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
        # Important assertion: NOT 403. The route always returns 200 here
        # because the cancel-deletion HTML page renders the outcome.
        assert resp.status_code != 403
        assert resp.status_code == 200

    async def test_cancel_deletion_post_cookie_only_no_header_returns_403(
        self, client
    ) -> None:
        """Half of the double-submit pair (cookie only, no header / form
        field) must still be rejected. Confirms we can't get past CSRF by
        merely passing the cookie back."""
        # Seed the cookie jar by hitting any portal GET.
        await _grab_csrf(client)
        # Now POST without supplying X-CSRF-Token or csrf_token form field.
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything"},
        )
        assert resp.status_code == 403

    async def test_non_ascii_form_token_returns_403_not_500(self, client) -> None:
        """A wrong token is a 403 whatever bytes it holds.

        ``compare_digest`` *raises* ``TypeError`` on a ``str`` operand carrying
        a non-ASCII character rather than returning False, and every operand
        here is peer-supplied text. ``require_csrf`` translates only its own
        ``HTTPException``, so an unencoded comparison turned one non-ASCII byte
        in this field into an unhandled error on every portal POST route — and
        under ASGI that escapes the transport instead of becoming a response,
        which is why the bare status assertion is the whole test.
        """
        await _grab_csrf(client)

        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything", "csrf_token": "csrf-tökén-Ω"},
        )

        assert resp.status_code == 403

    async def test_non_ascii_header_token_returns_403_not_500(self, client) -> None:
        """Same for the HTMX path: ``X-CSRF-Token`` is attacker-chosen too, and
        it is checked before the form field is ever consulted.

        Header values go over the wire as bytes and ASGI hands them up decoded
        as latin-1, so any octet above 0x7f arrives as a non-ASCII ``str``;
        httpx would reject the same value typed as ``str`` before it ever left
        the client, which no real caller is bound by.
        """
        await _grab_csrf(client)

        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything"},
            headers={"X-CSRF-Token": "csrf-tökén-Ω".encode()},
        )

        assert resp.status_code == 403

    async def test_non_ascii_cookie_token_returns_403_not_500(self, client) -> None:
        """And for the cookie half, which is compared against a freshly
        computed HMAC before the presented token is looked at. The value is
        shaped like a real token (``<nonce>.<digest>``) so the check reaches
        that comparison instead of bailing out on the missing separator.
        """
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything", "csrf_token": "irrelevant"},
            headers={"Cookie": "portal_csrf=nonce.dïgest-Ω".encode()},
        )

        assert resp.status_code == 403


# ---- Public cancel-by-email-token path ----------------------------------


class TestPublicCancelByToken:
    async def test_post_with_valid_token_clears_schedule(
        self, client, app, viewer_user
    ) -> None:
        """A real, unconsumed cancel token in a public POST cancels the
        scheduled deletion. CSRF is still enforced because the route is
        cookie-authed (CSRF cookie must accompany), but no portal_token
        is required — this is the email-link path."""
        future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        await app.state.db.set_scheduled_deletion(
            viewer_user["id"], scheduled_for=future, requested_at=now_iso,
        )
        await app.state.db.record_deletion_audit(
            user_id=viewer_user["id"],
            email_at_request=viewer_user["email"],
            requested_at=now_iso,
            scheduled_for=future,
            source="android",
        )
        raw_token = await app.state.db.create_deletion_cancel_token(
            viewer_user["id"], ttl_seconds=3600,
        )

        csrf_token, _ = await _grab_csrf(client)

        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": raw_token, "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        # Server cleared the schedule.
        refreshed = await app.state.db.get_user_by_id(viewer_user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None

    async def test_post_with_invalid_token_returns_200_with_invalid_outcome(
        self, client
    ) -> None:
        csrf_token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "definitely-not-a-real-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
        )
        # Page renders the "invalid_token" outcome with 200; never leaks
        # whether the token shape was right but unknown vs malformed.
        assert resp.status_code == 200
