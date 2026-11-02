# Mobaide Web Client

Browser SPA for controlling Mobaide agents through the central relay. React
19 + TypeScript (strict) + Vite, React Router (Data mode), Zustand, TanStack
Query, openapi-fetch, xterm.js 6. See `docs/web-client.md` at the repo root
for the full architecture write-up.

**On this page**

- [Prerequisites](#prerequisites)
- [Commands](#commands)
- [Typed API layer](#typed-api-layer)
- [Auth](#auth)
- [Deliberately deferred](#deliberately-deferred)
- [Realtime (WEB-04)](#realtime-web-04)
- [Sessions (WEB-07)](#sessions-web-07)
- [Agents and pairing (WEB-08)](#agents-and-pairing-web-08)
- [Terminal (WEB-05)](#terminal-web-05)

## Prerequisites

- Node `^22.22.2 || ^24.15.0 || >=26.0.0` (the intersection the committed
  lockfile actually supports — jsdom is the tightest constraint; Node 23/25
  are unsupported by the test stack)
- For schema work only: Python 3.12 with both server packages installed
  editable from the repo root:

  ```sh
  python -m pip install -e 'mobaide_agent[dev]' -e 'central_server[dev]'
  ```

## Commands

All run from `web/`.

| Command | Purpose |
|---|---|
| `npm ci` | Install exact locked dependencies |
| `npm run dev` | Dev server with proxy to a local central server on :9000 |
| `npm run build` | Typecheck (`tsc -b`) + production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run typecheck` | Strict typecheck only |
| `npm run lint` | ESLint (typed rules) |
| `npm test` | Vitest unit/component tests (jsdom) |
| `npm run test:e2e` | Playwright e2e against `vite preview` (first run: `npx playwright install chromium`) |
| `npm run schema:dump` | Regenerate `openapi/*.json` from the Python apps (needs the Python prerequisites) |
| `npm run schema:generate` | Regenerate `src/api/generated/*.ts` from `openapi/*.json` |
| `npm run schema:check` | Fail if either committed layer is stale |

## Typed API layer

Two independent OpenAPI schemas, two typed clients:

- **Relay axis** (`openapi/central-server.json` → `src/api/generated/central-server.ts` → `relayClient`): routes the central server serves directly.
- **Agent axis** (`openapi/agent.json` → `src/api/generated/agent.ts` → `createAgentClient(agentId)`): the relay's `/proxy/{agentId}/...` catch-all is untyped; the agent schema types the proxied surface.

`scripts/dump_openapi.py` builds both FastAPI apps in-process (no server, no
env vars) and writes deterministic JSON with `info.version` pinned to
`0.0.0`. Both the JSON schemas and the generated TS are committed; the
`web-ci.yml` workflow regenerates both layers on every PR and fails on drift.
After changing agent or central-server routes run:

```sh
npm run schema:dump && npm run schema:generate
```

and commit the result. Use Python 3.12 if the dump differs from CI.

Auth — the two axes authenticate differently (same split as Android's
`CentralAuthInterceptor` vs `AuthInterceptor`). `src/api/auth.ts` exposes:

- `setUserTokenProvider()`: the dashboard/user JWT, attached by `relayClient`.
- `setAgentTokenProvider()`: the per-agent `mobile_access` JWT, attached by each
  `createAgentClient(agentId)` for its own agent.

Both are filled in at startup by `src/auth/install.ts` (WEB-03).

## Auth

`src/auth/` owns the session:

- The user JWT lives in `sessionStorage` (per-tab, handed to new tabs over a `BroadcastChannel`).
- Per-agent tokens live in `localStorage`.
- A single-flight refresh runs against `POST /api/auth/refresh` under `navigator.locks`.
- Routes are guarded by React Router middleware (`src/app/guards.tsx`), which preserves the wanted path as `?next=`.

The Google Sign-In client id arrives from `GET /api/version` at runtime, so
there is no build-time env var. But the origin you serve from must be
listed under **Authorized JavaScript origins** on the Google OAuth client
(`http://localhost:5173` for `npm run dev`), or the button fails to render.

See the "App shell" section of `docs/web-client.md` for the full model.

## Deliberately deferred

- Prettier, TanStack Query devtools: adopt when the team wants them.

## Realtime (WEB-04)

WS frame/event types are hand-written — they are absent from both OpenAPI
schemas by design — and split in two:

- `src/protocol/` is framework-free: the binary terminal codec, the relay
  path/URL builder, the auto-scan frame decoder, and the Zod wire schemas.
  Its tests read the cross-platform goldens in repo-root
  `tests/protocol_fixtures/` directly (the same files the iOS test target
  consumes), run under `// @vitest-environment node`, and assert hex
  strings rather than typed arrays.
- `src/realtime/` is the channel manager: generation-counted reconnect with
  jittered 1 s→30 s backoff, the per-axis close-code policy, a Zustand
  connection-state store, and the WS-event → TanStack Query bridge.
  `RealtimeProvider` runs the user-events channel for as long as there is a
  session.

```sh
npm run test:protocol   # protocol suite only (what protocol-integrity CI runs)
```

`web/src/protocol/__fixtures__/coverage.ts` classifies every fixture file as
covered or deferred to a later milestone; adding a fixture without
classifying it fails the suite. `APP_PROTOCOL_VERSION` in
`src/api/bootstrap.ts` is compared against the Android and iOS constants by
`protocol-integrity` — see PROTOCOL.md "How to bump".

## Sessions (WEB-07)

`/` is the live session list, `/agents/:agentId/new-session` the create
wizard.

- The list seeds over REST and is kept live by the agent events channel.
  `sessions.changed` / `session.updated` / `session.attention` are written
  straight into the TanStack cache (`src/realtime/sessionCache.ts`) rather
  than invalidated — each carries a complete object, so there is no delta to
  drift, and invalidating would cost a round trip per title change.
- Auto-resume returns you to the last session on entry.
  Its once-per-attempt flag is module-scoped, not component state:
  a route remount on Back would otherwise re-navigate and trap you.
- The wizard keeps a separate option bucket per mode, so switching branches
  cannot leak a field into the request. `skip_permissions` defaults to
  **true** on wire absence, so every boolean is sent explicitly and the
  request is re-parsed through a strict schema before it goes out.
- The wizard opens on the options each kind was last **successfully** launched
  with, and pre-selects the last project.
  - These are written only after a launch succeeds, from values pinned before
    the request goes out — the option controls stay live while it is in flight.
  - Worktree/branch, Antigravity `add_dirs` and the Auto Scan prompt are
    per-session and deliberately not remembered, matching Android and iOS.
- Three new persisted keys: `mobaide-agent` (selected agent),
  `mobaide-sessions` (last-used session per agent) and `mobaide-launch-prefs`
  (last-used launch options per mode — global, not per agent, as on mobile).

## Agents and pairing (WEB-08)

`/agents` lists and manages agents; `/pair` completes a pairing from a link or
a pasted code.

- `/pair` is **outside both route guards** on purpose. The payload rides in the
  URL fragment, which `requireAuthMiddleware`'s `?next=` (built from
  `pathname + search`) would drop. So the route does four things:
  1. It captures the payload itself.
  2. It stashes the payload under `sessionStorage` key `mobaide-pairing`.
  3. It scrubs the URL with `navigate({… hash: ''}, {replace: true})` —
     assigning `location.hash` would *push* a history entry Back re-exposes.
  4. Only then does it bounce to sign-in.
- `src/protocol/pairingPayload.ts` is the TypeScript arm of the four-way
  `MOBA1|…` contract, driven by `tests/protocol_fixtures/pairing/qr_payload.json`.
  The paste box strips all whitespace first, because a terminal soft-wraps the
  one-line payload and a copied soft-wrap carries the breaks.
- Trust pins are TOFS in `localStorage` key `mobaide-trust-pins`; a mismatch is
  a hard refusal with no override, matching mobile. Safari's 7-day storage cap
  weakens this in a browser — CLA-199 replaces it with a server-side pin.
- The claim's 200 is not proof of pairing. `agent.status_changed` with
  `online: true` is, and it already invalidates `['agents']`, so the 60 s wait
  rides the existing query rather than polling.
- Two more persisted keys: `mobaide-trust-pins` (`localStorage`) and
  `mobaide-pairing` (`sessionStorage`, the deep-link handoff).

## Terminal (WEB-05)

`/agents/:agentId/sessions/:sessionId` is the **only lazy route** — xterm.js
and its WebGL addon outweigh the rest of the SPA, so they load on first
attach rather than on the sign-in screen. It is reached from the sessions
list at `/`. The layering mirrors the WEB-04 split:

- `src/terminal/` is framework-free: the replay, follow/unread, paste,
  submit and key-policy reducers, the clipboard/renderer/theme adapters, and
  the session model that owns the socket and the emulator. Models live
  outside React in `terminalStore`, so navigating away and back keeps the
  buffer instead of paying for a fresh replay.
- `src/routes/terminal/` is presentation only.
- `src/stores/terminalPrefsStore.ts` persists the terminal preferences under
  `localStorage` key `mobaide-terminal` (the second persisted key, after
  `mobaide-theme`; WEB-07 adds `mobaide-agent`, `mobaide-sessions` and
  `mobaide-launch-prefs`).

Dependencies: `@xterm/xterm` plus the `fit`, `webgl`, `clipboard` and
`unicode11` addons. `@xterm/headless` is a devDependency only — it backs the
buffer-contract test that pins the xterm behaviour the follow/unread logic
derives from, so an addon or major bump fails the suite rather than the UI.

Two behaviours deliberately diverge from Android and iOS under the CLAUDE.md
platform-convention exemption — **Full display mode only** (no Static /
forced-column mode, no horizontal pan) and the desktop keyboard copy/paste
surface. Both are spelled out with their reasons in the "Terminal screen
(WEB-05)" section of `docs/web-client.md`.
