# Mobaide feature reference

The detailed behaviour of each feature: the limits, the per-platform differences, and the main settings.

For the short overview, see the [README](../README.md). The wire contract itself lives in [PROTOCOL.md](../PROTOCOL.md).

**On this page**

| Area | Features |
|---|---|
| Sessions | [Chat Sessions](#chat-sessions) · [Remote Terminal](#remote-terminal) · [Multi-Session](#multi-session) · [Managed CLI agents](#managed-cli-agents) · [Terminal Sessions](#terminal-sessions) · [Custom Path Sessions](#custom-path-sessions) · [Reorderable New-Session Lists](#reorderable-new-session-lists) · [External Sessions Notice](#external-sessions-notice) · [Auto Scan Mode](#auto-scan-mode) · [1-hour Prompt Cache](#1-hour-prompt-cache) |
| Terminal input and display | [Terminal Display Modes](#terminal-display-modes) · [Explicit Keyboard & Direct TUI Input](#explicit-keyboard--direct-tui-input) · [Prompt Templates](#prompt-templates) · [Reconnection](#reconnection) |
| Git | [Git Management](#git-management) · [In-session Git Changes](#in-session-git-changes) |
| Account and platform | [Secure Access](#secure-access) · [Cross-Platform](#cross-platform) · [Account Self-Deletion](#account-self-deletion) · [Device Management](#device-management) · [Session Notifications](#session-notifications) · [Help & Support](#help--support) |

## Chat Sessions

Claude Code and Codex sessions open in a **chat view** by default: messages, tool calls, diffs, plans, permission prompts and questions are native UI on Android, iOS and the web, instead of a terminal.

- **How it works.** The agent runs the coding agent over the [Agent Client Protocol](https://agentclientprotocol.com) (ACP) and streams a structured timeline. Claude Code uses the ACP adapter `@agentclientprotocol/claude-agent-acp`, which drives the PC's own `claude` binary. Codex uses `@agentclientprotocol/codex-acp`, which drives the PC's own `codex app-server` (`CODEX_PATH`). Login, settings and CLI version are the same as in a terminal session.
- **Agent-agnostic.** The chat UI renders only ACP concepts (messages, tool kinds, config options, slash commands). Another ACP agent needs only a launch spec in `mobaide_agent/mobaide/acp/agents.py`.
- **Requirements on the PC.** Node.js 22+, or an adapter command on PATH. Otherwise the wizard shows why and creates a terminal session. `MOBAIDE_ACP_CLAUDE_COMMAND` and `MOBAIDE_ACP_CODEX_COMMAND` in the agent's env file override the adapter commands.
- **Adapter install.** The agent installs each adapter once, at a pinned version, into `<state prefix>/acp-adapters/`. It uses `npm install --ignore-scripts --omit=optional --before=<release date>` and runs the adapter with `node`, not `npx`. The first chat shows "Setting up the ... chat on your PC". Uninstalling the agent removes `acp-adapters`.
- **Codex modes.** Read-only maps to the read-only mode. Auto (workspace-write) maps to Workspace access, and a person approves anything outside it. Full access maps to full access only with `full_auto`, or `danger-full-access` with approval `never`. The mode is required: if the agent cannot apply it, the session does not start.
- **Folder trust.** Before a chat session opens a folder Claude Code has not trusted, the chat asks **Trust this folder?**. Trust is recorded in `~/.claude.json`, like the CLI. For Codex, the chat reads and writes `[projects."<dir>"] trust_level` in `$CODEX_HOME/config.toml`, with the same lookup order as Codex, including linked worktrees. For the home folder, a folder above it, or a root, trust lasts for this session only. Parent folders count only up to the enclosing git repo root. Declining ends the start; Restart offers the choice again.

### What the chat view offers

| Area | Behaviour |
|---|---|
| Timeline | Markdown answers, collapsible thinking, one compact card per tool call (icon by kind, status, `+N −M` for edits; tap for the diff, output and input). |
| Approvals | Permission prompts and multiple-choice questions appear as a card above the composer, with the agent's own options. The first answer from any device wins. |
| Composer | Slash-command suggestions, image attachments (when the agent accepts images), voice input, prompt templates. Messages sent while the agent works are queued. You can remove one, or tap **Send now** to steer the agent with it. |
| Controls | Stop, and chips for the mode, model, effort and other agent options. Context usage and cost in the top bar. Plan progress above the composer. |
| Command output | Command output shows as a console block, with a live tail while the command runs. |
| Sync | Every open device shows the same state; a reconnect fetches only what it missed. |

The terminal's control-key bar and custom keyboard shortcut buttons are not part of the chat view: they send raw terminal bytes. Templates and slash commands work in the chat view. Stop and the mode chip replace Esc / Ctrl+C and Shift+Tab.

### Terminal UI and continuing sessions

- **Terminal UI.** The Claude Code and Codex pages of the new-session wizard has a **Terminal UI** toggle under Advanced, off by default and remembered. On, the session runs in the classic terminal (needed for TUI-only features such as Rewind). Focus view and Debug logging apply only to terminal sessions.
- **Continue a session.** The same pages offer **Continue a session**: a list of the Claude Code or Codex sessions stored on the PC, for this folder or all folders, including sessions started outside Mobaide (for example by running `claude` in a terminal). Picking one opens it in the chat view with its history.
- **Live Codex sessions.** When the Codex shared app-server runs (the daemon that `codex` in a terminal starts), chat sessions join it through `mobaide/acp/codex_bridge.py`. A session open in a terminal and on the phone stay in sync both ways. This works on macOS and Linux, not on Windows, and not for projects with a conda env. A message typed in another app shows as **Message sent from another app on your PC**, followed by its answer. The adapter does not forward the text of that message. Closing the chat cancels only its own turn.
- **Claude Code sessions.** There is no live attach. Close the session in the other terminal first: two writers on one session interleave.

The wire details are in [PROTOCOL.md](../PROTOCOL.md).

## Remote Terminal

Full bidirectional terminal I/O with Claude Code sessions from your phone.

## Terminal Display Modes

A Settings toggle chooses between two modes:

| Mode | Behaviour |
|---|---|
| `Full` | Auto-fits the viewport and recomputes columns on rotation. |
| `Static` | User-chosen column count, 60–240, default 120. The surface is wider than the viewport, so you pan horizontally. Typing pans the view to keep the cursor visible. |

## Multi-Session

Run and switch between up to 32 concurrent sessions on one agent. 32 is the default `max_sessions`, which is configurable.

## Managed CLI agents

The agent runs Claude Code by default. It can also launch the [OpenAI Codex CLI](https://developers.openai.com/codex/cli) or the [Antigravity CLI](https://antigravity.google/docs/cli) (binary `agy`) as additional managed coding agents.

### Agent kinds at a glance

| Kind | `cli` value | Options block | Executable setting | If the binary is missing |
|---|---|---|---|---|
| Claude Code | `"claude"` | | `MOBAIDE_CLAUDE_BIN` | |
| Codex CLI | `"codex"` | `codex_options` | `MOBAIDE_CODEX_BIN` | 503 with `codex_cli_missing` |
| Antigravity CLI | `"antigravity"` | `antigravity_options` | `MOBAIDE_AGY_BIN` | 503 with `antigravity_cli_missing` |
| Custom agent type | `"custom_<id>"` | | The stored launch command | |

`MOBAIDE_CODEX_BIN` and `MOBAIDE_AGY_BIN` both mirror `MOBAIDE_CLAUDE_BIN`. The operator uses them to point the agent at each executable.

### Create-session contract

The create-session wire contract is REST `POST /api/sessions` and the relay `create_session` control message. It takes an optional `cli` discriminator and a matching options block:

| Options block | Fields |
|---|---|
| `codex_options` | model, sandbox, approval, reasoning effort, full-auto, initial prompt |
| `antigravity_options` | model, skip-permissions, sandbox, add-directories, worktree/branch |

In `antigravity_options`, skip-permissions and sandbox are mutually exclusive, and the server rejects the combination.

### Custom agent types

Users can define up to 20 account-scoped custom agent types (display name + launch command) through the central server's `/api/custom-agent-types` REST resource. Types sync live across the user's devices.

- **Managing them.** The create-session mode picker has one card per type, plus an "Add custom agent…" card. Long-press a custom card for Edit/Delete.
- **Launching one.** The client sets `cli` to the type's `custom_<id>` kind and never sends the command. The relay resolves the stored command server-side, and the agent spawns it via the user's login shell as a plain terminal session. The create flow keeps the "Create worktree" toggle for git projects.
- **Deleting one.** This soft-deletes its kind-scoped saved shortcuts in the same transaction.
- **Older agents.** Pre-v7 agents are gated with a 409 `agent_update_required` on custom launches.

The wire details are in [PROTOCOL.md](../PROTOCOL.md).

### Codex and Antigravity sign-in

**Codex.** When Codex is installed but not signed in, the agent detects this before spawning. It returns a `codex_auth_required` marker (HTTP 401) instead of hanging on Codex's interactive login. The app then prompts for an OpenAI API key and posts it to `POST /api/codex/login`, before retrying the create. That route runs `codex login --with-api-key`, with the key fed on stdin and never persisted by the agent.

**Antigravity.** There is no auth probe. An unauthenticated `agy` shows its Google Sign-In prompt in the PTY, so the operator pre-authenticates `agy` on the host before launching a session. There is no API-key env var and no in-app Antigravity login route.

### On-screen control-key bar

The terminal's on-screen control-key bar is CLI-aware and sends raw PTY bytes. It is Android/iOS only, together with the chord picker and saved shortcuts. The web client removed them because a desktop has a physical keyboard, so every chord types directly into the terminal instead (see [docs/web-client.md](web-client.md)).

| Session kind | Keys | Layout |
|---|---|---|
| Claude, shell, auto-scan | 14 | Mode `Shift+Tab`, Rewind `Esc Esc`, Esc, Ctrl+C, Enter, Tab, Backspace `DEL 0x7f`, arrow keys, `Ctrl+O`, `Ctrl+R`, `Ctrl+L` |
| Codex | 16 | The same bar, with the approval answers Approve `y` / Reject `n` prepended |
| Antigravity | 15 | `Ctrl+K`, `Ctrl+G`, `Ctrl+D`, then Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys, `Ctrl+O`, `Ctrl+R`, and `Ctrl+L` |
| Custom agent | 9 | Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys |

Notes on individual keys:

- **Mode `Shift+Tab`** cycles plan/accept-edits/normal.
- **Backspace `DEL 0x7f`** erases characters typed directly into a TUI.
- **`Ctrl+K`** fast-approves the pending action in Antigravity. **`Ctrl+G`** opens the tool-confirmation view, and **`Ctrl+D`** exits.
- **Custom-agent sessions** have a kind-neutral bar with no Claude-specific `Ctrl+O`/`Ctrl+R`/`Ctrl+L`, whose bindings an arbitrary CLI may not share.

### Custom shortcuts

Every kind's bar ends with a custom-shortcut trigger (⌘) that opens a chord picker.

- **Chords.** A chord is Ctrl/Alt/Shift (max two) + A–Z, 0–9, F1–F12, Tab, Enter, Space, or Backspace. Chords that legacy bytes cannot express use a CSI-u fallback and are marked as possibly unsupported per CLI.
- **Recents.** Recently used chords are remembered per session kind, on the device.
- **Saved shortcuts.** A chord can be saved as a button after the ⌘ trigger, with an optional name, per session kind. Tap sends the chord, and long-press offers Rename/Remove. Saved shortcuts are stored server-side per user and sync live across the user's devices.

## Explicit Keyboard & Direct TUI Input

**When the keyboard appears.** The soft keyboard is raised only by explicit user action: tapping the composer text field or the "Show keyboard" button. It never auto-raises from terminal output.

**When it goes away.** It is dismissed when you leave the session. On iOS, touching anywhere on the terminal surface while a keyboard is up also dismisses it, with that touch spent rather than also acting on the terminal. Android and web mirrors are tracked but not yet implemented.

**Hiding the composer.** Composer visibility is purely user-controlled.

- A "Hide command field" button collapses the "Type command…" composer behind a persistent labeled "Command field" chip, docked at the always-visible control-key bar.
- That collapse sticks across TUI episodes and reconnects until revealed.
- While it is hidden, the terminal is the input field. The "Show keyboard" icon button beside the "Command field" chip raises the IME on the terminal surface itself, so typed characters route directly to the remote PTY.

**Terminal size.** The soft keyboard never resizes the terminal grid or the remote PTY.

- The terminal-and-input column pans up and the terminal counter-pans to follow the cursor, so the TUI's active input row stays visible above the IME.
- That holds whether the row sits at the grid bottom (Claude Code, shells with history — full pan, as before) or near the top (a fresh shell prompt, Antigravity's banner + input box).
- Grid resizes happen only on rotation, composer show/hide, and display-mode changes.

## Terminal Sessions

The third tile on the create-session screen opens a raw shell anchored to the selected project's cwd. Session cards show the `Terminal` kind label.

| Platform | Shell |
|---|---|
| Windows | `cmd.exe` |
| Linux/Unix | The user's login shell: resolved `$SHELL`, falling back to the account's password-database shell, then `/bin/sh` |

For stored git projects, tapping the tile first shows a "Terminal session" config page with a "Create worktree" toggle and an optional branch name, mirroring the Claude Standard config page. Non-git projects and custom paths skip the config page and start the shell immediately.

## Custom Path Sessions

A "Custom path…" entry in the project picker launches a one-off Claude or Terminal session in any folder on the agent machine, without adding it to `projects.json`. The agent validates the path (absolute, exists, is-directory) and returns a clean 400 on bad input.

The last 5 per-agent paths are offered again, and each can be forgotten. A path is recorded only after the session is actually created. The list is device-local, and it is cleared on sign-out and account deletion, since the paths name the user's own workstation.

## Reorderable New-Session Lists

In the new-session wizard, users can drag projects and agent modes into the order they want. For example, the most-used project or agent goes to the top.

| List | Where the order is saved | Scope |
|---|---|---|
| Projects | The agent's `projects.json`, through `PATCH /api/projects/order` | Per agent. It is the same order as on the Projects screen. |
| Agent modes (built-in and custom) | The central server, through `PUT /api/session-mode-order` | Per account. |

- **Sync.** Every client shows the saved order, also in later sessions. A change reaches the other open clients live: `projects.changed` for projects, `session_mode_order.updated` for modes.
- **Cost.** One request per drop, never per drag step. Saves of one list run one at a time, in drop order. A drop that changes nothing sends nothing.
- **Fixed rows.** "Custom path…", "Create new project…" and "Add custom agent…" stay at the bottom.
- **New items.** A custom agent created after the last reorder goes below the ordered ones.
- **Failure.** If a save fails, the list goes back to the saved order and a "Reorder failed — reverted." message shows.

| Platform | How to reorder |
|---|---|
| Android | Drag the handle left of a card. The list scrolls near its edges. TalkBack offers "Move up" / "Move down" actions on each card. |
| iOS | Tap **Reorder**, drag the ≡ handles, then tap **Done**. |
| Web | Drag the ⠿ handle with a mouse, finger or pen. Or focus the handle and press ArrowUp / ArrowDown. |

The wire details are in [PROTOCOL.md](../PROTOCOL.md).

## External Sessions Notice

A one-time, dismissible explainer sits above the sessions list on all three clients:

> **Sessions started outside Mobaide won't appear here**
>
> Mobaide lists only sessions its own agent started. To continue a Claude Code or Codex session from your PC, start a new session and choose Continue a session.

The list itself never shows outside sessions: the agent's `SessionManager` owns every session it lists. Sessions started elsewhere can be continued in the chat view through [Continue a session](#terminal-ui-and-continuing-sessions). Codex sessions can also join the live shared app-server; Claude Code has no live attach to a running TUI.

Dismissal is permanent and device-local (Android DataStore, iOS `UserDefaults`, web `localStorage`), and it survives app restart and sign-out.

## Auto Scan Mode

Auto Scan polls Linear for matching issues using a per-project configurable filter (trigger labels, trigger states, active/done state, removal label). It auto-creates git worktrees and launches Claude sessions to work on them.

- **Remembered choices.** Each project remembers the last-applied initial prompt (`auto_scan_prompt` in `projects.json`). The wizard also remembers the last-used launch options on-device, in a bucket separate from the Claude Code sheet.
- **Skip permissions.** This is a live toggle, default ON for unattended scans. Switched OFF, the scan pauses at Claude Code permission prompts and raises the normal attention notification, so the user can open the session and approve.

## Reconnection

A per-session replay buffer ensures seamless reconnect after network drops. There are two buffers, deliberately:

| Buffer | Size | Used by |
|---|---|---|
| Rolling ring, `RING_MAX_BYTES` (`core/session.py`) | 4 MiB | The realtime WS path, which replays byte-exactly |
| Deque of read *chunks*, `scrollback_size` | 10,000 entries | The REST `/scrollback` endpoint |

So "10K lines" is not the reconnect guarantee.

## Secure Access

Going through the relay, every hop is authenticated over WSS:

| Hop | Credential |
|---|---|
| Android app → central server | A JWT |
| Agent → central server | Its own opaque per-agent bearer token, verified by SHA-256 hash comparison, not as a JWT |

This is transport security per hop (TLS/WSS), **not** end-to-end encryption. The relay terminates TLS and forwards plaintext frames through its in-memory routing. It never persists them; see `central_server/legal/privacy.md` §3.4.

No client exposes a direct connection to the agent: the relay is the only path a client can take.

## Cross-Platform

| Part | Platforms | Notes |
|---|---|---|
| Agent | Windows, macOS, Linux | Runs via a PTY abstraction (pywinpty / stdlib pty) |
| Android app | Android | Native, Kotlin, Jetpack Compose |
| iOS app | iOS 17+, iPhone only | Native, SwiftUI. Its wire layer is contract-locked to Android's by CI, see [docs/ios-client.md](ios-client.md) |
| Web client | Browser, at `app.mobaide.com` | See [docs/web-client.md](web-client.md) |

## Git Management

An in-app screen, reachable from Edit Project on git-backed projects and from a session's ⋯ → Git → Branches…

- **Browse.** Local branches, worktrees and remote-tracking branches appear as an alphabetized folder tree, with each tip commit's subject and date.
- **Delete.** Multi-select and batch delete, with undo and force-delete confirmation. Deleting on the remote too needs a confirmation and `capabilities.git.delete_remote`.
- **Remove worktree.** This drops only the worktree and keeps the branch.
- **Create and check out.** A New branch sheet (name, start point, optional check out), and a confirmed Check out of a local or remote branch.

Check out is refused while a session or auto-scan works there, while a fetch/pull/push job owns the repository, mid-merge/rebase, or when local changes would be overwritten.

The API is `GET …/git/items?remote=true` and `POST /api/projects/{name}/git/branches|checkout`. See [PROTOCOL.md](../PROTOCOL.md).

## In-session Git Changes

All three clients open these from a session's ⋯ → Git menu. Everything lives under one session-scoped agent prefix and works on Windows, Linux and macOS. The routes, refusals and capability keys are in [PROTOCOL.md](../PROTOCOL.md).

| Group | What you can do | Capability keys |
|---|---|---|
| Status and diff | See branch, ahead/behind, Conflicts / Staged / Changes / Untracked lists, and per-file diffs | `capabilities.git.changes` |
| Stage and commit | Stage, unstage and discard per row, per section or by multi-select. Commit with Summary + Description, Amend and Skip hooks | `capabilities.git.{stage,discard,commit}` |
| Network jobs | Fetch, pull and push in the background, with a progress bar and Cancel | `capabilities.git.{fetch,pull,push}` |
| Rebase | Rebase onto a picked ref, then Continue / Skip… / Abort… from a banner | `capabilities.git.rebase`, `capabilities.git.resolve` |
| Conflict resolver | Resolve one conflicted file at a time, take a whole side, save a draft, or restore the conflict | `capabilities.git.conflicts` |

**Good to know.**

- The git runner is hardened: timeouts, byte caps, a scrubbed environment, and no repo-controlled diff drivers.
- The web client shows Changes in a slide-over beside the live terminal, and polls every 10 s only while the tab is visible and focused.
- Jobs never prompt for credentials on the host. A credential helper or `ssh-agent` there is the only source.
- A second job or a staging write on a busy repository is refused with `job_running`.
- After a rejected push or pull, clients offer confirmed follow-ups: Pull with rebase / merge, and Force push (with lease).
- *Mark resolved* is refused while a file still holds `<<<<<<<`/`>>>>>>>` markers.
- The resolver names the two sides by label and role, never "ours"/"theirs".

## Prompt Templates

Save frequently used inputs as named templates, with optional favorites.

- An in-terminal picker bottom-sheet (recents / favorites / all sections, with search) inserts a template into the input bar.
- A save-as-template dialog captures the current input.
- Templates are stored server-side per user and sync across sessions via `template.created/updated/deleted` push events on the user-events WebSocket.

## 1-hour Prompt Cache

Every spawned Claude session runs with `ENABLE_PROMPT_CACHING_1H=1` preset, so the 1-hour prompt-cache TTL ([claude-code ≥ v2.1.108](https://github.com/anthropics/claude-code/releases/tag/v2.1.108)) is always on. That means lower cost and lower latency on long sessions.

## Account Self-Deletion

Users can delete their account from the app's Settings → Danger zone (re-auth required), or from the web client's Account screen at `app.mobaide.com`.

- A 14-day soft-delete grace window allows cancellation via the in-app banner, the web client's grace banner, or the one-click email link.
- After the window, the sweeper permanently purges data.
- The public URL `mobaide.com/portal/delete-account-info` is registered with Google Play Console Data Safety.

See [docs/account-deletion-workflow.md](account-deletion-workflow.md) for the full flow.

## Device Management

Android Settings → "Your devices" lists every paired installation (name, last-seen timestamp) and identifies the current device.

Users can revoke any device with a confirmation dialog. Revocation cuts the device off at once: its access tokens stop working, all its refresh tokens across agents are revoked, and its live connections are closed.

The central server exposes `GET /api/devices` and `DELETE /api/devices/{device_id}`.

## Session Notifications

The apps raise a notification whenever a Claude session needs attention. Tapping it opens the relevant session.

| Reason | When |
|---|---|
| `awaiting_permission` | A permission prompt is waiting |
| `awaiting_os_permission` | An OS file-access prompt is blocking on the Mac: "Action needed on your Mac" |
| `turn_finished` | Claude stopped |
| `ended` | The session ended |

- **Source.** Claude Code hooks post to the agent's local `POST /internal/hook` endpoint. `awaiting_os_permission` comes from the agent's own startup-stall watchdog.
- **One alert per stop.** An attention gate on the agent holds back `Stop` while background tasks are still running, so you get one alert rather than one per subagent.
- **Android.** A foreground service holds the events socket. No FCM or external push service is required.
- **iOS.** The central server sends APNs pushes to every registered device that has no live events WebSocket of its own (see [docs/ios-client.md](ios-client.md)).

## Help & Support

An in-app screen, reachable from the Login screen and from Settings, even when signed out. It offers "Ask the community" (the project's public GitHub Discussions) and "Email support" (a prefilled `mailto:` with app version, device model, anonymous device id, and the signed-in email when available).

On Android, the destinations are the build-time values `SUPPORT_GITHUB_URL` and `SUPPORT_EMAIL`.
