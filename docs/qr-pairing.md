# Agent QR pairing (CLA-50)

This page documents the agent-initiated QR pairing flow that replaces the
legacy `mobaide-agent pair --id ... --token ...` invocation.

## Overview

A fresh `mobaide_agent` install used to require the operator to (a) read
`agent_id` / `agent_token` from the Android app's credentials screen, (b) SSH
to the agent host, and (c) run `mobaide-agent pair --id <ID> --token <TOKEN>`.
Tokens leaked into shell history, copy-paste typos were common, and the
user drove both ends manually.

The QR flow flips the direction: the agent renders a QR code in its
terminal, the operator scans it from the Mobaide mobile app, and the
agent receives its credentials directly over a server-mediated WebSocket.
No token ever touches the mobile UI; no token ever appears on the agent
host's command line.

## Sequence

```
agent (mobaide-agent)        central_server                 mobile (Mobaide app)
        │                          │                                │
        │ POST /api/pairing/initiate                                 │
        │  {hostname, os, current_agent_id?}                         │
        │ ─────────────────────────►                                 │
        │                          │ INSERT pairing_tokens           │
        │                          │  (agent_id NULL,                │
        │                          │   agent_metadata=...)           │
        │ ◄── 200 {nonce, expires_at, trust_key_fp, relay_url,       │
        │           qr_payload}                                      │
        │                          │                                 │
        │ render QR(MOBA1|...)     │                                 │
        │ derive ws_url locally    │                                 │
        │   from RELAY_SERVER_URL  │                                 │
        │                          │                                 │
        │ open WS /ws/pairing/{nonce}                                │
        │ ─────────────────────────►                                 │
        │ ◄── {"type":"ready"}                                       │
        │                          │                                 │
        │                          │ ◄── scan QR ────────────────────┤
        │                          │                                 │
        │                          │ ◄── POST /api/pairing/claim     │
        │                          │      {nonce, fp, mobile_device_id}
        │                          │ CAS-consume nonce               │
        │                          │ if metadata.current_agent_id:   │
        │                          │   re-pair (rotate or 409)       │
        │                          │ else:                           │
        │                          │   register_agent()              │
        │                          │ sign payload (Ed25519)          │
        │                          │ ── 200 {agent_id, name,  ───────►│
        │                          │        ws_delivered?}            │ ← "Waiting for agent…"
        │ ◄── {agent_id, agent_token, relay_url, signature_b64}      │   (push + bounded resync)
        │ verify Ed25519 signature                                   │
        │ atomic .env write        │                                 │
        │ close WS                 │ clear claimed_token_raw         │
        │ _start_relay_in_process  │ agent.status_changed ──────────►│ ← "Paired"
        │ (relay client comes up   │  (online=true via               │
        │  in ~1-2 s, same proc)   │   UserEventBus push)            │
```

## QR payload schema

Versioned, compact, no secrets:

```
MOBA1|{relay_url}|{nonce}|{trust_key_fp}|{exp_unix}
```

* `MOBA1` — schema version. Mobile clients reject unknown versions.
* `relay_url` — public base URL of the central server.
* `nonce` — 192-bit `secrets.token_urlsafe(24)`. Single-use.
* `trust_key_fp` — SHA-256 hex of the relay's manifest-signing public
  key (the same Ed25519 key the agent's auto-updater pins for manifest
  verification, exposed at `GET /api/v1/agent/trust_key`).
* `exp_unix` — absolute expiry as Unix seconds. Default TTL: 120s
  (`PAIRING_QR_TOKEN_TTL_SECONDS`); production sets 600s so a pairing
  link survives the human hop to a browser on another device (the nonce
  stays single-use, so the wider window adds no replay exposure).

The token is never carried in the QR. The server mints it server-side
and pushes it to the agent over the pairing WebSocket.

The parse contract is pinned across all four implementations by
`tests/protocol_fixtures/pairing/qr_payload.json`: exactly five fields,
`MOBA1` case-sensitive, `relay_url` / `nonce` / `trust_key_fp` non-blank
once trimmed but stored **verbatim**, and `exp_unix` a signed 64-bit
integer parse that rejects surrounding whitespace, digit separators and
overflow. Expiry is deliberately **not** range-checked in the parser — a
negative `exp_unix` parses, and the staleness decision belongs to each
client's pairing state machine, which needs the value to say "expired N
minutes ago" rather than "malformed". The Android arm of the golden is
tracked in CLA-235; the `central_server` builder, still an inline f-string
rather than a named function, in CLA-236.

### Printed pairing link (webcam-less pairing)

Under the QR, the agent also prints the payload on one unbroken line and a
browser link, in that order — pasteable artifact first, following
`gh auth login`:

```
[mobaide-agent] Pairing code (copy this entire line):
MOBA1|https://api.mobaide.com|<nonce>|<fp>|<exp>
[mobaide-agent] Or open this link in a browser:
https://app.mobaide.com/pair#<base64url(payload)>
```

Both lines come from `pairing_qr.write_pairing_hints`, which all three
print sites call (`pair-qr`, `show-qr`, and the pairing monitor — the last
inside its must-not-raise `try`). Two constraints hold them together:

* **Never wrapped, never prefixed.** A terminal's soft wrap is invisible,
  but a copied soft-wrapped line carries embedded newlines. The web paste
  box strips all whitespace before parsing so it survives anyway, but the
  printer must not manufacture the problem.
* **Never raises.** The labels are ASCII, but `relay_url` is the operator's
  `PUBLIC_BASE_URL` and may be an internationalized host, so the payload and
  the link are probed against the stream's codec first and replaced with an
  ASCII notice when they do not fit. A Windows service redirects stdout to a
  `cp437`/`cp1252` log file: a `UnicodeEncodeError` from the monitor's render
  tears down the pairing WebSocket before `wait_for_claim` — the incident
  `_stream_supports_block_glyphs` documents — and the same error on the
  `show-qr` path fails the installer, which treats a non-zero exit as fatal.

The payload is base64url-encoded without padding in the fragment: a raw `|`
is not a legal URL character and terminal linkifiers stop at it, and a
fragment never reaches the relay's access logs. The web client's decoder
(`web/src/protocol/pairingPayload.ts`) is padding-tolerant.

The link's origin is **not** derivable from `relay_url`: that field is the
relay's `PUBLIC_BASE_URL` (`api.mobaide.com` in production) while the SPA
is a separate host. It is therefore a fixed default with an env override:

| Variable | Default | Effect |
|---|---|---|
| `MOBAIDE_AGENT_PAIR_WEB_ORIGIN` | `https://app.mobaide.com` | Origin of the web client the printed link points at. Self-hosted and development deployments set it; a value that is not ASCII `http(s)` is ignored rather than printed. |

Because the agent fleet auto-updates on a 60-minute poll, the printed link
only converges after the release ships — the web paste box and manual
`POST /api/agents` are the day-one paths.

### Pairing WS URL is agent-derived

The `/api/pairing/initiate` response intentionally omits a `ws_url`
field. The agent computes the URL it dials from its own
`RELAY_SERVER_URL` plus the response `nonce` (see
`mobaide_agent/mobaide/agent/pairing_monitor._pairing_ws_url`):

* `wss://host[:port]` → `wss://host[:port]/ws/pairing/{nonce}`
* `https://host` → `wss://host/ws/pairing/{nonce}` (scheme upgraded)
* `ws://` / `http://` are refused unless
  `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1` (loopback escape
  hatch, mirroring the trust-key fetch).

This makes the agent the single source of truth for the URL it dials
and removes a class of bugs where a deployment-skewed central_server
returned an `https://` ws_url that the Python `websockets` client
hard-rejected (`scheme isn't ws or wss`).

### `relay_url` is persisted at initiate time

The `relay_url` field in the QR payload **and** in every credential
push is read from `pairing_tokens.relay_url`, which the
`/api/pairing/initiate` handler writes once at nonce-creation time.
Both the claim-handler dispatch path and the WS-recovery branch use
the persisted column. Without this single source of truth the two
branches recomputed `relay_url` independently from different request
contexts (`request.base_url` for HTTP, `websocket.url.scheme` for
WS), which can disagree behind a reverse proxy that does not forward
`X-Forwarded-Proto` to uvicorn — the agent then rejected its own
credentials on the resulting strict-equality mismatch.

### `pending_pairing.json` is written AFTER the WS is open

`run_pairing_monitor` enters `async with PairingWsClient.connect(...)`
*before* saving `pending_pairing.json`. The on-disk file is the signal
that `install.ps1`'s `Show-PairingQrFromService` polls before rendering
the QR; pinning that signal to the WS-ready handshake ensures a fast
mobile claim cannot arrive while the agent's WS waiter is still
unregistered (which would force the server's recovery branch and
expose the deployment-skew bug described above).

### `ws_delivered` in the claim response

`POST /api/pairing/claim` returns an optional `ws_delivered: bool | null`
field (added in this fix; `null` from older server builds). When `true` the
server dispatched the credential push directly to the agent's open pairing
WebSocket; when `false` the push was staged in the recovery slot for the
agent's next WS reconnect to consume.

All three clients use this only to refine the copy shown if the 60-second
wait times out — a "not online yet" state, not an error, since the claim
already succeeded — it is **not** treated as confirmation that
pairing succeeded. The authoritative "paired" signal remains the
`agent.status_changed` event carrying `online=true` delivered via the
`UserEventBus`. The field is forward-compatible: older mobile clients that
ignore unknown JSON fields are unaffected.

## Trust-On-First-Scan (TOFS) on the mobile

The first scan against a given `relay_url` pins that relay's
`trust_key_fp` locally on the device (`mobaide_trust_pins` DataStore).
Subsequent scans against the same `relay_url` MUST present the same
fingerprint or the claim is hard-aborted with a "trust key changed —
possible MITM" error.

Recovery for a legitimately rotated relay key: clear the app's data
(Android Settings → Apps → Mobaide → Storage → Clear data) so the
fingerprint is unpinned. (A future settings-screen action may expose
this without a full app-data wipe.)

### TOFS in a browser is weaker, deliberately

The web client pins the same way, in `localStorage` under
`mobaide-trust-pins`, with the same key normalization (trim, strip every
trailing slash, lowercase) and the same hard refusal with no override. The
difference is durability: Safari caps script-written storage at 7 days of
no interaction, so an infrequently used relay silently degrades from
trust-on-first-scan to trust-on-most-recent-scan. The same wipe
regenerates the browser's device UUID, which under the
last-login-wins registry above orphans a `mobile_devices` row. Recovery
is clearing the site's data rather than the app's.

### The account-level pin, and what it does not do

Every account records the relay signing fingerprint it first paired against,
in `users.trust_key_fp`, and every later claim from any client is refused when
that fingerprint no longer matches the relay's current key. A browser whose
`localStorage` was wiped therefore cannot silently re-trust a rotated key: the
server still remembers, and the refusal happens server-side.

Be precise about what that buys. A claim whose `trust_key_fp` disagrees with
the relay's current key is *already* a 400, so the account pin only ever fires
when the relay's own signing key changed. It is a **rotation tripwire, not an
MITM detector** — the relay both stores the pin and serves the value it is
compared against, so a compromised relay simply returns a matching pair. The
local pin remains the only thing that records what the user personally
vouched for, which is why `GET /api/pairing/trust-pin` is read-only and no
client writes its response into a local trust store: an unattended write is a
permanent lockout waiting for a bad value.

The refusal is HTTP 409 with `detail.error = "trust_key_pin_mismatch"` — a
structured detail rather than prose, because 409 already carries the "agent
belongs to another account" refusal and the two need different copy.

## Re-pair semantics

The agent's CLI captures its current `RELAY_AGENT_ID` (if any) into the
initiate request's `agent_metadata.current_agent_id` field. The claim
handler then chooses one of three paths:

* **No current agent id** (`current_agent_id` absent) — the server calls
  `db.register_agent()` to create a new agent row (first-pair).
* **Unknown / deleted agent id** (`current_agent_id` present but no
  matching row) — the claim returns **HTTP 404**. The agent host must
  first-pair instead: run `mobaide-agent re-pair --fresh` to drop the dead
  id, then rescan. This happens when the agent was removed from the account
  or the relay database was redeployed — plain `re-pair` keeps the id and
  would 404 on every cycle.
* **Same owner** — `db.is_agent_owner(current_agent_id, claimer)`
  returns true. The server calls `db.reregister_agent(current_agent_id)`
  to rotate the token. The mobile UI sees the same agent row reappear
  with a fresh credential.
* **Different owner** — `POST /api/pairing/claim` returns **HTTP 409**.
  Cross-account agent transfer is not permitted via QR re-pair. To move
  a host to a different account, the current owner must remove the agent
  through the app first; the agent host then first-pairs as a new agent
  under the new account.

Authorization model: possession of shell access on the agent host alone
is no longer sufficient to claim ownership from another account. The
current owning account must explicitly release the agent before a
different account can acquire it.

### Mobile device registry

The `mobile_device_id` registry (the `mobile_devices` table) is
**last-login-wins**, unlike agent ownership above. A claim or token mint
from an authenticated account always moves the device row to the caller:
the caller proves account access (user JWT) plus device possession (the
per-install random UUID is never exposed to other users). The handover
revokes every live refresh token previously issued for that device and
clears its push registration, so the prior account can neither refresh
its mobile sessions nor keep receiving notifications on hardware it no
longer uses. This is what lets one phone switch between accounts (or a
factory-reset/hand-me-down device re-register) without any manual
release step — agents themselves still require the explicit release
described above.

## Operator runbook

### First install

The install scripts (`deploy/install.sh`, `deploy/install.ps1`) start
the supervisor, wait for the agent to report healthy and for the service
to write the pairing nonce file, then render the pairing QR. This
ordering guarantees the service is ready to accept the incoming pairing
claim before the operator scans — eliminating the boot-gap race that
left `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` empty. SSH-attached
operators see the QR once the service is up and can scan it from the
Mobaide mobile app to complete pairing.

If the operator misses the QR (e.g., SSH disconnected before scanning),
the agent's boot-time autorun re-renders it. Run on the agent host:

```bash
mobaide-agent show-qr
```

This re-renders the most-recent pending QR; if the cached nonce has
expired, it initiates a fresh one.

The installers pass `--require-pending` to `show-qr` so that the
installer itself never initiates a fresh nonce — the running service
is the single initiator. This prevents a race on `/ws/pairing/{nonce}`
if the installer and the boot autorun both tried to open a pairing WS
simultaneously.

### Re-pair to a different account

Direct cross-account transfer via QR is no longer permitted; the claim
returns HTTP 409 if the scanned agent is already owned by a different
account.

To move a host to a different account:

1. **Remove the agent** from the current owner's account (app → agent
   settings → Remove agent).
2. On the agent host, run:

   ```bash
   mobaide-agent re-pair --fresh
   ```

   `--fresh` blanks BOTH `RELAY_AGENT_ID` and `RELAY_AGENT_TOKEN` in `.env`
   (plain `re-pair` keeps the id) and restarts the supervisor, surfacing a
   fresh QR via the boot autorun. The id **must** be cleared here: after
   step 1 the relay no longer has this agent, so carrying the old id would
   make the claim fail with HTTP 404 "Re-pair target agent does not exist".
3. Scan the QR from the new account's mobile app. Because no agent ID is
   carried in the initiate request, the server registers a fresh agent
   row under the new owner.

### Re-pair after the agent was removed or the relay was redeployed

If pairing fails with **HTTP 404** `Re-pair target agent does not exist on
this relay`, the agent's `RELAY_AGENT_ID` points at an agent the relay no
longer knows — typically because the agent was removed from the account or
the relay database was redeployed. Plain `re-pair` keeps that dead id and
re-sends it on every cycle, so it 404s forever. Recover with a true
first-pair:

```bash
mobaide-agent re-pair --fresh
```

This clears `RELAY_AGENT_ID` as well as the token (and the cached pending
nonce), so the next claim registers a brand-new agent. Then rescan the QR.

### Skip the install-time QR

Set `MOBAIDE_AGENT_SKIP_INSTALL_QR=1` before running the installer (or
`$env:MOBAIDE_AGENT_SKIP_INSTALL_QR = "1"` on PowerShell) to suppress
the install-time render. The boot-time autorun still produces a QR on
the next service start.

## Device-code pairing (the reversed direction)

The QR direction needs a camera pointed at the agent's terminal. That fails
exactly where desktops live: an SSH jump host, a VM console, a machine whose
screen the phone cannot see and whose clipboard does not reach it. The
device-code direction inverts the flow so nothing has to travel *off* the
agent host — the operator only has to type eight characters *into* it.

```
client (web / phone)        central_server                 agent (mobaide-agent)
        │                          │                                │
        │ POST /api/pairing/device-code  (auth)                     │
        │ ─────────────────────────►                                │
        │                          │ INSERT pairing_device_codes    │
        │                          │  (code_hash = HMAC(pepper))    │
        │                          │  supersede this account's      │
        │                          │  earlier unredeemed codes      │
        │ ◄── 200 {request_id, code "WDJB-MJHT", expires_at,        │
        │           verification_command}                            │
        │                          │                                 │
        │ show "mobaide-agent pair WDJB-MJHT"                        │
        │                          │ ◄── POST …/device-code/redeem ──┤ (anon)
        │                          │      {code, redeemer_id,        │
        │                          │       hostname, os,             │
        │                          │       current_agent_id?}        │
        │                          │ CAS-redeem + INSERT             │
        │                          │   pairing_tokens (nonce)        │
        │                          │ ── 200 {nonce, trust_key_fp, ──►│
        │                          │        relay_url, account_hint} │
        │                          │                                 │ ← "Pair 'host' with
        │                          │                                 │    account p***l@g***.com?"
        │ GET …/device-code/{id} (poll)                              │
        │ ◄── {status: "pending"}   │  (a machine has it, but the     │
        │                          │   operator has not answered)     │
        │                          │ ◄── POST …/device-code/accept ──┤ y
        │                          │      {code, redeemer_id}        │
        │                          │ ◄── open WS /ws/pairing/{nonce} ┤
        │ GET …/device-code/{id} (poll)                              │
        │ ◄── {status: "redeemed", hostname, os, blocked_reason?}    │
        │ "Is this your machine pavel-mbp?"                          │
        │ POST …/device-code/{id}/confirm                            │
        │ ─────────────────────────►                                 │
        │                          │ _complete_pairing() — the same  │
        │                          │ path a QR claim takes           │
        │ ◄── 200 {agent_id, name, ws_delivered?}                    │
        │                          │ ── signed credential push ─────►│
```

Everything after the confirm is byte-identical to the QR flow: the same
`pairing_tokens` row, the same Ed25519 `PairingPushPayload`, the same
`claimed_token_raw` recovery slot, the same single-flight CAS. Only the front
half differs, and both halves finish through `_complete_pairing`.

### Re-pairing drops the old tunnel

When the claim names an agent the account already owns, `_complete_pairing`
rotates that agent's token — and then disconnects it, in *both* directions.
The rotated token no longer authenticates, so leaving the socket up kept an
agent connected on revoked credentials until its process happened to restart.

It also made the clients lie. "Waiting for it to come online" reads
`RoutingTable`, which is in-memory tunnel presence and knows nothing about the
token swap, so re-pairing an online agent reported success the instant it was
asked — whether or not the redeeming host ever received, wrote, or connected
with the new credentials. Dropping the tunnel makes that watcher wait for the
connection it is actually about.

Closed with 4012 (`WS_CLOSE_AGENT_TRANSIENT_OFFLINE`), never 4010: the agent is
expected back, and the paired mobile sockets that go down with it must keep
reconnecting instead of treating this as a destructive revocation.

### The code

Eight characters over the 20-consonant alphabet `BCDFGHJKLMNPQRSTVWXZ`
(RFC 8628 §6.1): 20⁸ ≈ 2³⁴·⁶, no vowels so it cannot spell a word, and no
confusable pair like `0`/`O` or `1`/`I`/`l`. Input is normalised by filtering
to that alphabet, so hyphens, spaces and a pasted trailing newline all vanish.

The code is stored as a **peppered HMAC** (`hash_device_code`), not a plain
digest: 34.6 bits is small enough to brute-force from a database dump, so the
relay's JWT secret is folded in the way `AuthService._hash_code` does for the
6-digit OTP. Unlike the OTP there is no `user_id` in the HMAC input — the
redeeming agent is anonymous and looks the row up by code alone.

There is deliberately **no per-code attempt counter**: a wrong guess HMACs to a
row that does not exist, so there is nothing to increment. The guessing defence
is the code's entropy plus at most one live code per account, a short TTL,
single use, a per-IP limiter on the redeem endpoint, and a global
constant-keyed circuit breaker for the distributed case the per-IP bucket
cannot see (it collapses to one shared bucket behind a proxy that does not
forward the client address — see `FORWARDED_ALLOW_IPS` in
`docs/deployment.md`).

The global breaker is charged on **failed** redeems only, and checked with
`ResendRateLimiter.peek` so the check itself costs nothing. Charging every
redeem would turn a guessing defence into a deployment-wide throughput
ceiling — with a 30/hr per-IP allowance, roughly twenty addresses would be
enough to deny pairing to everyone.

### Why the agent asks too

Reversing the mint direction reverses the phishing attack, and the browser's
"Is this your machine?" screen does not cover the new one. In the QR flow the
code proves presence at the machine being paired. Here an attacker can mint a
code on *their* account and talk someone into running
`mobaide-agent pair <code>` — at which point the victim's machine becomes an
agent the **attacker** owns, and the attacker is the one clicking the browser
confirmation. The `current_agent_id` ownership guard does not help: it only
protects hosts that are already paired, which is not the install-time case
this feature targets.

So the redeem response carries a masked `account_hint`, and the CLI requires an
interactive `y/N` naming that account and this hostname before it writes
anything. Non-interactive callers must pass `--yes`, and an already-paired host
refuses outright without `--force`. Declining burns the code — deliberately: an
attacker's code is then spent and cannot be redeemed again.

That prompt only controls anything if the relay waits for its answer, which is
what `POST /api/pairing/device-code/accept` is for. Redeeming alone leaves the
request polling as `pending`; the status flips to `redeemed` — and the confirm
endpoint stops answering 409 — only once the machine has reported that its
operator said yes. It is anonymous like the redeem and authorised the same way
(the code plus the `redeemer_id` that redeemed it), idempotent, and charged
against the same two rate-limit buckets.

Without it the account holder could confirm while the terminal prompt sat
unanswered, and `_complete_pairing` would run regardless of the eventual
answer: a declined first pair left an orphan agent on the confirming account,
and a declined `pair --force` left the working host's token already rotated —
the operator answered "no" and lost the agent anyway.

Conversely, the browser-side confirmation is real but limited. `hostname` and
`os` come from the anonymous redeem body and are attacker-settable, exactly as
`current_agent_id` is in the QR flow. They let a user recognise their own
machine; they authenticate nothing.

One case the confirm screen must not get wrong: the redeem body also carries
`current_agent_id`, and when it names an agent the confirming account *already
owns*, approving does not add a machine — it **moves** that agent, rotating its
token onto the redeeming host and knocking the real one offline. The status
response therefore returns `repair_agent_name` for that case, and all three
clients swap the heading from "Is this your machine?" to "Move ‘<name>’ to this
machine?" with an explicit warning. Asking the first-pair question there would
be asking about the wrong thing entirely.

### Retries, and what `redeemer_id` does and does not buy

The CLI generates a `redeemer_id`, persists it to
`<prefix>/run/pending_device_code.json` *before* the request, and re-sends it
on a retry. The relay recognises a matching retry from the existing row instead
of rejecting it as a second machine racing the code.

Be precise about the limit: **the raw nonce is never stored server-side**, so a
replayed redeem comes back with `nonce: null`. The retry therefore only
continues if the agent still holds its own copy — which the pending file gives
it, rewritten with the nonce as soon as the response lands, so a Ctrl-C during
the wait resumes rather than restarts. A redeem whose *response* was lost in
transit is not recoverable: the CLI exits 8 and the operator mints a new code.

A failed pairing WebSocket is the one failure that keeps the file rather than
clearing it. The relay parks the claimed credentials in
`pairing_tokens.claimed_token_raw` for a reconnect on the same nonce, and that
nonce exists nowhere else — so a dropped connection exits 6 and prints the
command to re-run, which reconnects and takes delivery through the recovery
branch. Clearing it there would redeem under a fresh `redeemer_id` on the next
attempt and burn the code. A record whose nonce has since expired is dropped by
`load_pending_device_code`, so a stale file cannot strand the next run.

The record is keyed by a digest of the code, so typing a *different* code
starts a fresh redemption rather than silently waiting out the earlier one. It
is also a **separate file** from `pending_pairing.json` on purpose: on a fresh
install the agent service is already running its own QR pairing cycle, and a
shared file would overwrite the nonce that cycle is waiting on.

### Cancelling

`DELETE /api/pairing/device-code/{request_id}` marks the code canceled and
expires its nonce in the same transaction.

A cancel racing the *redeem* is settled the same way. Minting the nonce is a
separate statement from the redeem CAS, so a DELETE can land between them, read
a still-NULL `pairing_nonce_hash`, and expire nothing; the update that links the
nonce is therefore guarded on `canceled_at IS NULL` and rolls the nonce back
when it loses. Otherwise a canceled request came away holding a live nonce, and
the agent waited out the whole confirm TTL for a confirm that could only 410.

Confirm and cancel are mutually exclusive, and the code row — not the nonce —
is what decides. `confirm` reserves the request (`confirmed_at`, CAS'd on
`confirmed_at IS NULL AND canceled_at IS NULL`) *before* it issues any
credentials, and cancel's update carries the same guard; both are single
statements over the one shared connection, so exactly one wins. A confirm the
ownership guard or the trust pin then refuses calls `reopen_device_code`, which
hands the reservation back so the request stays confirmable. Reserving
afterwards instead — as this endpoint originally did — left a window where a
DELETE arriving mid-pairing answered 204 while the confirm went on to hand the
machine an agent, telling both clients they had won.

It does **not** promptly stop an agent that has already connected. The WS
handler reads `expires_at` once and then blocks in a single
`asyncio.wait_for(queue.get(), timeout=…)`, so a cancel issued after the agent
dialled in leaves that CLI waiting until the nonce's original deadline
(`PAIRING_DEVICE_CODE_CONFIRM_TTL_SECONDS`, 300 s by default) and then exiting
6. The operator can Ctrl-C; the deadline is printed when the wait starts.

No "denied" frame is pushed: `pairing_ws._parse_and_verify` rejects any payload
whose `type` is not `claim_completed`, so older agents would surface it as an
opaque transport error rather than a clean refusal.

## Server-side configuration

The server **must** hold both halves of the manifest signing keypair
to operate the QR pairing flow:

| Variable | Required for | Behaviour when unset |
|---|---|---|
| `MANIFEST_SIGNING_PUBLIC_KEY` | trust-key endpoint, manifest verification, QR initiate | `GET /api/v1/agent/trust_key` returns 503; QR initiate returns 503 |
| `MANIFEST_SIGNING_PRIVATE_KEY` | QR initiate, claim push signing | `POST /api/pairing/initiate` returns 503 |
| `PAIRING_QR_TOKEN_TTL_SECONDS` | nonce TTL (default `120`; prod sets `600` for cross-device pairing links) | uses default |
| `PUBLIC_BASE_URL` | QR `relay_url` field | falls back to request `Host` header |
| `PAIRING_DEVICE_CODE_TTL_SECONDS` | how long a device code stays redeemable (default `600`) | uses default |
| `PAIRING_DEVICE_CODE_CONFIRM_TTL_SECONDS` | the redeem-to-confirm window, and the TTL of the nonce minted at redeem (default `300`) | uses default |
| `PAIRING_DEVICE_CODE_REDEEM_IP_HOURLY_CAP` | per-IP redeem attempts per hour (default `30`) | uses default |
| `PAIRING_DEVICE_CODE_REDEEM_IP_COOLDOWN_SECONDS` | minimum gap between redeems from one IP (default `1`) | uses default |
| `PAIRING_DEVICE_CODE_REDEEM_GLOBAL_HOURLY_CAP` | **failed** redeems per hour across all callers before the breaker trips (default `600`) | uses default |
| `PAIRING_DEVICE_CODE_MINT_COOLDOWN_SECONDS` | minimum gap between mints for one account (default `2`; no hourly cap by design — a new code supersedes the old one, so a page reload re-mints) | uses default |
| `TRUST_KEY_PIN_ENFORCED` | refuse claims whose relay key differs from the account pin (default `true`) | uses default |

### Trust-key rotation runbook

Rotating `MANIFEST_SIGNING_*` invalidates every account's pin at once, so the
rotation itself would lock out every existing account. The intended sequence:

1. Set `TRUST_KEY_PIN_ENFORCED=false` and restart the relay. Mismatching pins
   are then cleared as accounts pair, rather than refused.
2. Rotate the keypair; have each agent host re-run `mobaide-agent
   fetch-trust-key` (the agent's own pin is separate and fail-closed).
3. Once accounts have re-paired, set `TRUST_KEY_PIN_ENFORCED=true` again.

An account that currently owns **no** agents re-pins automatically even while
enforcement is on — the pin protects nothing in that state, so "remove every
agent, then pair again" is the self-service recovery for a single account.

> **Note on private-key residency.** The manifest signing private key
> was previously held only by the release pipeline. CLA-50 expands the
> trust surface by placing it on the relay so the server can sign
> pairing pushes. If your threat model rejects this, a future change
> can introduce a separate `PAIRING_SIGNING_PRIVATE_KEY` keypair plus a
> parallel pinned file `<prefix>/trust/pairing_trust.pub` on the agent.

## Multi-worker deployments

The pairing-WS waiter map is in-memory and per-process
(`PairingWsRegistry`, keyed by the SHA-256 of the nonce). Multi-worker
deployments (gunicorn `-w N`) break the flow: a claim can land on a different
worker than the one holding the agent's WS, and the push is silently dropped.

The device-code direction makes this worse in kind, not degree. A QR claim's
dropped push is recovered by the agent's next WS reconnect, which retries on a
loop; a device-code confirm is a **browser** request with no retry loop behind
it, so the user sees "waiting for the agent" until the timeout. Mitigations:

* Single uvicorn process per host (the default deployment model — see
  `docs/deployment.md`).
* If multi-worker is required, replace the in-memory registry with a
  shared backend: Redis pub/sub, or DB polling against the
  `claimed_token_raw` column (the credentials are persisted there
  briefly for exactly this reason).

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `pair-qr` or `fetch-trust-key` returns HTTP 403 (Cloudflare error 1010) | Cloudflare Bot Fight Mode blocked the default `Python-urllib` User-Agent | Agent v0.1.3+ sends `User-Agent: mobaide-agent/<version>`, which passes default Bot Fight Mode. If using Super Bot Fight Mode with JA3/JA4 "Block", add a Cloudflare WAF Skip rule: `(http.user_agent wildcard "mobaide-agent/*") and (http.request.uri.path starts_with "/api/")`. Older agents must be upgraded. |
| `pair-qr` returns 503 | Server lacks `MANIFEST_SIGNING_PRIVATE_KEY` | Set the env var on the relay; restart |
| `pair-qr` returns 4 ("trust key not pinned") | Agent never ran `fetch-trust-key` | Run `mobaide-agent fetch-trust-key` |
| `pair-qr` returns 5 ("fingerprint mismatch") | Server's trust key was rotated since the agent pinned it | Re-fetch the trust key (re-run `fetch-trust-key`); confirm rotation was intentional |
| Mobile shows "Pairing code consumed or expired" (HTTP 410) | QR scanned twice, or after TTL | Render a fresh QR via `show-qr` |
| Mobile shows "Trust key changed — possible MITM" | TOFS pin diverges from scanned fingerprint | If you intentionally rotated the relay key, clear the app's data; otherwise treat as a security incident |
| `pair-qr --wait` exits 6 | Pairing WS transport, TTL, or signature failure | Re-run; check relay logs |
| `pair <code>` exits 8 | The relay refused the code: unknown, expired, superseded by a newer code, or already redeemed by another machine | Get a fresh code in the app and re-run. A repeated 8 with "already redeemed from this machine" means the first redeem's response was lost — the nonce is unrecoverable, so mint a new code |
| `pair <code>` exits 6 | The pairing WebSocket dropped, timed out, or delivered a payload that failed signature/`relay_url` verification | Re-run the exact command the error prints, before the deadline it names. The redemption stays on file, so the re-run reconnects to the same nonce and takes delivery of credentials already issued; past the nonce TTL it reports 8 instead and a new code is needed |
| `pair <code>` exits 9 | Refused locally: the host already has credentials, stdin is not a terminal without `--yes`, or the operator answered no | Use `--force` to re-pair a working host, `--yes` for a non-interactive run — but only when the code came from your own account |
| `pair <code>` exits 3 after the prompt | The approval could not be delivered — the relay was unreachable between the `y/N` answer and the accept call | Re-run the same command: the record survives, so the resume re-sends the approval without asking again. Until it lands the browser stays on "waiting for a machine", by design |
| `pair <code>` returns 429 | Redeem rate limit tripped (per-IP or the global circuit breaker) | Wait for the `Retry-After` interval. Behind a proxy, check `FORWARDED_ALLOW_IPS` — without it every redeem is attributed to the proxy and shares one bucket |
| Client shows "This relay's signing key is not the one this account first paired with" (409 `trust_key_pin_mismatch`) | The relay's `MANIFEST_SIGNING_*` keypair was rotated after this account pinned it | Compare fingerprints with `mobaide-agent show-trust-fp` on an already-paired host. If the rotation was intentional, follow the trust-key rotation runbook above; otherwise treat as a security incident |
| Browser sits on "waiting for a machine to enter the code" until the code expires | Nothing redeemed the code, the operator has not answered the agent's `y/N` prompt yet (an expected intermediate state — the request stays `pending` until it is answered), or the relay runs multiple workers and the confirm landed on a worker without the agent's WS | Check the command actually ran on the agent host; confirm the relay is single-worker (see "Multi-worker deployments"). All three clients now bound this screen with the deadline the relay reported, so it ends in an expiry message rather than waiting forever even if the poll that would have reported it fails |
| QR scan succeeded but the client shows "Waiting for agent…" then, after 60 s, "Agent not online yet" (not an error — the claim already succeeded; "Keep waiting" restarts the wait) | Agent relay client did not come online within the timeout. Possible causes: agent process crashed after writing `.env`, Windows service stdout silenced (pre-v0.1.9), Windows service log file encoded as cp1252/cp437 causing `UnicodeEncodeError` in QR render and aborting the WS before `wait_for_claim` (pre-v0.1.11), or `relay_url` scheme mismatch caused the agent to discard its own credentials. | Check that the agent service is running on that host, or run `mobaide-agent show-qr` there to restart pairing (the same platform-neutral copy all three clients show). Upgrade to v0.1.11+ if on Windows service builds below that version. |
| QR scan succeeded (mobile shows "Paired") but agent stays offline / `pairing_pending: true` persists | Pre-v0.1.5 agent: post-pairing restart used `sudo -n systemctl restart`, which silently no-op'd when the install user lacked NOPASSWD. v0.1.5–v0.1.6 used `sys.exit(75)` + supervisor restart, which failed when credentials were not flushed before the process exited. v0.1.7–v0.1.8 on Windows service builds: the service host redirected stdout to DEVNULL, silently dropping every structlog line (the issue is visible only after checking `<prefix>\logs\agent.out.log`, which v0.1.9+ captures); a fast mobile scan could also hit the WS-recovery path and receive a push with a `relay_url` scheme that mismatched the initiate-time value (https vs http behind a proxy) — agent strict-equality check discarded its own credentials. | Upgrade to v0.1.9+. The agent now (a) captures stdout to `<prefix>\logs\agent.out.log`, (b) writes `pending_pairing.json` only after the WS is open, and (c) uses `pairing_tokens.relay_url` as the single source of truth for both the dispatch and recovery paths. On older installs: for Linux/macOS, manually run `sudo systemctl restart mobaide-agent` or `launchctl unload … && launchctl load …` once after the initial pairing scan. |

## Security tests

The flow is exercised by `central_server/tests/test_pairing_qr_router.py` and
`central_server/tests/test_pairing_qr_db_helpers.py`:

* **Replay** — the second claim of an already-consumed nonce returns
  410 Gone (CAS in `db.claim_pairing_nonce` returns `False`).
* **Expired** — claims arriving after `expires_at` return 410 Gone.
* **Concurrent double-claim** — two `asyncio.gather`'d claims race; only
  one succeeds, the other returns 410.
* **Signature** — agent-side `pairing_ws._verify_signature` rejects
  payloads where any field has been tampered with.
* **Trust-key mismatch** — the mobile-side TOFS check refuses to
  proceed when the scanned `trust_key_fp` disagrees with the pinned
  fingerprint for the same relay URL.

## Out of scope

* WiFi-only / LAN-only pairing. The mobile and the agent never need
  to be on the same network — pairing is mediated entirely through
  the central server.
* Bluetooth or NFC pairing fallback for camera-impractical
  environments.
* Recovery flow for "screenshot of QR is now in my photo roll" — the
  120s TTL is the answer; there is no separate revocation UI for
  in-flight QRs.
* Multi-user co-ownership of a single agent — agent ownership is 1-to-1.
