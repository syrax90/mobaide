# Web Client

Browser SPA for controlling Mobaide agents through the central relay. Lives
in `web/` at the repo root. This document covers the workspace layout, the
typed two-schema API pipeline, the app-shell architecture, testing, CI, and
how the web client fits the cross-platform parity policy. For day-to-day
commands see `web/README.md`.

## Status and roadmap

Bootstrapped by WEB-01 (CLA-188): production-grade shell with routing,
theming, error boundaries, toasts, state/data scaffolding, typed API
clients, tests, and CI. WEB-03 (CLA-190) added the auth & account gate:
login / register / verify-email / forgot-reset-password screens, a session
that survives reload and refreshes silently, and the devices and account
(incl. delete + 14-day cancel) screens. WEB-04 (CLA-191) added the realtime
protocol core — WebSocket channel manager, binary terminal codec, the
TypeScript arm of the cross-platform golden fixtures, and the WS-event →
TanStack Query bridge. The series is tracked in
Linear project "Web Client (React)" (CLA-188…CLA-200): deploy/relay
readiness (WEB-02), auth (WEB-03), WS protocol core (WEB-04), terminal
(WEB-05), keyboard/shortcuts (WEB-06), sessions (WEB-07), pairing (WEB-08),
projects/git/templates (WEB-09), autoscan/notifications/settings (WEB-10),
Apple sign-in on web (WEB-11), pairing phase 2 (WEB-12), QR image decode
(WEB-13).

Production serving model (WEB-02): `app.mobaide.com` as its own Caddy site
block serving the SPA from disk and reverse-proxying `/api`, `/ws`,
`/relay`, `/proxy` to the central server — fully same-origin, no CORS. The
Vite dev server mirrors this with a proxy to `http://localhost:9000`.

## Stack

| Concern | Choice |
|---|---|
| Build | Vite 8 (Rolldown), npm, Node `^22.22.2 \|\| ^24.15.0 \|\| >=26.0.0` (lockfile-supported intersection; jsdom is the tightest constraint) |
| UI | React 19, TypeScript strict (6.x — deliberately not 7, typescript-eslint has no stable TS 7 API yet) |
| Routing | React Router v8, **Data mode** (`createBrowserRouter`) |
| Client state | Zustand 5 (persisted theme store, toast store) |
| Server state | TanStack Query v5 (`src/lib/queryClient.ts`) |
| HTTP | openapi-fetch over generated `openapi-typescript` types |
| Unit/component tests | Vitest 4 + Testing Library (jsdom) |
| E2E | Playwright (chromium) against `vite preview` |
| Lint | typescript-eslint v8 `projectService` + `recommendedTypeChecked`, eslint-plugin-react-hooks v6 flat config |
| Forms | react-hook-form + Zod — locked for the series, first consumers are the WEB-03 auth forms |

## Workspace layout

```
web/
├── scripts/dump_openapi.py   # schema dump (see below)
├── openapi/*.json            # committed OpenAPI schemas (generated)
├── redocly.yaml              # multi-schema map driving openapi-typescript
├── src/
│   ├── main.tsx              # StrictMode > AppErrorBoundary > QueryClientProvider > ThemeProvider > RouterProvider + ToastViewport
│   ├── app/                  # router + route guards, RootLayout/AuthLayout, AppGate, RouteError, AppErrorBoundary, ThemeProvider
│   ├── routes/               # route components (Home, auth/*, account/*)
│   ├── auth/                 # session store + transport, user/agent token axes, single-flight refresh, GIS loader
│   ├── components/toast/     # zero-dependency toast primitives
│   ├── components/form/      # react-hook-form fields (+ auth/ Google button, account/ grace banner)
│   ├── protocol/             # framework-free wire layer (WEB-04): terminal codec,
│   │                         #   relay URL/path builder, wire JSON + Zod schemas,
│   │                         #   autoscan frames, __fixtures__/ golden-fixture loader
│   ├── realtime/             # WS channel manager, close policy, connection store,
│   │                         #   event→Query bridge, RealtimeProvider (WEB-04)
│   ├── stores/               # Zustand stores (themeStore, toastStore)
│   ├── lib/queryClient.ts    # TanStack Query defaults
│   ├── lib/queryKeys.ts      # central query-key factory + wsBackedQueryOptions
│   ├── api/
│   │   ├── generated/*.ts    # committed openapi-typescript output — never hand-edited
│   │   ├── auth.ts           # bearer-token provider seams (filled in by src/auth/install.ts)
│   │   ├── clients.ts        # relayClient + createAgentClient(agentId), auth + 401-recovery middleware
│   │   ├── errors.ts         # HTTP/network error normalisation
│   │   └── *Repository.ts    # typed call wrappers (auth, devices) + anonymous bootstrap
│   ├── styles/               # theme tokens (data-theme) + global styles
│   └── test/setup.ts         # jest-dom matchers for Vitest
├── e2e/                      # Playwright specs
└── vite.config.ts, tsconfig*.json, eslint.config.js, playwright.config.ts
```

## Typed API layer — two schemas by design

The relay's `/proxy/{agent_id}/{path:path}` catch-all
(`central_server/central_server/relay/mobile_proxy.py`) forwards arbitrary
REST requests to the paired agent and is untyped in the central-server
schema (no `response_model`). The proxied session/project/git surface is
typed only by the **agent's** own OpenAPI schema. The web client therefore
carries two generated type sets and two clients:

- **Relay axis** — `openapi/central-server.json` →
  `src/api/generated/central-server.ts` → `relayClient` (base URL `/`,
  same-origin).
- **Agent axis** — `openapi/agent.json` → `src/api/generated/agent.ts` →
  `createAgentClient(agentId)` (base URL `/proxy/<agentId>`; the relay
  strips the prefix, so agent-schema paths resolve beneath it).

The two axes authenticate differently, mirroring the Android split
(`CentralAuthInterceptor` vs `AuthInterceptor`): the relay's `get_mobile_user`
guard on `/proxy/...` accepts only the per-agent `mobile_access` JWT, while
central `/api/*` routes take the dashboard/user JWT. `src/api/auth.ts`
therefore exposes two seams — `setUserTokenProvider()` (consumed by
`relayClient`) and `setAgentTokenProvider(agentId => token)` (consumed by
each `createAgentClient(agentId)`) — both filled in at startup by
`src/auth/install.ts` (WEB-03).

Caveat: the agent schema types the agent's **entire** REST surface, but the
relay tunnel's agent-side dispatcher forwards only an allowlisted subset —
a call can type-check yet 404 through `/proxy` (e.g. `/api/health`,
`/api/auth/token`, `/internal/hook` are direct-only). Scoping the typed
surface to the tunnel is tracked in CLA-201.

WebSocket frame/event types are deliberately absent from both schemas; they
are hand-written in `src/protocol/` (see *Realtime layer* below).

### Schema pipeline

`web/scripts/dump_openapi.py` builds both FastAPI apps **in-process** via
`create_app(config)` + `app.openapi()` — no running server, no env vars. It
injects minimal configs (the central server's admin-gated docs exposure is
irrelevant to this path), points all store paths at a tempdir, and writes
deterministic JSON (`sort_keys`, `indent=2`, trailing newline) with
`info.version` pinned to `0.0.0` so component releases don't churn the
committed schemas. Requires both packages installed editable
(`pip install -e 'mobaide_agent[dev]' -e 'central_server[dev]'`, Python
3.12 to match CI).

Regeneration chain after changing agent or central-server routes:

```sh
cd web && npm run schema:dump && npm run schema:generate
```

`npm run schema:check` verifies both committed layers: the dump script's
`--check` mode compares the JSON without writing, then the TS layer is
regenerated and checked via `git status` (openapi-typescript's own `--check`
flag is a no-op whenever a `redocly.yaml` multi-schema config is present, so
the git-based gate — the same one CI uses — is authoritative).

The dump script also normalizes FastAPI's operationIds for multi-method
routes (the relay's `/proxy` catch-all): FastAPI assigns all five methods
one operationId whose embedded method suffix is drawn from a Python set, so
raw dumps are both spec-invalid and nondeterministic across processes; the
script rewrites them to `<base>_<method>`.

## Realtime layer (WEB-04)

Two modules, split by dependency weight. `src/protocol/` is plain TypeScript
— no React, no TanStack — so it can be tested in a Node environment against
the shared goldens; `src/realtime/` owns the sockets and is the only part
that touches app state.

**Channels and the relay asymmetry.** The SPA is same-origin with the relay,
so every URL is built from `window.location.origin`. Agent channels
(`ws/events`, `ws/session/{id}`, `ws/autoscan`) go through the relay path
rewrite — note the agent id precedes the session id
(`relay/ws/session/{agentId}/{sessionId}`) — while `ws/user-events` is built
in `direct` mode against the same origin and is **never** rewritten. Both
behaviours are golden rows in `relay/relay_mapping.json`. The token is
appended raw: `URLSearchParams` would percent-encode a JWT's `+` and `=` and
the relay would reject it.

**Lifecycle.** Ported from Android `WebSocketConnection` and iOS
`WebSocketCore`: backoff sleeps the current ladder value then doubles it
(1 s → 30 s), reset on open and on `forceReconnect()`; a generation counter
— incremented only by `forceReconnect()` — drops events from a superseded
socket; `buildUrl` is re-evaluated on every attempt so a rotated token is
picked up, and resolving to `null` stops without burning the ladder.
**Deliberate divergence from mobile:** web adds 50–100 % jitter on top of
the ladder, because a browser user commonly has several tabs and a relay
restart would otherwise bring them all back in lockstep.

**Close codes are per axis.** On the user axis 4001 → `tokenRejected`,
4010 → `accountRemoved`, 4002 → the incompatible-server takeover. On the
agent axis the same codes stop the loop but leave the session alone, because
per-agent tokens are always re-mintable — 4001 and 4003 additionally drop
the cached token so the next connect re-mints. 4011, 4012 (the ordinary
attach-to-an-offline-agent case) and 1013 always reconnect.

**Known browser limitation:** the server sends 4001/4010 *before*
`websocket.accept()`, ASGI turns that into an HTTP 403 handshake rejection,
and the WebSocket API exposes no upgrade status — so those two normally
reach a browser as `1006`, indistinguishable from a network failure. The
mitigation is that every connect attempt mints a fresh token through the
WEB-03 seams; the REST 401 path remains the authoritative session teardown.
Browsers also expose no ping API, unlike both mobile clients (CLA-219).

**Resume hooks.** The session I/O channel re-asserts the cached PTY size on
every open — the client is the sole size authority and the agent spawns at
24×80 — and exposes `resendCachedSize()` for the two further re-assert
points WEB-05's replay state machine owns. The auto-scan channel sends
`{subscribe, resume_from_seq}` as the first thing on every open (the agent's
grace window is 200 ms direct, 1 s through the relay), rebuilt from a live
per-project cursor so a reconnect resumes at the true high-water mark; it
also drops `seq <= cursor` because the server's replay is best-effort.

**Wire DTO policy.** `z.looseObject` everywhere, so unknown keys survive;
kind strings stay open (proto-v7 `custom_<12hex>` and anything newer are
preserved verbatim, only an explicit `null` falls back to `claude`); and
`parseWireJson` promotes integer literals beyond 2^53 to `bigint` via the
ES2026 `JSON.parse` source-access reviver, re-emitting them as bare JSON
integers. No `lossless-json` dependency, and runtimes without source access
degrade only above 2^53.

**Event → Query bridge.** Refresh-on-connect invalidates agents / templates
/ shortcuts / custom types on the user channel (Android `UserEventBus`
order) and sessions / projects on an agent channel. Per event the policy is
`invalidateQueries`, never a hand-applied delta; `custom_type.deleted` also
invalidates shortcuts because the server cascades them in one transaction
with no companion events. User-channel payloads are schema-validated before
anything is invalidated (a malformed payload is dropped, as on mobile);
agent-channel payloads are only routed by `type`, since their shapes belong
to WEB-07/09/10. Unknown envelope types are ignored.

`onlineManager` is driven from reachability rather than `navigator.onLine`,
conservatively: offline only after two consecutive pre-open failures of the
user-events channel, and back online on any WS open, any REST response, or
an intentional stop — so the signed-out login flow is never paused.
`staleTime` stays at the global 30 s; `wsBackedQueryOptions`
(`staleTime: Infinity`) is opt-in for lists that actually have a push
channel.

### Golden fixtures, TypeScript arm

`src/protocol/*.test.ts` read repo-root `tests/protocol_fixtures/` off disk
— the same files the iOS test target consumes — under
`// @vitest-environment node`, asserting hex strings rather than typed
arrays. `npm run test:protocol` runs just this suite, and
`protocol-integrity.yml` runs it on every PR alongside the Python checks.

Coverage is deliberately partial and declared in
`src/protocol/__fixtures__/coverage.ts`: WEB-04 asserts the frame, relay and
eight realtime DTO fixtures, and every other file is listed against the
milestone that will claim it. The gate fails when a fixture file is neither
asserted nor assigned an owner — the same drift protection iOS's
bidirectional registry provides. One cross-arm constraint: Swift `Codable`
drops unknown keys on re-encode while `z.looseObject` preserves them, so
unknown-key round-trips are asserted in TypeScript-only tests, never added
to a shared fixture.

## CI — `.github/workflows/web-ci.yml`

Runs on every PR and push to main (no paths filter — schema drift is caused
by `central_server/`/`mobaide_agent/` edits, not just `web/` ones). Single
job: Python 3.12 + editable installs → **schema drift gate** (regenerate
JSON, fail on `git status --porcelain` diff) → Node 22 + `npm ci` → **type
drift gate** (regenerate TS, fail on diff) → typecheck → lint → Vitest →
build → Playwright (`playwright install --with-deps chromium` + `npm run
test:e2e`). Playwright joined the workflow with WEB-03, once real user
flows existed to drive; the specs stub `/api/**` with `page.route`, so CI
needs no relay.

A second workflow also gates `web/`: `protocol-integrity.yml` carries a
`web-protocol-fixtures` job (Node 22, `npm ci`, `npm run test:protocol`) so
a change to the shared fixtures fails on the PR that makes it, and its
constants step compares `APP_PROTOCOL_VERSION` three ways — Android gradle,
iOS xcconfig, and `web/src/api/bootstrap.ts`.

Release integration: `web` is a release-please package (`release-type:
node`, component `web`) and a release-bearing PR-title scope
(`feat(web): …`); `web-v*` tags trigger no workflow yet. The SPA ships
manually via `deploy/prod/deploy_web.py` (WEB-02): `npm run build`, tar +
scp to `/opt/mobaide/www/releases/<tag>/`, then an atomic `current`
symlink swap — Caddy serves the new release immediately, no container
restart, `--rollback` repoints at the previously-live release. Runbook: the
"Deploying the web SPA" section of docs/deployment.md.

## App shell

- **Theming** — `themeStore` persists `light | dark | system`
  (`localStorage` key `mobaide-theme`); `ThemeProvider` resolves it to
  `document.documentElement.dataset.theme` and tracks
  `prefers-color-scheme` while in `system`. An inline pre-paint script in
  `index.html` stamps `data-theme` before first paint (no theme flash). All
  colors are CSS custom properties under `:root[data-theme=…]` in
  `src/styles/theme.css`. The shell ships neutral tokens; adopting the
  marketing "modern pixel" design system (docs/website.md) is a decision
  for the feature milestones. **CSP coupling:** the production Caddy site
  block pins that inline script by `sha256` hash in `script-src` — in
  BOTH `deploy/prod/Caddyfile.*` files. Editing the script means updating
  both hashes; `deploy_web.py` hard-fails on drift (the escape hatch is
  `--force`, at the cost of a theme flash).
- **Errors, two tiers** — the router's `errorElement` (`RouteError`)
  renders loader/render errors and doubles as the 404 page;
  `AppErrorBoundary` (class component) wraps the providers as a last
  resort.
- **Toasts** — Zustand store + two presentational components, no
  dependency; `ToastViewport` mounts as a sibling of `RouterProvider` so
  toasts survive route errors.
- **Auth & session (WEB-03)** — `src/auth/authState.ts` mirrors Android's
  `SessionManager.AuthState` state-for-state (`unauthenticated`,
  `userOnly`, `fullyConnected`, `incompatibleServer`). The user JWT lives
  in `sessionStorage` — per-tab, never on disk, because the relay keeps no
  JWT denylist — and a booting tab asks open peers to hand the session
  over via `BroadcastChannel`; the always-re-mintable per-agent
  `mobile_access` tokens live in `localStorage`. Sessions are minted with
  the server's opt-in `refreshable: true` flag, so the access token is
  short-lived (`USER_SESSION_ACCESS_EXPIRE_MINUTES`, default 15) and
  rotates against `POST /api/auth/refresh`; refreshes are single-flighted
  across tabs with `navigator.locks`, and a 401 is terminal (sign out)
  while anything else is transient (keep the tokens). Route access is
  enforced by React Router **middleware**, not loaders — loaders run in
  parallel, a parent middleware short-circuits the whole match — and the
  wanted path survives the bounce as `?next=`. `GET /api/version` is the
  single anonymous bootstrap call: protocol gate *and* the
  `google_client_id` the Google Identity Services button initialises with,
  so the SPA needs no build-time env var.

## Parity policy

The web client participates in the three-way Cross-Platform Parity rule in
the root `CLAUDE.md`: client-facing changes must be mirrored across
Android, iOS, and web (or a Linear issue filed) — with the transition rule
that feature areas not yet built on web are covered by their planned WEB-*
issue rather than per-change issues. Terminal behavior parity references:
the client is the sole PTY size authority and must re-assert size on every
reconnect (see `docs/ios-client.md` and PROTOCOL.md).
