# Web Client

Browser SPA for controlling Mobaide agents through the central relay. Lives
in `web/` at the repo root. This document covers the workspace layout, the
typed two-schema API pipeline, the app-shell architecture, testing, CI, and
how the web client fits the cross-platform parity policy. For day-to-day
commands see `web/README.md`.

## Status and roadmap

Bootstrapped by WEB-01 (CLA-188): production-grade shell with routing,
theming, error boundaries, toasts, state/data scaffolding, typed API
clients, tests, and CI — no feature screens yet. The series is tracked in
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
| Forms | react-hook-form + Zod — locked for the series, arrives with WEB-03 |

## Workspace layout

```
web/
├── scripts/dump_openapi.py   # schema dump (see below)
├── openapi/*.json            # committed OpenAPI schemas (generated)
├── redocly.yaml              # multi-schema map driving openapi-typescript
├── src/
│   ├── main.tsx              # StrictMode > AppErrorBoundary > QueryClientProvider > ThemeProvider > RouterProvider + ToastViewport
│   ├── app/                  # router, RootLayout (nav + theme toggle), RouteError, AppErrorBoundary, ThemeProvider
│   ├── routes/               # route components (Home placeholder)
│   ├── components/toast/     # zero-dependency toast primitives
│   ├── stores/               # Zustand stores (themeStore, toastStore)
│   ├── lib/queryClient.ts    # TanStack Query defaults
│   ├── api/
│   │   ├── generated/*.ts    # committed openapi-typescript output — never hand-edited
│   │   ├── auth.ts           # bearer-token provider seam (WEB-03 wires the real source)
│   │   └── clients.ts        # relayClient + createAgentClient(agentId), auth middleware
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
each `createAgentClient(agentId)`) — both wired by WEB-03.

Caveat: the agent schema types the agent's **entire** REST surface, but the
relay tunnel's agent-side dispatcher forwards only an allowlisted subset —
a call can type-check yet 404 through `/proxy` (e.g. `/api/health`,
`/api/auth/token`, `/internal/hook` are direct-only). Scoping the typed
surface to the tunnel is tracked in CLA-201.

WebSocket frame/event types are deliberately absent from both schemas; they
are hand-written and owned by WEB-04.

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

## CI — `.github/workflows/web-ci.yml`

Runs on every PR and push to main (no paths filter — schema drift is caused
by `central_server/`/`mobaide_agent/` edits, not just `web/` ones). Single
job: Python 3.12 + editable installs → **schema drift gate** (regenerate
JSON, fail on `git status --porcelain` diff) → Node 22 + `npm ci` → **type
drift gate** (regenerate TS, fail on diff) → typecheck → lint → Vitest →
build. Playwright is deliberately not in CI yet (placeholder page, heavy
browser download); it joins when real flows exist (WEB-03+).

Release integration: `web` is a release-please package (`release-type:
node`, component `web`) and a release-bearing PR-title scope
(`feat(web): …`); `web-v*` tags trigger no workflow yet — the SPA ships
with the central-server deploy (WEB-02).

## App shell

- **Theming** — `themeStore` persists `light | dark | system`
  (`localStorage` key `mobaide-theme`); `ThemeProvider` resolves it to
  `document.documentElement.dataset.theme` and tracks
  `prefers-color-scheme` while in `system`. An inline pre-paint script in
  `index.html` stamps `data-theme` before first paint (no theme flash). All
  colors are CSS custom properties under `:root[data-theme=…]` in
  `src/styles/theme.css`. The shell ships neutral tokens; adopting the
  marketing "modern pixel" design system (docs/website.md) is a decision
  for the feature milestones.
- **Errors, two tiers** — the router's `errorElement` (`RouteError`)
  renders loader/render errors and doubles as the 404 page;
  `AppErrorBoundary` (class component) wraps the providers as a last
  resort.
- **Toasts** — Zustand store + two presentational components, no
  dependency; `ToastViewport` mounts as a sibling of `RouterProvider` so
  toasts survive route errors.

## Parity policy

The web client participates in the three-way Cross-Platform Parity rule in
the root `CLAUDE.md`: client-facing changes must be mirrored across
Android, iOS, and web (or a Linear issue filed) — with the transition rule
that feature areas not yet built on web are covered by their planned WEB-*
issue rather than per-change issues. Terminal behavior parity references:
the client is the sole PTY size authority and must re-assert size on every
reconnect (see `docs/ios-client.md` and PROTOCOL.md).
