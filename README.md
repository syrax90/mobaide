# Mobaide

Mobile (Android/iOS) and web clients for remotely controlling [Claude Code](https://claude.ai/code) sessions running on an agent, with full terminal I/O streaming and multi-session support.

## The Problem

Running Claude Code CLI sessions requires a desktop terminal. There's no way to start, monitor, or interact with sessions from a mobile device — especially when you're away from your workstation. Additionally, the existing `claude_auto_executor` script (which automates Claude sessions with Linear integration, git worktree management, and Auto Scan mode) has no remote access capability.

## The Idea

Mobaide bridges this gap with a client-agent architecture:

- **Agent (Python / FastAPI)** — Runs on your workstation (Windows/Linux). Reimplements the `claude_auto_executor.ps1` logic natively in Python: manages cross-platform PTY terminal sessions (pywinpty on Windows, stdlib pty on Linux), streams I/O over WebSocket, handles Linear integration, git worktrees, and Auto Scan mode. Supports multiple concurrent Claude sessions.
- **Client (Android / Kotlin)** — Connects to the central server via WSS (OkHttp), which relays traffic to the paired agent. Provides a multi-session terminal UI to start, monitor, and interact with Claude Code sessions in real time. Built with Jetpack Compose and Material 3.
- **Web Client (React / TypeScript)** — Browser SPA (in progress under `web/`) served same-origin with the central server; talks to the same relay APIs and WebSockets as the mobile apps, no CORS involved.
- **Central Server (Python / FastAPI)** — Public relay hub (e.g. `mobaide.com`). The Android app talks to it over WSS/JSON, and each agent dials OUT to it over an authenticated WebSocket. No inbound ports, public IP, or reverse tunnel are needed on the workstation running the agent.

## Key Features

- **Remote Terminal** — Full bidirectional terminal I/O with Claude Code sessions from your phone
- **Terminal Display Modes** — Settings toggle between `Full` (auto-fits viewport, recomputes columns on rotation) and `Static` (user-chosen column count 60–240, default 120; surface wider than viewport, pan horizontally; typing pans the view to keep the cursor visible)
- **Multi-Session** — Run and switch between up to 32 concurrent Claude sessions
- **Managed CLI agents** — The agent runs Claude Code by default and can also launch the [OpenAI Codex CLI](https://developers.openai.com/codex/cli) or the [Antigravity CLI](https://antigravity.google/docs/cli) (binary `agy`) as additional managed coding agents. The create-session wire contract (REST `POST /api/sessions` and the relay `create_session` control message) takes an optional `cli` discriminator (`"claude"` | `"codex"` | `"antigravity"` | `"custom_<id>"`) and a matching options block — `codex_options` (model, sandbox, approval, reasoning effort, full-auto, initial prompt) or `antigravity_options` (model, skip-permissions, sandbox, add-directories, worktree/branch — skip-permissions and sandbox are mutually exclusive, rejected server-side); a missing Codex or Antigravity binary returns 503 with a `codex_cli_missing` / `antigravity_cli_missing` marker. Users can also define up to 20 account-scoped custom agent types (display name + launch command) via the central server's `/api/custom-agent-types` REST resource, managed in-app from the create-session mode picker (one card per type plus an "Add custom agent…" card opening a bottom-sheet editor with inline cap/name-conflict errors; long-press a custom card for Edit/Delete — delete confirms the shortcut cascade), synced live across their devices via `custom_type.created/updated/deleted` push events on the user-events WebSocket; launching one sets `cli` to the type's `custom_<id>` kind and the relay resolves the stored command server-side, injecting relay-only `resolved_command` / `resolved_name` fields into the forwarded create (clients never send the command) — the agent spawns the command via the user's login shell as a plain terminal session (the create flow keeps the Terminal-style "Create worktree" toggle for git projects), session wire dicts carry a nullable `kind_label` name snapshot (session cards label custom sessions from it, falling back to the live registry name, then `Custom`), deleting a type soft-deletes its kind-scoped saved shortcuts in the same transaction, and pre-v7 agents are gated with a 409 `agent_update_required` on custom launches (proto-v7). The operator points the agent at each executable via `MOBAIDE_CODEX_BIN` / `MOBAIDE_AGY_BIN` (both mirror `MOBAIDE_CLAUDE_BIN`). When Codex is installed but not signed in, the agent detects this before spawning and returns a `codex_auth_required` marker (HTTP 401) instead of hanging on Codex's interactive login; the app then prompts for an OpenAI API key and posts it to `POST /api/codex/login` (which runs `codex login --with-api-key`, key fed on stdin and never persisted by the agent) before retrying the create. Antigravity has no auth probe: an unauthenticated `agy` shows its Google Sign-In prompt in the PTY, so the operator pre-authenticates `agy` on the host before launching a session — there is no API-key env var and no in-app Antigravity login route. The terminal's on-screen control-key bar is CLI-aware and sends raw PTY bytes: Claude, shell and auto-scan sessions show a 14-key bar (Mode `Shift+Tab` — cycles plan/accept-edits/normal, Rewind `Esc Esc`, Esc, Ctrl+C, Enter, Tab, Backspace `DEL 0x7f` — erases characters typed directly into a TUI, arrow keys, `Ctrl+O`, `Ctrl+R`, `Ctrl+L`); Codex sessions extend it to 16 keys by prepending the approval answers Approve `y` / Reject `n`; Antigravity sessions show a 15-key bar leading with `Ctrl+K` (fast-approve the pending action), `Ctrl+G` (tool-confirmation view), `Ctrl+D` (exit), then the same Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys, `Ctrl+O`, `Ctrl+R` tail, and `Ctrl+L`; custom-agent sessions show a kind-neutral 9-key bar (Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys — no Claude-specific `Ctrl+O`/`Ctrl+R`/`Ctrl+L`, whose bindings an arbitrary CLI may not share). Every kind's bar additionally ends with a trailing custom-shortcut trigger (⌘) that opens a chord picker — Ctrl/Alt/Shift (max two) + A–Z, 0–9, F1–F12, Tab, Enter, Space, or Backspace — encoded legacy-first with a CSI-u fallback for chords legacy bytes cannot express (those are marked as possibly unsupported per CLI), with recently used chords remembered per session kind on-device and offered in the picker for one-tap resend. Chords can also be saved as persistent named custom-shortcut buttons (optional name, per session kind) that appear after the ⌘ trigger at the end of the bar — tap sends the chord, long-press offers Rename/Remove — and the picker gains a "Saved" chips section (tap to send, ✕ to remove) plus a two-state Save/Saved toggle on the chord-preview row that pins the composed chord as a button in one tap and unpins it again — always unnamed at create time, with naming handled afterwards from the `Saved as "…"` hint row's Name/Rename action (the same Rename dialog the long-press menu opens); saved shortcuts are stored server-side per user and sync live across the user's devices via `shortcut.created/updated/deleted` push events on the user-events WebSocket, while recents remain device-local. This on-screen bar, chord picker and saved shortcuts are Android/iOS only: the web client removed them because a desktop has a physical keyboard, so every chord types directly into the terminal instead (see [docs/web-client.md](docs/web-client.md))
- **Explicit Keyboard & Direct TUI Input** — The soft keyboard is raised only by explicit user action — tapping the composer text field or the "Show keyboard" button — and is dismissed when you leave the session — on iOS, also by touching anywhere on the terminal surface while a keyboard is up, with that touch spent rather than also acting on the terminal (Android and web mirrors are tracked but not yet implemented); it never auto-raises from terminal output. Composer visibility is purely user-controlled: a "Hide command field" button collapses the "Type command…" composer behind a persistent labeled "Command field" chip docked at the always-visible control-key bar, and that collapse sticks across TUI episodes and reconnects until revealed; while hidden, the terminal is the input field: the "Show keyboard" icon button beside the "Command field" chip raises the IME on the terminal surface itself, so typed characters route directly to the remote PTY. The soft keyboard never resizes the terminal grid or the remote PTY: the terminal-and-input column pans up and the terminal counter-pans to follow the cursor, so the TUI's active input row stays visible above the IME whether it sits at the grid bottom (Claude Code, shells with history — full pan, as before) or near the top (a fresh shell prompt, Antigravity's banner + input box), and grid resizes happen only on rotation, composer show/hide, and display-mode changes
- **Terminal Sessions** — Third tile on the create-session screen opens a raw shell (`cmd.exe` on Windows, the user's login shell — resolved `$SHELL`, falling back to the account's password-database shell, then `/bin/sh` — on Linux/Unix) anchored to the selected project's cwd; session cards show the `Terminal` kind label. For stored git projects, tapping the tile first shows a "Terminal session" config page with a "Create worktree" toggle (and optional branch name) — mirroring the Claude Standard config page; non-git projects and custom paths skip the config page and start the shell immediately
- **Custom Path Sessions** — "Custom path…" entry in the project picker lets users launch one-off Claude or Terminal sessions in any arbitrary folder on the agent machine without adding it to `projects.json`; the last 5 per-agent paths surface as quick-pick chips below the path text field on Android and as a "Recent paths" section of monospaced rows — head-truncated on iOS, tail-truncated with the full path on hover on the web client — and any of them can be forgotten: a ✕ on the Android chip, swipe or long-press on the iOS row, a per-row "×" on the web; a path is recorded only after the session is actually created, and re-launching an existing one moves it to the front rather than duplicating it; the list is device-local and every agent's is cleared on sign-out and account deletion (but not on an agent switch), since the paths name the user's own workstation; the agent validates the path (absolute, exists, is-directory) and returns a clean 400 on bad input
- **External Sessions Notice** — A one-time, dismissible explainer sits above the sessions list on all three clients: "Sessions started outside Mobaide won't appear here" / "Mobaide can only open sessions its own agent started. To control a session from here, start it from Mobaide instead of launching it yourself on the agent machine." It names no specific CLI because the limit applies to every session kind — Claude, Codex, Antigravity, custom agent types, and plain terminal sessions alike — since the agent's `SessionManager` spawns every PTY itself and has no attach or `--resume` path. Dismissal is permanent and device-local (Android DataStore, iOS `UserDefaults`, web `localStorage`) and survives app restart and sign-out
- **Auto Scan Mode** — Polls Linear for matching issues using a per-project configurable filter (trigger labels, trigger states, active/done state, removal label), auto-creates git worktrees, and launches Claude sessions to work on them. Each project remembers the last-applied initial prompt (persisted as `auto_scan_prompt` in `projects.json` and surfaced read-only on `GET /api/projects`), so starting a new scan pre-seeds it in the create-session wizard instead of resetting to the engine-wide default; the prompt is preserve-last-applied only and is not writable via the project form. The wizard also remembers the last-used launch options per session kind on-device — Auto Scan keeps its own bucket (model, effort, focus view, fast mode, ultracode, debug logging, append-system-prompt, plan mode, no-memory, skip-permissions) so scan choices never pre-fill the Claude Code sheet and vice versa. Skip permissions is a live toggle, default ON for unattended scans; switched OFF, the scan pauses at Claude Code permission prompts and raises the normal attention notification so the user can open the session and approve — scan sessions install the same Claude Code attention hooks and emit turn-finished / ended notifications like any other session
- **Reconnection** — Per-session replay buffer ensures seamless reconnect after network drops. Two buffers, deliberately: the realtime WS path replays byte-exactly from a rolling ring capped at `RING_MAX_BYTES` (4 MiB, `core/session.py`), while a separate 10,000-entry deque of read *chunks* (`scrollback_size`) backs the REST `/scrollback` endpoint — so "10K lines" is not the reconnect guarantee
- **Secure Access** — Going through the relay (the default), every hop is authenticated over WSS: the Android app presents a JWT to the central server, and each agent presents its own opaque per-agent bearer token (verified by SHA-256 hash comparison, not as a JWT). Two caveats worth stating plainly. This is transport security per hop (TLS/WSS), **not** end-to-end encryption: the relay terminates TLS and forwards plaintext frames through its in-memory routing (it never persists them — see `central_server/legal/privacy.md` §3.4). And in the optional DIRECT/LAN mode the app talks straight to the agent, which serves plain HTTP by default — that connection is `ws://` unless you front the agent with HTTPS yourself (`WebSocketManager.kt` picks `wss` only when the agent URL starts with `https`)
- **Cross-Platform** — Agent runs on both Windows and Linux out of the box via PTY abstraction (pywinpty / stdlib pty). Native iOS client (SwiftUI, iOS 17+) in progress under `ios/` — the wire layer is ported and contract-locked to Android's by CI (see [docs/ios-client.md](docs/ios-client.md)).
- **Git Management** — In-app screen (reachable from Edit Project on git-backed projects, and from a session's ⋯ → Git → Branches…) renders local branches and worktrees as an alphabetized folder tree (a branch name split on `/`, folders first) with each tip commit's subject and date, the remote-tracking branches nested the same way under a folder per remote in a collapsed section, and a filter once the list grows that opens every matching folder; supports long-press multi-select and batch delete with undo Snackbar and force-delete confirmation — deleting the selected branches on their remote too, behind a confirmation, once the agent advertises `capabilities.git.delete_remote` — a Remove worktree row action that drops only the worktree and keeps the branch, a New branch sheet (name, start point, optional check out) and a confirmed Check out of a local or remote branch in the project folder — refused while a session or auto-scan works there, while a fetch/pull/push job owns the repository, mid-merge/rebase, or when local changes would be overwritten (`GET …/git/items?remote=true`, `POST /api/projects/{name}/git/branches|checkout`, advertised as `capabilities.git.{branches,delete_remote}`)
- **In-session Git Changes** — The agent answers `GET /api/sessions/{id}/git/status` and `.../git/diff` for the directory a session was spawned in (project root or linked worktree): branch, ahead/behind, merge/rebase state, Conflicts / Staged / Changes / Untracked entries and a pre-parsed per-file diff (hunks and lines), bounded by a hardened git runner (timeouts, byte caps, scrubbed environment, no repo-controlled diff drivers) and short-circuited by an `etag` for polling. Works on Windows, Linux and macOS; advertised as `capabilities.git.changes` on `GET /api/version`. The same prefix also stages, unstages and discards files (`POST …/git/stage|unstage|discard`) and commits the staged set (`POST …/git/commit` with a message on stdin, optional amend and `--no-verify`; `GET …/git/commit/context` reports the author identity and the previous commit), serialised per repository and echoed as `git.changed` — advertised as `capabilities.git.{stage,discard,commit}`. All three clients expose them from the Changes screen: per-row Stage / Unstage / Discard…, section-level Stage all / Unstage all, multi-select, Stage / Unstage from the Diff screen, and a Commit sheet (Summary + Description, author line, Amend with prefill, Skip hooks) reachable from the screen and from ⋯ → Git → Commit…. The Android and iOS clients' Changes viewers build on it — terminal `⋯` overflow menu → Git → Changes… (shown when the session has a cwd), a grouped Conflicts/Staged/Changes/Untracked list with status badges and pull-to-refresh, and a Diff screen with hunks, a line-number gutter and a Changes / Full file switch. The web client mirrors it as a `⋯` → Git → Changes… menu on the terminal header opening a non-modal slide-over beside the live terminal (file list and diff pane, stacked on narrow screens), polled every 10 s only while the tab is visible and focused. The same prefix also runs the network operations as agent-side **jobs** (`POST …/git/fetch|pull|push` answer a `GitJobResponse` snapshot at once and the work continues in the background; `GET …/git/jobs/{id}` and `POST …/git/jobs/{id}/cancel` follow it), streaming git's own `--progress` counters as throttled `git.job.progress` events, one `git.job.done`, then `git.changed` — advertised as `capabilities.git.{fetch,pull,push}`, never prompting for credentials on the host (a credential helper or `ssh-agent` there is the only source), and refusing a second job or a staging write on a busy repository with `job_running`. All three clients expose them as ⋯ → Git → Fetch / Pull / Push…, a Sync button and a tappable `↑n ↓m` header line on the Changes screen, a three-row sheet (Fetch always, Pull when behind, Push when ahead or to publish a branch) with a progress bar and Cancel, a job banner on the list, and confirmed follow-ups — Pull with rebase / merge after a non-fast-forward when the branch still has a live upstream, Force push (with lease) after a rejected or non-fast-forward push. The same prefix also runs an interactive **rebase** onto a picked ref (`POST …/git/rebase {onto}`, then `…/rebase/continue|skip|abort`, editors disabled so nothing ever waits on a person), reporting a rebase in progress as `repo.rebase` on `GET …/git/status` — advertised as `capabilities.git.rebase`. All three clients expose it as ⋯ → Git → Rebase… (a project-backed session picks the target from its local and remote-tracking branches, any other session types the ref) and a Changes-screen banner that carries Continue (disabled while a conflict remains) / Skip… / Abort… when `repo.rebase` is set — a `git am` session or an older agent instead gets a banner pointing to the terminal; a conflict row's confirmed *Mark resolved* action stages a fixed file — refused by the agent while it still holds `<<<<<<<`/`>>>>>>>` markers — advertised as `capabilities.git.resolve` alongside `stage`. The same session-scoped prefix also answers `GET …/git/conflict` and `POST …/git/conflict/resolve|take|restore` for one conflicted path at a time — read its markers and the two sides' labels, write a resolution or a draft, take a whole side, or rebuild the conflict — advertised as `capabilities.git.conflicts`, which also adds `resolved_paths` to `GET …/git/status`. With it, all three clients open a full-file resolver from a conflict row instead of the `HEAD` diff: the two sides named by label and role (never "ours"/"theirs"), a conflicts-left counter, Apply & mark resolved / Save draft, and whole-file / Start over… picks behind a menu; a file resolved this way lists under a Resolved section with a confirmed Restore conflict….
- **Prompt Templates** — Save frequently used inputs as named templates (with optional favorites). An in-terminal picker bottom-sheet (recents / favorites / all sections, with search) inserts a template into the input bar; a save-as-template dialog captures the current input. Templates are stored server-side per user and sync across sessions via `template.created/updated/deleted` push events on the user-events WebSocket
- **1-hour Prompt Cache** — Every spawned Claude session runs with `ENABLE_PROMPT_CACHING_1H=1` preset, so the 1-hour prompt-cache TTL ([claude-code ≥ v2.1.108](https://github.com/anthropics/claude-code/releases/tag/v2.1.108)) is always on — lower cost and lower latency on long sessions
- **Account Self-Deletion** — Users can delete their account from the app's Settings → Danger zone (re-auth required) or from the web client's Account screen at `app.mobaide.com`. A 14-day soft-delete grace window allows cancellation via the in-app banner, the web client's grace banner, or the one-click email link; after the window, the sweeper permanently purges data. The public URL `mobaide.com/portal/delete-account-info` is registered with Google Play Console Data Safety. See [docs/account-deletion-workflow.md](docs/account-deletion-workflow.md) for the full flow.
- **Device Management** — Android Settings → "Your devices" lists every paired installation (name, last-seen timestamp) and identifies the current device. Users can revoke any device with a confirmation dialog; revocation cuts the device off at once: its access tokens stop working, all its refresh tokens across agents are revoked, and its live connections are closed. The central server exposes `GET /api/devices` and `DELETE /api/devices/{device_id}` (user-scoped; cross-tenant ownership is enforced). Each install is identified by a client-generated UUID v4 persisted in a DataStore excluded from Android Auto Backup and device-transfer.
- **Session Notifications** — When the Android app is backgrounded, a foreground service holds the events socket and surfaces a system notification whenever a Claude session needs attention: permission prompt awaiting (`awaiting_permission`), OS file-access prompt blocking on the Mac — "Action needed on your Mac" (`awaiting_os_permission`), turn finished (`turn_finished`), or session ended (`ended`). Tapping the notification deep-links directly to the relevant session. Every reason except `awaiting_os_permission` is driven by Claude Code hooks (`Notification`, `Stop`, `UserPromptSubmit`, `SessionEnd`) posting to the agent's local `POST /internal/hook` endpoint; `awaiting_os_permission` is raised by the agent's own startup-stall watchdog. Because `Stop` fires once per model turn — including every re-invocation a completing background subagent causes — "turn finished" is not raised from it directly: an attention gate on the agent drops a `Stop` whose payload still reports in-flight background tasks or scheduled wakeups, dedupes repeat stops within a turn, and debounces the rest, so you get one alert when Claude actually stops rather than one per subagent. No FCM or external push service is required on Android; the iOS client (which cannot hold a socket while suspended) instead receives these attention alerts as APNs pushes sent by the central server to every registered device except those already holding a live events WebSocket of their own — gating is per device, not per account, so Android's always-on foreground-service socket no longer silences pushes to a signed-in iPhone (see [docs/ios-client.md](docs/ios-client.md)).
- **Help & Support** — In-app screen reachable from the Login screen and from Settings (available even when signed out) offering two channels: "Ask the community" opens the project's public GitHub Discussions in a Chrome Custom Tab, and "Email support" opens a prefilled `mailto:` (app version, device model, anonymous device id, and the signed-in email when available), falling back to copying the support address to the clipboard when no email app is installed. The two destinations are build-time `buildConfigField`s `SUPPORT_GITHUB_URL` and `SUPPORT_EMAIL`, overridable via environment variables or `android/local.properties` (same resolution order as `PRIVACY_POLICY_URL`).

## Architecture

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
      |  PTY
      v
[ Claude / Codex / Antigravity CLI ]   (bash / cmd sessions)
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
| `ios/` | Swift / SwiftUI | iOS app (in progress) — wire DTOs, REST clients, reconnecting WebSocket core, terminal framing |
| `web/` | React 19 / TypeScript / Vite | Web client (in progress) — SPA shell, typed OpenAPI clients for the relay and agent APIs, wire protocol core, reconnecting WebSocket manager, terminal screen (xterm.js 6), live sessions list and create-session wizard |
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
   - **Web application** — copy its client ID (format `NNN-xxxxx.apps.googleusercontent.com`). This is the ID the Android app asks Credential Manager for AND the ID the server verifies ID tokens against. The browser client uses it too — it fetches the id at runtime from `GET /api/version`, so it needs no build-time value, but every origin the SPA is served from (`https://app.mobaide.com`, plus `http://localhost:5173` for `npm run dev`) must be listed under this client's **Authorized JavaScript origins** or the web Sign in with Google button fails to render.
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

The iOS app's sign-in setup (a separate **iOS application** OAuth client ID via the gitignored `ios/Config/Local.xcconfig`, plus Sign in with Apple, which requires `APPLE_CLIENT_ID` in the server's `.env`) is covered in the "Sign-in configuration" section of [docs/ios-client.md](docs/ios-client.md).

## Configuring Sign in with Apple on the web

The web client offers Sign in with Apple so accounts created in the iOS app — which often have a private-relay address and no password — can sign in on a browser at all. It needs one extra server value, `APPLE_WEB_CLIENT_ID`, holding an Apple **Services ID**. The client fetches it at runtime from `GET /api/version`, so there is no build-time value, but the Services ID must be created with `com.mobaide.app` as its **primary App ID** — that grouping is what makes Apple issue the same user identifier on web as in the app, and getting it wrong creates duplicate accounts rather than an error. The full setup, including the domain verification each registered origin needs, is in ["Sign in with Apple on the web"](docs/deployment.md#deploying-the-web-spa-appmobaidecom).

## Releases, versioning, and auto-update

Each component (Android, `mobaide_agent`, `central_server`, `web`) ships on
its own semver cadence; a single integer `protocol_version` is the only
compatibility signal between them.

**To cut a release, the PR title is the trigger.** Use `feat(agent): …` /
`fix(agent): …` for `mobaide_agent`, `feat(server): …` / `fix(server): …`
for `central_server`, `feat(android): …` / `fix(android): …` for the
Android app, and `feat(web): …` / `fix(web): …` for the web client. The repo enforces **squash-only merging** (merge and rebase
merges are disabled) with the squash title = PR title and a blank squash
body, so each PR lands as exactly one commit on `main` carrying the PR
title — the single source release-please reads; branch commit messages are
discarded at merge time. Breaking changes use `!` in the title
(`feat(agent)!: …`). `pr-title-lint.yml` blocks merge if the title doesn't
match. Anything else (`chore:`, `docs:`, `refactor:`) merges normally and
produces no release.

**Every automatic bump is a patch**, for the Android app permanently
(`"versioning": "always-bump-patch"` on its release-please package — a
breaking `feat(android)!` is a patch too) and for `mobaide_agent`,
`central_server` and `web` only while they stay below `1.0.0`. An Android
minor or major is cut by hand: add a `Release-As: X.Y.0` footer to the squash
commit body at merge time. Details and the pre-1.0 caveat are in
[docs/versioning-and-updates.md](docs/versioning-and-updates.md#cutting-a-release).

After merge, release-please opens a release-PR per affected component;
merging that release-PR pushes the matching tag below and fires the
corresponding workflow:

| Tag prefix | Triggers workflow | What it produces |
|------------|--------------------|------------------|
| `android-vX.Y.Z` | `release-android.yml` | Signed `.aab` uploaded to Play Console internal track. `inAppUpdatePriority` derived from the `CHANGELOG.md` section generated for the tag (breaking → 5, bug fix → 3, else → 1). |
| `agent-vX.Y.Z` | `release-agent.yml` | `uv build` wheel + sdist, Ed25519-signed manifest, GitHub Release in the public sibling repo `syrax90/mobaide-agent-releases`, manifest POSTed to `central_server`. |
| `server-vX.Y.Z` | `release-server.yml` | Docker image to GHCR, manual-approval-gated deploy step (`environment: production`). |

`web-vX.Y.Z` tags version the web client but trigger no workflow yet — the
SPA ships with the central-server deploy (Caddy serving, tracked in WEB-02).

See [docs/versioning-and-updates.md](docs/versioning-and-updates.md) for the
staged-binary auto-updater, signed-manifest format, install one-liners,
Android update flows, and full operator runbook (including the CI secret
list and the `RELEASE_PLEASE_TOKEN` PAT requirement for tag-triggered
chaining). [PROTOCOL.md](PROTOCOL.md) tracks `protocol_version` history.

## Status

Early development. Agent core (PTY sessions, WebSocket streaming, JWT auth, projects-aware sessions, structured logging) and agent↔central-server relay are in place; Android client, web client (`web/`), and Linear Auto Scan are in progress. Tracked in Linear.
