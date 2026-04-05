# Claude Mobile

A mobile application (Android/iOS) for remotely controlling [Claude Code](https://claude.ai/code) sessions running on a server, with full terminal I/O streaming and multi-session support.

## The Problem

Running Claude Code CLI sessions requires a desktop terminal. There's no way to start, monitor, or interact with sessions from a mobile device — especially when you're away from your workstation. Additionally, the existing `claude_auto_executor` script (which automates Claude sessions with Linear integration, git worktree management, and Auto Scan mode) has no remote access capability.

## The Idea

Claude Mobile bridges this gap with a client-server architecture:

- **Server (Python / FastAPI)** — Runs on your workstation (Windows/Linux). Reimplements the `claude_auto_executor.ps1` logic natively in Python: manages cross-platform PTY terminal sessions (pywinpty on Windows, stdlib pty on Linux), streams I/O over WebSocket, handles Linear integration, git worktrees, and Auto Scan mode. Supports multiple concurrent Claude sessions.
- **Client (Android / Kotlin)** — Connects to the server via raw WebSocket (OkHttp). Provides a multi-session terminal UI to start, monitor, and interact with Claude Code sessions in real time. Built with Jetpack Compose and Material 3.
- **NAT Traversal** — Uses Cloudflare Tunnel to securely expose the server over the internet without a public IP or port forwarding. Deployed via Docker Compose (server + cloudflared).

## Key Features

- **Remote Terminal** — Full bidirectional terminal I/O with Claude Code sessions from your phone
- **Multi-Session** — Run and switch between up to 16 concurrent Claude sessions
- **Auto Scan Mode** — Polls Linear for issues labeled "AutoPipeline", auto-creates git worktrees, and launches Claude sessions to work on them
- **Reconnection** — Scrollback buffer (10K lines) per session ensures seamless reconnect after network drops
- **Secure Access** — JWT authentication + Cloudflare Access service tokens (defense in depth)
- **Cross-Platform** — Server runs on both Windows and Linux out of the box via PTY abstraction (pywinpty / stdlib pty). iOS client planned via Kotlin Multiplatform shared layer.

## Architecture

```
[ Android App ]  <--WebSocket/JSON-->  [ Python Server ]  <--PTY-->  [ Claude CLI ]
     (Kotlin/Compose)      |           (FastAPI/uvicorn)             (bash/cmd sessions)
                           |
                  [ Cloudflare Tunnel ]
                  (NAT traversal, HTTPS)
```

## Server Stack

| Library | Purpose |
|---------|---------|
| FastAPI + uvicorn | HTTP + WebSocket server |
| pywinpty / pty (stdlib) | Cross-platform terminal sessions |
| PyJWT | JWT authentication |
| gql + httpx | Linear GraphQL integration |
| asyncio | Concurrent session management |

## Project Structure (Planned)

| Component | Tech | Description |
|-----------|------|-------------|
| `claude_mobile/core/` | Python 3.12+ | PTY backend abstraction, session manager, domain models |
| `claude_mobile/server/` | FastAPI | WebSocket endpoints, JWT auth, REST API |
| `claude_mobile/linear/` | Python (gql) | Linear GraphQL client, Auto Scan engine, worktree manager |
| `android/` | Kotlin / Compose | Android app — OkHttp WebSocket client, multi-session UI, terminal view |
| `docker-compose.yml` | Docker | Server + Cloudflare Tunnel deployment |

## Status

Early development. Tracked in [Linear (Claude_mobile project)](https://linear.app/claude-mobile) — issues CLA-6 through CLA-10.
