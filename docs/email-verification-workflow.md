# Email verification workflow

This document describes, at a high level, how Mobaide verifies that a
user actually owns the email address they used to register. Code-level details
live in the source; this page is for people who want to understand the flow,
configure it, or debug a failed delivery.

## When verification kicks in

Verification only runs for **email + password** registration. Google OAuth
sign-ins are already vouched for by Google (via the `email_verified` claim on
the ID token) and bypass the flow. The admin account created by the seeder is
also auto-verified. When a Google or Apple sign-in links to (or verifies) an
existing *unverified* email+password account, the password nobody proved they
were entitled to set, any pending codes and (when linked by email) the sign-up
ad attribution are dropped, so a squatter's registration cannot survive the
real owner's social sign-in.

## The happy path

```
 Android                       Central Server                   SMTP (Mailpit / prod)
 ───────                       ──────────────                   ─────────────────────
 RegisterScreen
    │  POST /api/auth/register
    ├──────────────────────────▶
    │                           create user (unverified)
    │                           generate 6-digit code
    │                           store HMAC-SHA256(secret, code)
    │                           schedule BackgroundTasks email
    │   200 {status:verification_required, access_token:null}
    │◀──────────────────────────
    │                           ─── BackgroundTask sends email ──▶
    │
 VerifyEmailScreen
    │  POST /api/auth/verify-email
    │  {email, code, password?}
    ├──────────────────────────▶
    │                           atomic CAS on email_verification_tokens
    │                           mark user email_verified, verified_at
    │                           (+ install `password` if sent)
    │                           issue JWT
    │   200 {access_token, ...}
    │◀──────────────────────────
    │  session persisted, markUserLoggedIn()
    ▼
 AGENT_LIST
```

## The unhappy paths

| Scenario | What happens |
|---|---|
| Wrong code | Attempt count bumps. After the configured max (default 5) the token is invalidated and the user must `POST /api/auth/resend-verification`. |
| Expired code | 410 `verification_code_expired`. Resend issues a fresh code. |
| Login before verifying | Server returns `403` with body `{"detail":{"error":"verification_required","email":...}}`. Android decodes the detail and routes the user to `VerifyEmailScreen`. |
| Resend too soon | 429 with `Retry-After` header. Android disables the "Resend" button for the returned seconds. |
| Resend beyond hourly cap | 429 with a much longer `Retry-After`. |
| Duplicate email on register | 409 as before. |
| Register password too short or long | 422 unless the password is 8-128 characters, same rule as reset. Login has no length rule, so older short passwords still sign in. |
| Re-register an abandoned signup | An unverified, password-only account (no Google/Apple link) whose code window is over (the TTL has elapsed, consumed codes included) is taken over by `POST /api/auth/register`: the password and sign-up ad attribution are replaced, a fresh code is sent, and the answer is `verification_required` like a first signup. While a code is still within its TTL, and for verified or Google/Apple-linked accounts, register still answers 409. The takeover uses the per-email resend limiter, so it can answer 429 `resend_rate_limited` with the same body and `Retry-After` as `/resend-verification`. |

`POST /api/auth/verify-email` accepts an optional `password`. After a correct
code it is hashed and installed atomically with `email_verified` (skipped for
Google/Apple-linked accounts), so if someone re-registered the address in
between, the password of whoever holds the code wins. Omitted, the stored
password is kept. Android, iOS and web keep the password typed at register (or
at a login answered `verification_required`) in memory only and send it with
the code. Clients that do not send it (older builds, or a web reload / process
death on the code screen) verify whatever password is stored.

The in-memory rate limiter is per-normalised-email (`email.lower().trim()`),
with two thresholds: a short cooldown between consecutive sends and a
rolling-hour cap on the total number of sends. The limiter uses
`time.monotonic()`, which means a process restart resets all counters — that
is acceptable for the single-instance central server but documented here so
it is not forgotten if the server ever scales out.

## Dev setup (local machine)

1. `docker compose --profile dev up` from the repo root.
   * Starts `central-server` **and** `mailpit`. The `dev` profile is the only
     way to bring up `mailpit` — production deployments using plain
     `docker compose up` never start it.
   * The agent runs natively (not in Docker) — start it separately via
     `start-all.ps1`, or from the agent's conda env:
     `cd mobaide_agent && python -m mobaide`.
2. Mailpit's web UI: <http://localhost:8025>. Every email the server sends
   shows up here in real time, with both the rendered HTML and raw source.
3. SMTP settings are already pointed at Mailpit in `central_server/.env.example`:

   ```env
   SMTP_HOST=mailpit
   SMTP_PORT=1025
   SMTP_USE_TLS=false
   SMTP_STARTTLS=false
   SMTP_FROM_ADDRESS=no-reply@mobaide.local
   ```

4. Register a new user in the Android app. The `VerifyEmailScreen` shows up;
   grab the 6-digit code from Mailpit, paste it, and you land on `AGENT_LIST`.

## Behaviour knobs (`central_server/.env`)

| Variable | Default | What it does |
|---|---|---|
| `EMAIL_VERIFICATION_REQUIRED` | `true` | Master switch. Set `false` to skip the OTP flow and hand out a JWT on register (useful for CI and operators without SMTP). |
| `EMAIL_VERIFICATION_TTL_MINUTES` | `15` | How long a code stays valid. |
| `EMAIL_VERIFICATION_MAX_ATTEMPTS` | `5` | Wrong guesses allowed per code before the token is invalidated. |
| `EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS` | `60` | Minimum gap between resends for the same email. |
| `EMAIL_VERIFICATION_RESEND_HOURLY_CAP` | `3` | Max resends per email in any rolling 1-hour window. |
| `SMTP_*` | see `.env.example` | SMTP connection. Swap to Resend / Postmark / SES in prod. |

## Disabling for CI

Set `EMAIL_VERIFICATION_REQUIRED=false`. Registration skips the token and email
entirely, marks the user verified on creation, and returns a JWT — same shape
as before this feature existed. The server-side test suite takes advantage of
this (`tests/conftest.py` sets the flag), so every pre-existing test keeps
passing. The dedicated `tests/test_email_verification.py` flips the flag back
on for its own fixtures.

For end-to-end tests that need the verification flow to actually run — so the
harness can read the OTP back rather than skip it — set `MOBAIDE_ENV=test`
instead. That swaps the real SMTP `EmailSender` for an in-process capturing
sink: no SMTP connection is ever attempted, and each message is recorded on
`app.state.email_sender.messages` for assertions. `MOBAIDE_ENV` defaults to
`production`; only the exact value `test` opts in, so leaving it unset (or any
other value) keeps the real-SMTP behaviour described above.

## Production notes

* Swap `SMTP_HOST` to your real provider (Resend / Postmark / SES). Keep the
  `SMTP_USE_TLS` / `SMTP_STARTTLS` flags in line with the provider's docs.
* **Don't enable the `dev` compose profile in production.** Mailpit silently
  accepts all mail on an unauthenticated port — exposing it past your laptop
  is a credentials-in-the-clear hazard.
* `SMTP_FROM_DOMAIN` is kept separate from `SMTP_FROM_ADDRESS` so DKIM/SPF
  tooling that needs the bare domain (for DNS records) can read it directly.

## Troubleshooting

* **No email in Mailpit** — check the central-server logs for an
  `email_verification.send_failed` event. Connection errors, authentication
  errors, or TLS mismatches all show up there. The background task swallows
  the exception so the HTTP response still succeeds; the raw code is **never**
  written to the log (only the recipient and the SMTP host).
* **User keeps getting 403 "verification_required"** — they haven't verified
  yet. Look at the `users` row: `email_verified` should be `0` and there
  should be a live token in `email_verification_tokens` for them.
* **`MP_SMTP_AUTH_ALLOW_INSECURE=1`** — required on the Mailpit side because
  we send plaintext AUTH over plaintext SMTP in dev. Absolutely never put this
  setting on a reachable production container.

## Security summary

* Codes are 6-digit CSPRNG (`secrets.randbelow`) — ~1 M keyspace.
* Codes are stored as `HMAC-SHA256(jwt_secret_key, f"{user_id}:{code}")`. The
  JWT secret doubles as a server-side pepper, so a DB-only leak does not
  let an attacker brute-force the hashes offline.
* Consumption is a single atomic CAS (`UPDATE ... WHERE consumed_at IS NULL
  AND attempts < max AND expires_at > now`) — no TOCTOU, no double-use.
* Wrong codes bump an `attempts` counter; once it reaches the configured max,
  even the correct code is rejected and the user must request a new one.
* Raw codes appear only in the rendered email. Nothing in the logs, nothing
  in the database, nothing in error responses.
* Rate limiting is per normalised email, which prevents an attacker from
  spamming other users' inboxes via the resend endpoint. Per-IP limiting is
  not yet applied to the resend endpoint; it is implemented for the
  password-reset flow (see `docs/password-reset-workflow.md`).
