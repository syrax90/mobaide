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
TanStack Query bridge. WEB-05 (CLA-192) added the terminal screen on
xterm.js 6: replay, follow mode and the unread badge, desktop copy/paste,
the composer and the prompt-template picker. WEB-07 (CLA-193) added the
sessions area — the live list with auto-resume, and the six-branch create
wizard. WEB-08 (CLA-194) added agents and pairing Phase 1 — the `/pair`
route with its deep link and paste box, the `/agents` screen, and the
pairing link the agent now prints under its QR. WEB-09 (CLA-195) added the
three CRUD surfaces: projects with accessible reorder and three-state updates,
the per-project git branch/worktree list with its deferred delete, and prompt
template management. WEB-10 (CLA-196) added the last live surfaces: the
Auto Scan monitor with its ANSI log pane, the attention-notification
surface, and the settings hub. WEB-11 (CLA-198) added Sign in with Apple to
login, register and the delete-account re-auth — the gate that lets an
account created in the iOS app, private-relay address and no password, reach
the web client at all; the same button also landed on `/portal/*`, and the
server's Apple audience check grew to accept the browser Services ID
alongside the iOS bundle id. WEB-12 (CLA-199) added pairing phase 2: the
reversed device-code direction at the guarded `/agents/pair` route,
`/agents`'s new "Add an agent" / "Pair from a code" button pair, and the
account-level `trust_key_fp` pin that outlives a wiped browser. The series
is tracked in
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
│   ├── routes/               # route components (sessions/*, auth/*, account/*, terminal/*)
│   ├── terminal/             # framework-free terminal core (WEB-05): session model,
│   │                         #   replay/follow/paste/submit/key reducers, clipboard,
│   │                         #   renderer ladder, theme, model store
│   ├── auth/                 # session store + transport, user/agent token axes, single-flight refresh,
│   │                         #   GIS + Sign in with Apple loaders
│   ├── components/toast/     # zero-dependency toast primitives
│   ├── components/form/      # react-hook-form fields (+ auth/ Google and Apple buttons,
│   │                         #   account/ grace banner)
│   ├── protocol/             # framework-free wire layer (WEB-04): terminal codec,
│   │                         #   relay URL/path builder, wire JSON + Zod schemas,
│   │                         #   autoscan frames, SGR parser, __fixtures__/ loader
│   ├── autoscan/             # framework-free auto-scan core (WEB-10): frame reducer
│   │                         #   with its reduction cursor, channel hook, display helpers
│   ├── notifications/        # attention content/suppression/service + the event
│   │                         #   bridge (WEB-10)
│   ├── realtime/             # WS channel manager, close policy, connection store,
│   │                         #   event→Query bridge, RealtimeProvider (WEB-04)
│   ├── stores/               # Zustand stores (themeStore, toastStore, terminalPrefsStore,
│   │                         #   selectedAgentStore, lastSessionStore,
│   │                         #   notificationPrefsStore, visibleSessionStore)
│   ├── lib/queryClient.ts    # TanStack Query defaults
│   ├── lib/queryKeys.ts      # central query-key factory + wsBackedQueryOptions
│   ├── api/
│   │   ├── generated/*.ts    # committed openapi-typescript output — never hand-edited
│   │   ├── auth.ts           # bearer-token provider seams (filled in by src/auth/install.ts)
│   │   ├── clients.ts        # relayClient + createAgentClient(agentId), auth + 401-recovery middleware
│   │   ├── errors.ts         # HTTP/network error normalisation
│   │   └── *Repository.ts    # typed call wrappers (auth, devices) + anonymous bootstrap
│   ├── styles/               # theme tokens (data-theme) + global styles
│   └── test/                 # jest-dom matchers + the localStorage stand-in
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

The 4002 branch is parity, not a live path: no server route sends it to a
user socket (it is the relay↔agent handshake only). What actually catches a
redeploy landing mid-session is the reconnect itself — a redeploy drops the
socket, and `refreshOnUserEventsConnect` invalidates `['server-info']`, whose
query is otherwise pinned at `staleTime: Infinity`. Android re-probes on every
launch for the same reason.

Because the relay only authenticates at handshake and a per-agent JWT is
minted without an `exp`, a rejected agent credential is otherwise invisible
and never rotated. Three consecutive failures to open an agent channel
therefore drop the cached token so the next attempt re-mints. This is also
why a null URL is treated differently per axis: on the user axis it means
signed out and the channel stops, while on the agent axis it usually means the
mint itself just failed, so the channel keeps backing off rather than
stranding on a blip.

**Known browser limitation:** the server sends 4001/4010 *before*
`websocket.accept()`, ASGI turns that into an HTTP 403 handshake rejection,
and the WebSocket API exposes no upgrade status — so those two normally
reach a browser as `1006`, indistinguishable from a network failure. The
mitigation is that every connect attempt mints a fresh token through the
WEB-03 seams; the REST 401 path remains the authoritative session teardown.
Browsers also expose no ping API, unlike both mobile clients (CLA-219).

**Resume hooks.** The session I/O channel re-asserts the cached PTY size on
every open — the client is the sole size authority and the agent spawns at
24×80. WEB-05's replay state machine owns two further re-assert points
(first inbound binary while reconnecting, and `replayEnd`) and drives them
through `setSize`, because its model caches the measured grid *before* the
resize debounce and can therefore be ahead of the channel's own cache. The
auto-scan channel sends
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
order) and projects on an agent channel. Per event the default policy is
`invalidateQueries`, never a hand-applied delta; `custom_type.deleted` also
invalidates shortcuts because the server cascades them in one transaction
with no companion events. Payloads are schema-validated before anything is
written, and dropped rather than thrown on failure — this runs inside the
socket's listener loop.

The three agent-channel session events are the one exception, added by
WEB-07 (`src/realtime/sessionCache.ts`): `sessions.changed` carries the
agent's complete list and `session.updated` / `session.attention` carry a
whole row each, so there is no delta that could drift, and they are written
straight into the cache. A snapshot **may** seed a cache nothing has
fetched; an upsert **must not**, or one row would masquerade as the whole
list. Sessions are therefore absent from refresh-on-connect: the agent
re-pushes its snapshot on every connect, and an extra REST GET would only
race it. The remaining agent-channel payloads are still routed by `type`
alone, their shapes belonging to WEB-09/10. Unknown envelope types are
ignored.

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

## Sessions (WEB-07)

The index route `/` is the live session list; `/agents/:agentId/new-session`
is the create wizard.

**Agent selection.** The list is agent-scoped, and web is the first client
that can see more than one agent, so `GET /api/agents` backs a small picker
(hidden when there is exactly one). The choice persists under `localStorage`
key `mobaide-agent` and the resolution is written back, which makes the
automatic pick sticky — otherwise an agent dropping offline would silently
move the user's list. Full pairing is WEB-08. While the selected agent is
offline the events channel stays open (close code 4012 is the ordinary
attach-to-an-offline-agent case and reconnects) but the REST query is
disabled, since an offline agent 503s on every mount.

**Auto-resume** ports `SessionListViewModel.maybeAutoResume` verbatim,
including its asymmetry: a stored id that is *absent from the list* is left
alone, and only one that is present but dead gets cleared. "Absent" only
counts once `useSessionsSynced` confirms the connected agent has pushed a
fresh `sessions.changed` snapshot — a list left over from a dropped channel
is not evidence a session is gone, and resuming off one is what used to
attach into an id the agent had already lost (Android
`SessionRepository.observeSessionsSynced`, iOS
`SessionRepository.sessionsSynced`). Two web-specific changes beyond the
port. The once-per-attempt flag lives at module scope keyed by agent id,
not on the component — a web route unmounts on navigate and remounts on
Back, so component state would re-fire and trap the user in a loop. And the
id is recorded **on open as well as on create** (`mobaide-sessions`, keyed
per agent): mobile records it only on create, which on web would leave the
feature dormant for anyone who starts sessions from their phone. Android and
iOS follow in CLA-229 / CLA-230.

**Cards** follow iOS: state dot, title, kind label, cwd, and the attention
chip Android does not render (`awaiting_permission` → "Needs permission",
`turn_finished` → "Ready", `ended` → "Ended"). Unknown kinds fall back to
`kind_label`, then the live custom-type registry, then "Custom" — never the
raw wire token. Stopping uses iOS's Stop / Force Stop / Cancel confirmation.

**The create wizard** is three steps (project → agent → options), iOS's
shape rather than Android's seven. State is one reducer over **per-mode
buckets**, and the request builder switches on mode, so a Codex request
cannot name a Claude field — the alternative, one form with
`shouldUnregister`, would make that a property of what happens to be
mounted. Auto Scan gets its own Claude bucket, since its `skip_permissions`
defaults the opposite way. Only `step`, `mode` and the project selection
live in the URL; the ~30 option fields do not, so no prompt text reaches
browser history.

`skip_permissions` is the trap the wizard is built around: its **wire-absence
default is `true`**, so an omitted field launches with
`--dangerously-skip-permissions`. Requests are re-parsed through
`createSessionRequestSchema` (a `z.strictObject`, unlike the inbound loose
schemas) and typed as the *generated* request, whose booleans are required —
so a dropped flag is a compile error rather than a silent escalation.

Custom agent types are listed, launched, created and deleted here; editing
is CLA-231. A launch sends only `cli: "custom_<id>"` — the relay resolves the
stored command server-side, and the agent-direct REST path rejects custom
kinds outright. The Auto Scan branch posts to `/api/autoscan/start` and
returns to the list; its monitor screen is WEB-10.

## Agents and pairing (WEB-08)

Three routes: `/agents` (guarded, in the app shell) lists and manages
agents; `/pair` completes a pairing from a code printed elsewhere; the
guarded `/agents/pair` (WEB-12) mints one instead.

**Why `/pair` sits outside both guards.** The payload rides in the URL
*fragment*, and `requireAuthMiddleware` builds its `?next=` from
`pathname + search` only, so a bounce through sign-in would drop it. Putting
the nonce in a query string instead would put it in the relay's access log.
So the route is public: it reads `useLocation().hash` in a mount effect,
stashes the payload under `sessionStorage` key `mobaide-pairing`, scrubs the
URL, and only then sends a signed-out visitor to `/login?next=/pair` —
`useAfterAuth` brings them back. The scrub is `navigate({… hash: ''},
{replace: true})`, never `location.hash = ''`: assigning to `location.hash`
*pushes* a history entry, so Back would re-expose the nonce, and a bare
`history.replaceState` would desync the router's own location.
`RequireAnonymous` is equally wrong for the route, because a signed-in user
must be able to open a pairing link.

**The payload contract.** `src/protocol/pairingPayload.ts` is the fourth
implementation of `MOBA1|{relay_url}|{nonce}|{trust_key_fp}|{exp_unix}`, and
the first shared golden for it —
`tests/protocol_fixtures/pairing/qr_payload.json` — now drives the Python,
TypeScript and Swift arms (Android reads no fixtures at all; CLA-235).
Building it surfaced that the agent's `parse_qr_payload` was *looser* than
both mobile parsers: Python's `int()` strips surrounding whitespace, honours
`_` digit separators and has no 64-bit ceiling, and its blank check missed
whitespace-only fields. That was tightened rather than encoded into the
golden. The web arm adds one documented divergence: an `exp_unix` outside
IEEE-754 safe-integer range is rejected rather than silently rounded, which
only matters 285 million years from now.

The paste box strips **all** whitespace before the anchored `^MOBA1|` test —
the payload is printed on one unbroken line, but a terminal soft-wraps it and
a copied soft-wrapped line carries the breaks. It also accepts the pairing
link and a bare fragment, since the agent prints the payload and the link next
to each other and either can end up on a clipboard.

**Order of operations** follows both mobile clients exactly: parse → expiry →
trust pin → confirm → claim. Expiry before trust is load-bearing (Android pins
it in `skipsTrustKeyCheck`) — a stale screenshot from before a legitimate key
rotation must read as "expired", not as a MITM refusal. The confirm step is
kept even on the deep link: it names the relay, which is the phishing
mitigation for a clicked link exactly as it is for a scanned QR.

**Trust pinning** is TOFS in `localStorage` (`mobaide-trust-pins`), keyed by
the relay URL normalized the way `TrustKeyPinning` normalizes it — trim, strip
every trailing slash, lowercase. A mismatch never overwrites and has no
override affordance, matching the mobile rule.

One deliberate split from mobile: both mobile clients fold the read and the
write into a single `verifyOrPin` that pins on first sight, which is safe when
the only input channel is a camera the user is aiming. Here the input is a URL,
so `checkTrustPin` (read-only) runs during validation and `pinTrustKey` runs
from the confirm button. Folded together, any link a user merely *opened* would
write a permanent pin — and since a mismatch is a hard refusal with no
override, a payload naming the real relay with a wrong fingerprint would lock
that browser out of pairing for good, recoverable only by clearing site data.

The confirm step also carries the RFC 8628 §5.4 remote-phishing instruction,
which is what makes it a mitigation rather than a speed bump: a pairing link
works for whoever opens it, an attacker can run `pair-qr` against the *real*
relay on their own machine, and every field on that screen would look correct.
Only the code the user can read off their own terminal separates the two cases,
so the screen says to compare it. Mobile has the same dialog without the
instruction, but its delivery channel is a camera pointed at the host, so the
remote vector does not exist there.

The browser is a weaker home
for this than a phone and knowingly so: Safari caps script-written storage at
7 days of no interaction, degrading the pin to trust-on-most-recent-scan (and
regenerating the device UUID, which under last-login-wins orphans a device
row). CLA-199 replaces it with an account-level server-side pin.

**Waiting for the agent.** A claim's 200 only proves the relay persisted
credentials; `ws_delivered` is tri-state diagnostic (`null` from an older
relay must never collapse to `false`) and only refines the timeout copy. The
authoritative signal is `agent.status_changed` with `online: true`. Android
polls its cached list once a second; web does not need to, because that event
already invalidates `['agents']` and an invalidation refetches the mounted
query. What web keeps is the reason mobile refreshes *twice* — a status event
landing between the claim's refresh and the watcher's start would be rolled
back by the in-flight stale snapshot — plus a 5 s defensive interval so a
dropped socket cannot strand the screen for the full 60 s. The timeout copy
is iOS's short form; Android's enumerates `Get-ScheduledTask` and
`agent.err.log` paths, which are Windows-specific by design.

**`/agents`** is full mobile parity: cards (status dot, name, Online/Offline,
agent id, version, added, last seen), manual add, re-register, remove, and the
install-commands panel. Two web-only card fields, both because a browser is
the first client that can hold several agents at once: the agent id, which is
what each host's `.env` carries, and the agent version, which is the fastest
way to spot a host that missed an auto-update. The per-OS `.env` table and the
install one-liners are byte-identical to `AgentCredentialsSheet` on both
mobile clients, and are deliberately not OS-detected — the host is a different
machine from the browser.

Removing an agent calls `clearAgentToken(agentId)`, never
`sessionStore.agentDisconnect()`, which clears *every* agent's tokens; it also
drops the agent's events channel and its `mobaide-agent` / `mobaide-sessions`
entries. The credentials panel is an inline panel rather than a `<dialog>`
precisely so no Escape handler or backdrop can dismiss the one and only
showing of a token — the web analogue of Android's `confirmValueChange` and
iOS's `interactiveDismissDisabled`. Android's `EXTRA_IS_SENSITIVE` clipboard
flag has no browser equivalent, so a copied token lands in ordinary clipboard
history; that is why the panel points at pairing instead.

**Pairing phase 2 (WEB-12).** `/agents/pair` (`PairHost`) is guarded,
unlike `/pair`: minting a code needs a session, and there is no URL
payload for a bounce through sign-in to drop. It mints a code from
`POST /api/pairing/device-code`, polls `GET .../device-code/{id}` for
the agent's redeem, and confirms through `POST .../confirm` — the same
completion path `/pair`'s claim uses. See [qr-pairing.md](qr-pairing.md)
for the protocol and the account-level `trust_key_fp` pin referenced
above.

## Projects, git & templates (WEB-09)

Three routes: `/projects` (agent-scoped, with the same agent picker and
`mobaide-agent` selection the sessions list uses),
`/agents/:agentId/projects/:projectName/git`, and `/templates`. Projects and
git are agent-axis calls through `/proxy`; templates are central-server ones,
so the templates screen works while every agent is offline.

**`projects.changed` is written, not invalidated.** It carries the agent's
complete list, republished after every mutation *and* on every events-socket
connect — the relay sends `request_events_snapshot` and the agent answers by
publishing the whole list again (`relay/client.py`). So it joins
`sessions.changed` in `queryBridge`'s full-snapshot exception, via
`src/realtime/projectsCache.ts`. That also closes CLA-234: TanStack only
self-cancels a refetch once `dataUpdatedAt` is truthy, so an invalidation
landing during a **cold** fetch was deduped into it, the older list resolved,
and `staleTime: Infinity` pinned it. The general rule the bridge now states is
that an event carrying the new state is written, an event carrying only a
pointer to it is invalidated — and a pointer event's query must **not** pin
`staleTime: Infinity`, which is why the git-items query keeps ordinary
staleness. `git.changed` is the pointer case, and it now invalidates
`['agent', id, 'git', projectName]` rather than the project list it used to
hit. `refreshOnAgentEventsConnect` correspondingly swapped projects for the
git prefix: projects arrive in the connect snapshot, git items never do.

Because both REST and the push write the same cache, the project list keeps
its **wire shape** (`projectInfoSchema`) instead of a camelCase mapping — the
rule `sessionRepository.ts` already documents. The wizard keeps a narrow
`ProjectSummary = Pick<ProjectInfo, …>` of the seven fields it reads.

**Reorder is buttons first, drag second.** Every row carries Move up / Move
down plus a live region announcing `"<name> moved to position N of M"`; a
native `draggable` handle is a desktop-mouse convenience on top. Pragmatic
drag-and-drop was considered and rejected: it rides the HTML5 drag API, which
is mouse-based and unreliable on touch, and a phone browser is a first-class
target here. `PATCH /api/projects/order` takes the **complete** name set and
400s on any mismatch, so the move is applied to the full list even when a row
is hidden by an open undo window; the visible neighbour only names the
destination. The optimistic order is an *override*, never a cache write, and
is released only when the server's own order matches it exactly — Android's
`combine` / iOS's `recompute`, which is what keeps the two-client case
flicker-free.

**Project delete is deferred-commit**, not mobile's immediate delete with a
recreating Undo. The compensating `POST` cannot restore `linear_api_key` (the
server never echoes it) or the project's `order`, and web already had the
deferred model for git delete; one undo model beats two. Android and iOS
follow in CLA-240 / CLA-241. Only one deletion is in flight at a time — there
is no batch endpoint — so a second delete commits the first and opens a fresh
window.

**The project form tracks the live snapshot.** React Hook Form's `values` +
`resetOptions: { keepDirtyValues: true }` re-seed every untouched field while
protecting the edited ones, and `buildProjectUpdate` diffs against that same
snapshot, so the two agree by construction: untouched ⇒ equal ⇒ absent from
the patch. Flags and the Auto Scan filter are arrays, where a field-level
merge has no honest answer, so they carry their own touch gate instead. The
three-state contract is `linear_api_key` and `conda_env` only — absent keeps,
`""` clears, a value replaces; `name`/`path` have `min_length=1` server-side
so `""` there is a 422, `default_flags` clears with `{}`, and
`auto_scan_filter` has no clear state and is always replaced whole. Patches
are built by conditional key insertion, so absence is asserted with
`Object.hasOwn` rather than left to `JSON.stringify` dropping `undefined`.
Dirty-field registries are deliberately not used: RHF reports a field edited
and changed back, and misses one changed programmatically.

The Linear key gets the same handling as every other secret in the SPA: a
masked `PasswordField` with a reveal toggle (labelled "key", matching both
mobile clients' `SecretTextField`), and the save mutation pins `gcTime: 0` with
`onSettled: save.reset()` — otherwise TanStack keeps the submitted variables,
plaintext key included, readable app-wide through `getMutationCache()` for
minutes after the dialog closes. `NewSession`'s Codex login mutation already
does this for the same reason. Two related traps the form closes: `editing` is
resolved by name, so a project deleted or renamed by another client would flip
the dialog into *create* mode with the dirty fields intact — the editor now
closes instead; and the Auto Scan filter is validated even while hidden, so its
message renders precisely when the section is **not** shown, or emptying the
filter and then clearing the key would disable Save with no reason on screen.

**Git delete ports `GitScreenModel` whole**: rows hide immediately, the batch
lives outside the timer so a coalescing restart cannot discard it, `results`
map positionally onto the request, `force_required` restores the row *and*
re-prompts, a short `results` array restores the unreported tail, and the busy
409 restores the entire batch because the guard runs before anything is
deleted. The one thing not ported is the overlapping-load guard: `git.changed`
does arrive before the delete response, but the list lives in TanStack Query,
whose invalidation cancels an in-flight refetch on a warm cache. The 409 body
(`{"detail": {"busy_by": […]}}`) is in neither OpenAPI schema, so it has a
hand-written Zod mirror and a shared parser in `src/api/conflicts.ts` —
alongside the string-shaped project conflicts, which is the split
`ConflictParsing.swift` makes and Android duplicates.

**Templates** add create/edit/delete to the picker's existing list/touch/
favourite calls. The management grouping is Favorites then All, each sorted
case-insensitively — deliberately not the picker's recents-first grouping,
because a management list must show every template exactly once. The editor
keeps its draft on a failed save (iOS's guard; Android has none), and the body
is sent verbatim while the name is trimmed, since a body reaches the PTY
unchanged.

Fixture coverage: `ProjectInfo`, `BusyByResponse` and `BusyHolder` moved into
`COVERED_FIXTURES` with Zod mirrors — the first because it crosses a socket
into the cache, the other two because no schema documents them. The eleven
remaining WEB-09 fixtures are REST request/response bodies and were re-pointed
to `OWNER.generated`, the same call WEB-08 made for the pairing group.

## Terminal screen (WEB-05)

Built on **xterm.js 6.0** (`@xterm/xterm` plus the `fit`, `webgl`,
`clipboard` and `unicode11` addons). Route
`/agents/:agentId/sessions/:sessionId` — the **only lazy route**: xterm and
its WebGL addon outweigh the rest of the SPA, so they load on first attach
rather than on the sign-in screen. It is reached from the sessions list at
`/`.

**Layering.** `src/terminal/` is framework-free — pure reducers
(`terminalReplay`, `terminalFollow`, `terminalPaste`, `terminalSubmit`,
`terminalKeys`), the clipboard/renderer/theme adapters, and
`TerminalSessionModel`, which owns the socket, the emulator and a per-model
Zustand store the React tree reads. `src/routes/terminal/` is presentation
only. Models live in `terminalStore` outside React so navigating away and
back keeps the buffer instead of paying for a fresh replay; three stay
resident, least-recently-acquired evicted (iOS keeps one per session until
the session dies, a signal web has no list to deliver yet). Each model
self-registers with `liveChannels`, so `stopAllRealtime()` on a session
change tears it down — the screen re-acquires when its entry disappears.

**One Terminal in the DOM.** `open()` runs once, into a host `div` the model
owns and views adopt; a hidden terminal is an *unmounted* one, never a
`display: none` one, because xterm cannot `open()` there and `FitAddon`
mis-measures under it. `open()` is further deferred until the container has
a non-zero size.

**A session that ends while open stays readable.** `useSessionGone` treats
"absent from a synced snapshot" as the same proof of death auto-resume uses
(`useSessionsSynced`) and returns to `/` with "That session has ended." — but
never for a session it has already seen alive, so a session ending or being
stopped under the user leaves the output on screen instead (Android
`TerminalViewModel.sessionEnded`, iOS
`TerminalSessionModel.noteSessionSnapshot`). Because the route is directly
reachable with no session list ever mounted, it holds the agent's events
channel for its own lifetime rather than assuming one is already open. The
terminal on screen is also exempt from `evictTerminalSessions`'s
death-driven eviction, so navigating to a session that then dies cannot
dispose the model out from under the user. Creating a session seeds this
cache synchronously from the create response before navigating, so the gap
before the agent's own `sessions.changed` echo cannot read as the brand new
session already being gone.

**Replay** follows the mobile state machine exactly: `'b'` feeds
`ESC[H ESC[2J ESC[3J` **into the write stream** (never `Terminal.reset()`,
which executes immediately, jumps queued writes and clears the program's
DECSET modes), `'o'` chunks append, `'e'` is guarded on being in a span.
Any inbound binary upgrades a reconnecting channel to live *before* the
decode. The overlay is driven by socket state, not by the first frame
(ttyd's stuck-overlay bug), and stays up past `'e'` until the replay writes
report parsed through their `write` callbacks.

**Size authority.** The grid is cached before the 100 ms resize debounce and
re-asserted at all three live entries — socket open, first inbound binary
of a cycle, and `replayEnd` — because the agent spawns every PTY at 24×80
and never reports back.

**Follow mode is derived, not commanded**: pinned ⇔ `viewportY === baseY`.
That gets the CLA-184/CLA-178 fix for free — xterm scrolls to the tail on
genuine user input only, so a program's own `ESC[6n` cannot un-pause a
scrolled-up viewport, and the `isApplyingEmulatorOutput` discriminator both
mobile clients needed must **not** be ported. The unread tally counts rows
entering the scrollback as
`max(0, ΔbaseY) + max(0, −ΔviewportY)`, which stays exact past the
`scrollback` cap where `baseY` saturates (the web analogue of the iOS
`yDisp` freeze). Wheel, viewport pointer-drag, Shift+paging keys, resize and
the replay wipe re-baseline instead of accruing. Cap 9999, label `999+`,
cleared by re-pinning — same rule as mobile, and like iOS it does not count
DECSTBM region scrolls, because xterm shifts those in place; Android counts
them because its Termux fork genuinely appends them.

**Renderer.** WebGL, with `onContextLoss` → dispose (which reverts to the DOM
renderer with the buffer intact) → one bounded re-arm → stay on DOM after a
second loss.

**The grid is always dark**, whatever the app's theme preference — the same
choice both mobile clients make. An ANSI palette is a matched set with its
background, and pairing a dark 16-colour palette with the light app background
puts white on near-white (~1.4:1, and ~1.1:1 for bright white), which Claude
Code prints constantly. The 16 colours are spelled out as
`MobaideColors.ansiColors`, the table Android and iOS render with, so a red is
the same red everywhere: xterm's own built-in default is the **Tango** palette,
a different set entirely.

**Copy/paste is web-only design**, not a port: mobile has no physical
keyboard, its `Ctrl+C` is unconditionally `0x03`, and copy/paste live on the
long-press selection toolbar. Here Ctrl+C copies when there is a selection,
Ctrl+Shift+C always copies (never SIGINT), and Ctrl+V, Ctrl+Shift+V and
Cmd+V hand off to the browser's native paste — deliberately, since
`clipboard.readText()` is prompt-gated in Firefox and Safari. A persisted
"pass Ctrl+V to terminal" toggle sends `0x16` instead, for Claude Code's
image paste. The resulting `paste` event is intercepted in the **capture**
phase, ahead of xterm's own handler, which does the newline rewrite and the
bracket wrap but no ESC/C1 strip. Paste encoding is the mobile encoder:
strip ESC + C1 **first** (so a clipboard carrying `ESC[201~` degrades to a
literal `[201~`), then `\r?\n` → `\r`, then wrap iff the program enabled
DECSET 2004 — as **one** `i` frame (iOS's shape; three frames over a lossy
WS + relay can strand the closing marker) and capped at 1 MiB, because one
oversized frame trips the agent tunnel's 4 MB limit and drops *every*
session on that agent. The toggle is the one piece of terminal state that
outlives the tab: `terminalPrefsStore` persists it under `localStorage` key
`mobaide-terminal`, the second persisted key after `mobaide-theme` (WEB-07
adds `mobaide-agent` and `mobaide-sessions`).

**OSC 52**: writes allowed, reads refused. The refusal is a handler
registered *after* `ClipboardAddon` (xterm dispatches OSC handlers
last-registered-first) so the query is swallowed rather than answered with
an empty payload. No browser analogue of Android's `EXTRA_IS_SENSITIVE` or
iOS's `localOnly` + 60 s expiry exists — accepted divergence.

**Synchronized output (DEC 2026) is native** in xterm 6, so neither iOS's
byte scanner nor Android's deferred-paint timer is ported. Their *consumer*
discipline is: while `terminal.modes.synchronizedOutputMode` is armed the
painted screen lags the buffer, so the selection actions refuse and say so —
otherwise Copy would hand back text the user cannot see. The mode is read at
the instant of the action, never cached in view state: xterm disarms it on a
1 s safety timeout inside `RenderService` with **no** event, so a sampled
flag has a transition it can never observe and would strand the toolbar
disabled over a screen that is actually current.

**Frames sent.** `i` from keystrokes, mouse reports (`onBinary`) and paste;
`m` from the composer, prefixed with `CLEAR_INPUT_LINE` (`0x01 0x0B`) and
bracket-wrapped only when multi-line; `x` from the selection's "Run" action,
the desktop stand-in for mobile's tap-a-row-to-execute. Neither `m` nor `x`
carries a trailing CR — the agent owns that timing (proto-v4). Keystrokes,
paste and submit are gated on `live && running` **plus a web-only third
term: the replay write queue must have drained.** `'e'` flips the connection
synchronously while `term.write` is asynchronous, so a DECRQM / XTVERSION /
DECRQSS / OSC 4 query still sitting in the replayed history would otherwise
be answered after the gate opened and land in the user's live prompt — the
agent's ring scrub does not cover those, and mobile never sees it because
its replay append finishes before its gate flips. "Run" is on `live` only,
like the row tap it replaces. Every outbound text frame is capped at 1 MiB,
not just paste: the relay forwards a client frame to the agent's tunnel
unchunked and that tunnel is opened with `max_size=4 MB`, so one oversized
frame closes it with 1009 and drops *every* session on that agent.

**Accepted divergences from mobile**, all under the CLAUDE.md
platform-convention exemption: **Full display mode only** — no Static /
forced-column mode and no horizontal pan, because a desktop window is wide
and fit-to-window is the browser convention, where a phone at ~40 columns
needs the forced grid; and the whole desktop keyboard surface above. The
prompt-template picker is read-only here (list, recents/favourites grouping,
filter, insert, favourite toggle); creating and deleting templates is
WEB-09.

## Desktop keyboard & shortcuts (WEB-06)

The input model on top of the terminal. Native keystrokes are the default —
every built-in bar key is typeable in every desktop browser — so the work here
is deciding what the app takes back, and making the chords the browser steals
reachable anyway.

**The shared chord contract is now a fixture, not three tables.** The token
grammar (`ctrl+shift+i`: modifiers in ctrl, alt, shift order, at most two, over
a–z / 0–9 / F1–F12 / tab / enter / space / backspace, Esc excluded) and the
legacy-first encoder (C0 for Ctrl+letter, ESC-prefix for Alt over a single-byte
core, xterm SS3 / CSI forms for F-keys, kitty CSI-u for everything legacy bytes
cannot express) were pinned by hand-mirrored assertion tables in
`KeyChordEncoderTest.kt` and `KeyChordEncoderTests.swift`. They are now
`tests/protocol_fixtures/keys/key_chords.json`, read by all three clients:
`web/src/protocol/keyChord.ts` (gated by `protocol-integrity.yml`, which is why
the encoder lives under `protocol/` and not `terminal/`), a new
`KeyChordFixtureTests.swift`, and a new `KeyChordFixtureTest.kt` — Android's
**first** fixture consumer, with the loader it never had
(`testing/ProtocolFixtures.kt`; extending it to `dto/` is CLA-235). Kotlin stays
the reference implementation: a disagreement means the fixture is wrong. The two
suites keep only what a table cannot state — that Ctrl+Shift+I never collapses
onto Ctrl+I's byte, that every chord in the domain encodes, and that no two
chords share a CSI-u sequence.

**Passthrough.** `attachCustomKeyEventHandler` remains the funnel for the WEB-05
clipboard policy, and the send gate is still mirrored into `disableStdin` and
never into the key handler. App-owned chords do **not** go through it:
`keyboard/useAppShortcuts` claims them on a capture-phase `document` listener,
because xterm registers its `keydown` on the terminal's *textarea* — a
model-level handler is dead whenever focus sits in the composer, on a bar button,
or on `document.body`, which is exactly where it lands after a dialog closes. The
skip list is one entry long (`keyboard/appShortcuts.ts`); every chord the app
takes is a chord the PTY loses. The hook releases its own chord while the
recorder is open, since both are document-capture listeners and
`stopPropagation` does not silence a sibling on the same node.

**Reload and unload guards.** `useReloadGuard` is a **capture-phase**
`preventDefault` on Ctrl/Cmd+R, its Shift form and F5, and nothing else: capture
runs before xterm and `preventDefault` does not stop propagation, so a focused
terminal still receives `0x12` exactly as before — what the guard adds is the
case xterm cannot cover, focus parked on the composer or a bar button.
`useUnloadGuard` registers `beforeunload` only while the session is live *and*
running; it needs sticky activation to produce a prompt at all, and in Firefox on
Windows and Linux it is the only thing between an unsuppressable Ctrl+W and a
closed tab.

**The bar** (`terminal/controlKeys.ts` + `routes/terminal/ShortcutBar.tsx`)
carries the mobile per-kind sets byte-for-byte, then the "…" composer trigger,
then the kind's synced saved shortcuts. It mounts as a flex sibling *below* the
stage, never inside it — xterm's `SelectionService` clears the selection on any
row-count change. Expanded by default and persisted, because it is how anyone
discovers Mode, Rewind and saved shortcuts, and because on Windows and Linux the
browser takes Ctrl+L and Ctrl+O outright. Saved chips send only; renaming and
removing live in the composer, as they do on both phones.

**CLA-158 is fixed here, on all three clients at once**: a Shell session now
renders the neutral 9-key set rather than Claude's 14. Mode (`CSI Z`) and Rewind
(`ESC ESC`) are Claude-TUI input, and Ctrl+O / Ctrl+R / Ctrl+L are readline
bindings a shell user may have remapped.

**The composer** (`ShortcutPicker.tsx`) records on `e.code`, never `e.key`, so a
Dvorak or AZERTY user records the physical key and Shift does not turn `2` into
`@`. It ignores `isComposing || keyCode === 229` (IME) and any press while
`getModifierState('AltGraph')` is set — Windows AltGr reports Ctrl+Alt and is
indistinguishable from a real Ctrl+Alt chord. Mobile's modifier-toggles-plus-key-grid
survives as the **Choose** tab, which is the only way to compose a chord the
browser eats. Readout is VS Code-shaped: the chord, the resolved bytes
(`ChordEncoding.summary`), the same-byte-as disclosure, the CSI-u hint, and four
conflict classes — already saved, shadows a built-in (compared on **bytes**, so
Shift+Tab ↔ Mode is caught), shadows the command palette, and browser-reserved.
None of them block saving: a contract-valid token has to round-trip to phones that
have none of these problems. Saving is deliberately not session-gated; sending is.
Saving is refused only while the kind is unresolved, because a shortcut filed
under a guessed kind stays in the wrong bucket forever (`useSessionKind` is a
one-way latch, matching `resolvedKind.compareAndSet`).

**The reserved-key table** (`keyboard/reservedKeys.ts`) splits "what the browser
does" from "whether the page can stop it", because only the first is documented.
Every row ships `documented`; `/dev/key-sweep` — a DEV-only route, dropped from
the production bundle — measures the second. Sources, runbook and the empty
results matrix are in docs/web-keyboard-reserved-keys.md; running it is CLA-251.

**Command palette** (Ctrl+Shift+P, ⌘⇧P on macOS, plus a toolbar button) lists the
kind's keys, its saved shortcuts and the prompt templates. The chord is a
deliberate departure from the issue's Ctrl+K / F1 proposal: `ctrl+k` encodes to
`0x0B`, which is Antigravity's built-in fast-approve key and readline's
kill-line, and plain `f1` is a TUI help key users can save in its own right.
Ctrl+Shift+P is itself in the reserved table (Firefox private window on Windows
and Linux), which is why the button exists.

**Immersive mode** is fullscreen plus `navigator.keyboard.lock()` — the only way
Ctrl+W, Ctrl+T, Ctrl+N and F11 ever reach a web page. Chromium-only and
feature-detected. The permission prompt Chrome announced for 131 was never
launched, so there is none to design around. Tier-1 PWA widening is wired but conservative:
a minimal `manifest.webmanifest` makes `display-mode: standalone` observable, and
rows flagged `relaxedInStandalone` only *soften* their warning until the macOS
Cmd+W probe settles whether the claim is true at all.

## Auto Scan, notifications & settings (WEB-10)

The last live-surface milestone: the Auto Scan monitor, the attention
surface, and a settings hub.

**Monitor, two channels.** The card list rides the shared agent-events bus
that `SessionList` already opened and mounts as an "Active Auto Scans"
section above the sessions, which is where iOS puts it; only the detail
screen at `agents/:agentId/autoscan/:projectName` opens the dedicated
`ws/autoscan` channel. Both ids ride in the path for the same reason the git
route's do — a scan is one project on one host, and the selected-agent store
would make a shared link resolve elsewhere. That channel is capacity-limited:
past its ten-subscription cap, or when the agent's send queue is full, the
relay accepts the socket and then closes it with `1013`, which the screen
latches into a declined banner with a manual retry rather than the close-code
policy's default reconnect.

**The three `autoscan.*` events, judged by the snapshot-vs-pointer rule.**
None of them is an invalidation. `autoscan.list.changed` carries the agent's
entire `list_response()` and `autoscan.status` a complete
`AutoScanInstanceDto`, so both are *written* (`realtime/autoscanCache.ts`) —
the status upsert replaces a row and deliberately never inserts one, because
the event carries no `capacity`/`max_scans`/`available_projects` and the
`autoscan.list.changed` that every start publishes is what introduces it.
`autoscan.event` is *ignored*: it is one scan event, the stream the detail
screen reads from its own channel, and `AutoScanInstance._emit` publishes one
per emitted log line — invalidating on it, as the bridge did before this
milestone, cost a `/proxy` GET per line of output.

The Auto Scan list is nonetheless the one written cache that also needs a
connect-time refresh, and not for the usual reason. The agent *does* publish
`autoscan.list.changed` as a connect snapshot, but only on the direct
`ws/events` path; `relay/client.py::_handle_request_events_snapshot`
republishes sessions and projects and stops there, and the relay is the only
path a browser takes. `refreshOnAgentEventsConnect` covers the gap, which is
also why the query must not pin `staleTime: Infinity` (CLA-234). Filed as
CLA-249 for the agent; once that ships the refresh can go and the list can be
treated exactly like sessions and projects. `GET /api/autoscan/status` has no
relay handler at all, so detail state is seeded from the WS `snapshot` frame
and never from REST.

**Two dedupe layers, both needed.** The wire cursor
(`protocol/autoscanFrames.ts`, WEB-04) is rebuilt from the live high-water
mark on every reopen and drops `seq <= cursor`, which is mandatory rather
than an optimisation: when its ring has already rolled past the client's
cursor the agent skips its trim filter and replays everything it still holds.
The reduction cursor (`autoscan/autoscanModel.ts`) makes reducing the same
frames twice idempotent, which is what a remount or a snapshot overlapping
live events produces. Both are `bigint`; a `number` stops discriminating past
2^53, and `seq` is an unbounded per-instance counter. Android has neither
layer (CLA-119, still open in the code despite being closed).

**ANSI without a dependency.** `protocol/ansi.ts` is a port of the iOS
`AnsiParser` — full 16-base palette, the 6×6×6 cube, the grayscale ramp,
truecolor, background/bold/italic/underline — and it returns integer `[r,g,b]`
runs, which is the shape `tests/protocol_fixtures/ansi/ansi_sgr.json` asserts.
`ansi_up` was rejected on that ground alone: it emits an HTML string, so
nothing of the right shape exists to assert. Colours ride React's `style`
prop, a CSSOM write that CSP never gates — and the SPA's `style-src` carries
`'unsafe-inline'` regardless, forced by Google Identity Services.

**The log pane is not virtualised, deliberately.** The ring caps at 2000
lines, rows memoise on an id that is never recycled, and
`content-visibility: auto` skips layout and paint for anything off screen.
What that keeps over a windowing library is what a log reader actually needs:
selecting and copying the whole buffer, and the browser's own scroll
anchoring holding the view still when the ring trims its oldest line. Follow
mode is derived from the viewport, not latched, and the released-at line id
doubles as the unread watermark. Frames are buffered and flushed on an
animation frame, so a verbose scan collapses into one render per frame.

**Notifications are edge-triggered only.** `attention` is *latched* on the
agent — cleared only by a client PTY write, a `UserPromptSubmit` hook, or
output resuming — so every full-state snapshot keeps reporting it and
`core/attention_gate.py` warns clients not to re-post from one. Web consumes
only the one-shot `session.attention` edge, which is what makes Android
correct by construction and what iOS needed a per-session seen-store to
emulate. There is therefore no reconcile and no seen-store here: a closed tab
shows nothing, which is honest, because there is no background delivery to
reconcile against.

It listens on the **user-events** channel, not the agent bus. That payload is
the slim `{session_id, reason, title?, project_name?, exit_reason?, agent_id}`
and the channel is account-wide, so a browser — the first client that
routinely holds several agents — is alerted about all of them rather than
only the one whose list is open. Consuming both channels would double-post.

*Clearing* an alert cannot be edge-triggered, though — the agent publishes no
"attention resolved" event, only the full session row with the field gone. So
`dismissResolvedAttention` watches `session.updated` and `sessions.changed` on
the agent channel and closes the matching notification, which is the second
collector Android runs (`SessionEventService` → `notifier.cancel`). Reading a
snapshot to *dismiss* is safe in the way reading one to *post* is not: the trap
`attention_gate.py` warns about is re-raising an alert from a latched field, and
this only ever takes one away.
`notifications/attentionContent.ts` is a byte-for-byte port of the same map
iOS, Android and `central_server/push/content.py` carry, em dashes included;
a phone and a browser watching one session must say the same thing.

Delivery follows the rule both mobile clients share — never alert about the
session already on screen — with the split a browser makes possible: a hidden
tab gets a system notification (tagged with the session id, so it replaces
rather than stacks, matching the APNs collapse id), a visible one gets an
in-app toast. Permission is requested only from the button in Settings, never
on load, and `e2e/settings.spec.ts` asserts that by counting
`Notification.requestPermission()` calls from before any app code runs.

**Tab lifecycle: the socket stays open when hidden.** Presence gating for
APNs became per device rather than per account
(`relay/agent_ws.py::_handle_user_event` excluding
`routing.get_user_device_ids()`), so a browser tab no longer mutes a phone.
Web deliberately sends no `device_id` hint on `ws/user-events`, exactly as
Android does not: it registers no push token, so it can never be a
`list_push_targets` row, and an unverifiable hint would only add log noise. A
tab therefore never appears in the exclusion set — an iPhone receives its
attention push whether this tab is open, hidden or closed — and keeping the
socket open buys live badges in a background tab at no cost to the phone.

Keeping it open that long has a cost of its own, though: a browser exposes no
`ping()`, so a half-open socket (NAT rebind, Wi-Fi to cellular, a laptop waking)
sits at `readyState === OPEN` with no data and no close event, where both mobile
clients would have caught it with their 30 s application ping. `resumeOnFocus.ts`
covers the two observable causes — `online`, and returning to a tab that was
hidden longer than a minute — by force-reconnecting the events channels the
registry owns, plus anything registered through its `onResume` seam. Not every
`visibilitychange`: alt-tabbing is constant, and reconnecting on each would be
its own handshake storm.

The auto-scan monitor's socket uses that seam, and needs it most: at the default
300 s scan interval it is silent for most of every cycle, behind a screen a user
deliberately leaves open, so a black-holed connection is indistinguishable from a
healthy idle scan. Firing it blindly is safe in both directions — `forceReconnect`
no-ops once `disconnect()` has cleared `shouldReconnect`, so a declined channel
stays declined, and the replay a reconnect triggers is dropped by both cursors.
The terminal is deliberately left out: it is user-driven, so a dead socket
announces itself the moment someone types. The residual case (a path
black-holing while the tab stays visible) needs a heartbeat the browser can
observe, which is a protocol change across all three clients and is not
attempted here.

**Settings** is a hub plus the preferences that had nowhere else to live:
notification permission and the in-app opt-out, the Ctrl+V terminal
preference, support and privacy links (the same constants iOS and Android
carry), sign out, and a protocol/server version footer. It links onward to
agents, projects, templates, devices and the account danger zone, so someone
who learned the app on a phone finds what they expect; `Devices` left the top
nav in exchange. There is **no** terminal display-mode picker — web ships
Full display only, the WEB-05 divergence above.

## Sign in with Apple (WEB-11)

Accounts created with Sign in with Apple on iOS usually carry a private-relay
address and **no password**, so before this milestone they could not reach the
web client at all. The endpoint they need already existed — `POST
/api/auth/apple`, the one iOS uses — but it verified the identity token
against a single audience, the iOS bundle id, and browser tokens carry an
Apple **Services ID** instead. The server now checks a *list*, so one endpoint
serves both clients, and the two identifiers resolve to the same account
because the Services ID is grouped under the bundle id as its primary App ID
(the Apple-side setup, including domain verification, is in
[deployment.md](deployment.md)).

Three findings out of the shipped `appleid.auth.js` bundle shaped the client,
none of them stated in Apple's docs:

- **The SDK does not hash the nonce.** It URL-encodes the value onto the
  authorize URL and Apple echoes the claim back verbatim — there is no
  `crypto`, `subtle` or `sha256` symbol in the bundle. So the browser reuses
  `generateNonce()` and the server's byte-for-byte `compare_digest` unchanged,
  and the no-double-hash convention now spans all three clients.
- **`usePopup: true` opens a real window, not an iframe**: the SDK appends
  `response_mode=web_message`, calls `window.open`, and resolves from a
  `postMessage` listener. That is why the SPA's CSP grows exactly one
  `script-src` entry and no `frame-src`/`connect-src` — and why COOP
  `same-origin-allow-popups` now protects two flows. Without the flag the SDK
  calls `window.location.assign`, which would destroy SPA state and return as
  a cross-site POST.
- **`redirectURI` is mandatory even in popup mode**, where it is never
  navigated to; Apple only matches it against the registered Return URLs. It
  is derived from `window.location.origin`, so one Services ID covers every
  host the SPA is deployed to.

The button is hand-drawn rather than `AppleID.auth.renderButton`: Apple's
rendered button binds its own click handler to the *globally* initialised
nonce and reports through document events, which leaves nowhere to bind a
per-attempt nonce, and it cannot be disabled mid-request. Only the flow comes
from the SDK. Two consequences shape `AppleSignInButton`: the SDK load, the
`init` call and the nonce all happen **at mount**, because anything awaited
inside the click spends the user activation and the popup gets blocked; and
the button ships disabled until the SDK answers. Dismissing the popup
resolves as `AppleSignInCancelledError` and is swallowed — only a
browser-blocked popup produces a message, because it is the one the user can
act on.

The delete-account panel gains a third re-auth branch for the same reason the
sign-in exists: an Apple-only account has no password and no Google identity,
so without it this milestone would create users who can sign in and never
delete themselves. Unlike Google it needs no `disableAutoSelect` equivalent —
Apple's popup always runs a fresh authorization, so the token is inside the
server's five-minute freshness window by construction.

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
  `google_client_id` and `apple_web_client_id` the two sign-in buttons
  initialise with, so the SPA needs no build-time env var. Each button
  mounts only when its id is present, so a deployment configures either
  provider, both, or neither, entirely server-side.

## Parity policy

The web client participates in the three-way Cross-Platform Parity rule in
the root `CLAUDE.md`: client-facing changes must be mirrored across
Android, iOS, and web (or a Linear issue filed) — with the transition rule
that feature areas not yet built on web are covered by their planned WEB-*
issue rather than per-change issues. Terminal behavior parity references:
the client is the sole PTY size authority and must re-assert size on every
reconnect (see `docs/ios-client.md` and PROTOCOL.md).

Web-only behaviour that needs **no** mirror issue is the platform-convention
exemption the rule already carries: the desktop keyboard surface (Ctrl+C /
Ctrl+V / Ctrl+Shift+C/V, the "pass Ctrl+V" toggle) and Full-only display
mode both exist because a browser window has a physical keyboard and a wide
viewport, neither of which a phone has. Each is listed in the WEB-05 section
above with its reason.

WEB-06 adds to that exempt set for the same reason: the press-to-record
composer, the reserved-key table, the reload and unload guards, the command
palette and immersive mode all answer "a physical keyboard exists and a browser
competes for it", which is not a question either phone can be asked. Two things
in that milestone are the opposite of a divergence — the shared encoder fixture
pulls all three clients onto one contract, and the CLA-158 Shell fix lands on
Android, iOS and web in the same change.

WEB-07's two divergences are **not** exempt and are tracked accordingly:
recording the last-used session on open as well as on create is a behaviour
change mobile has to follow (CLA-229 / CLA-230), and the session-card
attention chip is iOS parity that Android does not yet render.

WEB-10's divergences are platform-convention exempt too: no reconcile and no
seen-store, because a browser has no background delivery to reconcile against
and consumes only the one-shot attention edge, as Android does; alerting from
the account-wide user-events channel rather than the per-agent bus, because a
browser is the first client that routinely holds several agents; requesting
notification permission from a settings button, which is what a browser
permission model requires; and no display-mode picker, which is the WEB-05
exemption restated. The tab-lifecycle contract — the socket stays open when
hidden, web sends no `device_id`, and a tab therefore never suppresses a push
— is a cross-client contract rather than a divergence, and matches Android's
existing choice not to send the hint. Two gaps this milestone found in other
components are filed rather than fixed: the agent's relay events-snapshot
handler omits `autoscan.list.changed` (CLA-249), and Android's ANSI parser is
still a 16-colour subset with no fixture consumer (CLA-250).

WEB-08's divergences are all platform-convention exempt: the paste box and
the `/pair` deep link exist because a desktop browser has no camera, the
agent id and version on the card because a browser is the first client that
can hold more than one agent, and the trust-mismatch copy's final sentence
because a browser has no "clear app data". The `parse_qr_payload` tightening
went the other way — it moved the agent's Python parser onto the mobile
clients' stricter contract rather than away from it — and the shared golden
now pins all four implementations, with the Android arm tracked in CLA-235
and the duplicated `central_server` encoder in CLA-236.
