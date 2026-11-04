# Agent QR pairing (CLA-50)

This page documents the agent-initiated QR pairing flow that replaces the
legacy `mobaide-agent pair --id ... --token ...` invocation.

**On this page**

- [Overview](#overview)
- [Sequence](#sequence)
- [QR payload schema](#qr-payload-schema)
- [Trust-On-First-Scan (TOFS) on the mobile](#trust-on-first-scan-tofs-on-the-mobile)
- [Re-pair semantics](#re-pair-semantics)
- [Operator runbook](#operator-runbook)
- [Device-code pairing (the reversed direction)](#device-code-pairing-the-reversed-direction)
- [Server-side configuration](#server-side-configuration)
- [Multi-worker deployments](#multi-worker-deployments)
- [Troubleshooting](#troubleshooting)
- [Security tests](#security-tests)
- [Out of scope](#out-of-scope)

## Overview

The legacy flow made the operator read `agent_id` / `agent_token` from the Android app, SSH to the agent host and run `mobaide-agent pair --id <ID> --token <TOKEN>`. Tokens leaked into shell history and the user drove both ends manually.

The QR flow flips the direction: the agent renders a QR code in its terminal, the operator scans it from the Mobaide mobile app, and the agent receives its credentials directly over a server-mediated WebSocket. No token ever touches the mobile UI; no token ever appears on the agent host's command line.

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
* `trust_key_fp` — SHA-256 hex of the relay's manifest-signing public key (the same Ed25519 key the agent's auto-updater pins, exposed at `GET /api/v1/agent/trust_key`).
* `exp_unix` — absolute expiry as Unix seconds. Default TTL: 120s (`PAIRING_QR_TOKEN_TTL_SECONDS`); production sets 600s so a pairing link survives the hop to a browser on another device (the nonce stays single-use, so the wider window adds no replay exposure).

The token is never carried in the QR. The server mints it and pushes it to the agent over the pairing WebSocket.

The parse contract is pinned across all four implementations by `tests/protocol_fixtures/pairing/qr_payload.json`:

- exactly five fields;
- `MOBA1` case-sensitive;
- `relay_url` / `nonce` / `trust_key_fp` non-blank once trimmed but stored **verbatim**;
- `exp_unix` a signed 64-bit integer parse that rejects surrounding whitespace, digit separators and overflow.

Expiry is deliberately **not** range-checked in the parser: a negative `exp_unix` parses. Each client's pairing state machine decides staleness, because it needs the value to say "expired N minutes ago" rather than "malformed".

The Android arm of the golden is tracked in CLA-235; the `central_server` builder in CLA-236.

### Printed pairing link (webcam-less pairing)

Under the QR, the agent also prints the payload on one unbroken line and a browser link, in that order:

```
[mobaide-agent] Pairing code (copy this entire line):
MOBA1|https://api.mobaide.com|<nonce>|<fp>|<exp>
[mobaide-agent] Or open this link in a browser:
https://app.mobaide.com/pair#<base64url(payload)>
```

Both lines come from `pairing_qr.write_pairing_hints` (`pair-qr`, `show-qr`, pairing monitor). The payload line is **never wrapped or prefixed**, because a copied soft-wrapped line carries embedded newlines (the web paste box strips whitespace anyway). The printer **never raises**: `relay_url` may be an internationalized host, so the output is probed against the stream's codec and replaced with an ASCII notice when it does not fit. A `UnicodeEncodeError` (e.g. a Windows service logging to a `cp437`/`cp1252` file) would otherwise tear down the pairing WebSocket or fail the installer.

The payload is base64url-encoded without padding in the fragment: a raw `|` is not a legal URL character, and a fragment never reaches the relay's access logs.

The link's origin is **not** derivable from `relay_url` (the relay's `PUBLIC_BASE_URL`, `api.mobaide.com` in production; the SPA is a separate host). It is a fixed default with an env override:

| Variable | Default | Effect |
|---|---|---|
| `MOBAIDE_AGENT_PAIR_WEB_ORIGIN` | `https://app.mobaide.com` | Origin of the web client the printed link points at. Self-hosted and development deployments set it; a value that is not ASCII `http(s)` is ignored rather than printed. |

The agent fleet auto-updates on a 60-minute poll, so until the release ships the web paste box and manual `POST /api/agents` are the day-one paths.

### Pairing WS URL is agent-derived

The `/api/pairing/initiate` response intentionally omits a `ws_url` field. The agent computes the URL it dials from its own `RELAY_SERVER_URL` plus the response `nonce` (see `mobaide_agent/mobaide/agent/pairing_monitor._pairing_ws_url`):

* `wss://host[:port]` → `wss://host[:port]/ws/pairing/{nonce}`
* `https://host` → `wss://host/ws/pairing/{nonce}` (scheme upgraded)
* `ws://` / `http://` are refused unless `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1` (loopback escape hatch, mirroring the trust-key fetch).


### `relay_url` is persisted at initiate time

The `relay_url` in the QR payload **and** in every credential push is read from `pairing_tokens.relay_url`, written once by `/api/pairing/initiate`. Both the claim-handler dispatch path and the WS-recovery branch use it.

Recomputing it per request context can disagree behind a reverse proxy that does not forward `X-Forwarded-Proto` to uvicorn, and the agent then rejects its own credentials.

### `pending_pairing.json` is written AFTER the WS is open

`run_pairing_monitor` saves `pending_pairing.json` only *after* the pairing WebSocket is open. `install.ps1` polls that file before rendering the QR, so a fast mobile claim cannot arrive while the agent's WS waiter is still unregistered (which would force the server's recovery branch).

### `ws_delivered` in the claim response

`POST /api/pairing/claim` returns an optional `ws_delivered: bool | null` (`null` from older server builds). `true`: the credential push went directly to the agent's open pairing WebSocket. `false`: it was staged in the recovery slot for the agent's next WS reconnect.

All three clients use it only to refine the copy shown if the 60-second wait times out (a "not online yet" state, not an error). It is **not** confirmation of pairing: the authoritative signal is the `agent.status_changed` event with `online=true`, delivered via the `UserEventBus`.

## Trust-On-First-Scan (TOFS) on the mobile

The first scan against a given `relay_url` pins that relay's `trust_key_fp` locally on the device (`mobaide_trust_pins` DataStore). Subsequent scans against the same `relay_url` MUST present the same fingerprint or the claim is hard-aborted with a "trust key changed — possible MITM" error.

Recovery for a legitimately rotated relay key: clear the app's data (Android Settings → Apps → Mobaide → Storage → Clear data) so the fingerprint is unpinned.

### TOFS in a browser is weaker, deliberately

The web client pins the same way, in `localStorage` under `mobaide-trust-pins`, with the same key normalization (trim, strip every trailing slash, lowercase) and the same hard refusal with no override.

The difference is durability: Safari caps script-written storage at 7 days of no interaction, so an infrequently used relay silently degrades from trust-on-first-scan to trust-on-most-recent-scan. Recovery is clearing the site's data rather than the app's (the wipe also regenerates the browser's device UUID, orphaning a `mobile_devices` row).

### The account-level pin, and what it does not do

Every account records the relay signing fingerprint it first paired against, in `users.trust_key_fp`. Every later claim from any client is refused when that fingerprint no longer matches the relay's current key, so a browser whose `localStorage` was wiped cannot silently re-trust a rotated key.

The pin only fires when the relay's own signing key changed (a claim whose `trust_key_fp` disagrees with the current key is *already* a 400). It is a **rotation tripwire, not an MITM detector** — the relay both stores the pin and serves the value it is compared against, so a compromised relay simply returns a matching pair.

The local pin remains the only record of what the user personally vouched for, so `GET /api/pairing/trust-pin` is read-only and no client writes its response into a local trust store (an unattended write is a permanent lockout waiting for a bad value).

The refusal is HTTP 409 with `detail.error = "trust_key_pin_mismatch"` — a structured detail rather than prose, because 409 already carries the "agent belongs to another account" refusal and the two need different copy.

### The first-pairing marker

`_complete_pairing` stamps `users.first_paired_at` on the account's first-ever successful claim (a compare-and-set right after the trust pin is taken). Unlike `trust_key_fp` it is never cleared, which makes it the safe trigger for the once-per-account Google Ads "Agent paired" conversion upload (see *Google Ads conversion upload* in docs/deployment.md). When the CAS wins, the user id goes to `app.state.ads_uploader` after the credential push, so a scheduling fault can never fail a delivered pairing.

The same CAS broadcasts the `account.first_agent_paired` user event (`{"agent_id"}`), and the stamp is readable from `GET /api/auth/me`; the mobile in-app conversion (docs/app-conversion-measurement.md) is built on those two signals.

## Re-pair semantics

The agent's CLI captures its current `RELAY_AGENT_ID` (if any) into the initiate request's `agent_metadata.current_agent_id` field. The claim handler then chooses one of three paths:

* **No current agent id** (`current_agent_id` absent) — the server calls `db.register_agent()` to create a new agent row (first-pair).
* **Unknown / deleted agent id** (`current_agent_id` present but no matching row) — the claim returns **HTTP 404**. The agent host must first-pair instead: run `mobaide-agent re-pair --fresh` to drop the dead id, then rescan. This happens when the agent was removed from the account or the relay database was redeployed — plain `re-pair` keeps the id and would 404 on every cycle.
* **Same owner** — `db.is_agent_owner(current_agent_id, claimer)` returns true. The server calls `db.reregister_agent(current_agent_id)` to rotate the token. The mobile UI sees the same agent row reappear with a fresh credential.
* **Different owner** — `POST /api/pairing/claim` returns **HTTP 409**. Cross-account agent transfer is not permitted via QR re-pair. The current owner must remove the agent through the app first; the host then first-pairs as a new agent under the new account.

Shell access on the agent host alone is not enough to claim ownership from another account: the current owning account must explicitly release the agent first.

### Mobile device registry

The `mobile_device_id` registry (the `mobile_devices` table) is **last-login-wins**, unlike agent ownership above. A claim or token mint from an authenticated account always moves the device row to the caller: the caller proves account access (user JWT) plus device possession (the per-install random UUID is never exposed to other users).

The handover revokes every live refresh token previously issued for that device and clears its push registration, so the prior account can neither refresh its sessions nor keep receiving notifications there. This lets one phone switch accounts without a manual release step (agents still require the explicit release above).

Two cases refuse to move the row, on `POST /api/agents/{id}/token` and on pairing claim/confirm:

- **Device mismatch (`401`).** A session bound to one device (its user JWT carries the `device_id` sent at sign-in) naming a different device gets `401`. The client signs out and the next sign-in binds to the new id (e.g. an iPhone restored from a backup).
- **Signed in before the revoke (`403`).** A session signed in at or before the device's revoke gets `403`, and so does a mint or claim that a revoke overtakes mid-request. For a refreshable session the sign-in time is its `auth_time` claim (carried forward on every rotation), not its `iat`.

Revoking a device records a `tokens_valid_after` watermark in `device_revocations(device_id, tokens_valid_after)`:

- The row is never cleared and has no foreign key, so it survives re-registration, handover to another account, and deletion of any account.
- Every JWT bound to that device (mobile access token, or user JWT with a `device_id` claim) with `iat` at or before the watermark is rejected. A fresh sign-in on the device after the revoke may register it again.
- User sessions signed in without a `device_id` are not bound and survive a revoke.

## Operator runbook

### First install

The install scripts (`deploy/install.sh`, `deploy/install.ps1`) start the supervisor, wait for the agent to report healthy and for the service to write the pairing nonce file, then render the pairing QR. This ensures the service is ready to accept the claim before the operator scans (otherwise `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` could stay empty).

If the operator misses the QR (e.g., SSH disconnected before scanning), the agent's boot-time autorun re-renders it. Run on the agent host:

```bash
mobaide-agent show-qr
```

This re-renders the most-recent pending QR; if the cached nonce has expired, it initiates a fresh one. It first asks the agent answering on `127.0.0.1:<PORT>` for `/api/health`:

* `pairing_pending: true` with no pending file yet (the service is still booting, e.g. right after `re-pair`): it waits up to 60 s for the service's own QR rather than initiating a nonce the service would then replace, and exits 7 if none appears.
* `pairing_pending: false` while `.env` holds no credentials: the running agent started before they were blanked and still serves them, so it would never answer a QR. `show-qr` renders nothing and exits 9; restart the agent by running `mobaide-agent re-pair` again.
* nothing answering: unchanged — render the pending file, or initiate.

`mobaide-agent` is a shim the installer puts on PATH: `/usr/local/bin/mobaide-agent` on Linux and macOS (or `<prefix>/bin/mobaide-agent`, printed by full path), and `<prefix>\bin` on Windows (open a new terminal after installing). Older installs need the installer re-run; see [The `mobaide-agent` command](deployment.md#linux-systemd-and-macos-launchd--automated).

The installers pass `--require-pending` to `show-qr` so the installer never initiates a nonce itself — the running service is the single initiator, which prevents a race on `/ws/pairing/{nonce}` if both opened a pairing WS.

### Re-pair to a different account

Direct cross-account transfer via QR is not permitted; the claim returns HTTP 409 if the scanned agent is already owned by a different account. To move a host to a different account:

1. **Remove the agent** from the current owner's account (app → agent settings → Remove agent). Clear the *uninstall* option (Android/web checkbox, iOS "Delete Only"): with it on, the host's install is deleted remotely and there is nothing left to re-pair (see [deployment.md](deployment.md) *Uninstalling the agent*).
2. On the agent host, run:

      ```bash
   mobaide-agent re-pair --fresh
   ```

   `--fresh` blanks BOTH `RELAY_AGENT_ID` and `RELAY_AGENT_TOKEN` in `.env` (plain `re-pair` keeps the id) and restarts the supervisor, surfacing a fresh QR via the boot autorun. The id **must** be cleared: after step 1 the relay no longer has this agent, so the old id would make the claim fail with HTTP 404 "Re-pair target agent does not exist".

3. Scan the QR from the new account's mobile app. No agent ID is carried in the initiate request, so the server registers a fresh agent row under the new owner.

### Re-pair after the agent was removed or the relay was redeployed

If pairing fails with **HTTP 404** `Re-pair target agent does not exist on this relay` (agent removed, or relay database redeployed), plain `re-pair` keeps the dead id and 404s forever. Recover with a true first-pair, which also clears the id and cached pending nonce, then rescan the QR:

```bash
mobaide-agent re-pair --fresh
```

### Skip the install-time QR

Set `MOBAIDE_AGENT_SKIP_INSTALL_QR=1` before running the installer (or `$env:MOBAIDE_AGENT_SKIP_INSTALL_QR = "1"` on PowerShell) to suppress the install-time render. The boot-time autorun still produces a QR on the next service start.

## Device-code pairing (the reversed direction)

The QR direction needs a camera pointed at the agent's terminal, which fails on an SSH jump host, a VM console, or any machine whose screen the phone cannot see. The device-code direction inverts the flow so nothing has to travel *off* the agent host — the operator only types eight characters *into* it.

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

Everything after the confirm is identical to the QR flow: the same `pairing_tokens` row, Ed25519 `PairingPushPayload`, `claimed_token_raw` recovery slot and single-flight CAS. Both halves finish through `_complete_pairing`.

### Re-pairing drops the old tunnel

When the claim names an agent the account already owns, `_complete_pairing` rotates that agent's token and then disconnects it, in *both* directions. Otherwise the agent would stay connected on revoked credentials, and clients would report success instantly (their online watcher reads `RoutingTable`, which knows nothing about the token swap).

The close code is 4012 (`WS_CLOSE_AGENT_TRANSIENT_OFFLINE`), never 4010: the agent is expected back, and the paired mobile sockets that go down with it must keep reconnecting instead of treating this as a destructive revocation.

### The code

Eight characters over the 20-consonant alphabet `BCDFGHJKLMNPQRSTVWXZ` (RFC 8628 §6.1): 20⁸ ≈ 2³⁴·⁶, no vowels (so it cannot spell a word), no confusable pairs. Input is filtered to that alphabet, so hyphens, spaces and a pasted trailing newline vanish.

The code is stored as a **peppered HMAC** (`hash_device_code`), not a plain digest: 34.6 bits is small enough to brute-force from a database dump, so the relay's JWT secret is folded in. There is no `user_id` in the HMAC input — the redeeming agent is anonymous and looks the row up by code alone.

There is deliberately **no per-code attempt counter**: a wrong guess HMACs to a row that does not exist, so there is nothing to increment. The guessing defence is:

- the code's entropy;
- at most one live code per account;
- a short TTL;
- single use;
- a per-IP limiter on the redeem endpoint;
- a global constant-keyed circuit breaker for the distributed case the per-IP bucket cannot see (it collapses to one shared bucket behind a proxy that does not forward the client address — see `FORWARDED_ALLOW_IPS` in `docs/deployment.md`).

The global breaker is charged on **failed** redeems only (checked with `ResendRateLimiter.peek`, which costs nothing). Charging every redeem would turn it into a deployment-wide throughput ceiling: with a 30/hr per-IP allowance, roughly twenty addresses could deny pairing to everyone.

### Why the agent asks too

Reversing the mint direction reverses the phishing attack. In the QR flow the code proves presence at the machine being paired. Here an attacker can mint a code on *their* account and talk someone into running `mobaide-agent pair <code>` — the victim's machine becomes an agent the **attacker** owns, and the attacker clicks the browser confirmation. The `current_agent_id` ownership guard only protects hosts that are already paired.

So the redeem response carries a masked `account_hint`, and the CLI requires an interactive `y/N` naming that account and this hostname before it writes anything. Non-interactive callers must pass `--yes`, and an already-paired host refuses outright without `--force`. Declining burns the code — deliberately: an attacker's code is then spent and cannot be redeemed again.

That prompt only controls anything if the relay waits for its answer, which is what `POST /api/pairing/device-code/accept` is for. Redeeming alone leaves the request polling as `pending`; the status flips to `redeemed` — and the confirm endpoint stops answering 409 — only once the machine has reported that its operator said yes. It is anonymous like the redeem, authorised the same way (the code plus the `redeemer_id`), idempotent, and charged against the same two rate-limit buckets. Without it, a declined pair would still have completed: an orphan agent on the confirming account, or a working host's token already rotated.

The browser-side confirmation is real but limited. `hostname` and `os` come from the anonymous redeem body and are attacker-settable, exactly as `current_agent_id` is in the QR flow. They let a user recognise their own machine; they authenticate nothing.

One case the confirm screen must not get wrong: when the redeem body's `current_agent_id` names an agent the confirming account *already owns*, approving **moves** that agent — rotating its token onto the redeeming host and knocking the real one offline. The status response therefore returns `repair_agent_name`, and all three clients swap the heading from "Is this your machine?" to "Move ‘<name>’ to this machine?" with an explicit warning.

### Retries, and what `redeemer_id` does and does not buy

The CLI generates a `redeemer_id`, persists it to `<prefix>/run/pending_device_code.json` *before* the request, and re-sends it on a retry, so the relay recognises the retry instead of rejecting a second machine racing the code.

**The raw nonce is never stored server-side**, so a replayed redeem comes back with `nonce: null`. The retry only continues if the agent still holds its own copy (the pending file is rewritten with the nonce as soon as the response lands, so a Ctrl-C during the wait resumes). A redeem whose *response* was lost in transit is not recoverable: the CLI exits 8 and the operator mints a new code.

A failed pairing WebSocket is the one failure that keeps the file. The relay parks the claimed credentials in `pairing_tokens.claimed_token_raw` for a reconnect on the same nonce, and that nonce exists nowhere else — so a dropped connection exits 6 and prints the command to re-run, which takes delivery through the recovery branch. Clearing the file would redeem under a fresh `redeemer_id` and burn the code. A record whose nonce has expired is dropped by `load_pending_device_code`.

The record is keyed by a digest of the code, so a *different* code starts a fresh redemption. It is a **separate file** from `pending_pairing.json`, because a shared file would overwrite the nonce the service's own QR cycle is waiting on.

### Cancelling

`DELETE /api/pairing/device-code/{request_id}` marks the code canceled and expires its nonce in the same transaction (a cancel racing the redeem rolls the linked nonce back, so a canceled request never keeps a live nonce).

**Confirm and cancel are mutually exclusive**, and the code row decides: `confirm` reserves the request (`confirmed_at`, CAS'd on `confirmed_at IS NULL AND canceled_at IS NULL`) *before* issuing credentials, and cancel carries the same guard, so exactly one wins. A confirm the ownership guard or trust pin then refuses calls `reopen_device_code` to hand the reservation back.

Cancel does **not** promptly stop an agent that has already connected: that CLI waits until the nonce's original deadline (`PAIRING_DEVICE_CODE_CONFIRM_TTL_SECONDS`, 300 s by default) and then exits 6. No "denied" frame is pushed, because older agents would show an opaque transport error.

## Server-side configuration

The server **must** hold both halves of the manifest signing keypair
to operate the QR pairing flow:

| Variable | Required for | Behaviour when unset |
|---|---|---|
| `MANIFEST_SIGNING_PUBLIC_KEY` | trust-key endpoint, manifest verification, QR initiate | `GET /api/v1/agent/trust_key` returns 503; QR initiate returns 503 |
| `MANIFEST_SIGNING_PRIVATE_KEY` | QR initiate, claim push signing | `POST /api/pairing/initiate` returns 503 |
| `PAIRING_QR_TOKEN_TTL_SECONDS` | nonce TTL (default `120`; prod sets `600` for cross-device pairing links) | uses default |
| `PAIRING_INITIATE_IP_HOURLY_CAP` | per-IP `POST /api/pairing/initiate` calls per hour (default `120`; `0` disables). A healthy unpaired agent makes one call per TTL | uses default |
| `PUBLIC_BASE_URL` | QR `relay_url` field | falls back to request `Host` header |
| `PAIRING_DEVICE_CODE_TTL_SECONDS` | how long a device code stays redeemable (default `600`) | uses default |
| `PAIRING_DEVICE_CODE_CONFIRM_TTL_SECONDS` | the redeem-to-confirm window, and the TTL of the nonce minted at redeem (default `300`) | uses default |
| `PAIRING_DEVICE_CODE_REDEEM_IP_HOURLY_CAP` | per-IP redeem attempts per hour (default `30`) | uses default |
| `PAIRING_DEVICE_CODE_REDEEM_IP_COOLDOWN_SECONDS` | minimum gap between redeems from one IP (default `1`) | uses default |
| `PAIRING_DEVICE_CODE_REDEEM_GLOBAL_HOURLY_CAP` | **failed** redeems per hour across all callers before the breaker trips (default `600`) | uses default |
| `PAIRING_DEVICE_CODE_MINT_COOLDOWN_SECONDS` | minimum gap between mints for one account (default `2`; no hourly cap by design — a new code supersedes the old one, so a page reload re-mints) | uses default |
| `TRUST_KEY_PIN_ENFORCED` | refuse claims whose relay key differs from the account pin (default `true`) | uses default |

### Trust-key rotation runbook

Rotating `MANIFEST_SIGNING_*` invalidates every account's pin at once, so the rotation itself would lock out every existing account. The intended sequence:

1. Set `TRUST_KEY_PIN_ENFORCED=false` and restart the relay. Mismatching pins are then cleared as accounts pair, rather than refused.
2. Rotate the keypair; have each agent host re-run `mobaide-agent fetch-trust-key` (the agent's own pin is separate and fail-closed).
3. Once accounts have re-paired, set `TRUST_KEY_PIN_ENFORCED=true` again.

An account that currently owns **no** agents re-pins automatically even while enforcement is on — the pin protects nothing in that state, so "remove every agent, then pair again" is the self-service recovery for a single account.

> **Note on private-key residency.** The manifest signing private key was previously held only by the release pipeline. CLA-50 expands the trust surface by placing it on the relay so the server can sign pairing pushes. If your threat model rejects this, a future change can introduce a separate `PAIRING_SIGNING_PRIVATE_KEY` keypair plus a parallel pinned file `<prefix>/trust/pairing_trust.pub` on the agent.

## Multi-worker deployments

The pairing-WS waiter map is in-memory and per-process (`PairingWsRegistry`, keyed by the SHA-256 of the nonce). Multi-worker deployments (gunicorn `-w N`) break the flow: a claim can land on a different worker than the one holding the agent's WS, and the push is silently dropped.

The device-code direction makes this worse. A QR claim's dropped push is recovered by the agent's next WS reconnect (it retries on a loop); a device-code confirm is a **browser** request with no retry loop, so the user sees "waiting for the agent" until the timeout. Mitigations:

* Single uvicorn process per host (the default deployment model — see `docs/deployment.md`).
* If multi-worker is required, replace the in-memory registry with a shared backend: Redis pub/sub, or DB polling against the `claimed_token_raw` column.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| `pair-qr` returns 503 | Server lacks `MANIFEST_SIGNING_PRIVATE_KEY` | Set the env var on the relay; restart |
| `pair-qr` returns 4 ("trust key not pinned") | Agent never ran `fetch-trust-key` | Run `mobaide-agent fetch-trust-key` |
| `pair-qr` returns 5 ("fingerprint mismatch") | Server's trust key was rotated since the agent pinned it | Re-fetch the trust key (re-run `fetch-trust-key`); confirm rotation was intentional |
| Mobile shows "Pairing code consumed or expired" (HTTP 410) | QR scanned twice, or after TTL | Render a fresh QR via `show-qr` |
| `show-qr` exits 7 without `--require-pending` | The agent is in pairing mode but did not publish a QR within 60 s — most likely it cannot reach the relay | Check the agent logs; it retries on its own, so run `show-qr` again later |
| Mobile shows "Trust key changed — possible MITM" | TOFS pin diverges from scanned fingerprint | If you intentionally rotated the relay key, clear the app's data; otherwise treat as a security incident |
| `pair-qr --wait` exits 6 | Pairing WS transport, TTL, or signature failure | Re-run; check relay logs |
| `pair <code>` exits 8 | The relay refused the code: unknown, expired, superseded by a newer code, or already redeemed by another machine | Get a fresh code in the app and re-run. A repeated 8 with "already redeemed from this machine" means the first redeem's response was lost — the nonce is unrecoverable, so mint a new code |
| `pair <code>` exits 6 | The pairing WebSocket dropped, timed out, or delivered a payload that failed signature/`relay_url` verification | Re-run the exact command the error prints, before the deadline it names: the redemption stays on file, so the re-run takes delivery of credentials already issued. Past the nonce TTL it reports 8 and a new code is needed |
| `pair <code>` exits 9 | Refused locally: the host already has credentials, stdin is not a terminal without `--yes`, or the operator answered no | Use `--force` to re-pair a working host, `--yes` for a non-interactive run — but only when the code came from your own account |
| `pair <code>` exits 3 after the prompt | The approval could not be delivered — the relay was unreachable between the `y/N` answer and the accept call | Re-run the same command: it re-sends the approval without asking again. Until it lands the browser stays on "waiting for a machine", by design |
| Agent log shows `pairing_monitor.initiate_failed … HTTP Error 429`, or `pair-qr` says the server is rate-limiting this network; relay logs `pairing.initiate_rate_limited` | More than `PAIRING_INITIATE_IP_HOURLY_CAP` initiates from one IP in an hour — usually an agent stuck in a retry loop on that network | Find the looping agent (`pairing_monitor.ws_failed` / `pairing_monitor.clock_skew` in its log) and fix the cause. Behind a proxy, check `FORWARDED_ALLOW_IPS` — without it every agent shares one bucket |
| Agent log shows `pairing_monitor.clock_skew` | The agent host's clock is ahead of the relay by more than the nonce TTL, so every fresh nonce looks expired | Enable NTP time sync on the agent host. Pairing cannot complete until the clock is correct (claims are also rejected beyond 5 min skew) |
| `pair <code>` returns 429 | Redeem rate limit tripped (per-IP or the global circuit breaker) | Wait for the `Retry-After` interval. Behind a proxy, check `FORWARDED_ALLOW_IPS` — without it every redeem is attributed to the proxy and shares one bucket |
| Client shows "This relay's signing key is not the one this account first paired with" (409 `trust_key_pin_mismatch`) | The relay's `MANIFEST_SIGNING_*` keypair was rotated after this account pinned it | Compare fingerprints with `mobaide-agent show-trust-fp` on an already-paired host. If the rotation was intentional, follow the trust-key rotation runbook above; otherwise treat as a security incident |

Entries too long for a table cell:

- **HTTP 403 (Cloudflare error 1010) on `pair-qr` or `fetch-trust-key`.** Cloudflare Bot Fight Mode blocked the default `Python-urllib` User-Agent. Agent v0.1.3+ sends `User-Agent: mobaide-agent/<version>`, which passes default Bot Fight Mode; older agents must be upgraded. With Super Bot Fight Mode and JA3/JA4 "Block", add a Cloudflare WAF Skip rule: `(http.user_agent wildcard "mobaide-agent/*") and (http.request.uri.path starts_with "/api/")`.
- **`show-qr` exits 9 ("still paired with the credentials it started with").** The agent on the host booted before `re-pair` blanked `.env`, so it is still paired and no pairing monitor is waiting on any QR. Run `mobaide-agent re-pair` again from a terminal outside Mobaide (its restart also stops any agent process left from the install). On Windows, re-run the installer once to get the supervisor that ends its whole process tree (before it, `schtasks /End` killed only the supervisor, the orphaned agent kept the port, and the restarted one exited).
- **Browser sits on "waiting for a machine to enter the code" until the code expires.** Causes: nothing redeemed the code; the operator has not answered the agent's `y/N` prompt yet (expected — the request stays `pending` until answered); or the relay runs multiple workers and the confirm landed on a worker without the agent's WS. Check the command actually ran on the agent host and that the relay is single-worker (see "Multi-worker deployments"). All three clients bound this screen with the deadline the relay reported, so it ends in an expiry message even if the poll fails.
- **QR scan succeeded but the client shows "Waiting for agent…", then after 60 s "Agent not online yet"** (not an error — the claim already succeeded; "Keep waiting" restarts the wait). The agent relay client did not come online in time: the agent process crashed after writing `.env`, a Windows service build older than v0.1.11 hit a `cp1252`/`cp437` `UnicodeEncodeError` in the QR render, or a `relay_url` scheme mismatch made the agent discard its own credentials. Check that the agent service is running, or run `mobaide-agent show-qr` there to restart pairing; upgrade Windows service builds below v0.1.11.
- **QR scan succeeded (mobile shows "Paired") but the agent stays offline / `pairing_pending: true` persists.** Upgrade to v0.1.9+; older agents lost the post-pairing restart (no-op `sudo -n systemctl restart`, or `sys.exit(75)` before credentials were flushed), and Windows service builds v0.1.7–v0.1.8 dropped all log output (v0.1.9+ captures stdout to `<prefix>\logsgent.out.log`). On older Linux/macOS installs, manually run `sudo systemctl restart mobaide-agent` or `launchctl unload … && launchctl load …` once after the initial pairing scan.

## Security tests

`central_server/tests/test_pairing_qr_router.py` and `test_pairing_qr_db_helpers.py` cover:

* **Replay** and **expiry** — a second claim of a consumed nonce, or a claim after `expires_at`, returns 410 Gone.
* **Concurrent double-claim** — only one of two racing claims succeeds; the other returns 410.
* **Signature** — the agent rejects payloads with any tampered field.
* **Trust-key mismatch** — the mobile TOFS check refuses a scanned `trust_key_fp` that differs from the pinned one.

## Out of scope

* WiFi-only / LAN-only pairing (pairing is mediated entirely through the central server).
* Bluetooth or NFC pairing fallback.
* Revocation of in-flight QRs (e.g. a screenshot in a photo roll) — the 120s TTL is the answer.
* Multi-user co-ownership of a single agent — agent ownership is 1-to-1.
