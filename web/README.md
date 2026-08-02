# Mobaide Web Client

Browser SPA for controlling Mobaide agents through the central relay. React
19 + TypeScript (strict) + Vite, React Router (Data mode), Zustand, TanStack
Query, openapi-fetch. See `docs/web-client.md` at the repo root for the full
architecture write-up.

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
`CentralAuthInterceptor` vs `AuthInterceptor`): `src/api/auth.ts` exposes
`setUserTokenProvider()` (dashboard/user JWT, attached by `relayClient`) and
`setAgentTokenProvider()` (per-agent `mobile_access` JWT, attached by each
`createAgentClient(agentId)` for its own agent). Both are wired by the auth
milestone, WEB-03.

## Deliberately deferred

- react-hook-form + Zod: locked for the series, first consumer is the WEB-03
  auth forms.
- Playwright in CI: joins `web-ci.yml` once real user flows exist (WEB-03+).
- Prettier, TanStack Query devtools: adopt when the team wants them.

## WebSockets

WS frame/event types are hand-written and owned by the protocol milestone
(WEB-04) — they are absent from both OpenAPI schemas by design.
