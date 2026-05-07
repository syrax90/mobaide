# Agent QR pairing (CLA-50)

This page documents the agent-initiated QR pairing flow that replaces the
legacy `mobaide-agent pair --id ... --token ...` invocation.

## Overview

A fresh `mobaide_agent` install used to require the operator to (a) read
`agent_id` / `agent_token` from the Android `CredentialsDialog`, (b) SSH to
the agent host, and (c) run `mobaide-agent pair --id <ID> --token <TOKEN>`.
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
        │ ◄── 200 {nonce, expires_at, ws_url, trust_key_fp,          │
        │           qr_payload}                                      │
        │                          │                                 │
        │ render QR(MOBA1|...)     │                                 │
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
        │                          │   re-pair (rotate or transfer)  │
        │                          │ else:                           │
        │                          │   register_agent()              │
        │                          │ sign payload (Ed25519)          │
        │                          │ ── 200 {agent_id, name} ────────►│
        │ ◄── {agent_id, agent_token, relay_url, signature_b64}      │
        │ verify Ed25519 signature                                   │
        │ atomic .env write        │                                 │
        │ close WS                 │ clear claimed_token_raw         │
        │ supervisor restart                                         │
        │ (relay client comes up   │                                 │
        │  on next boot)           │                                 │
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
handler then chooses one of two paths:

* **Same owner** — `db.is_agent_owner(current_agent_id, claimer)`
  returns true. The server calls `db.reregister_agent(current_agent_id)`
  to rotate the token. The mobile UI sees the same agent row reappear
  with a fresh credential.
* **Different owner** — the server calls
  `db.transfer_agent_owner(current_agent_id, claimer)`, which deletes
  the prior owner's `mobile_refresh_tokens` and `pairings` rows and
  updates `agents.owner_user_id`. Then `reregister_agent` rotates the
  token. The prior owner's mobile clients are evicted on their next
  refresh; live WebSocket connections are not torn down server-side
  in this flow (they rely on token rotation to take effect on the
  next reauth).

Authorization model: shell access on the agent host is sufficient. The
operator who controls the host implicitly authorises any account
transfer by triggering `mobaide-agent re-pair` on that host.

## Operator runbook

### First install

The install scripts (`deploy/install.sh`, `deploy/install.ps1`) render
the pairing QR after `init-env` and before service start. SSH-attached
operators see the QR in their terminal immediately and can scan it
from the Mobaide mobile app to complete pairing.

If the operator misses the QR (e.g., SSH disconnected before scanning),
the agent's boot-time autorun re-renders it. Run on the agent host:

```bash
mobaide-agent show-qr
```

This re-renders the most-recent pending QR; if the cached nonce has
expired, it initiates a fresh one.

### Re-pair to a different account

```bash
mobaide-agent re-pair
```

Blanks `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` in `.env`, restarts the
supervisor, and surfaces a fresh QR via the boot autorun. Scan from the
new account's mobile app to complete the transfer.

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
| `pair-qr` returns 503 | Server lacks `MANIFEST_SIGNING_PRIVATE_KEY` | Set the env var on the relay; restart |
| `pair-qr` returns 4 ("trust key not pinned") | Agent never ran `fetch-trust-key` | Run `mobaide-agent fetch-trust-key` |
| `pair-qr` returns 5 ("fingerprint mismatch") | Server's trust key was rotated since the agent pinned it | Re-fetch the trust key (re-run `fetch-trust-key`); confirm rotation was intentional |
| Mobile shows "Pairing code consumed or expired" (HTTP 410) | QR scanned twice, or after TTL | Render a fresh QR via `show-qr` |
| Mobile shows "Trust key changed — possible MITM" | TOFS pin diverges from scanned fingerprint | If you intentionally rotated the relay key, clear the app's data; otherwise treat as a security incident |
| `pair-qr --wait` exits 6 | Pairing WS transport, TTL, or signature failure | Re-run; check relay logs |

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
