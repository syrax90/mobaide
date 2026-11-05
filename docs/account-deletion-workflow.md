# User self-service account deletion

Google Play and Apple App Store both require any app that supports account
creation to ship two delete-account paths: one **in-app** and one
**publicly reachable web URL**. GDPR Article 17 (right to erasure) adds a
parallel obligation. CLA-61 satisfies both with a single 14-day
soft-delete pipeline shared between the mobile apps and the browser client at
`app.mobaide.com`. (The server-rendered portal that originally carried the web
half has been retired; only its two anonymous pages remain — see *Public
information page* below.)

## Architecture summary

```
┌────────────────────┐                  ┌────────────────────┐
│  Android / iOS     │                  │  Web client        │
│  (Settings         │                  │  app.mobaide.com   │
│   → Danger zone)   │                  │  /account          │
└─────────┬──────────┘                  └─────────┬──────────┘
          │ POST /api/auth/me/delete             │  POST /api/auth/me/delete
          │   {method, password|id_token, nonce, mode:"scheduled"|"immediate"}
          ▼                                       ▼
       ┌─────────────────────────────────────────┐
       │ AuthService.request_account_deletion    │
       │                                          │
       │  1. verify fresh credential proof        │
       │  2a. mode=scheduled: set                 │
       │      users.scheduled_deletion_at (+14d)  │
       │  2b. mode=immediate: cascade purge now   │
       │  3. write deletion_audit (status=pending │
       │     or completed, mode column set)       │
       │  4. revoke all refresh tokens + clear    │
       │     every device push token              │
       │  5. WS-4010 close (user + each agent)    │
       │  6. issue cancel-link token + email      │
       │  7. return 204                           │
       └─────────────────────────────────────────┘
                          │
            14 days later │ (configurable via
                          │  ACCOUNT_DELETION_GRACE_DAYS)
                          ▼
       ┌─────────────────────────────────────────┐
       │  _account_deletion_sweeper_loop          │
       │   (asyncio task in app.py lifespan)      │
       │                                          │
       │  list_users_due_for_purge → per-user:    │
       │   • refetch + re-check (race-safe)       │
       │   • WS-4010 close (live reconnects)      │
       │   • Database.delete_user cascade         │
       │   • deletion_audit → completed           │
       │   • send_account_deletion_completed email│
       └─────────────────────────────────────────┘
```

## Re-authentication requirement

The existing session JWT alone is never sufficient. `request_account_deletion`
accepts a fresh credential proof, validated by `AuthService._verify_fresh_credentials`:

| Method | Proof | Server check |
|---|---|---|
| `password` | Current password | Argon2id verify against `users.password_hash`. Wrong passwords are throttled per email (`LOGIN_EMAIL_FAILURE_CAP`); when blocked, the reply is `403 reauth_required` with "Too many incorrect passwords. Try again in N minutes." |
| `google` | Fresh `id_token` + matching `nonce` | `verify_oauth2_token` (signature + audience), then `iat` within last 5 min, `sub` equals stored `google_sub`, and `nonce` claim matches the client-supplied value. |
| `apple` | Fresh `id_token` + matching `nonce` | Signature verified against Apple's JWKS, audience matching either `APPLE_CLIENT_ID` (iOS bundle id) or `APPLE_WEB_CLIENT_ID` (Services ID, browser flow), then the same `iat` / `nonce` checks with `sub` equal to the stored `apple_sub`. Added in CLA-99 for the iOS app; the browser audience arrived with CLA-198. |

This mirrors the Auth0 recommended pattern for destructive-action re-auth.

### Browser re-authentication

The web client's delete panel always offers "Continue with password", and adds a
Google or Apple button for whichever providers the *server* has configured — it
reads the two client ids from `GET /api/version` and nothing else. **It does not
know which identities are linked to the account**: `UserSession` carries only
`accessToken`/`userId`/`email`/`role`/`scheduledDeletionAt`, and no endpoint
publishes the linked set. A wrong choice is caught server-side by
`_verify_fresh_credentials`, which answers 403.

That is a real divergence from the retired portal, which rendered its offerable
methods from the user row and so could hide a method the account could not
complete. In practice a social-only account (`password_hash` NULL) still deletes
from a browser, because the provider button is offered whenever the server is
configured — but it is also shown a password option it cannot use. Closing that
properly needs the server to publish the linked providers on an
`/api/auth/me`-style read; it is not a client-side fix.

Both providers run in popup mode; the resulting identity token is posted to
`POST /api/auth/me/delete` with the nonce the client minted, exactly as the
mobile apps do.

### Deletion mode (`DeleteAccountRequest.mode`)

Both clients now send a `mode` field on the delete request:

| Value | Behaviour |
|---|---|
| `"scheduled"` (default) | 14-day soft-delete grace window; cancellable via the in-app banner, the web client's grace banner, or the email link. |
| `"immediate"` | Irreversible cascade purge executed synchronously; no grace window, no cancel. |

Older clients that omit `mode` (or send `null`) are normalised to `"scheduled"`,
preserving backwards compatibility.

## Cancellation paths

A user can restore their account at any time within the grace window:

1. **In-app banner** — `GraceBannerSection` (Compose) renders on the first
   authenticated landing screen whenever `UserIdentityStore.getScheduledDeletionAt()`
   returns non-null. Tapping "Cancel scheduled deletion" calls
   `AuthRepository.cancelAccountDeletion` → `POST /api/auth/me/delete/cancel`.
2. **Web-client banner** — `GraceBanner` (`web/src/components/account/GraceBanner.tsx`)
   renders whenever the session carries a `scheduled_deletion_at`. It is a
   two-step: the button reveals a confirmation panel, and only "Keep account
   active" calls `POST /api/auth/me/delete/cancel`. The two-step exists to
   prevent accidental cancellations, the same reason the retired portal's
   version fetched a modal first.

   Scheduling a deletion from this client signs the browser out and lands the
   user on `/login` with the notice `deletionScheduled` carries
   (`web/src/auth/authState.ts`), which is what tells them the grace window is
   the way back.

   That notice has to be true on every path back in, so `postAuthPath`
   (`web/src/app/guards.tsx`) sends a sign-in that would otherwise resolve to `/`
   to `/account` instead whenever `scheduledDeletionAt` is set — an explicit
   `?next=` still wins. Both `useAfterAuth` and `RequireAnonymous` call it,
   because adopting a session re-renders the guard and the two redirects race.
   **This is a divergence from mobile**, which mounts its grace banner on the
   authenticated landing screen (`AgentListScreen` on both clients) rather than
   only on the account screen, and so needs no redirect.
3. **One-click email link** — The confirmation email carries a single-use
   token (`account_deletion_cancel_tokens` table; 24h TTL by default). Visiting
   `/portal/cancel-deletion?token=…` consumes the token and clears the
   schedule. Works without a logged-in session — important for users who
   have signed out everywhere or lost their device.

Cancelling stamps the existing `deletion_audit` row to `status=cancelled` and
records the cancellation source (`user_initiated` for in-session cancels,
`email_link` for the token path).

Push tokens cleared when the deletion request was filed (see the
architecture diagram, step 4) are **not** restored on cancel. The affected
client — iOS today, the only platform with a push pipeline — re-registers
its token automatically on its next launch, so no user action is required.

## Schema

Migrations `0009_account_deletion.py` + `0010_deletion_mode.py` (Alembic) and
the runtime `PRAGMA user_version=11` / `user_version=12` blocks in
`central_server/db/database.py`:

* `users.scheduled_deletion_at TEXT NULL` — ISO-8601 timestamp when the
  sweeper is expected to run the cascade purge. Indexed via partial index
  `idx_users_scheduled_deletion`.
* `users.deletion_requested_at TEXT NULL` — when the request was filed.
* `account_deletion_cancel_tokens(token_hash, user_id, created_at, expires_at, consumed_at)`
  — single-use cancel-link tokens, mirrors `password_reset_tokens`.
* `deletion_audit(id, user_id, email_at_request, requested_at, scheduled_for,
  source, status, mode, completed_at, cancellation_reason)` — **no FK** on
  `user_id` so audit rows survive the final cascade purge (GDPR Art. 17 trail).
  `source ∈ {android, portal, email_link}`, `status ∈ {pending, cancelled, completed, failed}`,
  `mode ∈ {scheduled, immediate}` (migration `0010`; runtime guard
  `user_version < 12`). `'portal'` stays in the CHECK constraint permanently:
  the surface that wrote it is gone, but its historical rows are not, and
  dropping the value would invalidate them.
* `device_revocations(device_id, tokens_valid_after)` — device revoke
  watermarks, also **no FK**, so they are kept after the final cascade purge
  (no account link) and revoked devices' old tokens stay invalid.

`Database.delete_user` was also extended (latent-bug fix discovered during
CLA-61 planning): it now cascades `password_reset_tokens` and
`account_deletion_cancel_tokens`. Previously a user with an outstanding
unconsumed reset token would FK-violate at admin-delete time.

## Sweeper safety

`AuthService.run_deletion_sweep` does NOT trust the snapshot from
`list_users_due_for_purge`. For each candidate it:

1. Re-fetches the user by `user_id`.
2. Re-checks `scheduled_deletion_at` against `now()` (a cancel that landed
   between scan and delete becomes a no-op).
3. Closes any reconnected WebSockets with WS-4010.
4. Calls `Database.delete_user` (which is the same cascade the admin path
   already uses).
5. Stamps the audit row `completed`.
6. Fires the final-purge confirmation email.

Per-user exceptions are logged and the audit row is stamped `failed`. The
loop never raises — the next sweep tick will retry.

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `ACCOUNT_DELETION_GRACE_DAYS` | `14` | Industry-norm window (X 30d, Spotify 7d, Pinterest 14d). |
| `DELETION_SWEEPER_INTERVAL_SECONDS` | `3600` | Loop interval. Lower bound clamped to 60 s. |
| `ACCOUNT_DELETION_CANCEL_TOKEN_TTL_HOURS` | `24` | Capped at the grace window — a link is useless past final purge. |
| `CSRF_SECRET_KEY` | derived from `JWT_SECRET_KEY` | Override only if you need to rotate CSRF independently of the JWT secret. Still required: the cancel-deletion page's form POST is CSRF-guarded. |
| `PORTAL_BASE_URL` | falls back to `PUBLIC_BASE_URL` | Used to construct absolute URLs in the cancel-link email. |
| `WEB_APP_BASE_URL` | `https://app.mobaide.com` | Origin the two surviving pages link to for sign-in and account management. |

Both pages' footers always link to `/privacy.html`, which the central server
renders from `central_server/legal/privacy.md`.

## Public delete-info page

`GET /portal/delete-account-info` is reachable without authentication —
**this is the URL submitted to Google Play Console's Data Safety form** and
the Apple App Store's equivalent listing.
