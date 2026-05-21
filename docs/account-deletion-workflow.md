# User self-service account deletion

Google Play and Apple App Store both require any app that supports account
creation to ship two delete-account paths: one **in-app** and one
**publicly reachable web URL**. GDPR Article 17 (right to erasure) adds a
parallel obligation. CLA-61 satisfies both with a single 14-day
soft-delete pipeline shared between Android and the new `mobaide.com/portal`
web portal.

## Architecture summary

```
┌────────────────────┐                  ┌────────────────────┐
│  Android (Settings │                  │  Portal (browser)  │
│   → Danger zone)   │                  │  /portal/account   │
└─────────┬──────────┘                  └─────────┬──────────┘
          │ POST /api/auth/me/delete             │  POST /portal/account/delete
          │   {method, password|id_token,nonce}  │
          ▼                                       ▼
       ┌─────────────────────────────────────────┐
       │ AuthService.request_account_deletion    │
       │                                          │
       │  1. verify fresh credential proof        │
       │  2. set users.scheduled_deletion_at      │
       │  3. write deletion_audit (status=pending)│
       │  4. revoke all refresh tokens            │
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
| `password` | Current password | Argon2id verify against `users.password_hash`. |
| `google` | Fresh `id_token` + matching `nonce` | `verify_oauth2_token` (signature + audience), then `iat` within last 5 min, `sub` equals stored `google_sub`, and `nonce` claim matches the client-supplied value. |

This mirrors the Auth0 recommended pattern for destructive-action re-auth.

## Cancellation paths

A user can restore their account at any time within the grace window:

1. **In-app banner** — `GraceBannerSection` (Compose) renders on the first
   authenticated landing screen whenever `UserIdentityStore.getScheduledDeletionAt()`
   returns non-null. Tapping "Cancel scheduled deletion" calls
   `AuthRepository.cancelAccountDeletion` → `POST /api/auth/me/delete/cancel`.
2. **Portal banner** — Same flow via the cookie-authenticated portal,
   posting to `/portal/account/cancel-deletion`.
3. **One-click email link** — The confirmation email carries a single-use
   token (`account_deletion_cancel_tokens` table; 24h TTL by default). Visiting
   `/portal/cancel-deletion?token=…` consumes the token and clears the
   schedule. Works without a logged-in session — important for users who
   have signed out everywhere or lost their device.

Cancelling stamps the existing `deletion_audit` row to `status=cancelled` and
records the cancellation source (`user_initiated` for in-session cancels,
`email_link` for the token path).

## Schema

Migration `0009_account_deletion.py` (Alembic) + the runtime
`PRAGMA user_version=11` block in `central_server/db/database.py`:

* `users.scheduled_deletion_at TEXT NULL` — ISO-8601 timestamp when the
  sweeper is expected to run the cascade purge. Indexed via partial index
  `idx_users_scheduled_deletion`.
* `users.deletion_requested_at TEXT NULL` — when the request was filed.
* `account_deletion_cancel_tokens(token_hash, user_id, created_at, expires_at, consumed_at)`
  — single-use cancel-link tokens, mirrors `password_reset_tokens`.
* `deletion_audit(id, user_id, email_at_request, requested_at, scheduled_for,
  source, status, completed_at, cancellation_reason)` — **no FK** on
  `user_id` so audit rows survive the final cascade purge (GDPR Art. 17 trail).
  `source ∈ {android, portal, email_link}`, `status ∈ {pending, cancelled, completed, failed}`.

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
| `PORTAL_TOKEN_EXPIRE_MINUTES` | `0` (session) | Portal cookie TTL; matches `dashboard_token` convention. |
| `CSRF_SECRET_KEY` | derived from `JWT_SECRET_KEY` | Override only if you need to rotate CSRF independently of the JWT secret. |
| `PRIVACY_POLICY_URL` | unset | When set, portal footer renders the link. Hosted externally (e.g., GitHub Pages). |
| `PORTAL_BASE_URL` | falls back to `PUBLIC_BASE_URL` | Used to construct absolute URLs in the cancel-link email. |

## Public delete-info page

`GET /portal/delete-account-info` is reachable without authentication —
**this is the URL submitted to Google Play Console's Data Safety form** and
the Apple App Store's equivalent listing.
