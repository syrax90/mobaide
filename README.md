# Claude Mobile

A mobile application (Android/iOS) for remotely controlling [Claude Code](https://claude.ai/code) sessions running on an agent, with full terminal I/O streaming and multi-session support.

## The Problem

Running Claude Code CLI sessions requires a desktop terminal. There's no way to start, monitor, or interact with sessions from a mobile device — especially when you're away from your workstation. Additionally, the existing `claude_auto_executor` script (which automates Claude sessions with Linear integration, git worktree management, and Auto Scan mode) has no remote access capability.

## The Idea

Claude Mobile bridges this gap with a client-agent architecture:

- **Agent (Python / FastAPI)** — Runs on your workstation (Windows/Linux). Reimplements the `claude_auto_executor.ps1` logic natively in Python: manages cross-platform PTY terminal sessions (pywinpty on Windows, stdlib pty on Linux), streams I/O over WebSocket, handles Linear integration, git worktrees, and Auto Scan mode. Supports multiple concurrent Claude sessions.
- **Client (Android / Kotlin)** — Connects to the central server via WSS (OkHttp), which relays traffic to the paired agent. Provides a multi-session terminal UI to start, monitor, and interact with Claude Code sessions in real time. Built with Jetpack Compose and Material 3.
- **Central Server (Python / FastAPI)** — Public relay hub (e.g. `mobaide.com`). The Android app talks to it over WSS/JSON, and each agent dials OUT to it over an authenticated WebSocket. No inbound ports, public IP, or reverse tunnel are needed on the workstation running the agent.

## Key Features

- **Remote Terminal** — Full bidirectional terminal I/O with Claude Code sessions from your phone
- **Multi-Session** — Run and switch between up to 16 concurrent Claude sessions
- **Auto Scan Mode** — Polls Linear for issues labeled "AutoPipeline", auto-creates git worktrees, and launches Claude sessions to work on them
- **Reconnection** — Scrollback buffer (10K lines) per session ensures seamless reconnect after network drops
- **Secure Access** — JWT authentication end-to-end between Android, central-server, and agent; agent↔central-server relay is secured with a per-agent bearer token over WSS
- **Cross-Platform** — Agent runs on both Windows and Linux out of the box via PTY abstraction (pywinpty / stdlib pty). iOS client planned via Kotlin Multiplatform shared layer.

## Architecture

```
[ Android App ]          (Kotlin / Compose, OkHttp)
      |
      |  WSS / JSON
      v
[ Central Server ]       (FastAPI, public — e.g. mobaide.com)
      |
      |  WSS relay  (agent dials out, authenticated with RELAY_AGENT_TOKEN)
      v
[ Python Agent ]         (FastAPI / uvicorn, on the user's workstation)
      |
      |  PTY
      v
[ Claude CLI ]           (bash / cmd sessions)
```

## Agent Stack

| Library | Purpose |
|---------|---------|
| FastAPI + uvicorn | HTTP + WebSocket agent |
| pywinpty / pty (stdlib) | Cross-platform terminal sessions |
| PyJWT | JWT authentication |
| gql + httpx | Linear GraphQL integration |
| asyncio | Concurrent session management |

## Project Structure

| Component | Tech | Description |
|-----------|------|-------------|
| `claude_mobile/core/` | Python 3.12+ | PTY backend abstraction, session manager, domain models |
| `claude_mobile/agent/` | FastAPI | WebSocket endpoints, JWT auth, REST API |
| `claude_mobile/linear/` | Python (gql) | Linear GraphQL client, Auto Scan engine, worktree manager |
| `android/` | Kotlin / Compose | Android app — OkHttp WebSocket client, multi-session UI, terminal view |
| `central_server/` | Python 3.12+ / FastAPI | Multi-tenant relay hub; auth, projects, and agent↔client WebSocket broker |

## Configuration Profiles

Environment-specific files (`claude_agent/.env`, `android/local.properties`, Android TLS trust config) are managed via named profiles under `config/profiles/`. One command swaps the whole bundle between, e.g., a local `start-all.ps1` stack and production (`mobaide.com`):

```powershell
./config/use-profile.ps1 -List           # see available profiles
./config/use-profile.ps1 local           # apply the 'local' profile
./config/use-profile.ps1 mobaide         # switch to production
```

See [`config/README.md`](config/README.md) for the full file mapping, merge semantics for `android/local.properties` (preserves `sdk.dir`), and how to create new profiles from the committed `_template`.

## Configuring Google Sign-In

The Android "Sign in with Google" button uses the Credential Manager API and requires an OAuth 2.0 **Web** client ID provisioned in Google Cloud Console:

1. In Google Cloud Console → **APIs & Services → Credentials**, create (or reuse) a project and add two OAuth 2.0 client IDs:
   - **Web application** — copy its client ID (format `NNN-xxxxx.apps.googleusercontent.com`). This is the ID the Android app asks Credential Manager for AND the ID the server verifies ID tokens against.
   - **Android application** — register package `com.claudemobile.app` and your debug + release signing-certificate SHA-1 fingerprints. Required by Google Play Services before any credential is returned even though the app only passes the Web client ID.
2. Put the Web client ID in your profile's `android.local.properties`:
   ```
   GOOGLE_WEB_CLIENT_ID=NNN-xxxxx.apps.googleusercontent.com
   ```
   The profile switcher merges this into `android/local.properties` (git-ignored) without touching `sdk.dir`. For CI builds, export `GOOGLE_WEB_CLIENT_ID` as an environment variable instead.
3. In the server's `.env`, set the SAME value:
   ```
   GOOGLE_CLIENT_ID=NNN-xxxxx.apps.googleusercontent.com
   ```
4. Rebuild the APK. If the client ID is missing or still the placeholder, the app surfaces a clear config error instead of silently pretending no Google account exists on the device.

## Status

Early development. Agent core (PTY sessions, WebSocket streaming, JWT auth, projects-aware sessions, structured logging) and agent↔central-server relay are in place; Android client and Linear Auto Scan are in progress. Tracked in Linear (Claude_mobile project).
