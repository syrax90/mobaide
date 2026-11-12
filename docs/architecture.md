# Mobaide architecture

How the parts of the repository fit together. For what the product does, see the
[README](../README.md) and the [feature reference](features.md).

## The problem

Running a coding-agent CLI such as Claude Code requires a desktop terminal. There
is no way to start, monitor or interact with those sessions from a mobile device,
especially when you are away from your workstation.

## The idea

Mobaide bridges this gap with a client-agent-relay architecture:

- **Agent (Python / FastAPI)** — Runs on your workstation (Windows, macOS or
  Linux). Manages cross-platform PTY terminal sessions (pywinpty on Windows, stdlib
  pty elsewhere), streams I/O over WebSocket, and handles Linear integration, git
  worktrees and Auto Scan mode. Supports multiple concurrent sessions.
- **Android client (Kotlin)** — Connects to the central server via WSS (OkHttp),
  which relays traffic to the paired agent. Provides a multi-session terminal UI to
  start, monitor and interact with sessions in real time. Built with Jetpack
  Compose and Material 3.
- **iOS client (Swift)** — The same product in SwiftUI, iOS 17+. See
  [ios-client.md](ios-client.md).
- **Web client (React / TypeScript)** — Browser SPA at `app.mobaide.com`, served
  same-origin with the central server. It talks to the same relay APIs and
  WebSockets as the mobile apps, with no CORS involved. See
  [web-client.md](web-client.md).
- **Central server (Python / FastAPI)** — Public relay hub (`mobaide.com`). The
  clients talk to it over WSS/JSON, and each agent dials OUT to it over an
  authenticated WebSocket. No port forwarding, public IP or reverse tunnel is needed
  on the workstation running the agent.

## Data flow

```
[ Android / iOS / Web Client ]   (Kotlin / Swift / React)
      |
      |  WSS / JSON
      v
[ Central Server ]       (FastAPI, public — e.g. mobaide.com)
      |
      |  WSS relay  (agent dials out, authenticated with RELAY_AGENT_TOKEN)
      v
[ Python Agent ]         (FastAPI / uvicorn, on the user's workstation)
      |
      |  PTY (terminal sessions)  ·  ACP over stdio (chat sessions)
      v
[ Claude / Codex / Antigravity / OpenCode CLI ]   (bash / cmd sessions, ACP adapters)
```

Chat sessions run the coding agent through an [Agent Client Protocol](https://agentclientprotocol.com) adapter (`mobaide_agent/mobaide/acp/`) and stream a structured timeline on the same per-session socket. See [features.md](features.md#chat-sessions).

## Agent stack

| Library | Purpose |
|---------|---------|
| FastAPI + uvicorn | HTTP + WebSocket agent |
| pywinpty / pty (stdlib) | Cross-platform terminal sessions |
| PyJWT | JWT authentication |
| gql + httpx | Linear GraphQL integration |
| asyncio | Concurrent session management |

## Project structure

| Component | Tech | Description |
|-----------|------|-------------|
| `mobaide_agent/mobaide/core/` | Python 3.12+ | PTY backend abstraction, session manager, domain models |
| `mobaide_agent/mobaide/acp/` | Python 3.12+ | Chat sessions: ACP client over stdio, agent launch specs, timeline, stored-session listing |
| `mobaide_agent/mobaide/agent/` | FastAPI | WebSocket endpoints, JWT auth, REST API |
| `mobaide_agent/mobaide/linear/` | Python (gql) | Linear GraphQL client, Auto Scan engine, worktree manager |
| `android/` | Kotlin / Compose | Android app — OkHttp WebSocket client, multi-session UI, terminal view |
| `ios/` | Swift / SwiftUI | iOS app — wire DTOs, REST clients, reconnecting WebSocket core, terminal framing, SwiftUI screens |
| `web/` | React 19 / TypeScript / Vite | Web client — SPA shell, typed OpenAPI clients for the relay and agent APIs, wire protocol core, reconnecting WebSocket manager, terminal screen (xterm.js 6) |
| `central_server/` | Python 3.12+ / FastAPI | Multi-tenant relay hub; auth, projects, agent↔client WebSocket broker, and the marketing site |
| `deploy/` | Shell / PowerShell / Python | Agent install scripts and production deploy tooling |
| `config/` | PowerShell | Environment profiles, see [configuration-profiles.md](configuration-profiles.md) |
| `tests/` | Python | Cross-component tests, the E2E harness and the protocol golden fixtures |
