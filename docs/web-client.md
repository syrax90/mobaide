# Web Client

Browser SPA for controlling Mobaide agents through the central relay. Lives
in `web/` at the repo root. This document covers the workspace layout, the
typed two-schema API pipeline, the app-shell architecture, testing, CI, and
how the web client fits the cross-platform parity policy. For day-to-day
commands see `web/README.md`.

On this page:

- [Status and roadmap](#status-and-roadmap)
- [Stack](#stack)
- [Workspace layout](#workspace-layout)
- [Typed API layer — two schemas by design](#typed-api-layer--two-schemas-by-design)
- [Realtime layer (WEB-04)](#realtime-layer-web-04)
- [Sessions (WEB-07)](#sessions-web-07)
- [Agents and pairing (WEB-08)](#agents-and-pairing-web-08)
- [Projects, git & templates (WEB-09)](#projects-git--templates-web-09)
- [Terminal screen (WEB-05)](#terminal-screen-web-05)
- [Desktop keyboard & shortcuts (WEB-06)](#desktop-keyboard--shortcuts-web-06)
- [Auto Scan, notifications & settings (WEB-10)](#auto-scan-notifications--settings-web-10)
- [Sign in with Apple (WEB-11)](#sign-in-with-apple-web-11)
- [CI — `.github/workflows/web-ci.yml`](#ci--githubworkflowsweb-ciyml)
- [App shell](#app-shell)
- [Parity policy](#parity-policy)

## Status and roadmap

The series is tracked in Linear project "Web Client (React)"
(CLA-188…CLA-200):

| Milestone | Ticket | Scope |
|---|---|---|
| WEB-01 | CLA-188 | Shell: routing, theming, error boundaries, toasts, typed API clients, tests, CI |
| WEB-02 | | Deploy/relay readiness |
| WEB-03 | CLA-190 | Auth and account gate (incl. delete + 14-day cancel) |
| WEB-04 | CLA-191 | Realtime protocol core |
| WEB-05 | CLA-192 | Terminal screen on xterm.js 6 |
| WEB-06 | | Keyboard and shortcuts |
| WEB-07 | CLA-193 | Sessions list and six-branch create wizard |
| WEB-08 | CLA-194 | Agents and pairing phase 1 |
| WEB-09 | CLA-195 | Projects, git and templates |
| WEB-10 | CLA-196 | Auto Scan, notifications, settings |
| WEB-11 | CLA-198 | Sign in with Apple |
| WEB-12 | CLA-199 | Pairing phase 2 |
| WEB-13 | | QR image decode |

### Production serving model

**Production serving model (WEB-02):** `app.mobaide.com` as its own Caddy site
block serving the SPA from disk and reverse-proxying `/api`, `/ws`,
`/relay`, `/proxy` to the central server — fully same-origin, no CORS.

The Vite dev server mirrors this with a proxy to `http://localhost:9000`. The
config-only `MOBAIDE_WEB_PROXY_TARGET` env var repoints all four routes at
another host:port, which is how `tests/e2e/manual_stack.py up --web` serves
the SPA against the manual stack (see
[manual-e2e-testing.md](manual-e2e-testing.md)).

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
│   ├── terminal/             # framework-free terminal core (WEB-05)
│   ├── auth/                 # session store + transport, user/agent token axes, single-flight refresh, GIS + Apple loaders
│   ├── components/           # toast primitives; react-hook-form fields
│   ├── protocol/             # framework-free wire layer (WEB-04): codec, relay URL builder, Zod schemas, __fixtures__/ loader
│   ├── autoscan/             # framework-free auto-scan core (WEB-10)
│   ├── notifications/        # attention content/suppression/service + event bridge (WEB-10)
│   ├── realtime/             # WS channel manager, close policy, connection store, event→Query bridge (WEB-04)
│   ├── stores/               # Zustand stores, plus recentPaths.ts, a plain module not a store (CLA-256)
│   ├── lib/                  # queryClient.ts (TanStack defaults), queryKeys.ts (key factory + wsBackedQueryOptions)
│   ├── api/
│   │   ├── generated/*.ts    # committed openapi-typescript output — never hand-edited
│   │   ├── auth.ts           # bearer-token provider seams (filled in by src/auth/install.ts)
│   │   ├── clients.ts        # relayClient + createAgentClient(agentId), auth + 401-recovery middleware
│   │   └── errors.ts, *Repository.ts
│   └── styles/               # theme tokens (data-theme) + global styles
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
central `/api/*` routes take the dashboard/user JWT. `src/api/auth.ts` exposes
two seams — `setUserTokenProvider()` (for `relayClient`) and
`setAgentTokenProvider(agentId => token)` (for each `createAgentClient`) — both
filled in at startup by `src/auth/install.ts` (WEB-03).

Caveat: the agent schema types the agent's **entire** REST surface, but the
relay tunnel's agent-side dispatcher forwards only an allowlisted subset —
a call can type-check yet 404 through `/proxy` (e.g. `/api/health`,
`/api/auth/token`, `/internal/hook` are direct-only). Scoping the typed
surface to the tunnel is tracked in CLA-201.

WebSocket frame/event types are deliberately absent from both schemas; they
are hand-written in `src/protocol/` (see *Realtime layer* below).

### Schema pipeline

`web/scripts/dump_openapi.py` builds both FastAPI apps **in-process** via
`create_app(config)` + `app.openapi()` — no running server, no env vars — and
writes deterministic JSON with `info.version` pinned to `0.0.0` so component
releases don't churn the committed schemas. Requires both packages installed
editable (`pip install -e 'mobaide_agent[dev]' -e 'central_server[dev]'`,
Python 3.12 to match CI).

Regeneration chain after changing agent or central-server routes:

```sh
cd web && npm run schema:dump && npm run schema:generate
```

`npm run schema:check` verifies both committed layers: the dump script's
`--check` mode compares the JSON without writing, then the TS layer is
regenerated and checked via `git status` (openapi-typescript's own `--check`
flag is a no-op whenever a `redocly.yaml` multi-schema config is present, so
the git-based gate — the same one CI uses — is authoritative).

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
(1 s → 30 s), reset on open and on `forceReconnect()`. A generation counter,
incremented only by `forceReconnect()`, drops events from a superseded socket.
`buildUrl` is re-evaluated on every attempt so a rotated token is picked up,
and resolving to `null` stops without burning the ladder.

**Deliberate divergence from mobile:** web adds 50–100 % jitter on top of
the ladder, because a browser user commonly has several tabs and a relay
restart would otherwise bring them all back in lockstep.

**Close codes are per axis.** On the user axis 4001 → `tokenRejected`,
4010 → `accountRemoved`, 4002 → the incompatible-server takeover. On the
agent axis the same codes stop the loop but leave the session alone, because
per-agent tokens are always re-mintable — 4001 and 4003 additionally drop
the cached token so the next connect re-mints. 4011, 4012 (the ordinary
attach-to-an-offline-agent case) and 1013 always reconnect.

The 4002 branch is parity, not a live path: no server route sends it to a user
socket. A redeploy mid-session is caught by the reconnect itself, because
`refreshOnUserEventsConnect` invalidates `['server-info']`, whose query is
otherwise pinned at `staleTime: Infinity`.

Because the relay only authenticates at handshake and a per-agent JWT minted
while its TTL was 0 has no `exp`, a rejected agent credential is otherwise
invisible and never rotated. Three consecutive failures to open an agent
channel therefore drop the cached token so the next attempt re-mints. A null
URL is treated differently per axis: on the user axis it means signed out and
the channel stops; on the agent axis it usually means the mint just failed, so
the channel keeps backing off.

**Known browser limitation:** the server sends 4001/4010 *before*
`websocket.accept()`, ASGI turns that into an HTTP 403 handshake rejection,
and the WebSocket API exposes no upgrade status — so those two normally
reach a browser as `1006`, indistinguishable from a network failure. The
mitigation is that every connect attempt mints a fresh token through the
WEB-03 seams; the REST 401 path remains the authoritative session teardown.
Browsers also expose no ping API, unlike both mobile clients (CLA-219).

**Resume hooks.** The session I/O channel re-asserts the cached PTY size on
every open — the client is the sole size authority and the agent spawns at
24×80 (WEB-05's replay state machine owns two further re-assert points, driven
through `setSize`). The auto-scan channel sends `{subscribe, resume_from_seq}`
as the first thing on every open (the agent's grace window is 200 ms direct, 1 s
through the relay), rebuilt from a live per-project cursor; it
also drops `seq <= cursor` because the server's replay is best-effort.

**Wire DTO policy.** `z.looseObject` everywhere, so unknown keys survive; kind
strings stay open (proto-v7 `custom_<12hex>` and anything newer are preserved
verbatim, only an explicit `null` falls back to `claude`); and `parseWireJson`
promotes integer literals beyond 2^53 to `bigint` via the ES2026 `JSON.parse`
source-access reviver. Runtimes without source access degrade only above 2^53.

**Event → Query bridge.** Refresh-on-connect invalidates agents / templates /
custom types on the user channel and projects on an agent channel. Web dropped
saved shortcuts entirely, so it neither subscribes to `shortcut.*` nor needs the
`custom_type.deleted` cascade. Per event the default policy is
`invalidateQueries`, never a hand-applied delta. Payloads are schema-validated
before anything is written, and dropped rather than thrown on failure — this
runs inside the socket's listener loop.

The three agent-channel session events are the one exception
(`src/realtime/sessionCache.ts`): `sessions.changed` carries the agent's
complete list and `session.updated` / `session.attention` carry a whole row
each, so they are written straight into the cache. A snapshot **may** seed a
cache nothing has fetched; an upsert **must not**, or one row would masquerade
as the whole list.

`onlineManager` is driven from reachability rather than `navigator.onLine`,
conservatively: offline only after two consecutive pre-open failures of the
user-events channel, and back online on any WS open, any REST response, or
an intentional stop — so the signed-out login flow is never paused.
`staleTime` stays at the global 30 s; `wsBackedQueryOptions`
(`staleTime: Infinity`) is opt-in for lists that actually have a push
channel.

### Golden fixtures, TypeScript arm

`npm run test:protocol` runs just this suite, and
`protocol-integrity.yml` runs it on every PR alongside the Python checks.

Coverage is deliberately partial and declared in
`src/protocol/__fixtures__/coverage.ts`. The gate fails when a fixture file is
neither asserted nor assigned an owner. One cross-arm constraint: Swift
`Codable` drops unknown keys on re-encode while `z.looseObject` preserves them,
so unknown-key round-trips are asserted in TypeScript-only tests, never added to
a shared fixture.

## Sessions (WEB-07)

The index route `/` is the live session list; `/agents/:agentId/new-session`
is the create wizard.

### Session list and cards

**Agent selection.** The list is agent-scoped, and web is the first client
that can see more than one agent, so `GET /api/agents` backs a small picker
(hidden when there is exactly one). The choice persists under `localStorage`
key `mobaide-agent` and the resolution is written back, which makes the
automatic pick sticky — otherwise an agent dropping offline would silently
move the user's list. Full pairing is WEB-08.

While the selected agent is
offline the events channel stays open (close code 4012 is the ordinary
attach-to-an-offline-agent case and reconnects) but the REST query is
disabled, since an offline agent 503s on every mount.

**Auto-resume** ports `SessionListViewModel.maybeAutoResume` verbatim,
including its asymmetry: a stored id that is *absent from the list* is left
alone, and only one that is present but dead gets cleared. "Absent" only counts
once `useSessionsSynced` confirms the connected agent has pushed a fresh
`sessions.changed` snapshot.

Two web-specific changes go beyond the port:

- The once-per-attempt flag lives at module scope keyed by agent id, because a
  web route unmounts on navigate and remounts on Back.
- The id is recorded **on open as well as on create** (`mobaide-sessions`, keyed
  per agent), because mobile's create-only recording would leave the feature
  dormant for anyone who starts sessions from their phone.

Android and iOS follow in CLA-229 / CLA-230.

**Cards** follow iOS: state dot, title, kind label, cwd, and the attention
chip Android does not render (`awaiting_permission` → "Needs permission",
`turn_finished` → "Ready", `ended` → "Ended"). Unknown kinds fall back to
`kind_label`, then the live custom-type registry, then "Custom" — never the
raw wire token. Stopping uses iOS's Stop / Force Stop / Cancel confirmation.

### Create wizard

**The create wizard** is three steps (project → agent → options), iOS's shape
rather than Android's seven. State is one reducer over **per-mode buckets**, and
the request builder switches on mode, so a Codex request cannot name a Claude
field. Auto Scan gets its own Claude bucket, since its `skip_permissions`
defaults the opposite way. Only `step`, `mode` and the project selection live in
the URL; the ~30 option fields do not, so no prompt text reaches browser
history.

#### Step advance behaviour

**Every step advances on the click that decides it.** Picking a project row,
a recent directory or an agent card writes the URL and moves on. The project
step keeps a Continue button only for the one choice with no row to click — a
path typed into the one-off directory field — enabled only while that field
holds a valid absolute path.

The advance is a single **push**, never a replace followed by a push: two
`setSearchParams` calls in one handler race the data router, which aborts the
in-flight navigation and drops the replace. Browser Back therefore returns to
the project step *as it was before the click*, while the wizard's own Back
carries the selection forward.

This is a deliberate divergence from the list + Continue that Android and iOS
keep, tracked as #650 (Android) and #651 (iOS). The same applies to
**creating a project**: the project list always ends with a "Create new
project…" row, which opens `ProjectFormDialog` over the wizard, and saving
advances the step like a row click. Android and iOS stay on the project step
until their own Continue.

#### Remembered launch options

**The wizard opens where the last launch left off** (CLA-232), as both mobile
clients do. `localStorage` key `mobaide-launch-prefs` holds the four option
buckets plus the last project name, and `launchPrefs.ts` is the seam in both
directions. The pre-fill only *highlights* the row; it never advances the step,
so a remembered project cannot launch past a screen the user never saw.

Three things are load-bearing:

- **The buckets are global, not per agent**, as on mobile. A model or autonomy
  preset is a preference about how the user works, and a global bucket has
  nothing to re-seed when the router keeps one wizard instance across a change
  of `:agentId`.
- **A bucket that was never launched stays `null`** rather than being written
  out at its defaults. This keeps Auto Scan's `skipPermissions: true` alive for
  a fresh profile while letting a stored `false` win.
- **The values are pinned by `submit` before the round trip.** The config step's
  controls stay enabled while a create is in flight, so reading live state in
  `onSuccess` would file options the launch never carried (the mobile instance
  of that race is CLA-259).

Sanitising on read is all-or-nothing per bucket: a missing key takes the mobile
default, but a present-but-wrong value or out-of-vocabulary enum voids that
whole bucket back to `INITIAL_WIZARD_STATE`. `codex.reasoningEffort` persists
nullable (`null` renders "Agent default"). Restore re-applies the Antigravity
`sandbox && !skipPermissions` interlock (the server 422s the pair) and clamps a
restored effort through `effectiveClaudeEffort`. Worktree/branch, `add_dirs` and
the Auto Scan initial prompt are per-session and are not persisted on any
client.

**Not purged at sign-out**, matching mobile and unlike the recents below. What
survives is a project name and `append_system_prompt`, free text the user typed,
which the agent puts on argv. Android and iOS store the same values in
per-OS-user app storage, while `localStorage` is per *browser profile*, so on a
shared profile the next account to sign in inherits both, pre-filled. The
decision stands deliberately.

> [!WARNING]
> `skip_permissions` is the trap the wizard is built around: its **wire-absence
> default is `true`**, so an omitted field launches with
> `--dangerously-skip-permissions`.

Requests are re-parsed through `createSessionRequestSchema` (a
`z.strictObject`) and typed as the *generated* request, whose booleans are
required — so a dropped flag is a compile error rather than a silent escalation.

### Recent custom paths

**Recent custom paths** (CLA-256) mirror the mobile stores exactly: up to 5
per agent, newest first, deduped by exact string equality after one trailing
separator (never a root) is trimmed, with no `~` expansion — and never filtered
against what is being typed.

Picking one advances the step, exactly as a project row does, after the
container re-validates it. A path is recorded in `create`'s `onSuccess` from the
**mutation's variables**, not from live state, because the wizard stays
interactive during the POST. `cwd` is `undefined` for a stored project, which
keeps stored launches out of the bucket. Each row can be forgotten individually
(CLA-255).

#### Storage key and shape

The bucket is the only persisted key that does **not** carry the `mobaide-`
prefix: `src/stores/recentPaths.ts` writes one raw `localStorage` key per
agent, `recent_custom_paths::<agentId>`, holding a JSON array — Android's and
iOS's key shape verbatim. It is a plain module rather than a zustand store.

- An empty agent id is mobile's null `agentScopeKey` — reads and writes are both
  skipped rather than sharing one unscoped bucket.
- Any blank or non-string member voids the whole blob, matching the typed
  mobile decoders.
- Deleting an agent drops its bucket here, alongside its token and its
  last-used session — mobile has no equivalent yet, which is a known parity gap.

#### Storage key and shape

The bucket is the only persisted key that does **not** carry the `mobaide-`
prefix: `src/stores/recentPaths.ts` writes one raw `localStorage` key per
agent, `recent_custom_paths::<agentId>`, holding a JSON array — Android's and
iOS's key shape verbatim, so the concept stays recognisable across the three
clients.

- It is a plain module rather than a zustand store, which costs a `useState` in
  the wizard to stay reactive.
- The try/catch around every storage call is not a new cost but the same idiom
  `auth/agentTokens.ts` and `auth/deviceIdentity.ts` already carry, minus their
  in-memory mirror (a lost suggestion is cosmetic, a lost token is not).
- The key shape buys one thing: the agent id is a *storage* key and never an
  object key, so none of the `__proto__` guards `lastSessionStore` needs apply.
- An empty agent id is mobile's null `agentScopeKey` — reads and writes are both
  skipped rather than sharing one unscoped bucket.
- Any blank or non-string member voids the whole blob, matching the typed
  mobile decoders rather than salvaging the readable entries.
- Deleting an agent drops its bucket here, alongside its token and its
  last-used session — mobile has no equivalent yet, which is a known parity gap.

#### Sign-out purge of recents

Signing out drops **every** agent's bucket, on all three clients (CLA-258): the
paths name the user's own workstation, so they end with the identity that typed
them.

The purge is a prefix sweep driven from each client's identity teardown:

- `clearAllRecentPaths()` from `sessionStore.invalidateUserSession` and the
  peer-tab `logout` branch here.
- `PreferencesStore.removeAllRecentCustomPaths()` from
  `SessionManager.invalidateUserSession` on both mobile clients.

It sweeps by prefix rather than by agent id because teardown clears the
credentials that name the current agent in the same breath, and a current-key
delete would strand every previously-connected agent's bucket. `agentDisconnect`
deliberately does **not** purge: an agent switch is not an identity change.

#### Which invalidation reasons end an identity

Which reasons count as an identity ending is decided by `endsIdentity` in
`sessionStore.ts`. The same question is asked separately, with different
membership, by the branch that revokes the browser's refresh cookie.

- `manual` and `deletionScheduled` are in both. Scheduling a deletion is as
  deliberate as pressing Sign out, and leaving it out of either would keep a
  revoked account's recents on disk or let its refresh cookie restore the
  session.
- `accountRemoved` ends the identity but does not revoke.
- `tokenRejected` does neither. It also fires when a refresh merely failed to
  complete, so treating it as an ending would destroy un-re-mintable paths over
  a flaky connection.

**A new `InvalidationReason` must be classified against both.**

At boot the rule is: **disk state follows the server's verdict, never the
network's mood.**

- A boot whose cookie restore returns `terminal` (the server says the credential
  is dead) purges recents and agent tokens.
- One that returns `transient` — offline, timed out — touches neither, because a
  recent cannot be re-minted and a subway ride is not a sign-out.

Without `navigator.locks`, concurrent boots can produce a spurious 401, so the
purge asks peers one final time before running.

### Custom agent types and Auto Scan launch

Custom agent types are listed, launched, created and deleted here; editing
is CLA-231. A launch sends only `cli: "custom_<id>"` — the relay resolves the
stored command server-side, and the agent-direct REST path rejects custom
kinds outright. The Auto Scan branch posts to `/api/autoscan/start` and
returns to the list; its monitor screen is WEB-10.

## Agents and pairing (WEB-08)

Three routes: `/agents` (guarded, in the app shell) lists and manages
agents; `/pair` completes a pairing from a code printed elsewhere; the
guarded `/agents/pair` (WEB-12) mints one instead.

### Pairing phase 1: the `/pair` route

**Why `/pair` sits outside both guards.** The payload rides in the URL
*fragment*, and `requireAuthMiddleware` builds its `?next=` from
`pathname + search` only, so a bounce through sign-in would drop it. Putting
the nonce in a query string instead would put it in the relay's access log.
`RequireAnonymous` is equally wrong, because a signed-in user must be able to
open a pairing link.

So the route is public. It does the following in order:

1. Reads `useLocation().hash` in a mount effect.
2. Stashes the payload under `sessionStorage` key `mobaide-pairing`.
3. Scrubs the URL.
4. Only then sends a signed-out visitor to `/login?next=/pair` — `useAfterAuth`
   brings them back.

The scrub is `navigate({… hash: ''},
{replace: true})`, never `location.hash = ''`: assigning to `location.hash`
*pushes* a history entry, so Back would re-expose the nonce.

**The payload contract.** `src/protocol/pairingPayload.ts` implements
`MOBA1|{relay_url}|{nonce}|{trust_key_fp}|{exp_unix}`.
`tests/protocol_fixtures/pairing/qr_payload.json` drives the Python, TypeScript
and Swift arms (Android reads no fixtures at all; CLA-235). The agent's
`parse_qr_payload` was tightened to match the stricter mobile parsers. The web
arm adds one documented divergence: an `exp_unix` outside IEEE-754 safe-integer
range is rejected rather than silently rounded.

The paste box strips **all** whitespace before the anchored `^MOBA1|` test,
because a copied soft-wrapped terminal line carries the breaks. It also accepts
the pairing link and a bare fragment.

**Order of operations** follows both mobile clients exactly: parse → expiry →
trust pin → confirm → claim. Expiry before trust is load-bearing — a stale
screenshot from before a legitimate key rotation must read as "expired", not as
a MITM refusal. The confirm step is kept even on the deep link: it names the
relay, which is the phishing mitigation for a clicked link exactly as it is for
a scanned QR.

**Trust pinning** is TOFS in `localStorage` (`mobaide-trust-pins`), keyed by
the relay URL normalized the way `TrustKeyPinning` normalizes it — trim, strip
every trailing slash, lowercase. A mismatch never overwrites and has no
override affordance, matching the mobile rule.

> [!WARNING]
> One deliberate split from mobile: `checkTrustPin` (read-only) runs during
> validation and `pinTrustKey` runs from the confirm button, instead of mobile's
> single `verifyOrPin` that pins on first sight. Folded together, any link a
> user merely *opened* would write a permanent pin — and since a mismatch is a
> hard refusal with no override, a payload naming the real relay with a wrong
> fingerprint would lock that browser out of pairing for good, recoverable only
> by clearing site data.

The confirm step also carries the RFC 8628 §5.4 remote-phishing instruction: a
pairing link works for whoever opens it, an attacker can run `pair-qr` against
the *real* relay on their own machine, and every field on that screen would look
correct. Only the code the user can read off their own terminal separates the
two cases, so the screen says to compare it. Mobile omits the instruction
because its delivery channel is a camera pointed at the host.

The browser is a weaker home for the pin than a phone: Safari caps
script-written storage at 7 days of no interaction, degrading the pin to
trust-on-most-recent-scan (and regenerating the device UUID, which under
last-login-wins orphans a device row). CLA-199 replaces it with an
account-level server-side pin.

### Waiting for the agent after claim

**Waiting for the agent.** A claim's 200 only proves the relay persisted
credentials; `ws_delivered` is tri-state diagnostic (`null` from an older
relay must never collapse to `false`) and only refines the wait-timeout copy.
The authoritative signal is `agent.status_changed` with `online: true`, and
the push stays primary on all three clients — that event already invalidates
`['agents']`. The safety net for a lost push is a bounded REST resync:

- A 5 s interval (`DEFENSIVE_REFETCH_SECONDS` on Android/iOS, web's
  `DEFENSIVE_REFETCH_MS`, also running in background tabs via
  `refetchIntervalInBackground: true`).
- One final resync on the 60 s deadline tick before any verdict, so "not
  online" is never shown while the agent actually is.

All three clients render a shared "Agent not online yet" state on timeout, with
a "Keep waiting" action that restarts the 60 s wait.

### The `/agents` screen

**`/agents`** is full mobile parity: cards (status dot, name, Online/Offline,
agent id, version, added, last seen), manual add, re-register, remove (with an
"Also uninstall Mobaide from this computer" checkbox, on by default, offered
when the agent advertises `supports_uninstall`), and the install-commands
panel. The agent id and version are web-only card fields, because a browser is
the first client that can hold several agents at once. The per-OS `.env` table
and the install one-liners are byte-identical to `AgentCredentialsSheet` on both
mobile clients, and are deliberately not OS-detected — the host is a different
machine from the browser.

#### First-run empty state

**The first-run empty state** (`AgentsEmptyState`) is the web arm of Android
and iOS `EmptyAgentsState`: install first, pair second, escape hatches last.
Mobile's step 2 is "scan the QR"; a browser has no camera, so the web
equivalent is the two lines `pairing_qr.write_pairing_hints` prints *under* that
QR — the `MOBA1|…` payload and the `/pair#…` link — which is why web's steps 2
and 3 are alternatives rather than a sequence.

Three divergences from mobile, all platform-convention: the install one-liners
render inline rather than behind a bottom sheet; the guide quotes an example of
the agent's stdout so a user can match it against their own scrollback; and it
names `mobaide-agent show-qr` for a nonce that expired before they got back to
the browser.

**The example payload and link.**

- The example payload carries `<placeholder>` fields that `parseMoba1` rejects,
  so it can never be mistaken for a live code.
- The link's origin is `window.location.origin`, never a hard-coded
  `app.mobaide.com`: the agent's `MOBAIDE_AGENT_PAIR_WEB_ORIGIN` means "origin of
  the web client the printed link points at", which is *this* client.
- Every other varying field, the payload's relay URL included, is a
  `<placeholder>`. The relay URL is the agent's own `PUBLIC_BASE_URL` and is not
  derivable here, because the SPA reaches the relay same-origin through a proxy
  (`baseUrl: '/'`).

While the guide is up the flat four-button row is suppressed. The sessions and
projects "No agents yet" lines point at `/agents` rather than `/pair`, because
an account with nothing paired has no code to paste yet.

#### Guide paste-box hand-off

**The guide's paste box hands off rather than pairing.** `/pair` owns the
confirm → claim → wait machine and the expiry-before-trust ordering, so a second
copy on `/agents` would drift into a security bug. Instead the box runs
`readPairingInput` (which takes the payload, the whole link, or a bare
fragment) and navigates to `/pair#…` with the payload re-encoded by
`encodePairingFragment` — base64url, so the fragment is `[A-Za-z0-9_-]` only and
no layer in between can re-encode it. That reuses the deep-link path verbatim,
including its scrub, so the nonce still never survives in history.

In a context that denies storage (a private or partitioned window) the capture
effect would scrub the fragment without the `sessionStorage` write landing, so
`Pair` also holds the payload in a `handoffRef` and prefers that on read;
`sessionStorage` stays the arm that survives the sign-out bounce.

#### Removing an agent and the credentials panel

Removing an agent calls `clearAgentToken(agentId)`, never
`sessionStore.agentDisconnect()`, which clears *every* agent's tokens; it also
drops the agent's events channel, its `mobaide-agent` / `mobaide-sessions`
entries and its `recent_custom_paths::<agentId>` bucket.

The credentials panel is an inline panel rather than a `<dialog>` so no Escape
handler or backdrop can dismiss the one and only showing of a token. Android's
`EXTRA_IS_SENSITIVE` clipboard flag has no browser equivalent, so a copied token
lands in ordinary clipboard history; that is why the panel points at pairing
instead.

### Pairing phase 2 route

**Pairing phase 2 (WEB-12).** `/agents/pair` (`PairHost`) is guarded, unlike
`/pair`: minting a code needs a session, and there is no URL payload for a
bounce through sign-in to drop. It mints a code from
`POST /api/pairing/device-code`, polls `GET .../device-code/{id}` for the
agent's redeem, and confirms through `POST .../confirm` — the same completion
path `/pair`'s claim uses. See [qr-pairing.md](qr-pairing.md) for the protocol
and the account-level `trust_key_fp` pin.

## Projects, git & templates (WEB-09)

Three routes: `/projects` (agent-scoped, with the same agent picker and
`mobaide-agent` selection the sessions list uses),
`/agents/:agentId/projects/:projectName/git`, and `/templates`. Projects and
git are agent-axis calls through `/proxy`; templates are central-server ones,
so the templates screen works while every agent is offline.

### Projects cache and snapshot events

**`projects.changed` is written, not invalidated.** It carries the agent's
complete list, republished after every mutation *and* on every events-socket
connect (the relay sends `request_events_snapshot`; `relay/client.py`). So it
joins `sessions.changed` in `queryBridge`'s full-snapshot exception, via
`src/realtime/projectsCache.ts`. That also closes CLA-234: an invalidation
landing during a **cold** fetch was deduped into it and `staleTime: Infinity`
pinned the older list.

The general rule: an event carrying the new state is written; an event carrying
only a pointer to it is invalidated, and a pointer event's query must **not**
pin `staleTime: Infinity`. `git.changed` is the pointer case and invalidates
`['agent', id, 'git', projectName]`. `refreshOnAgentEventsConnect` covers the
git prefix: projects arrive in the connect snapshot, git items never do.

Because both REST and the push write the same cache, the project list keeps its
**wire shape** (`projectInfoSchema`) instead of a camelCase mapping.

### Project reorder, delete and form

**Reorder is buttons first, drag second.** Every row carries Move up / Move
down plus a live region announcing `"<name> moved to position N of M"`; a
native `draggable` handle is a desktop-mouse convenience on top. Pragmatic
drag-and-drop was rejected: it rides the HTML5 drag API, which is unreliable on
touch.

`PATCH /api/projects/order` takes the **complete** name set and 400s on any
mismatch, so the move is applied to the full list even when a row is hidden by
an open undo window. The optimistic order is an *override*, never a cache write,
and is released only when the server's own order matches it exactly.

**Project delete is deferred-commit**, not mobile's immediate delete with a
recreating Undo: the compensating `POST` cannot restore `linear_api_key` (the
server never echoes it) or the project's `order`. Android and iOS follow in
CLA-240 / CLA-241. Only one deletion is in flight at a time — there is no batch
endpoint — so a second delete commits the first and opens a fresh window.

**The project form tracks the live snapshot.** React Hook Form's `values` +
`resetOptions: { keepDirtyValues: true }` re-seed every untouched field while
protecting the edited ones, and `buildProjectUpdate` diffs against that same
snapshot, so untouched ⇒ equal ⇒ absent from the patch. Flags and the Auto Scan
filter are arrays, so they carry their own touch gate instead.

The three-state contract is `linear_api_key` and `conda_env` only:

- Absent keeps, `""` clears, a value replaces.
- `name`/`path` have `min_length=1` server-side, so `""` there is a 422.
- `default_flags` clears with `{}`.
- `auto_scan_filter` has no clear state and is always replaced whole.

The Linear key gets the same handling as every other secret in the SPA: a
masked `PasswordField` with a reveal toggle, and the save mutation pins
`gcTime: 0` with `onSettled: save.reset()` — otherwise TanStack keeps the
submitted variables, plaintext key included, readable app-wide through
`getMutationCache()` for minutes after the dialog closes.

Two related traps the form closes: `editing` is resolved by name, so a project
deleted or renamed by another client closes the editor instead of flipping the
dialog into *create* mode; and the Auto Scan filter is validated even while
hidden, so Save is never disabled with no reason on screen.

### Git delete and branch management

**Git delete ports `GitScreenModel` whole**: rows hide immediately, the batch
lives outside the timer so a coalescing restart cannot discard it, `results` map
positionally onto the request, `force_required` restores the row *and*
re-prompts, a short `results` array restores the unreported tail, and the busy
409 restores the entire batch because the guard runs before anything is deleted.
The overlapping-load guard is not ported, because the list lives in TanStack
Query, whose invalidation cancels an in-flight refetch on a warm cache.

The 409 body (`{"detail": {"busy_by": […]}}`) is in neither OpenAPI schema, so
it has a hand-written Zod mirror and a shared parser in `src/api/conflicts.ts`.

**Branch management (#677)** rides the same screen once
`capabilities.git.branches` is advertised — probed with the terminal's
`useSessionGitCapabilities`, which is per agent despite its name.

The branch list always asks for `?remote=true` (an older agent ignores it) and
renders local and remote rows as an A→Z folder tree (`gitTreeRows`). The remote
rows sit inside a `<details className="advanced">` under the local ones, closed
until opened or until the filter — a plain `type="search"` input that appears at
eight rows — is non-empty. Rows show the tip commit's subject and a
`YYYY-MM-DD` prefix of `committed_at` (a slice, never a parse, so the day is the
committer's own on all three clients).
- Shows the tip commit's subject and a `YYYY-MM-DD` prefix of `committed_at` on
  each row (a slice, never a parse, so the day is the committer's own on all
  three clients).

Branch actions:

- *New branch…* opens `GitNewBranchDialog` (name, a `SelectField` start point
  defaulting to the checked-out branch with `null` for the agent's `HEAD`, a
  checkout checkbox).
- Check out is a per-row button on `branch_only` and `remote_branch` rows behind
  an `alertdialog` confirmation whose initial focus is Cancel.
- Both mutations reuse `toSessionGitError` after the busy check. A `busy_by` 409
  opens the existing "Item is in use" dialog. Every coded detail becomes a
  `SessionGitError` whose copy `gitDisplay.ts` maps (`dirty`,
  `checked_out_elsewhere`, `operation_in_progress`, `job_running`,
  `ref_not_found`, and `exists` / `bad_branch_name` / `unknown_start_point`
  inline in the dialog).
- `isDeletable` is now a type guard gated on `capabilities.git.delete_remote` (an
  older agent answers the whole batch with a `422`). A remote row is deletable
  only behind it, and a batch holding one opens a "Delete on the remote?"
  confirmation before it enters the undo window.
- A `branch_with_worktree` row also gets a *Remove worktree* action that drops
  only the worktree and keeps the branch (`enqueueWorktreeRemoval`).

The terminal's `⋯` menu gains *Branches…* after *Fetch / Pull / Push…* for a
project-backed session, navigating to
`/agents/:agentId/projects/:projectName/git`.

### Templates and fixture coverage

**Templates** add create/edit/delete to the picker's existing list/touch/
favourite calls. The management grouping is Favorites then All, each sorted
case-insensitively — deliberately not the picker's recents-first grouping,
because a management list must show every template exactly once. The editor
keeps its draft on a failed save (iOS's guard; Android has none), and the body
is sent verbatim while the name is trimmed, since a body reaches the PTY
unchanged. `TemplateEditorDialog` is shared with the terminal's picker, which
creates mid-session through its optional `initialBody` (see *Terminal screen*
below); `/templates` stays the only place one is edited or deleted.

Fixture coverage: `ProjectInfo`, `BusyByResponse` and `BusyHolder` moved into
`COVERED_FIXTURES` with Zod mirrors. The eleven remaining WEB-09 fixtures are
REST request/response bodies and were re-pointed to `OWNER.generated`;
#677's `GitBranchCreateRequest`, `GitCheckoutRequest` and `GitBranchResponse`
follow them there.

## Terminal screen (WEB-05)

Built on **xterm.js 6.0** (`@xterm/xterm` plus the `fit`, `webgl`,
`clipboard` and `unicode11` addons). Route
`/agents/:agentId/sessions/:sessionId` — the **only lazy route**: xterm and
its WebGL addon outweigh the rest of the SPA, so they load on first attach
rather than on the sign-in screen. It is reached from the sessions list at
`/`, or from the sessions panel below.

The same route renders the chat view instead of xterm for sessions whose
`ui` is `chat` (`routes/chat/SessionRoute.tsx` picks by `ui`); see *Chat
Sessions* in `features.md`.

### Route and sessions panel

**Sessions panel.** A `☰` "Sessions" button in `.terminal-bar` opens a panel
listing the connected agent's sessions (state dot, title, kind · project,
attention chip; the current row highlighted), a "New session" shortcut and the
account's other agents (online → select it and go to `/`; offline rows
disabled).

- **Unpinned** (or on a viewport under `(min-width: 901px)`, see
  `useWideViewport`): a `popover="auto"` overlay sliding in from the left.
- **Pinned and wide:** a docked
  `<aside class="sessions-panel sessions-panel-docked">` beside `.terminal-page`
  (xterm refits once). The pin persists in `useSessionsPanelStore`
  (`localStorage` key `mobaide-sessions-panel`).

Picking a session `navigate`s with `replace: true` (and records
`setLastUsedSession`), so Back does not step through every switch.
`TerminalView` is keyed on the channel key so per-session dialog state resets.

**Stopping from the panel.** Each running/starting row has a compact Stop icon
button, opening the same Stop / Force Stop / Cancel confirmation as the list
(shared mutation in `routes/sessions/useStopSession.ts`). Stopping another
session folds its row out of the list; stopping the one on screen navigates to
the nearest open (running/starting) session by panel position, below first then
above, and to `/` only when none remains. The switch hands off through
`stores/stopHandOffStore.ts` because `TerminalView` remounts per session.
Failures toast "Failed to stop session: …". Android and iOS open the same list
from a left-edge swipe or the top-bar button.

### Terminal model and layering

**Layering.** `src/terminal/` is framework-free — pure reducers
(`terminalReplay`, `terminalFollow`, `terminalPaste`, `terminalKeys`), the
clipboard/renderer/theme adapters, and `TerminalSessionModel`, which owns the
socket, the emulator and a per-model Zustand store the React tree reads.
`src/routes/terminal/` is presentation only.

Models live in `terminalStore` outside React so navigating away and back keeps
the buffer instead of paying for a fresh replay; three stay resident,
least-recently-acquired evicted
(iOS keeps one per session until the session dies). Residency is not the same
as streaming: see *Unwatched sessions* below. Each model self-registers with
`liveChannels`, so `stopAllRealtime()` on a session change tears it down.

**One Terminal in the DOM.** `open()` runs once, into a host `div` the model
owns and views adopt; a hidden terminal is an *unmounted* one, never a
`display: none` one, because xterm cannot `open()` there and `FitAddon`
mis-measures under it. `open()` is further deferred until the container has
a non-zero size.

**`.terminal-surface { isolation: isolate }` traps xterm's own z-indexes.**
xterm numbers its layers only against each other (up to 11 for the scrollbar)
but nothing in its own tree opens a stacking context, so without this rule they
escape into the *root* one and outrank every `z-index: auto` app overlay. The
link canvas silently ate clicks meant for the templates picker, the
jump-to-bottom badge and the connection overlay's Reconnect button.

**A session that ends while open stays readable.** `useSessionGone` treats
"absent from a synced snapshot" as the same proof of death auto-resume uses
(`useSessionsSynced`) and returns to `/` with "That session has ended." — but
never for a session it has already seen alive, so a session ending or being
stopped under the user leaves the output on screen instead. Because the route
is directly reachable with no session list ever mounted, it holds the agent's
events channel for its own lifetime. The terminal on screen is also exempt from
`evictTerminalSessions`'s death-driven eviction. Creating a session seeds the
sessions cache synchronously from the create response before navigating, so the
gap before the agent's own `sessions.changed` echo cannot read as the session
already being gone.

### In-session Git panel

#### Menu button and Changes panel

**In-session Git (`⋯` → Git → Changes…).** The header's `⋯` "More options"
button (`components/MenuButton.tsx`, shown once the session row carries a
`cwd`) is an APG menu button on the Popover API: `popover="auto"` puts the menu
on the top layer, so it sits above xterm's canvases and outside the header's
horizontal scroll clip with no portal. Its open state is mirrored from
`beforetoggle` so a light dismiss or a modal dialog opening over it cannot
desync `aria-expanded`.

"Changes…" opens `routes/terminal/git/GitChangesPanel.tsx`, a
`popover="manual"` slide-over (`role="dialog" aria-modal="false"`) — not a
`<dialog>`, because the terminal must stay live underneath. It is the web mirror
of the Android and iOS Changes/Diff screens (#669): the repo header, the
Conflicts / Staged / Changes / Untracked list with status badges, and the diff
pane with a line-number gutter and, only for a hunked text diff, a *Changes* /
*Full file* switch. *Changes* is context 3; *Full file* is the agent's
5 000-line ceiling (PROTOCOL.md). The layout is two panes from 900 px and
stacked below that.

#675 turned the viewer into an editor: per-row Stage / Unstage / Discard
buttons and each section's Stage all / Unstage all, a checkbox multi-select with
its own toolbar, and matching buttons on the diff pane. Every one is gated on
the matching `capabilities.git.{stage,discard,commit}` key, with Discard always
behind a confirmation dialog.

#### Commit, sync and rebase dialogs

The panel header's `Commit…` button, and the terminal menu's
`⋯ → Git → Commit…` entry (a `commitRequest` counter), open
`GitCommitDialog.tsx`: Summary + Description, an author line and Amend prefill
read from a `commit-context` query, and a Skip hooks (`--no-verify`) checkbox,
with hook output shown on a `hook_failed` refusal.

#676 adds the network operations as agent-side jobs: the terminal menu's
`⋯ → Git → Fetch / Pull / Push…` (a `syncRequest` counter), the panel header's
`Sync…` button and the repo header's `↑n ↓m` line all open `GitSyncDialog.tsx`.
It shows three rows, each with the disabled reason under it (Fetch always, Pull
when behind, Push when ahead or *Publish branch* without an upstream), then the
running job's `phase` / `percent` with Cancel, or the outcome with its
follow-ups: Pull with rebase / merge after `not_fast_forward` when the branch
still has a live upstream, and Force push (with lease) after `rejected` or a
non-fast-forward push. Each follow-up sits behind a nested `alertdialog` whose
initial focus is Cancel. The panel shows the same job as a notice strip until
its result is dismissed (a success clears itself after 6 s).

#678 adds an in-progress **rebase**: `⋯ → Git → Rebase…` (gated on
`capabilities.git.rebase`) opens `GitRebaseDialog.tsx`. A project-backed session
picks the target from `listGitItems`, each sent as a fully qualified
`refs/heads/…` / `refs/remotes/…` ref so a same-named tag cannot shadow it; any
other session, or a list that fails to load, types the ref instead. Nothing is
preselected. While the status reports `state: rebasing` the panel shows a third
strip; with a non-null `repo.rebase` (not a `git am` session, not an older
agent) it carries Continue (disabled while any conflict remains), Skip… and
Abort…, the latter two behind an `alertdialog` with Cancel focused. Otherwise it
points to the terminal.

#### Conflict resolution

A conflict row's list entry and diff pane both gain a *Mark resolved* (✓)
action, gated on `capabilities.git.{stage,resolve}` together; only `resolve`
certifies the agent refuses a file still holding `<<<<<<<`/`>>>>>>>` markers.

With `capabilities.git.conflicts` a conflict row opens `GitConflictPane.tsx` in
the diff pane's place: a whole-file `<textarea wrap="off">` editor naming the two
sides by label and role (never "ours"/"theirs"), a conflicts-left counter with
Next conflict, Apply & mark resolved (Ctrl/⌘+Enter) and Save draft, and an
unsaved-edits guard on every way out (`useUnloadGuard` covers tab close). A file
resolved this way lists in a *Resolved* section with a confirmed *Restore
conflict…*. `queryKeys.sessionGitConflict` deliberately sits outside the
`session-git` prefix so neither `git.changed` nor the reconnect sweep re-reads
the file under a draft.

#### Job state and Escape handling

Job state is one holder per panel (`activeJobId`). The frames `git.job.progress`
/ `git.job.done` are *written* into
`['agent', id, 'session-git', sid, 'job', jobId]` by
`realtime/sessionGitJobCache.ts` as monotonic snapshots (a `running` frame never
overwrites a `done` one). `useSessionGitJob` polls `GET …/git/jobs/{id}` every
3 s, only while the job runs, so the `git.changed` invalidation after a job
cannot refetch it into a `404` after the agent's retention window. `busy` —
which gates every staged-set write, because the agent answers `job_running` —
includes a running job.

Because a top-layer `<dialog>` still bubbles its `Escape` keydown to this DOM
parent, the panel's own handler closes the panel only when none of the commit,
sync or rebase sheets, a pending rebase-step confirmation or the discard
confirmation is open.

#### Data layer and polling

The data layer is `api/sessionGitRepository.ts` on the generated client. The two
404s are told apart by FastAPI's `detail` string, after a coded `detail` object
has been checked first, so the job routes' `404 job_not_found` reads as a lost
job, never as an outdated agent. `GitJobResponse` also rides the socket and keeps
`protocol/dto/gitJob.ts`.

`useSessionGitStatus` polls every 10 s only while the panel is open, the tab
visible and the window focused; each poll carries the last `etag`. The queries
live under `['agent', id, 'session-git', …]`, deliberately not the `gitItems`
prefix. `git.changed` invalidates the viewers whose session `project_name`,
`repo.toplevel` or `repo.common_dir` matches, and the reconnect sweep covers the
root. The four mutations settle by invalidating only the status key (a commit
also drops `commit-context`), and their outcome renders in the panel's own
notice strip rather than a toast, because toasts paint below the top layer.

#### Platform deviations from mobile

The diff pane scrolls on both axes with no 2 000-column cut (the mobile cut
exists because their layout engines cap a pinned row width). Gutters are
CSS-generated content so a copy takes the code alone. There is no watch hold:
the unwatched-suspend governor below keys on `document.hidden` and the
mounted-terminal pointer only, and the terminal stays mounted under the panel.

### Replay, sizing and follow mode

**Replay** follows the mobile state machine exactly: `'b'` feeds
`ESC[H ESC[2J ESC[3J` **into the write stream** (never `Terminal.reset()`,
which executes immediately, jumps queued writes and clears the program's
DECSET modes), `'o'` chunks append, `'e'` is guarded on being in a span. Any
inbound binary upgrades a reconnecting channel to live *before* the decode. The
overlay is driven by socket state, not by the first frame, and stays up past
`'e'` until the replay writes report parsed through their `write` callbacks.

**Size authority.** The grid is cached before the 100 ms resize debounce and
re-asserted at all four live triggers, because the agent spawns every PTY at
24×80 and never reports back:

1. Socket open.
2. First inbound binary of a cycle.
3. `replayEnd` — only the one closing this client's own handshake, since the
   relay fans every attach replay out to all clients and a hidden retained tab
   must not resize the PTY back.
4. The tab-shown / window-focus switch-back edge (GH #631). It bypasses the
   same-size dedupe cache, since a peer client may have resized the shared PTY
   while this one sat idle, and fires only while already live.

**Follow mode is derived, not commanded**: pinned ⇔ `viewportY === baseY`.
xterm scrolls to the tail on genuine user input only, so a program's own
`ESC[6n` cannot un-pause a scrolled-up viewport, and the
`isApplyingEmulatorOutput` discriminator both mobile clients needed must **not**
be ported. The unread tally counts rows entering the scrollback as
`max(0, ΔbaseY) + max(0, −ΔviewportY)`, which stays exact past the `scrollback`
cap where `baseY` saturates. Wheel, viewport pointer-drag, Shift+paging keys,
resize and the replay wipe re-baseline instead of accruing. Cap 9999, label
`999+`, cleared by re-pinning — same rule as mobile; like iOS it does not count
DECSTBM region scrolls, while Android does.

### Scrollback slider

**The scrollback slider** is the visible half of that state, and on a touch
device the only way to reach it: xterm 6 scrolls through a VS Code
`ScrollableElement` and registers no touch gesture target of its own, so a
finger swipe on the grid moves nothing.

**One 14 px gutter, one scrollbar.** `FitAddon` reserves the gutter as the
**constant** `options.overviewRuler?.width || 14` whenever `scrollback` is
non-zero, so the track costs no columns and forces no reflow.
`--terminal-scrollbar-width` is the other half of that pair; keep the two equal
across an addon bump. The `.xterm-viewport` native bar and the
`ScrollableElement` slider (`z-index: 11`, `opacity: 0` but
`pointer-events: auto`, so it swallows every press) are hidden; the wheel and
scroll APIs live on the scrollable element itself.

Geometry is `size = rows / (baseY + rows)`, `offset = viewportY / baseY`, in a
**second Zustand store** on the model, so a busy PTY does not re-render the
header bar and every overlay once per row; the model quantizes to a thousandth
of the track. Every scroll command re-baselines the tally first, so the slider
cannot light its own unread badge. Drag, track-click paging, the wheel over the
track, and ↑/↓/PageUp/PageDown/Home/End on a focused `role="scrollbar"` all
route through the same model methods. A pixel-mode wheel converts at one row per
cell height with a carried sub-row remainder. Presses are gated on the primary
button and cancelled (the track is `tabIndex={0}`, so an un-cancelled click
would move focus off the grid); drag events are keyed on the pointer that began
the drag.

**The control has two forms**, the web spelling of the mobile scroll ladder.
With scrollback it is the scrollbar above. Without — the alternate screen, or
any program holding mouse tracking — it becomes a **grip** parked mid-track that
reports travel and springs back; every full-screen TUI, so Claude Code and
Codex, is *only* ever there. Scrollback wins over the program when both apply.
Only when neither applies does the track go inert with `pointer-events: none`.

**The grip encodes nothing.** `relativeScroll` synthesizes a `WheelEvent` at a
pinned anchor inside `.xterm-screen` and lets xterm encode it (a mouse report
with buttons 64/65, or a DECCKM-correct `ESC O A`/`ESC [ A` on the alternate
screen), because `IModes.mouseTrackingMode` does not expose the mouse *encoding*
protocol. `deltaMode` is LINE with a unit delta, so one event is exactly one
step; the per-call ceiling is one screen, matching iOS, because every step is a
frame crossing a WebSocket.

Where the primary pointer is coarse the thumb takes a 2.75rem transparent hit
area, deliberately **not** on `any-pointer`, which would swallow mouse
selections of the right-hand columns on a touchscreen laptop. Forced-colors
rules live in their own block *after* the base ones, or the shared block would
lose the cascade.

### Renderer, theme and clipboard

**Renderer.** WebGL, with `onContextLoss` → dispose (which reverts to the DOM
renderer with the buffer intact) → one bounded re-arm → stay on DOM after a
second loss.

**The grid is always dark**, whatever the app's theme preference — the same
choice both mobile clients make. A dark 16-colour ANSI palette on the light app
background puts white on near-white (~1.1:1 for bright white). The 16 colours
are spelled out as `MobaideColors.ansiColors`, the table Android and iOS render
with; xterm's own default is the **Tango** palette, a different set entirely.

**Copy/paste is web-only design**, not a port: mobile has no physical keyboard
and its `Ctrl+C` is unconditionally `0x03`. The web client has no in-app
selection surface — the browser already owns the selection and the context menu.

- Ctrl+C copies when there is a selection; Ctrl+Shift+C always copies (never
  SIGINT).
- Ctrl+V, Ctrl+Shift+V and Cmd+V hand off to the browser's native paste, since
  `clipboard.readText()` is prompt-gated in Firefox and Safari.
- A persisted "pass Ctrl+V to terminal" toggle sends `0x16` instead, for Claude
  Code's image paste.

The `paste` event is intercepted in the **capture** phase, ahead of xterm's own
handler, which does the newline rewrite and bracket wrap but no ESC/C1 strip.
Paste encoding is the mobile encoder:

1. Strip ESC + C1 **first** (so a clipboard carrying `ESC[201~` degrades to a
   literal `[201~`).
2. Then `\r?\n` → `\r`.
3. Then wrap iff the program enabled DECSET 2004 — as **one** `i` frame (three
   frames over a lossy WS + relay can strand the closing marker) capped at
   1 MiB, because one oversized frame trips the agent tunnel's 4 MB limit and
   drops *every* session on that agent.

The toggle outlives the tab: `terminalPrefsStore` persists it under
`localStorage` key `mobaide-terminal`. The other persisted keys are
`mobaide-theme`, `mobaide-agent`, `mobaide-sessions`, `mobaide-launch-prefs`,
`mobaide-sessions-panel`, `mobaide-external-sessions-notice` (the dismissible
external-sessions notice above the sessions list), and the wizard's per-agent
`recent_custom_paths::<agentId>`, deliberately unprefixed for mobile parity —
CLA-258's `clearAllRecentPaths()` keys off `recent_custom_paths::` itself.

**OSC 52**: live writes allowed, replayed writes and reads refused. Both
refusals ride one handler registered *after* `ClipboardAddon` (xterm dispatches
OSC handlers last-registered-first), so the sequence is swallowed rather than
answered or acted on. The agent replays its ring verbatim on every attach, so a
single vim/tmux yank left in that history would otherwise re-copy on every
open. The gate is `isParsingReplay()`, i.e. `inReplay || pendingReplayWrites >
0`, because `term.write` is asynchronous. All three clients gate this and
attribute a sequence to the span its *terminator* lands in.

Known gap, tracked in GitHub issue #634: a program can end its output *inside*
`ESC]52;c;<base64>` with no terminator. The ring then ends mid-sequence, and
the first live byte that terminates it dispatches the write as live —
re-opening the hijack on every attach. On web and iOS a bare `ESC` is enough;
Android needs a real `BEL`/`ST`. Any keystroke echo supplies the byte, but not
the connect-time size assert. The fix is to abort any open sequence at the
replay boundary with a `CAN` (`0x18`) fed through the replay-attributed path,
not to change the attribution rule.

No browser analogue of Android's `EXTRA_IS_SENSITIVE` or iOS's `localOnly` +
60 s expiry exists — accepted divergence.

### Synchronized output, frames and divergences

**Synchronized output (DEC 2026) is native** in xterm 6, so neither iOS's byte
scanner nor Android's deferred-paint timer is ported. While
`terminal.modes.synchronizedOutputMode` is armed the painted screen lags the
buffer, so a copy refuses and says so. The mode is read at the instant of the
copy, never cached in view state: xterm disarms it on a 1 s safety timeout with
**no** event.

**Both copy paths carry that guard.** Ctrl+C is ours already, but the context
menu's Copy never reaches `copySelection`: xterm answers the `copy` event from
the buffer, wholly inside xterm. A capture-phase `copy` listener refuses it on
an unsettled screen, and leaves a copy carrying no terminal selection alone.

**Frames sent.** `i` from keystrokes, mouse reports (`onBinary`) and paste,
carrying no trailing CR — the agent owns that timing (proto-v4). Web produces
neither the `m` submit frame nor the `x` execute frame; both stay in the
protocol for Android and iOS.

Keystrokes and paste are gated on `live && running` **plus a web-only third
term: the replay write queue must have drained.** `'e'` flips the connection
synchronously while `term.write` is asynchronous, so a DECRQM / XTVERSION /
DECRQSS / OSC 4 query still sitting in the replayed history would otherwise be
answered after the gate opened and land in the user's live prompt.

Every outbound text frame is capped at 1 MiB, not just paste: the relay forwards
a client frame to the agent's tunnel unchunked and that tunnel is opened with
`max_size=4 MB`, so one oversized frame closes it with 1009 and drops *every*
session on that agent.

**Accepted divergences from mobile**, all under the CLAUDE.md
platform-convention exemption:

- **Full display mode only** — no Static / forced-column mode and no horizontal
  pan, because a desktop window is wide and fit-to-window is the browser
  convention; and the whole desktop keyboard surface above.
- **No in-app selection toolbar**: mobile's long-press bar (Send to Input · Copy
  · Run) has no web counterpart; the browser's own context menu covers Copy.
  Removing it retires web's only "Send to input" path and its only producer of
  the `x` execute frame.
- **A draggable scrollback thumb**, which neither mobile client has. The
  *ladder* is not a divergence — web runs the same three arms mobile does, and
  the control that drives them is a grip rather than a pan on the grid. Grid
  drag-to-scroll remains unported: under mouse reporting the program already
  claims the drag.

### Prompt-template picker

The prompt-template picker is a `Modal` — a native `<dialog>` on the browser top
layer opened via `showModal()`. It has the search field focused on open,
Arrow-key row navigation, an Enter that picks the first match but steps into the
list instead when the filter is empty (so nothing inserts blind), Escape closing
deterministically, and focus returning to the terminal grid on dismissal.

Messages raised *while the picker is open* render inline rather than as toasts,
since `.toast-viewport` sits below the top layer. Loading, load failure (with
Retry), an empty account and a filter with no matches are each their own state,
and rows disable with an explanation while the session refuses input. It lists,
groups Recents/Favorites/All, filters, inserts and toggles a favourite; editing
and deleting a template still belong to `/templates`.

#### Creating a template from the session

**Creating one without leaving the session.** A **New template** button sits
beside Close in the picker's head and opens the same `TemplateEditorDialog`
`/templates` uses, seeded through its optional `initialBody`.

- **The two dialogs are never co-mounted.** `TerminalView` keeps a single
  `TemplatePanel` union (`none` / `picker` / `editor`), so opening the editor
  unmounts the picker. An element on the top layer still propagates events
  through its DOM parent, so a nested editor would lose ArrowUp and ArrowDown
  in its textarea to the picker's row walk, and its draft to the Escape that
  dismisses the picker. The union also keeps the picker-to-editor hop from
  reading as a dismissal and yanking focus.
- **The body is the terminal selection, snapshotted when the picker opens**, in
  the Templates button's own handler. WEB-06 removed the composer, so the
  selection is the only in-session text source web has. PTY output keeps
  arriving behind the modal and `SelectionService` clears on user input, so it
  cannot be read later; the grid is inert while the dialog is up.
- **The pre-fill declines `screenIsSettled()`**, which every other selection read
  carries. That guard protects a copy onto the system clipboard; this text lands
  in an editable field the user has to name and save.

The success toast is the one message here exempt from the inline rule: the
editor closes first, so the viewport is clear by the time it paints. A failed
save stays inline and keeps the draft.

**Mobile's shape differs, deliberately.** Android and iOS put a *Save as
template* button in the composer's action row: the body is the composer text,
rendered as a read-only preview. Web has no composer, so the body is the
selection and stays editable, and — as with the management editor
below — the dialog survives a failed save.

## Desktop keyboard & shortcuts (WEB-06)

The input model on top of the terminal. Native keystrokes are the **only** input
path: every chord types straight into the PTY, and the work here is a few guards
around that and making the chords the browser steals reachable anyway.

**The control-key bar, its saved-shortcut chips and picker, the command palette
and the composer were removed** (`ShortcutBar.tsx`, `ShortcutPicker.tsx`,
`RenameShortcutDialog.tsx`, `CommandPalette.tsx`, `Composer.tsx`, along with
`terminal/controlKeys.ts`, `keyboard/appShortcuts.ts`,
`keyboard/useAppShortcuts.ts`, `keyboard/chordConflicts.ts`,
`routes/terminal/useSessionKind.ts` and `api/shortcutRepository.ts`). A desktop
has a physical keyboard, so web now has **no app-owned chords at all**.

The prompt-template picker survived the cut — it is reachable from a
**Templates** button in the terminal header, and picking a template pastes its
body onto the running program's input line via the sanitized bracketed-paste
path (insert for review, never auto-execute).

Android and iOS keep their control-key bar, saved shortcuts and composer
unchanged; the central server keeps `/api/shortcuts` and `shortcut.*` for them.

### Clipboard copy on template pick

**A pick also copies the body to the system clipboard**, and says so in a
toast. Claude Code collapses a paste over 800 characters, or with more than
`min(rows - 10, 2)` newlines, into a `[Pasted text #n +m lines]` placeholder —
nothing is lost, the full body is still submitted on Enter — and prints its
own hint, `paste again to expand`. Expansion requires pasting text that
normalises identically to the first paste, so without a clipboard copy that
hint is unactionable. That threshold is row-dependent and lives in a binary this
repo does not pin, so the web client copies unconditionally.

What goes on the clipboard is the body minus ESC and C1, via `stripControls`
— **not** the fully sanitized `\r`-joined form, which would render as one
run-on line outside a terminal. The wire bytes are unaffected: a browser paste
re-enters the same `pasteText` → `sanitizePaste` encoder, and `stripControls` is
idempotent.

Stripping matters because the clipboard is a consumer the encoder's
escape-injection defense cannot reach — a native terminal pasted into next
receives those bytes live (the CVE-2021-31701 class `terminalPaste.ts`
documents). That covers this path only: OSC 52 writes (`terminalClipboard.ts`)
still copy the program's bytes verbatim, as Android and iOS do. The write is
best-effort (`writeClipboardText`), fires only after `pasteText` reported the
frame sent, and is issued without an intervening `await` so Safari and Firefox
still see the click's transient user activation.

A picked body lands in the ordinary system clipboard and in whatever history or
cross-device sync the OS keeps — the web has no `EXTRA_IS_SENSITIVE` or
`localOnly` analogue — and it cannot be purged on sign-out. **Mobile needs no
mirror**: a template there lands in the app's own composer, so no placeholder is
ever left for the user to expand.

### Shared chord contract

**The shared chord contract is still a fixture, not three tables.** The token
grammar (`ctrl+shift+i`: modifiers in ctrl, alt, shift order, at most two, over
a–z / 0–9 / F1–F12 / tab / enter / space / backspace, Esc excluded) and the
legacy-first encoder (C0 for Ctrl+letter, ESC-prefix for Alt over a single-byte
core, xterm SS3 / CSI forms for F-keys, kitty CSI-u for everything legacy bytes
cannot express) live in `tests/protocol_fixtures/keys/key_chords.json`, read by
all three clients: `web/src/protocol/keyChord.ts` (gated by
`protocol-integrity.yml` — the **only** CI-enforced arm, since iOS CI is
push-to-main-only and Android's tests are not CI-gated),
`KeyChordFixtureTests.swift`, and `KeyChordFixtureTest.kt` (Android's first
fixture consumer; extending its loader to `dto/` is CLA-235). Kotlin stays the
reference implementation: a disagreement means the fixture is wrong.
`keyboard/recordChord.ts` and `keyboard/reservedKeys.ts` also survive, serving
only the dev-only key-sweep screen below.

### Passthrough, guards and immersive mode

**Passthrough.** `attachCustomKeyEventHandler` remains the funnel for the WEB-05
clipboard policy, and the send gate is mirrored into `disableStdin` and never
into the key handler.

**Reload and unload guards.** `useReloadGuard` is a **capture-phase**
`preventDefault` on Ctrl/Cmd+R, its Shift form and F5, and nothing else, so a
focused terminal still receives `0x12`; it covers focus parked outside the grid.
`useUnloadGuard` registers `beforeunload` only while the session is live *and*
running; in Firefox on Windows and Linux it is the only thing between an
unsuppressable Ctrl+W and a closed tab.

**The reserved-key table** (`keyboard/reservedKeys.ts`) splits "what the browser
does" from "whether the page can stop it", because only the first is documented.
Every row ships `documented`; `/dev/key-sweep` — a DEV-only route, dropped from
the production bundle — measures the second. Sources, runbook and the empty
results matrix are in docs/web-keyboard-reserved-keys.md; running it is CLA-251.

**Immersive mode** is fullscreen plus `navigator.keyboard.lock()` — the only way
Ctrl+W, Ctrl+T, Ctrl+N and F11 ever reach a web page. Chromium-only and
feature-detected. The permission prompt Chrome announced for 131 was never
launched, so there is none to design around.

Tier-1 PWA widening is wired but conservative: a minimal `manifest.webmanifest`
makes `display-mode: standalone` observable, and rows flagged
`relaxedInStandalone` only *soften* their warning until the macOS Cmd+W probe
settles whether the claim is true at all.

## Auto Scan, notifications & settings (WEB-10)

The last live-surface milestone: the Auto Scan monitor, the attention
surface, and a settings hub.

### Auto Scan monitor and caches

**Monitor, two channels.** The card list rides the shared agent-events bus
that `SessionList` already opened and mounts as an "Active Auto Scans" section
above the sessions; only the detail screen at
`agents/:agentId/autoscan/:projectName` opens the dedicated `ws/autoscan`
channel. Both ids ride in the path, because a scan is one project on one host
and the selected-agent store would make a shared link resolve elsewhere.

That channel is capacity-limited: past its ten-subscription cap, or when the
agent's send queue is full, the relay accepts the socket and then closes it with
`1013`, which the screen latches into a declined banner with a manual retry
rather than the close-code policy's default reconnect.

**The three `autoscan.*` events, judged by the snapshot-vs-pointer rule.** None
of them is an invalidation. `autoscan.list.changed` carries the agent's entire
`list_response()` and `autoscan.status` a complete `AutoScanInstanceDto`, so
both are *written* (`realtime/autoscanCache.ts`); the status upsert replaces a
row and deliberately never inserts one, because the event carries no
`capacity`/`max_scans`/`available_projects`. `autoscan.event` is *ignored*: it
is one scan event, the stream the detail screen reads from its own channel, and
`AutoScanInstance._emit` publishes one per emitted log line.

The Auto Scan list is nonetheless the one written cache that also needs a
connect-time refresh: the agent publishes `autoscan.list.changed` as a connect
snapshot only on the direct `ws/events` path, while
`relay/client.py::_handle_request_events_snapshot` republishes sessions and
projects and stops there, and the relay is the only path a browser takes.
`refreshOnAgentEventsConnect` covers the gap, which is also why the query must
not pin `staleTime: Infinity` (CLA-234). Filed as CLA-249 for the agent.
`GET /api/autoscan/status` has no relay handler at all, so detail state is
seeded from the WS `snapshot` frame and never from REST.

**Two dedupe layers, both needed.** The wire cursor
(`protocol/autoscanFrames.ts`, WEB-04) is rebuilt from the live high-water mark
on every reopen and drops `seq <= cursor`, which is mandatory: when its ring has
already rolled past the client's cursor the agent replays everything it still
holds. The reduction cursor (`autoscan/autoscanModel.ts`) makes reducing the
same frames twice idempotent, as a remount or a snapshot overlapping live events
produces. Both are `bigint`, because `seq` is an unbounded per-instance counter
and a `number` stops discriminating past 2^53. Android has neither layer
(CLA-119, still open in the code despite being closed).

**ANSI without a dependency.** `protocol/ansi.ts` is a port of the iOS
`AnsiParser` that returns integer `[r,g,b]` runs, the shape
`tests/protocol_fixtures/ansi/ansi_sgr.json` asserts; `ansi_up` was rejected
because it emits an HTML string. Colours ride React's `style` prop, a CSSOM
write that CSP never gates — and the SPA's `style-src` carries `'unsafe-inline'`
regardless, forced by Google Identity Services.

### Attention notifications

**Notifications are edge-triggered only.** `attention` is *latched* on the
agent — cleared only by a client PTY write, a `UserPromptSubmit` hook, or output
resuming — so every full-state snapshot keeps reporting it and
`core/attention_gate.py` warns clients not to re-post from one. Web consumes
only the one-shot `session.attention` edge, with no reconcile and no seen-store:
a closed tab shows nothing, because there is no background delivery to reconcile
against.

It listens on the **user-events** channel, not the agent bus. That payload is
the slim `{session_id, reason, title?, project_name?, exit_reason?, agent_id}`
and the channel is account-wide, so a browser — the first client that
routinely holds several agents — is alerted about all of them rather than
only the one whose list is open. Consuming both channels would double-post.

*Clearing* an alert cannot be edge-triggered: the agent publishes no "attention
resolved" event, only the full session row with the field gone. So
`dismissResolvedAttention` watches `session.updated` and `sessions.changed` on
the agent channel and closes the matching notification. Reading a snapshot to
*dismiss* is safe, because the trap `attention_gate.py` warns about is
re-raising an alert from a latched field.

`notifications/attentionContent.ts` is a byte-for-byte port of the same map iOS,
Android and `central_server/push/content.py` carry, em dashes included.

Delivery follows the rule both mobile clients share — never alert about the
session already on screen — with the split a browser makes possible: a hidden
tab gets a system notification (tagged with the session id, so it replaces
rather than stacks), a visible one gets an in-app toast. Permission is requested
only from the button in Settings, never on load.

### Unwatched sessions and tab lifecycle

**Unwatched sessions: the PTY socket does not stay open.** *Watched* means the
tab is visible **and** this session is the terminal the route is rendering, so
the two retained-but-off-screen models `MAX_RETAINED` allows are treated exactly
like a hidden tab.

The session protocol has no pause frame (C→S is only `i`/`x`/`m`/`r`), so
closing the socket is the only way to stop the bytes, and every reopen costs a
full ring replay (4 MiB by default, uncompressed, and it wipes the emulator).
`terminal/terminalSuspend.ts` is therefore a conjunction: suspend once the
session has spent `RECOUP_BYTES` (4 MiB, one ring) **and** gone unwatched for
`GRACE_MS` (60 s), with `FIREHOSE_BYTES` (32 MiB) cutting the grace short for
`yes`-grade output. It is a pure predicate evaluated only as frames arrive, with
no timers. Android `TerminalSuspend` and iOS `TerminalSuspend` mirror the same
three constants.

The two inputs come from `realtime/tabVisibility.ts` and
`terminal/watchedTerminal.ts` (the mounted-terminal pointer). A deliberate
suspend lands in the connection store as `stopped` like any other, so the model
publishes `channelStopped` only when it is *not* suspended and the overlay says
**Paused** rather than offering a Reconnect button. Replay spans are never
charged and never interrupted, so a reconnect that lands while still unwatched
cannot re-suspend itself into a loop.

The auto-scan socket is paused on tab hide too, with no budget: its subscribe
frame is rebuilt from the live wire cursor on every open, so a resume picks up
at the high-water mark and costs one handshake rather than a replay.

**Tab lifecycle: the user-events socket stays open when hidden.** Presence
gating for APNs is per device rather than per account
(`relay/agent_ws.py::_handle_user_event` excluding
`routing.get_user_device_ids()`), so a browser tab no longer mutes a phone. Web
deliberately sends no `device_id` hint on `ws/user-events`, exactly as Android
does not: it registers no push token, so it can never be a `list_push_targets`
row. A tab therefore never appears in the exclusion set — an iPhone receives its
attention push whether this tab is open, hidden or closed.

A browser exposes no `ping()`, so a half-open socket (NAT rebind, Wi-Fi to
cellular, a laptop waking) sits at `readyState === OPEN` with no data and no
close event. `resumeOnFocus.ts` covers the two observable causes — `online`,
and returning to a tab that was hidden longer than a minute — by
force-reconnecting the events channels the registry owns, plus anything
registered through its `onResume` seam. Not every `visibilitychange`:
alt-tabbing is constant.

The auto-scan monitor's socket uses that seam while it is open: at the default
300 s scan interval it is silent for most of every cycle, so a black-holed
connection is indistinguishable from a healthy idle scan. A hidden tab is the one
case where the seam does not apply — the socket is closed after a minute, which
leaves `shouldReconnect` false, so `onResume`'s `forceReconnect` no-ops until
the visibility handler's own `connect()` re-arms it. The terminal is
deliberately left out: it is user-driven. The residual case (a path
black-holing while the tab stays visible) needs a heartbeat the browser can
observe, which is a protocol change across all three clients and is not
attempted here.

### Settings hub

**Settings** is a hub plus the preferences that had nowhere else to live: the
Appearance picker (the same `ThemePicker` the header carries, plus the OS
appearance as body text, because the header's `title` is unreachable on a
touchscreen), notification permission and the in-app opt-out, the Ctrl+V
terminal preference, support and privacy links (the same constants iOS and
Android carry), and sign out. It links onward to agents, projects, templates,
devices and the account danger zone; `Devices` left the top nav in exchange.
There is **no** terminal display-mode picker — web ships Full display only, the
WEB-05 divergence above.

## Sign in with Apple (WEB-11)

Accounts created with Sign in with Apple on iOS usually carry a private-relay
address and **no password**, so they can reach the web client only through
Apple sign-in. `POST /api/auth/apple`, the endpoint iOS uses, verified the
identity token against a single audience, the iOS bundle id, while browser
tokens carry an Apple **Services ID**. The server now checks a *list*, and the
two identifiers resolve to the same account because the Services ID is grouped
under the bundle id as its primary App ID (the Apple-side setup, including
domain verification, is in [deployment.md](deployment.md)).

Three findings out of the shipped `appleid.auth.js` bundle shaped the client,
none of them stated in Apple's docs:

- **The SDK does not hash the nonce.** It URL-encodes the value onto the
  authorize URL and Apple echoes the claim back verbatim. So the browser reuses
  `generateNonce()` and the server's byte-for-byte `compare_digest` unchanged,
  and the no-double-hash convention spans all three clients.
- **`usePopup: true` opens a real window, not an iframe**: the SDK appends
  `response_mode=web_message`, calls `window.open`, and resolves from a
  `postMessage` listener. That is why the SPA's CSP grows exactly one
  `script-src` entry and no `frame-src`/`connect-src`, and why COOP
  `same-origin-allow-popups` protects two flows. Without the flag the SDK calls
  `window.location.assign`, which would destroy SPA state and return as a
  cross-site POST.
- **`redirectURI` is mandatory even in popup mode**, where it is never navigated
  to; Apple only matches it against the registered Return URLs. It is derived
  from `window.location.origin`, so one Services ID covers every host the SPA is
  deployed to.

The button is hand-drawn rather than `AppleID.auth.renderButton`: Apple's
rendered button leaves nowhere to bind a per-attempt nonce and cannot be
disabled mid-request. Only the flow comes from the SDK. In `AppleSignInButton`
the SDK load, the `init` call and the nonce all happen **at mount**, because
anything awaited inside the click spends the user activation and the popup gets
blocked; the button ships disabled until the SDK answers. Dismissing the popup
resolves as `AppleSignInCancelledError` and is swallowed — only a
browser-blocked popup produces a message.

Drawing it by hand also lets the two providers match. GIS renders its button
inside a cross-origin frame with no light-on-dark fill, so the pair is chosen by
resolved theme: `filled_black` beside Apple's black button on the light surface,
`outline` beside Apple's white variant on the dark one. `GoogleSignInButton`
reads `useResolvedTheme()` and redraws when it flips, clearing the container
first because GIS appends rather than replaces. Both use the same 320×40 box. GIS bleeds its opaque white frame past its layout
box, which on the dark card framed the button in a white halo, so
`.google-signin-slot` clips to its own 320×40 box and takes over the focus ring
GIS drew in the margin. No `theme` option avoids this.

The delete-account panel has a third re-auth branch, because an Apple-only
account has no password and no Google identity. Unlike Google it needs no
`disableAutoSelect` equivalent — Apple's popup always runs a fresh
authorization, so the token is inside the server's five-minute freshness window.

### Ad attribution at sign-up

`web/src/auth/adAttribution.ts` reads the `mobaide_ads` cookie the central
server issues on `.mobaide.com` after the marketing site's Google Ads consent
banner is accepted (docs/website.md, *The consent-gated Google Ads tag*), and
`authRepository.ts` spreads it into the register / Google / Apple bodies as the
optional `ad_attribution` field — only those three, because only they can create
an account, and only when the cookie exists.

The value is `<base64url(payload)>.<signature>`. The parser decodes it, mirrors
the server's Pydantic bounds, forwards the fields, the network fingerprint and
the signature verbatim, and returns `null` for anything off instead of throwing:
any same-site host can plant a cookie, the server alone decides (by verifying
the signature) whether it counts as consent, and forwarding garbage would turn a
marketing cookie into a 422 on sign-up. Adding the field touched the committed
OpenAPI snapshot and generated types (`npm run schema:dump && npm run
schema:generate`).

**Parity mirror for the in-app conversion.** The mobile apps log a Firebase
`agent_paired` conversion once per account (docs/app-conversion-measurement.md)
off two server signals that web mirrors without acting on:

- The `account.first_agent_paired` user event (`firstAgentPairedSchema` in
  `protocol/dto/userEvents.ts`, a pointer rule in `queryBridge.ts` that
  invalidates the agent list — the first agent just appeared — pinned by
  `tests/protocol_fixtures/dto/central/FirstAgentPairedPayload.json`).
- `GET /api/auth/me` (`authRepository.getMe()`, typed from the generated
  schema; `MeResponse.json` is deferred to `OWNER.generated`).

Web carries no analytics SDK, so nothing here logs anything.

## CI — `.github/workflows/web-ci.yml`

Runs on every PR and push to main (no paths filter — schema drift is caused
by `central_server/`/`mobaide_agent/` edits, not just `web/` ones). Single
job, in this order:

1. Python 3.12 + editable installs.
2. **Schema drift gate** (regenerate JSON, fail on `git status --porcelain`
   diff).
3. Node 22 + `npm ci`.
4. **Type drift gate** (regenerate TS, fail on diff).
5. Typecheck.
6. Lint.
7. Vitest.
8. Build.
9. Playwright (`playwright install --with-deps chromium` + `npm run
   test:e2e`).

The Playwright specs stub `/api/**` with `page.route`, so CI needs no relay.

A second workflow also gates `web/`: `protocol-integrity.yml` carries a
`web-protocol-fixtures` job (Node 22, `npm ci`, `npm run test:protocol`) so
a change to the shared fixtures fails on the PR that makes it, and its
constants step compares `APP_PROTOCOL_VERSION` three ways — Android gradle,
iOS xcconfig, and `web/src/api/bootstrap.ts`.

### Release and SPA deploy

Release integration: `web` is a release-please package (`release-type:
node`, component `web`) and a release-bearing PR-title scope
(`feat(web): …`); `web-v*` tags trigger no workflow yet. The SPA ships
manually via `deploy/prod/deploy_web.py` (WEB-02): `npm run build`, tar +
scp to `/opt/mobaide/www/releases/<tag>/`, then an atomic `current`
symlink swap — Caddy serves the new release immediately, no container
restart, `--rollback` repoints at the previously-live release. Runbook: the
"Deploying the web SPA" section of docs/deployment.md.

## App shell

### Theming

**Theming** — `themeStore` persists `light | dark | system`
(`localStorage` key `mobaide-theme`); `ThemeProvider` resolves it to
`document.documentElement.dataset.theme` and tracks
`prefers-color-scheme` while in `system`. An inline pre-paint script in
`index.html` stamps `data-theme` before first paint (no theme flash).

The control is `ThemePicker`: a three-segment `radiogroup` of native radios
(Auto | Light | Dark), rendered by `RootLayout` and `AuthLayout` and again in
the Settings Appearance card, with the group `name` from `useId()` so two
mounted instances stay separate groups. It is a direct selector rather than a
cycling button because `system` paints identically to whichever of light/dark
the OS is, so a cycle step from `system` looked dead.

Auto's `title` and `aria-label` name the OS value, from `useSystemTheme()`.

#### Design tokens and fonts

All colors are CSS custom properties under `:root[data-theme=…]` in
`src/styles/theme.css`. The SPA ships the marketing "modern pixel" design system
(docs/website.md): the dark block mirrors `site.css`'s tokens **verbatim**
(`site.css` stays the source of truth — a token change there must be mirrored
here), the light block is a derived variant that exists only in `theme.css`, and
token names follow `site.css`. The iOS app mirrors **both** blocks in
`ios/Mobaide/UI/Theme/Theme.swift` (docs/ios-client.md), so a change to either
must be carried there as well.

Purple + grey stay the identity in both themes: `--brand`/`--brand-2` are
theme-invariant fills, and the accent is `#d0bcff` on dark / `#6008ac` on light.
Departure Mono is self-hosted at `web/public/fonts/` with its OFL license file,
preloaded from `index.html` with a URL that must stay byte-identical to the
`@font-face` `url()`. The terminal grid and the autoscan log pane stay
always-dark regardless of theme.

> [!WARNING]
> **CSP coupling:** the production Caddy site block pins that inline script by
> `sha256` hash in `script-src` — in BOTH `deploy/prod/Caddyfile.*` files.
> Editing the script means updating both hashes; `deploy_web.py` hard-fails on
> drift (the escape hatch is `--force`, at the cost of a theme flash).

The font needs no Caddyfile change: the SPA CSP has no `font-src`, so
same-origin fonts ride `default-src 'self'`.

### Brand mark and icons

**Brand mark and icons** — the mirror extends past tokens to the assets.
`web/public/wordmark.png`, `favicon.ico`, `apple-touch-icon.png` and both
`fonts/DepartureMono-*` files are **byte-for-byte copies** of
`central_server/static/site/`, and `brandAssets.test.ts` fails if any of the
five diverges — the two origins deploy separately with no shared asset
pipeline. The font is the one most likely to be regenerated alone, under an
unchanged filename.

Both shells carry the mark and link it: `RootLayout` to `/`, `AuthLayout`
cross-origin to `https://mobaide.com/`. The auth shell also has a footer with
the site's own three links (Home, Privacy policy, Account deletion); the
signed-in `RootLayout` has none, because Settings already carries those links.

`index.html` declares the icons exactly as `marketing_landing.html` does; the
manifest carries the 180px PNG, which avoids the known Chromium failure on an
SVG-only `sizes: "any"` set.

> [!NOTE]
> Whether it clears Chrome's install bar is unverified — the published criteria
> ask for 192 + 512, and closing that properly is CLA-252, so do not treat the
> current icon set as evidence that `display-mode: standalone` resolves true on
> Android.

### Errors and toasts

**Errors, two tiers** — the router's `errorElement` (`RouteError`)
renders loader/render errors and doubles as the 404 page;
`AppErrorBoundary` (class component) wraps the providers as a last
resort.

**Toasts** — Zustand store + two presentational components, no
dependency; `ToastViewport` mounts as a sibling of `RouterProvider` so
toasts survive route errors.

### Auth and session (WEB-03)

**Auth & session (WEB-03)** — `src/auth/authState.ts` mirrors Android's
`SessionManager.AuthState` state-for-state (`unauthenticated`,
`userOnly`, `fullyConnected`, `incompatibleServer`).

#### Tokens and storage

- The user JWT lives in `sessionStorage` — per-tab, never on disk, because the
  relay keeps no JWT denylist — and a booting tab asks open peers to hand the
  session over via `BroadcastChannel`.
- The always-re-mintable per-agent `mobile_access` tokens live in
  `localStorage`.
- Sessions are minted with the server's opt-in `refreshable: true` flag, so the
  access token is short-lived (`USER_SESSION_ACCESS_EXPIRE_MINUTES`, default 15)
  and rotates against `POST /api/auth/refresh`.
- Refreshes are single-flighted across tabs with `navigator.locks`, and a 401 is
  terminal (sign out) while anything else is transient (keep the tokens).

#### Refresh cookie and boot legs

The **refresh token is not in the page at all**: clients send
`session_transport: "cookie"` alongside `refreshable`, and the server returns it
as a `__Host-mobaide_refresh` HttpOnly cookie (see docs/deployment.md). That
lets a session survive closing the last tab and caps an XSS at one tab's
15-minute token instead of a 30-day renewable session.

Boot has three legs, in order: this tab's `sessionStorage`, a 300 ms peer
handover, then `restoreSessionFromCookie()`. A readable companion cookie
(`__Host-mobaide_signed_in`) gates only that last leg, so a visitor who never
signed in reaches the login form without a wasted request. It gates the network
leg rather than the handover on purpose: with cookies blocked, a peer can hold a
real session while no marker exists.

The marker is read under **both** names, because which one the server emits
depends on its runtime `USER_SESSION_COOKIE_SECURE`; but only the `__Host-` name
is *trusted*, since a sibling host can plant the bare one. Presence licenses a
restore attempt; only a trusted marker licenses the `terminal` purge, so a
planted cookie costs one 401 rather than a user's recents. The network leg lives
in `src/auth/userSessionApi.ts` because `sessionStore` must be able to call it
and `userTokens` imports the store.

#### Sign-out, guards and bootstrap

Sign-out revokes server-side via `POST /api/auth/logout`, and
`POST /api/auth/logout/all` backs the Account screen's "Sign out of all
browsers" — browser-scoped by construction, since mobile never opts into
`refreshable`, and bounded by the access-token TTL rather than instant, since
there is still no JWT denylist. Both cookie-reading routes require an
`X-Mobaide-Session` header: `SameSite=Strict` alone is not enough when a sibling
host on the same registrable domain is third-party-hosted.

Route access is enforced by React Router **middleware**, not loaders — loaders
run in parallel, a parent middleware short-circuits the whole match — and the
wanted path survives the bounce as `?next=`.

`GET /api/version` is the single anonymous bootstrap call: protocol gate *and*
the `google_client_id` and `apple_web_client_id` the two sign-in buttons
initialise with, so the SPA needs no build-time env var. Each button mounts only
when its id is present, so a deployment configures either provider, both, or
neither, entirely server-side.

## Parity policy

The web client participates in the three-way Cross-Platform Parity rule in
the root `CLAUDE.md`: client-facing changes must be mirrored across
Android, iOS, and web (or a Linear issue filed) — with the transition rule
that feature areas not yet built on web are covered by their planned WEB-*
issue rather than per-change issues.

### Exempt divergences by milestone

Web-only behaviour that needs **no** mirror issue is the platform-convention
exemption the rule already carries. A browser has a physical keyboard and a wide
viewport; a phone has neither:

- The desktop keyboard surface (Ctrl+C / Ctrl+V / Ctrl+Shift+C/V, the "pass
  Ctrl+V" toggle) and Full-only display mode (WEB-05 section above).
- WEB-06: the reserved-key table, the reload and unload guards and immersive
  mode. The shared encoder fixture is the opposite of a divergence — it pulls
  all three clients onto one contract.
- **The control-key bar, saved shortcuts, command palette and composer
  removal** is a deliberate divergence, not a gap to mirror. Android and iOS keep
  them, and the central server keeps `/api/shortcuts` and `shortcut.*` serving
  them. No Linear issue is needed in either direction.
- The clipboard copy on a template pick: a template on mobile lands in the app's
  own composer and is submitted immediately, so no collapsed placeholder is ever
  left for the user to expand.

### Tracked divergences

WEB-07's two divergences are **not** exempt and are tracked accordingly:
recording the last-used session on open as well as on create is a behaviour
change mobile has to follow (CLA-229 / CLA-230), and the session-card
attention chip is iOS parity that Android does not yet render.

WEB-10's divergences are platform-convention exempt too: no reconcile and no
seen-store (a browser has no background delivery to reconcile against); alerting
from the account-wide user-events channel rather than the per-agent bus;
requesting notification permission from a settings button; and no display-mode
picker. The tab-lifecycle contract — the socket stays open when hidden, web
sends no `device_id`, and a tab therefore never suppresses a push — is a
cross-client contract rather than a divergence. Two gaps found in other
components are filed rather than fixed: the agent's relay events-snapshot
handler omits `autoscan.list.changed` (CLA-249), and Android's ANSI parser is
still a 16-colour subset with no fixture consumer (CLA-250).

WEB-08's divergences are all platform-convention exempt: the paste box and the
`/pair` deep link (a desktop browser has no camera, as does the setup guide's
step 2), the agent id and version on the card (a browser is the first client
that can hold more than one agent), and the trust-mismatch copy's final sentence
(a browser has no "clear app data"). The `parse_qr_payload` tightening moved the
agent's Python parser onto the mobile clients' stricter contract; the shared
golden now pins all four implementations, with the Android arm tracked in
CLA-235 and the duplicated `central_server` encoder in CLA-236.

One item from the setup guide *is* mirrorable rather than exempt: the
"expired? run `mobaide-agent show-qr` on the host" hint. A stale QR is a real
failure mode on a phone too, and neither mobile empty state says how to print
a fresh one. It is called out here rather than filed because Linear issue
creation has been rejecting new issues since 2026-08-12 (free-plan cap).
