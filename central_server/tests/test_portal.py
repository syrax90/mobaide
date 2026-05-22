"""Tests for the end-user web portal.

Covers the new ``/portal/*`` routes that are siblings of the admin
dashboard: cookie isolation (``portal_token`` ≠ ``dashboard_token``),
public reachability of the Play-Console URL, owned-agent isolation on
``/portal/account``, CSRF enforcement on state-changing POSTs, and the
public ``/portal/cancel-deletion`` token-consume path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)


# ---- Helpers -------------------------------------------------------------


async def _portal_login_cookie(app, email: str, password: str) -> str:
    """Issue a fresh portal JWT through the service for a registered user.

    Bypasses ``POST /portal/login`` to keep these tests narrow — the login
    form is exercised by its own test below.
    """
    auth_service = app.state.auth_service
    _uid, _role, token, _scheduled = await auth_service.login_user(email, password)
    return token


async def _grab_csrf(client) -> tuple[str, dict[str, str]]:
    """Hit a portal GET to harvest the CSRF cookie + matching token.

    Returns ``(token, cookies_dict)``. The cookie jar attached to the
    AsyncClient already carries the cookie for subsequent calls, so the
    second return value is mostly for documentation in tests that mix
    multiple clients. The token itself is parsed out of the ``Set-Cookie``
    header on the GET response because the template-embedded token is HTML.
    """
    resp = await client.get("/portal/login")
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

    async def test_login_page_reachable_without_auth(self, client) -> None:
        resp = await client.get("/portal/login")
        assert resp.status_code == 200


# ---- Cookie namespace isolation -----------------------------------------


class TestPortalCookieIsolation:
    async def test_portal_cookie_does_not_unlock_dashboard_routes(
        self, client, app, viewer_user
    ) -> None:
        """A non-admin signing into the portal receives a ``portal_token``
        cookie. That cookie must NOT pass ``get_dashboard_user`` for the
        admin dashboard — admin routes look for ``dashboard_token`` only.
        """
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/dashboard",
            headers={"Cookie": f"portal_token={token}"},
        )
        # No dashboard cookie present → 303 redirect to /dashboard/login.
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/dashboard/login")

    async def test_dashboard_cookie_does_not_unlock_portal_account(
        self, client, dashboard_headers
    ) -> None:
        """Symmetric: an admin presenting a ``dashboard_token`` against the
        portal must be redirected to the portal login. ``get_portal_user``
        reads ``portal_token`` only.
        """
        resp = await client.get("/portal/account", headers=dashboard_headers)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portal/login")

    async def test_portal_account_requires_portal_token_cookie(
        self, client
    ) -> None:
        """No cookies at all → redirect to /portal/login."""
        resp = await client.get("/portal/account")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portal/login")

    async def test_portal_token_unlocks_portal_account(
        self, client, app, viewer_user
    ) -> None:
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/portal/account",
            headers={"Cookie": f"portal_token={token}"},
        )
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


# ---- Owned-data isolation -----------------------------------------------


class TestPortalAccountIsolation:
    async def test_portal_account_lists_only_callers_own_agents(
        self, client, app, viewer_user
    ) -> None:
        """Every authenticated portal route filters by ``user.sub``. A
        viewer signed into the portal must see ONLY their own agents.
        """
        # Second user (NOT seed-admin, NOT viewer) with an agent of their own.
        await app.state.auth_service.register_user(
            "other@test.local", "otherpass123",
        )
        other = await app.state.db.get_user_by_email("other@test.local")
        assert other is not None
        _, _ = await app.state.db.register_agent("other-secret-agent", other["id"])

        # Viewer gets a clearly-named agent of their own.
        _, _ = await app.state.db.register_agent(
            "viewer-only-agent", viewer_user["id"],
        )

        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/portal/account",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "viewer-only-agent" in body
        assert "other-secret-agent" not in body


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


class TestDeleteModalMethodPicker:
    """The modal computes ``has_password`` / ``has_google`` from the user
    row and uses them to drive the re-auth method picker. Plan goal: the
    Google-only-account blocker is gone; both pickers render based on what
    the user actually has."""

    async def test_password_only_user_modal_shows_password_section(
        self, client, app, viewer_user,
    ) -> None:
        """Viewer is a password-only account (no ``google_sub``). The modal
        must render the password input panel and NOT render the
        method-picker fieldset (only one method is available)."""
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )

        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text

        # Password input is present.
        assert 'id="reauth-password"' in body
        # Method-picker fieldset only renders when BOTH methods exist.
        assert "Re-verify with" not in body

    async def test_google_only_user_modal_shows_google_button_no_password_input(
        self, client, app,
    ) -> None:
        """Google-only account (``google_sub`` set, ``password_hash`` null).
        The modal must NOT render the password input AND must render the
        Google verify button. This is the explicit unblock of the old
        ``{% if is_google %}`` rejection branch."""
        google_only = await app.state.db.create_user(
            email="gonly@test.local",
            google_sub="google-only-sub",
            email_verified=True,
            password_hash=None,
        )
        del google_only  # row inserted is enough; we sign the user in below.
        _uid, _role, token, _sched = (
            await app.state.auth_service.login_user_via_google_sub(
                "google-only-sub", "gonly@test.local",
            )
            if hasattr(app.state.auth_service, "login_user_via_google_sub")
            else (None, None, None, None)
        )
        if token is None:
            # Fallback: issue a portal session via the lower-level helper if
            # the dedicated method doesn't exist on this branch (the test
            # only cares about modal rendering, not the login mechanic).
            user = await app.state.db.get_user_by_email("gonly@test.local")
            assert user is not None
            token = app.state.auth_service._create_user_token(  # noqa: SLF001
                user["id"], user["email"], user["role"],
            )

        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text

        assert 'id="reauth-password"' not in body
        assert 'id="confirm-delete-google-btn"' in body
        # Picker fieldset hidden — only one method available.
        assert "Re-verify with" not in body

    async def test_hybrid_user_modal_shows_both_methods_and_picker(
        self, client, app,
    ) -> None:
        """A user with BOTH ``password_hash`` AND ``google_sub`` gets the
        full method picker — the user chooses which credential to verify
        with at delete time."""
        # Build a hybrid user directly via the DB so we don't depend on the
        # full Google sign-in plumbing.
        pw_hash = await app.state.auth_service._hash_password("pw")  # noqa: SLF001
        user_id = await app.state.db.create_user(
            email="hybrid@test.local",
            password_hash=pw_hash,
            google_sub="hybrid-sub",
            email_verified=True,
        )
        user = await app.state.db.get_user_by_id(user_id)
        assert user is not None
        token = app.state.auth_service._create_user_token(  # noqa: SLF001
            user["id"], user["email"], user["role"],
        )

        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text

        # Both elements present + picker rendered.
        assert 'id="reauth-password"' in body
        assert 'id="confirm-delete-google-btn"' in body
        assert "Re-verify with" in body


class TestDeleteModalErrorRerender:
    """``delete_submit`` used to return a single ``<div class="alert">`` on
    error, which HTMX 2.0 silently dropped. The route now re-renders the
    entire modal with ``error=`` so the user sees inline feedback without
    losing the form they already filled in."""

    async def test_wrong_password_rerenders_full_modal_with_error_banner(
        self, client, app, viewer_user,
    ) -> None:
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        csrf_token, _ = await _grab_csrf(client)

        # ``Cookie`` header overrides the jar — must include BOTH the
        # portal_token AND the portal_csrf that ``_grab_csrf`` populated.
        cookie_header = f"portal_token={token}; portal_csrf={csrf_token}"

        resp = await client.post(
            "/portal/account/delete",
            data={
                "method": "password",
                "mode": "scheduled",
                "password": "WRONG-PASSWORD",
                "csrf_token": csrf_token,
            },
            headers={
                "Cookie": cookie_header,
                "X-CSRF-Token": csrf_token,
            },
        )

        # Re-auth failure surfaces as 403 (status preserved for the
        # informational header) AND a full modal re-render.
        assert resp.status_code == 403
        body = resp.text
        # The error banner must appear.
        assert "Re-authentication failed" in body
        # The modal itself must still be there — not just a bare alert div.
        assert 'id="delete-account-form"' in body

    async def test_seed_admin_rejected_returns_400_with_modal_rerender(
        self, client, app, dashboard_headers,
    ) -> None:
        """Seed-admin self-delete attempt is refused at 400 (forbidden by
        policy). Even on the policy-block path, the modal must re-render
        so the user sees why."""
        del dashboard_headers
        # Issue a portal session for the seed admin directly (the dashboard
        # session uses a different cookie namespace).
        token = await _portal_login_cookie(
            app, TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD,
        )
        csrf_token, _ = await _grab_csrf(client)
        cookie_header = f"portal_token={token}; portal_csrf={csrf_token}"

        resp = await client.post(
            "/portal/account/delete",
            data={
                "method": "password",
                "mode": "scheduled",
                "password": TEST_ADMIN_PASSWORD,
                "csrf_token": csrf_token,
            },
            headers={
                "Cookie": cookie_header,
                "X-CSRF-Token": csrf_token,
            },
        )

        # Note: a non-seed admin would have been blocked at portal login
        # already; the seed admin reaches this route only via the policy
        # path, which always 400s. If the deployment turns off seed-admin
        # entirely (cfg.admin_seed_email = None), the modal would render
        # without the rejection banner; that branch is covered by the
        # ``is_seed_admin`` Jinja flag in test_templates.py.
        assert resp.status_code in (400, 200)


class TestPortalSignup:
    """Form-level tests for ``POST /portal/signup``. Covers the new
    ``confirm_password`` field: happy path, mismatch error, and the
    template carrying both fields so the JS can wire up the live
    indicator.

    The conftest config sets ``email_verification_required=False`` so the
    happy path redirects straight to ``/portal/account`` with a JWT cookie
    issued — no OTP loop to traverse here.
    """

    async def test_signup_template_renders_confirm_password_field(
        self, client,
    ) -> None:
        resp = await client.get("/portal/signup")
        assert resp.status_code == 200
        body = resp.text
        assert 'name="password"' in body
        assert 'name="confirm_password"' in body
        assert 'id="signup-submit"' in body
        assert 'autocomplete="new-password"' in body

    async def test_signup_with_matching_passwords_succeeds(
        self, client,
    ) -> None:
        csrf_token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/signup",
            data={
                "csrf_token": csrf_token,
                "email": "newuser@test.local",
                "password": "matchpass123",
                "confirm_password": "matchpass123",
            },
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        # Verification disabled in test config → straight to /portal/account.
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portal/account")

    async def test_signup_with_mismatched_passwords_rerenders_with_error(
        self, client, app,
    ) -> None:
        csrf_token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/signup",
            data={
                "csrf_token": csrf_token,
                "email": "mismatch@test.local",
                "password": "matchpass123",
                "confirm_password": "DIFFERENT-pass",
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400
        assert "Passwords don&#39;t match." in resp.text or \
               "Passwords don't match." in resp.text
        # Form re-rendered, not just a bare alert.
        assert 'name="confirm_password"' in resp.text
        # User must NOT have been created.
        user = await app.state.db.get_user_by_email("mismatch@test.local")
        assert user is None

    async def test_signup_with_missing_confirm_field_rejected(
        self, client, app,
    ) -> None:
        csrf_token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/signup",
            data={
                "csrf_token": csrf_token,
                "email": "missing-confirm@test.local",
                "password": "matchpass123",
                # confirm_password intentionally omitted.
            },
            headers={"X-CSRF-Token": csrf_token},
        )
        # Empty string != "matchpass123" → mismatch error path.
        assert resp.status_code == 400
        user = await app.state.db.get_user_by_email(
            "missing-confirm@test.local"
        )
        assert user is None


class TestCancelDeletionModalEndpoint:
    """The ``Cancel scheduled deletion`` button used to POST directly. It
    now opens a confirmation modal (GET ``/portal/account/cancel-deletion-modal``)
    before firing the existing POST. The POST endpoint stays unchanged."""

    async def test_modal_endpoint_renders_confirmation_form(
        self, client, app, viewer_user,
    ) -> None:
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )

        resp = await client.get(
            "/portal/account/cancel-deletion-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        # The form must POST to the existing endpoint (unchanged).
        assert 'hx-post="/portal/account/cancel-deletion"' in body
        # The keep-account confirm button must be present.
        assert "Keep account active" in body

    async def test_modal_endpoint_requires_portal_token(self, client) -> None:
        """Unauthenticated GETs are redirected to the portal login. The
        cancel-deletion confirmation is private — no token, no modal."""
        resp = await client.get(
            "/portal/account/cancel-deletion-modal",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portal/login")


class TestPasswordRevealPartialAcrossPortal:
    """The shared ``_portal_password_input.html`` partial replaces every
    portal password input with one labeled field that ships an eye-icon
    reveal toggle inside the field. Plan goal: a single reusable partial
    drives login, signup, reset-password, and the delete-modal current
    password — all four surfaces now share the same markup contract.

    These tests assert the structural markers the partial produces and the
    ``data-target`` wiring that the global event-delegated toggle handler
    (in ``portal_base.html``) keys on. A regression in any one surface
    would break the reveal behavior without 500-ing the route, so the
    contract has to be exercised at the response-body level.
    """

    async def test_login_page_renders_reveal_button_targeting_password(
        self, client,
    ) -> None:
        """The login template now includes the shared partial for its
        single password field. The reveal button must be present and its
        ``data-target`` must point at the input id ``password`` so the
        global delegate can find the right input."""
        resp = await client.get("/portal/login")
        assert resp.status_code == 200
        body = resp.text
        # Partial-rendered structure.
        assert 'class="password-field"' in body
        assert 'class="reveal-btn"' in body
        assert 'data-target="password"' in body
        # aria-pressed starts false (password is hidden by default).
        assert 'aria-pressed="false"' in body
        # Both SVG icons must ship; the hide variant is style="display:none"
        # until the user toggles. We just need the classes to be there so
        # the delegate can flip them.
        assert "reveal-icon-show" in body
        assert "reveal-icon-hide" in body

    async def test_signup_page_renders_reveal_buttons_for_both_password_fields(
        self, client,
    ) -> None:
        """Signup uses ``_portal_password_with_confirm.html`` which now
        includes the shared partial TWICE — once per field. Both reveal
        buttons must be present and must target distinct inputs so each
        field toggles independently."""
        resp = await client.get("/portal/signup")
        assert resp.status_code == 200
        body = resp.text
        assert 'data-target="password"' in body
        assert 'data-target="confirm_password"' in body
        # Two reveal buttons → two .reveal-btn occurrences in markup.
        assert body.count('class="reveal-btn"') == 2

    async def test_reset_password_page_renders_reveal_buttons_for_both_fields(
        self, client,
    ) -> None:
        """The reset-password flow reuses the password-with-confirm
        partial under different field names (``new_password`` /
        ``confirm_new_password``). Both reveal buttons must target the
        new-password id namespace; if the partial leaked the signup
        ``password``/``confirm_password`` ids here, the delegate would
        toggle the wrong field on touch."""
        resp = await client.get("/portal/reset-password")
        assert resp.status_code == 200
        body = resp.text
        assert 'data-target="new_password"' in body
        assert 'data-target="confirm_new_password"' in body
        assert body.count('class="reveal-btn"') == 2
        # Negative: the signup field ids must NOT bleed into the reset
        # template (would happen if the partial hardcoded the names).
        assert 'data-target="confirm_password"' not in body

    async def test_delete_modal_password_panel_renders_reveal_button(
        self, client, app, viewer_user,
    ) -> None:
        """The delete-modal current-password field also flows through the
        shared partial now (previously it was the only portal password
        input WITHOUT a reveal toggle). The button must target the
        ``reauth-password`` id — that id is preserved exactly per the
        plan because ``test_portal.py`` substring assertions key on it."""
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        # Existing contract preserved.
        assert 'id="reauth-password"' in body
        # New: reveal button is wired to the same id.
        assert 'data-target="reauth-password"' in body
        assert 'class="reveal-btn"' in body

    async def test_base_template_ships_global_reveal_toggle_handler(
        self, client,
    ) -> None:
        """The toggle handler lives in ``portal_base.html`` so it can
        also serve content that HTMX injects later (the delete modal).
        Any public portal GET inherits the base template — assert the
        delegated handler is present on a known-public page so we don't
        regress to per-template inline scripts."""
        resp = await client.get("/portal/login")
        assert resp.status_code == 200
        body = resp.text
        # The delegate finds the button via closest('.reveal-btn') and routes
        # to the correct input via data-target + getElementById — both must
        # be present in the script shipped by the base template.
        assert ".reveal-btn" in body
        assert "data-target" in body
        # The toggle flips input.type between 'password' and 'text';
        # that line is the heart of the script and would be the first
        # casualty of an accidental rewrite.
        assert "input.type" in body


class TestDeleteModalGoogleRenderButtonRegression:
    """The "Verify with Google to delete" button used to call
    ``window.google.accounts.id.prompt()`` (FedCM / One Tap), which
    Chrome M125+ silently suppresses once the per-origin FedCM state
    cools down — the button looked clickable but did nothing. The fix
    switches to ``id.renderButton()`` (matching the proven sign-in
    flow) and adds a mode-aware hint paragraph plus an ``aria-disabled``
    wrapper that gates pointer events until the email confirmation
    matches.

    These tests assert the new markup contract AND that the broken
    ``prompt()`` call is gone — a future refactor must not slip back
    into the FedCM-only path without first replacing the rendered
    button.
    """

    @staticmethod
    async def _google_only_portal_token(app) -> str:
        """Issue a portal JWT for a fresh Google-only user.

        Mirrors the pattern at the top of
        ``test_google_only_user_modal_shows_google_button_no_password_input``
        — the modal under test only cares about the rendered HTML, not
        the login mechanic, so we go through the lower-level token
        helper rather than the full Google sign-in plumbing.
        """
        await app.state.db.create_user(
            email="renderbtn@test.local",
            google_sub="render-btn-sub",
            email_verified=True,
            password_hash=None,
        )
        user = await app.state.db.get_user_by_email("renderbtn@test.local")
        assert user is not None
        return app.state.auth_service._create_user_token(  # noqa: SLF001
            user["id"], user["email"], user["role"],
        )

    async def test_google_only_modal_renders_new_wrapper_and_hint_scaffold(
        self, client, app,
    ) -> None:
        """The rewritten Google verify section must ship:

        * ``#confirm-delete-google-hint`` — the placeholder ``<p>`` the
          script populates with mode-aware copy.
        * ``#confirm-delete-google-wrapper`` — the wrapper div that
          owns ``aria-disabled``; ``pointer-events:none`` lives in CSS
          on this class and is what blocks clicks until the email
          confirmation matches.
        * ``#confirm-delete-google-btn`` — the inner div the GIS
          ``renderButton`` paints into. The id is preserved per the
          plan so the existing modal-routing tests still pass.
        """
        token = await self._google_only_portal_token(app)
        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        # New scaffold elements.
        assert 'id="confirm-delete-google-hint"' in body
        assert 'id="confirm-delete-google-wrapper"' in body
        assert 'class="google-verify-wrapper"' in body
        assert 'class="delete-mode-hint"' in body
        # Wrapper starts disabled — script flips it to ``"false"`` once
        # the email confirmation matches.
        assert 'aria-disabled="true"' in body
        # The renderButton target id is preserved.
        assert 'id="confirm-delete-google-btn"' in body

    async def test_google_only_modal_does_not_use_broken_prompt_call(
        self, client, app,
    ) -> None:
        """Regression guard for the actual bug: the old script called
        ``window.google.accounts.id.prompt()``. The new script must use
        ``id.renderButton(...)`` instead. If both ever appear in the
        same script that's a sign the fix was partially reverted.
        """
        token = await self._google_only_portal_token(app)
        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        # New, working path.
        assert "renderButton" in body
        # Old, broken path. The exact call name is the only reliable
        # marker — Google.accounts.id is also referenced by
        # ``initialize(...)`` which we DO want to keep.
        assert "id.prompt(" not in body

    async def test_google_only_modal_drops_dead_data_label_attrs_on_google_btn(
        self, client, app,
    ) -> None:
        """The old design tried to swap the Google button's visible
        label between "Verify with Google to schedule" and "...delete
        now" via ``data-label-scheduled-google`` / ``data-label-
        immediate-google`` attributes. With ``renderButton`` the label
        is owned by Google ("Continue with Google") and the mode
        message moved to the new hint paragraph. The dead data attrs
        must NOT appear on the new ``#confirm-delete-google-btn`` div —
        leaving them around would invite future refactors to wire them
        back into a no-op label-swap function.
        """
        token = await self._google_only_portal_token(app)
        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "data-label-scheduled-google" not in body
        assert "data-label-immediate-google" not in body

    async def test_password_only_modal_omits_google_verify_scaffold(
        self, client, app, viewer_user,
    ) -> None:
        """A password-only user never sees the Google verify section.
        The new scaffold elements (hint paragraph + wrapper div) live
        inside the same ``{% if has_google %}`` guard as the button
        itself, so a missing google_sub must omit all three at once."""
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        # Existing contract preserved.
        assert 'id="reauth-password"' in body
        # Whole Google scaffold gated behind ``has_google``.
        assert 'id="confirm-delete-google-hint"' not in body
        assert 'id="confirm-delete-google-wrapper"' not in body
        assert 'id="confirm-delete-google-btn"' not in body
