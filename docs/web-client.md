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
pairing link the agent now prints under its QR. The series is tracked in
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
│   ├── auth/                 # session store + transport, user/agent token axes, single-flight refresh, GIS loader
│   ├── components/toast/     # zero-dependency toast primitives
│   ├── components/form/      # react-hook-form fields (+ auth/ Google button, account/ grace banner)
│   ├── protocol/             # framework-free wire layer (WEB-04): terminal codec,
│   │                         #   relay URL/path builder, wire JSON + Zod schemas,
│   │                         #   autoscan frames, __fixtures__/ golden-fixture loader
│   ├── realtime/             # WS channel manager, close policy, connection store,
│   │                         #   event→Query bridge, RealtimeProvider (WEB-04)
│   ├── stores/               # Zustand stores (themeStore, toastStore, terminalPrefsStore,
│   │                         #   selectedAgentStore, lastSessionStore)
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
alone, and only one that is present but dead gets cleared. Two web-specific
changes. The once-per-attempt flag lives at module scope keyed by agent id,
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

Two routes: `/agents` (guarded, in the app shell) lists and manages agents;
`/pair` completes a pairing.

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

Web-only behaviour that needs **no** mirror issue is the platform-convention
exemption the rule already carries: the desktop keyboard surface (Ctrl+C /
Ctrl+V / Ctrl+Shift+C/V, the "pass Ctrl+V" toggle) and Full-only display
mode both exist because a browser window has a physical keyboard and a wide
viewport, neither of which a phone has. Each is listed in the WEB-05 section
above with its reason.

WEB-07's two divergences are **not** exempt and are tracked accordingly:
recording the last-used session on open as well as on create is a behaviour
change mobile has to follow (CLA-229 / CLA-230), and the session-card
attention chip is iOS parity that Android does not yet render.

WEB-08's divergences are all platform-convention exempt: the paste box and
the `/pair` deep link exist because a desktop browser has no camera, the
agent id and version on the card because a browser is the first client that
can hold more than one agent, and the trust-mismatch copy's final sentence
because a browser has no "clear app data". The `parse_qr_payload` tightening
went the other way — it moved the agent's Python parser onto the mobile
clients' stricter contract rather than away from it — and the shared golden
now pins all four implementations, with the Android arm tracked in CLA-235
and the duplicated `central_server` encoder in CLA-236.
