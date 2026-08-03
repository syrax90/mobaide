# Password-reset workflow

How a user who forgets their Mobaide password recovers access. Mirrors the
email-verification flow (`docs/email-verification-workflow.md`) — same OTP
primitive, same HMAC-peppered storage, same atomic CAS in SQLite, same
in-memory rate limiter — extended with per-IP throttling and a
post-reset "your password was changed" notification.

## When reset is available

Only for **email + password** accounts that have already verified their
email. Google-OAuth users have no stored password — they sign back in with
Google. An account that exists but is unverified is treated the same as a
non-existent address (silent success, no email sent) so the endpoint can't
be used to enumerate either the address itself or its verification state.

## The happy path

```
 Android                       Central Server                   SMTP (Mailpit / prod)
 ───────                       ──────────────                   ─────────────────────
 LoginScreen
    │  tap "Forgot password?"
    ▼
 ForgotPasswordScreen
    │  POST /api/auth/forgot-password
    │  {email}
    ├──────────────────────────▶
    │                           per-IP cap check (429 if exhausted)
    │                           load user by email
    │                           per-email cap check (silent throttle)
    │                           generate 6-digit code
    │                           store HMAC-SHA256(secret, f"{user_id}:{code}")
    │                           schedule BackgroundTasks email
    │   200 {status:"ok"}                 (always 200 — anti-enumeration)
    │◀──────────────────────────
    │                           ─── BackgroundTask sends email ──▶
    │
 ResetPasswordScreen
    │  POST /api/auth/reset-password
    │  {email, code, new_password}
    ├──────────────────────────▶
    │                           atomic CAS on password_reset_tokens
    │                           verify new != current  (Argon2id check)
    │                           hash + update users.password_hash
    │                           UPDATE mobile_refresh_tokens
    │                             SET revoked_at = now()
    │                             WHERE agent_id IN (user's agents)
    │                           schedule "password changed" email
    │   200 {status:"ok"}
    │◀──────────────────────────
    │  pop back to LoginScreen
    ▼
 LOGIN  (user signs in with new password)
```

## The unhappy paths

| Scenario | Server response | UX |
|---|---|---|
| Unknown email | `200 {"status":"ok"}` (silent) | Generic "if an account exists, code sent" snackbar. |
| Unverified user | `200 {"status":"ok"}` (silent) | Same generic message — never reveals state. |
| Per-email cap hit | `200 {"status":"ok"}` (silent throttle, no email sent) | Same generic message. |
| Per-IP cap hit | `429 {"detail":{"error":"reset_ip_rate_limited","retry_after_seconds":N}}` plus `Retry-After` header | "Too many requests from this network. Try again in Ns." |
| Wrong code | `400 {"detail":{"error":"reset_code_invalid"}}` | "That code isn't right. Try again." Attempt counter bumps; after `PASSWORD_RESET_MAX_ATTEMPTS` the active token is invalidated. |
| Expired code | `410 {"detail":{"error":"reset_code_expired"}}` | "That code expired. Tap Resend to get a new one." |
| Exhausted attempts | `429 {"detail":{"error":"reset_attempts_exhausted"}}` | "Too many attempts. Request a new code." |
| New = current password | `400 {"detail":{"error":"new_password_same_as_old"}}` | "New password must differ from your current password." |

## Dev setup (local machine)

Identical to the email-verification flow: `docker compose --profile dev up`
starts both `central-server` and `mailpit`. Mailpit's web UI at
<http://localhost:8025> captures both the reset OTP email and the
"password changed" notification email.

The Mailpit container is on profile `dev` only — `docker compose up`
without the flag does NOT start it. Production deployments never bring it up.

## Behaviour knobs (`central_server/.env`)

| Variable | Default | What it does |
|---|---|---|
| `PASSWORD_RESET_TTL_MINUTES` | `30` | How long a reset OTP stays valid. Per NIST SP 800-63-4, ≤60 min is the recommended ceiling for link/OTP tokens. |
| `PASSWORD_RESET_MAX_ATTEMPTS` | `5` | Wrong guesses allowed per code before the token is invalidated. |
| `PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS` | `60` | Minimum gap between reset emails for the same address. |
| `PASSWORD_RESET_EMAIL_HOURLY_CAP` | `3` | Max resends per email in any rolling 1-hour window. |
| `PASSWORD_RESET_IP_HOURLY_CAP` | `10` | Max reset requests from any one source IP per rolling hour. Hits `429`. |

## Production notes

* Swap `SMTP_HOST` to your real provider (Resend / Postmark / SES). Same
  caveats as the verification flow.
* **Don't enable the `dev` compose profile in production.** Mailpit accepts
  all mail on an unauthenticated port — exposing it past your laptop is a
  credentials-in-the-clear hazard.

## Troubleshooting

* **No email in Mailpit** — check the central-server logs for a
  `password_reset.send_failed` event. Connection / auth / TLS errors all
  surface there. The background task swallows the exception so the HTTP
  response still succeeds; the raw code is **never** written to the log
  (only the recipient and the SMTP host).
* **User sees "If an account exists…" but still no email** — the per-email
  cap (`PASSWORD_RESET_EMAIL_HOURLY_CAP`) may have absorbed the request
  silently. This is intentional — alternative would leak which addresses
  are throttled, which leaks which addresses exist.
* **User can still see content from a paired mobile session** after reset
  — the mobile access JWTs are still valid until they expire on their own,
  but the *refresh* token has been revoked, so as soon as the access token
  expires the session is dead. Set `MOBILE_TOKEN_EXPIRE_MINUTES` to a finite
  value if you want a hard cap on the post-reset window.

## Security summary

* Codes are 6-digit CSPRNG (`secrets.randbelow`) — same primitive as the
  verification flow.
* Codes are stored as `HMAC-SHA256(jwt_secret_key, f"{user_id}:{code}")`. A
  DB-only leak does not let an attacker brute-force the hashes offline.
* Consumption is a single atomic CAS (`UPDATE … WHERE consumed_at IS NULL
  AND attempts < max AND expires_at > now`) — no TOCTOU, no double-use.
* Wrong codes bump an `attempts` counter; once it reaches the configured max,
  even the correct code is rejected and the user must request a new one.
* **Anti-enumeration**: `/forgot-password` always returns 200 regardless of
  whether the email exists or is verified. The unknown/unverified branch
  still runs one Argon2id hash so the server-side CPU cost matches the
  happy path. Wall-clock parity over the wire is dominated by network
  jitter — the goal is to remove the in-process timing channel, not the
  total response time.
* **Per-IP rate limit** prevents an attacker from spraying the per-email
  cap across many addresses from a single source.
* **Per-email rate limit** prevents using the endpoint to flood somebody
  else's inbox.
* **Mobile refresh tokens are bulk-revoked** for every agent the user
  owns. A compromised mobile session can't outlast the password rotation
  past the access-token TTL. The same call revokes the user's browser
  session refresh tokens (the opt-in `refreshable: true` sessions the web
  client mints), so a hijacked tab dies on its next rotation too.
* **Password-changed notification email** is fired on every successful
  reset so the legitimate owner sees the event even if their primary
  inbox is the only thing the attacker hasn't compromised.

## Known limitations

* **Dashboard JWTs are stateless.** The codebase has no token-revocation
  table for dashboard sessions, so any active dashboard JWT remains
  valid until `DASHBOARD_TOKEN_EXPIRE_MINUTES` elapses (default 0 = never
  expires). Reset kills the mobile attack surface; dashboard sessions
  require a manual logout. If you want dashboard sessions to die on
  reset, set `DASHBOARD_TOKEN_EXPIRE_MINUTES` to a finite value so they
  rotate naturally.
* **Rate limiters are in-memory.** Same caveat as the verification
  resend limiter (`auth/rate_limit.py:1-10`) — they reset on process
  restart and don't share state across instances. Acceptable for the
  current single-process deployment; flag as future work alongside the
  resend limiter when scaling out.
