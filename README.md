# Mobaide

A mobile application (Android/iOS) for remotely controlling [Claude Code](https://claude.ai/code) sessions running on an agent, with full terminal I/O streaming and multi-session support.

## The Problem

Running Claude Code CLI sessions requires a desktop terminal. There's no way to start, monitor, or interact with sessions from a mobile device — especially when you're away from your workstation. Additionally, the existing `claude_auto_executor` script (which automates Claude sessions with Linear integration, git worktree management, and Auto Scan mode) has no remote access capability.

## The Idea

Mobaide bridges this gap with a client-agent architecture:

- **Agent (Python / FastAPI)** — Runs on your workstation (Windows/Linux). Reimplements the `claude_auto_executor.ps1` logic natively in Python: manages cross-platform PTY terminal sessions (pywinpty on Windows, stdlib pty on Linux), streams I/O over WebSocket, handles Linear integration, git worktrees, and Auto Scan mode. Supports multiple concurrent Claude sessions.
- **Client (Android / Kotlin)** — Connects to the central server via WSS (OkHttp), which relays traffic to the paired agent. Provides a multi-session terminal UI to start, monitor, and interact with Claude Code sessions in real time. Built with Jetpack Compose and Material 3.
- **Central Server (Python / FastAPI)** — Public relay hub (e.g. `mobaide.com`). The Android app talks to it over WSS/JSON, and each agent dials OUT to it over an authenticated WebSocket. No inbound ports, public IP, or reverse tunnel are needed on the workstation running the agent.

## Key Features

- **Remote Terminal** — Full bidirectional terminal I/O with Claude Code sessions from your phone
- **Terminal Display Modes** — Settings toggle between `Full` (auto-fits viewport, recomputes columns on rotation) and `Static` (user-chosen column count 60–240, default 120; surface wider than viewport, pan horizontally)
- **Multi-Session** — Run and switch between up to 16 concurrent Claude sessions
- **Terminal Sessions** — Third tile on the create-session screen opens a raw shell (`cmd.exe` on Windows, `/bin/bash` on Linux/Unix) anchored to the selected project's cwd, with no Claude config step; session cards show the `Terminal` kind label
- **Custom Path Sessions** — "Custom path…" entry in the project picker lets users launch one-off Claude or Terminal sessions in any arbitrary folder on the agent machine without adding it to `projects.json`; the last 5 per-agent paths surface as quick-pick chips above the path text field; the agent validates the path (absolute, exists, is-directory) and returns a clean 400 on bad input
- **Auto Scan Mode** — Polls Linear for matching issues using a per-project configurable filter (trigger labels, trigger states, active/done state, removal label), auto-creates git worktrees, and launches Claude sessions to work on them
- **Reconnection** — Scrollback buffer (10K lines) per session ensures seamless reconnect after network drops
- **Secure Access** — JWT authentication end-to-end between Android, central-server, and agent; agent↔central-server relay is secured with a per-agent bearer token over WSS
- **Cross-Platform** — Agent runs on both Windows and Linux out of the box via PTY abstraction (pywinpty / stdlib pty). iOS client planned via Kotlin Multiplatform shared layer.
- **Git Management** — In-app screen (reachable from Edit Project on git-backed projects) lists local branches and worktrees; supports long-press multi-select and batch delete with undo Snackbar and force-delete confirmation
- **Prompt Templates** — Save frequently used inputs as named templates (with optional favorites). An in-terminal picker bottom-sheet (recents / favorites / all sections, with search) inserts a template into the input bar; a save-as-template dialog captures the current input. Templates are stored server-side per user and sync across sessions via `template.created/updated/deleted` push events on the user-events WebSocket
- **1-hour Prompt Cache** — Every spawned Claude session runs with `ENABLE_PROMPT_CACHING_1H=1` preset, so the 1-hour prompt-cache TTL ([claude-code ≥ v2.1.108](https://github.com/anthropics/claude-code/releases/tag/v2.1.108)) is always on — lower cost and lower latency on long sessions
- **Account Self-Deletion** — Users can delete their account from Android Settings → Danger zone (re-auth required) or via the public web portal at `mobaide.com/portal/`. A 14-day soft-delete grace window allows cancellation via in-app banner, portal banner, or one-click email link; after the window, the sweeper permanently purges data. The public URL `mobaide.com/portal/delete-account-info` is registered with Google Play Console Data Safety. See [docs/account-deletion-workflow.md](docs/account-deletion-workflow.md) for the full flow.
- **Device Management** — Android Settings → "Your devices" lists every paired installation (name, last-seen timestamp) and identifies the current device. Users can revoke any device with a confirmation dialog; revocation cascades to all refresh tokens for that device across agents. The central server exposes `GET /api/devices` and `DELETE /api/devices/{device_id}` (user-scoped; cross-tenant ownership is enforced). Each install is identified by a client-generated UUID v4 persisted in a DataStore excluded from Android Auto Backup and device-transfer.
- **Session Notifications** — When the Android app is backgrounded, a foreground service holds the events socket and surfaces a system notification whenever a Claude session needs attention: permission prompt awaiting (`awaiting_permission`), turn finished (`turn_finished`), or session ended (`ended`). Tapping the notification deep-links directly to the relevant session. Detection is driven by Claude Code hooks (`Notification`/`Stop`/`SessionEnd`) posting to the agent's local `POST /internal/hook` endpoint; no FCM or external push service is required.

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
| `mobaide/core/` | Python 3.12+ | PTY backend abstraction, session manager, domain models |
| `mobaide/agent/` | FastAPI | WebSocket endpoints, JWT auth, REST API |
| `mobaide/linear/` | Python (gql) | Linear GraphQL client, Auto Scan engine, worktree manager |
| `android/` | Kotlin / Compose | Android app — OkHttp WebSocket client, multi-session UI, terminal view |
| `central_server/` | Python 3.12+ / FastAPI | Multi-tenant relay hub; auth, projects, and agent↔client WebSocket broker |

## Configuration Profiles

Environment-specific files (`mobaide_agent/.env`, `android/local.properties`, Android TLS trust config) are managed via named profiles under `config/profiles/`. One command swaps the whole bundle between, e.g., a local `start-all.ps1` stack and production (`mobaide.com`):

```powershell
./config/use-profile.ps1 -List           # see available profiles
./config/use-profile.ps1 local           # apply the 'local' profile
./config/use-profile.ps1 mobaide         # switch to production
```

See [`docs/configuration-profiles.md`](docs/configuration-profiles.md) for the full file mapping, merge semantics for `android/local.properties` (preserves `sdk.dir`), and how to create new profiles from the committed `_template`.

## Configuring Google Sign-In

The Android "Sign in with Google" button uses the Credential Manager API and requires an OAuth 2.0 **Web** client ID provisioned in Google Cloud Console:

1. In Google Cloud Console → **APIs & Services → Credentials**, create (or reuse) a project and add two OAuth 2.0 client IDs:
   - **Web application** — copy its client ID (format `NNN-xxxxx.apps.googleusercontent.com`). This is the ID the Android app asks Credential Manager for AND the ID the server verifies ID tokens against.
   - **Android application** — register package `com.mobaide.app` and your debug + release signing-certificate SHA-1 fingerprints. Required by Google Play Services before any credential is returned even though the app only passes the Web client ID.
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

## Releases, versioning, and auto-update

Each component (Android, `mobaide_agent`, `central_server`) ships on its own
semver cadence; a single integer `protocol_version` is the only compatibility
signal between them.

**To cut a release, the PR title is the trigger.** Use `feat(agent): …` /
`fix(agent): …` for `mobaide_agent`, `feat(server): …` / `fix(server): …`
for `central_server`, and `feat(android): …` / `fix(android): …` for the
Android app. The repo enforces **squash-only merging** (merge and rebase
merges are disabled) with the squash title = PR title and a blank squash
body, so each PR lands as exactly one commit on `main` carrying the PR
title — the single source release-please reads; branch commit messages are
discarded at merge time. Breaking changes use `!` in the title
(`feat(agent)!: …`). `pr-title-lint.yml` blocks merge if the title doesn't
match. Anything else (`chore:`, `docs:`, `refactor:`) merges normally and
produces no release.

After merge, release-please opens a release-PR per affected component;
merging that release-PR pushes the matching tag below and fires the
corresponding workflow:

| Tag prefix | Triggers workflow | What it produces |
|------------|--------------------|------------------|
| `android-vX.Y.Z` | `release-android.yml` | Signed `.aab` uploaded to Play Console internal track. `inAppUpdatePriority` derived from the `CHANGELOG.md` section generated for the tag (breaking → 5, bug fix → 3, else → 1). |
| `agent-vX.Y.Z` | `release-agent.yml` | `uv build` wheel + sdist, Ed25519-signed manifest, GitHub Release in the public sibling repo `syrax90/mobaide-agent-releases`, manifest POSTed to `central_server`. |
| `server-vX.Y.Z` | `release-server.yml` | Docker image to GHCR, manual-approval-gated deploy step (`environment: production`). |

See [docs/versioning-and-updates.md](docs/versioning-and-updates.md) for the
staged-binary auto-updater, signed-manifest format, install one-liners,
Android update flows, and full operator runbook (including the CI secret
list and the `RELEASE_PLEASE_TOKEN` PAT requirement for tag-triggered
chaining). [PROTOCOL.md](PROTOCOL.md) tracks `protocol_version` history.

## Status

Early development. Agent core (PTY sessions, WebSocket streaming, JWT auth, projects-aware sessions, structured logging) and agent↔central-server relay are in place; Android client and Linear Auto Scan are in progress. Tracked in Linear.
