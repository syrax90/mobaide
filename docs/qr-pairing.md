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
        │ ◄── {agent_id, agent_token, relay_url, signature_b64}      │   (polls agent.online)
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
  (`PAIRING_QR_TOKEN_TTL_SECONDS`).

The token is never carried in the QR. The server mints it server-side
and pushes it to the agent over the pairing WebSocket.

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

The mobile uses this only to refine the 60-second timeout-error copy in the
"Waiting for agent…" phase — it is **not** treated as confirmation that
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

## Server-side configuration

The server **must** hold both halves of the manifest signing keypair
to operate the QR pairing flow:

| Variable | Required for | Behaviour when unset |
|---|---|---|
| `MANIFEST_SIGNING_PUBLIC_KEY` | trust-key endpoint, manifest verification, QR initiate | `GET /api/v1/agent/trust_key` returns 503; QR initiate returns 503 |
| `MANIFEST_SIGNING_PRIVATE_KEY` | QR initiate, claim push signing | `POST /api/pairing/initiate` returns 503 |
| `PAIRING_QR_TOKEN_TTL_SECONDS` | nonce TTL (default `120`) | uses default |
| `PUBLIC_BASE_URL` | QR `relay_url` field | falls back to request `Host` header |

> **Note on private-key residency.** The manifest signing private key
> was previously held only by the release pipeline. CLA-50 expands the
> trust surface by placing it on the relay so the server can sign
> pairing pushes. If your threat model rejects this, a future change
> can introduce a separate `PAIRING_SIGNING_PRIVATE_KEY` keypair plus a
> parallel pinned file `<prefix>/trust/pairing_trust.pub` on the agent.

## Multi-worker deployments

The pairing-WS waiter map is in-memory and per-process
(`PairingWsRegistry`). Multi-worker deployments (gunicorn `-w N`)
break the flow: a claim can land on a different worker than the one
holding the agent's WS, and the push is silently dropped. Mitigations:

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
| QR scan succeeded but mobile shows "Waiting for agent…" then times out with an error after 60 s | Agent relay client did not come online within the timeout. Possible causes: agent process crashed after writing `.env`, Windows service stdout silenced (pre-v0.1.9), Windows service log file encoded as cp1252/cp437 causing `UnicodeEncodeError` in QR render and aborting the WS before `wait_for_claim` (pre-v0.1.11), or `relay_url` scheme mismatch caused the agent to discard its own credentials. | On the agent host run: `mobaide-agent show-qr` (re-render QR and restart pairing), `Get-Service MobaideAgent` (check service state on Windows), `Get-Content "$env:ProgramFiles\MobaideAgent\logs\agent.err.log" -Tail 50` (Windows error log). Upgrade to v0.1.11+ if on Windows service builds below that version. |
| QR scan succeeded (mobile shows "Paired") but agent stays offline / `pairing_pending: true` persists | Pre-v0.1.5 agent: post-pairing restart used `sudo -n systemctl restart`, which silently no-op'd when the install user lacked NOPASSWD. v0.1.5–v0.1.6 used `sys.exit(75)` + supervisor restart, which failed when credentials were not flushed before the process exited. v0.1.7–v0.1.8 on Windows service builds: the service host redirected stdout to DEVNULL, silently dropping every structlog line (the issue is visible only after checking `<prefix>\logs\agent.out.log`, which v0.1.9+ captures); a fast mobile scan could also hit the WS-recovery path and receive a push with a `relay_url` scheme that mismatched the initiate-time value (https vs http behind a proxy) — agent strict-equality check discarded its own credentials. | Upgrade to v0.1.9+. The agent now (a) captures stdout to `<prefix>\logs\agent.out.log`, (b) writes `pending_pairing.json` only after the WS is open, and (c) uses `pairing_tokens.relay_url` as the single source of truth for both the dispatch and recovery paths. On older installs: for Linux/macOS, manually run `sudo systemctl restart mobaide-agent` or `launchctl unload … && launchctl load …` once after the initial pairing scan. |

## Security tests

The flow is exercised by `central_server/tests/test_pairing_qr.py`:

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
