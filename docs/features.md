# Mobaide feature reference

The detailed behaviour of each feature: the limits, the per-platform differences, and the routes, events and capability keys behind it.

For the short overview, see the [README](../README.md). The wire contract itself lives in [PROTOCOL.md](../PROTOCOL.md).

**On this page**

| Area | Features |
|---|---|
| Sessions | [Remote Terminal](#remote-terminal) · [Multi-Session](#multi-session) · [Managed CLI agents](#managed-cli-agents) · [Terminal Sessions](#terminal-sessions) · [Custom Path Sessions](#custom-path-sessions) · [External Sessions Notice](#external-sessions-notice) · [Auto Scan Mode](#auto-scan-mode) · [1-hour Prompt Cache](#1-hour-prompt-cache) |
| Terminal input and display | [Terminal Display Modes](#terminal-display-modes) · [Explicit Keyboard & Direct TUI Input](#explicit-keyboard--direct-tui-input) · [Prompt Templates](#prompt-templates) · [Reconnection](#reconnection) |
| Git | [Git Management](#git-management) · [In-session Git Changes](#in-session-git-changes) |
| Account and platform | [Secure Access](#secure-access) · [Cross-Platform](#cross-platform) · [Account Self-Deletion](#account-self-deletion) · [Device Management](#device-management) · [Session Notifications](#session-notifications) · [Help & Support](#help--support) |

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

Users can define up to 20 account-scoped custom agent types (display name + launch command) through the central server's `/api/custom-agent-types` REST resource.

**Managing them in the app.** Management lives in the create-session mode picker:

- There is one card per type, plus an "Add custom agent…" card that opens a bottom-sheet editor with inline cap and name-conflict errors.
- Long-press a custom card for Edit/Delete. Delete confirms the shortcut cascade.
- Types sync live across the user's devices via `custom_type.created/updated/deleted` push events on the user-events WebSocket.

**Launching one.**

1. The client sets `cli` to the type's `custom_<id>` kind. Clients never send the command.
2. The relay resolves the stored command server-side and injects relay-only `resolved_command` / `resolved_name` fields into the forwarded create.
3. The agent spawns the command via the user's login shell as a plain terminal session. The create flow keeps the Terminal-style "Create worktree" toggle for git projects.

**Other rules.**

- Session wire dicts carry a nullable `kind_label` name snapshot. Session cards label custom sessions from it, falling back to the live registry name, then `Custom`.
- Deleting a type soft-deletes its kind-scoped saved shortcuts in the same transaction.
- Pre-v7 agents are gated with a 409 `agent_update_required` on custom launches (proto-v7).

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

Every kind's bar also ends with a trailing custom-shortcut trigger (⌘) that opens a chord picker.

**Chords.**

- A chord is Ctrl/Alt/Shift (max two) + A–Z, 0–9, F1–F12, Tab, Enter, Space, or Backspace.
- Chords are encoded legacy-first, with a CSI-u fallback for chords legacy bytes cannot express. Those are marked as possibly unsupported per CLI.
- Recently used chords are remembered per session kind on-device and offered in the picker for one-tap resend.

**Saved shortcuts.** Chords can also be saved as persistent named custom-shortcut buttons (optional name, per session kind).

- The buttons appear after the ⌘ trigger at the end of the bar. Tap sends the chord, and long-press offers Rename/Remove.
- The picker gains a "Saved" chips section: tap to send, ✕ to remove.
- A two-state Save/Saved toggle on the chord-preview row pins the composed chord as a button in one tap, and unpins it again.
- A saved shortcut is always unnamed at create time. Naming happens afterwards from the `Saved as "…"` hint row's Name/Rename action, which opens the same Rename dialog as the long-press menu.

**Storage.** Saved shortcuts are stored server-side per user and sync live across the user's devices via `shortcut.created/updated/deleted` push events on the user-events WebSocket. Recents remain device-local.

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

A "Custom path…" entry in the project picker lets users launch one-off Claude or Terminal sessions in any arbitrary folder on the agent machine, without adding it to `projects.json`. The agent validates the path (absolute, exists, is-directory) and returns a clean 400 on bad input.

**Recent paths.** The last 5 per-agent paths are offered again:

| Client | How recent paths appear | How to forget one |
|---|---|---|
| Android | Quick-pick chips below the path text field | ✕ on the chip |
| iOS | "Recent paths" section of monospaced rows, head-truncated | Swipe or long-press on the row |
| Web | "Recent paths" section of monospaced rows, tail-truncated with the full path on hover | Per-row "×" |

**Rules for the list.**

- A path is recorded only after the session is actually created.
- Re-launching an existing path moves it to the front rather than duplicating it.
- The list is device-local. Every agent's list is cleared on sign-out and account deletion, but not on an agent switch, since the paths name the user's own workstation.

## External Sessions Notice

A one-time, dismissible explainer sits above the sessions list on all three clients:

> **Sessions started outside Mobaide won't appear here**
>
> Mobaide can only open sessions its own agent started. To control a session from here, start it from Mobaide instead of launching it yourself on the agent machine.

It names no specific CLI because the limit applies to every session kind: Claude, Codex, Antigravity, custom agent types, and plain terminal sessions alike. The agent's `SessionManager` spawns every PTY itself and has no attach or `--resume` path.

Dismissal is permanent and device-local (Android DataStore, iOS `UserDefaults`, web `localStorage`), and it survives app restart and sign-out.

## Auto Scan Mode

Auto Scan polls Linear for matching issues using a per-project configurable filter (trigger labels, trigger states, active/done state, removal label). It auto-creates git worktrees and launches Claude sessions to work on them.

**Remembered prompt.** Each project remembers the last-applied initial prompt, persisted as `auto_scan_prompt` in `projects.json` and surfaced read-only on `GET /api/projects`. Starting a new scan pre-seeds it in the create-session wizard instead of resetting to the engine-wide default. The prompt is preserve-last-applied only and is not writable via the project form.

**Remembered launch options.** The wizard also remembers the last-used launch options per session kind on-device. Auto Scan keeps its own bucket (model, effort, focus view, fast mode, ultracode, debug logging, append-system-prompt, plan mode, no-memory, skip-permissions), so scan choices never pre-fill the Claude Code sheet and vice versa.

**Skip permissions.** This is a live toggle, default ON for unattended scans. Switched OFF, the scan pauses at Claude Code permission prompts and raises the normal attention notification, so the user can open the session and approve. Scan sessions install the same Claude Code attention hooks and emit turn-finished / ended notifications like any other session.

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

**What it shows.**

- Local branches and worktrees as an alphabetized folder tree: a branch name split on `/`, folders first, with each tip commit's subject and date.
- Remote-tracking branches nested the same way, under a folder per remote, in a collapsed section.
- A filter once the list grows, which opens every matching folder.

**What it does.**

- Long-press multi-select and batch delete, with an undo Snackbar and force-delete confirmation.
- Deleting the selected branches on their remote too, behind a confirmation, once the agent advertises `capabilities.git.delete_remote`.
- A Remove worktree row action that drops only the worktree and keeps the branch.
- A New branch sheet (name, start point, optional check out).
- A confirmed Check out of a local or remote branch in the project folder.

**When Check out is refused.**

- While a session or auto-scan works there.
- While a fetch/pull/push job owns the repository.
- Mid-merge/rebase.
- When local changes would be overwritten.

**API.** `GET …/git/items?remote=true` and `POST /api/projects/{name}/git/branches|checkout`, advertised as `capabilities.git.{branches,delete_remote}`.

## In-session Git Changes

Everything here lives under one session-scoped prefix on the agent and works on Windows, Linux and macOS.

| Group | Routes | Capability keys |
|---|---|---|
| [Status and diff](#status-and-diff) | `GET /api/sessions/{id}/git/status`, `.../git/diff` | `capabilities.git.changes` |
| [Stage and commit](#stage-and-commit) | `POST …/git/stage\|unstage\|discard`, `POST …/git/commit`, `GET …/git/commit/context` | `capabilities.git.{stage,discard,commit}` |
| [Network jobs](#network-jobs) | `POST …/git/fetch\|pull\|push`, `GET …/git/jobs/{id}`, `POST …/git/jobs/{id}/cancel` | `capabilities.git.{fetch,pull,push}` |
| [Rebase](#rebase) | `POST …/git/rebase {onto}`, `…/rebase/continue\|skip\|abort` | `capabilities.git.rebase`, `capabilities.git.resolve` |
| [Conflict resolver](#conflict-resolver) | `GET …/git/conflict`, `POST …/git/conflict/resolve\|take\|restore` | `capabilities.git.conflicts` |

Capabilities are advertised on `GET /api/version`.

### Status and diff

The agent answers `GET /api/sessions/{id}/git/status` and `.../git/diff` for the directory a session was spawned in (project root or linked worktree). The answer contains:

- Branch, ahead/behind, and merge/rebase state.
- Conflicts / Staged / Changes / Untracked entries.
- A pre-parsed per-file diff (hunks and lines).

It is bounded by a hardened git runner (timeouts, byte caps, scrubbed environment, no repo-controlled diff drivers) and short-circuited by an `etag` for polling.

**Android and iOS.** The Changes viewers open from the terminal `⋯` overflow menu → Git → Changes… (shown when the session has a cwd). They have a grouped Conflicts/Staged/Changes/Untracked list with status badges and pull-to-refresh, and a Diff screen with hunks, a line-number gutter and a Changes / Full file switch.

**Web.** The web client mirrors it as a `⋯` → Git → Changes… menu on the terminal header, opening a non-modal slide-over beside the live terminal (file list and diff pane, stacked on narrow screens). It polls every 10 s only while the tab is visible and focused.

### Stage and commit

The same prefix also stages, unstages and discards files (`POST …/git/stage|unstage|discard`) and commits the staged set.

- `POST …/git/commit` takes a message on stdin, with optional amend and `--no-verify`.
- `GET …/git/commit/context` reports the author identity and the previous commit.
- These writes are serialised per repository and echoed as `git.changed`.

All three clients expose them from the Changes screen:

- Per-row Stage / Unstage / Discard…
- Section-level Stage all / Unstage all.
- Multi-select.
- Stage / Unstage from the Diff screen.
- A Commit sheet (Summary + Description, author line, Amend with prefill, Skip hooks), reachable from the screen and from ⋯ → Git → Commit….

### Network jobs

The same prefix also runs the network operations as agent-side **jobs**.

- `POST …/git/fetch|pull|push` answer a `GitJobResponse` snapshot at once, and the work continues in the background.
- `GET …/git/jobs/{id}` and `POST …/git/jobs/{id}/cancel` follow it.
- Git's own `--progress` counters stream as throttled `git.job.progress` events, then one `git.job.done`, then `git.changed`.
- A job never prompts for credentials on the host. A credential helper or `ssh-agent` there is the only source.
- A second job or a staging write on a busy repository is refused with `job_running`.

All three clients expose them as:

- ⋯ → Git → Fetch / Pull / Push…
- A Sync button and a tappable `↑n ↓m` header line on the Changes screen.
- A three-row sheet (Fetch always, Pull when behind, Push when ahead or to publish a branch) with a progress bar and Cancel.
- A job banner on the list.
- Confirmed follow-ups: Pull with rebase / merge after a non-fast-forward when the branch still has a live upstream, and Force push (with lease) after a rejected or non-fast-forward push.

### Rebase

The same prefix also runs an interactive **rebase** onto a picked ref: `POST …/git/rebase {onto}`, then `…/rebase/continue|skip|abort`. Editors are disabled, so nothing ever waits on a person. A rebase in progress is reported as `repo.rebase` on `GET …/git/status`.

All three clients expose it as:

- ⋯ → Git → Rebase… A project-backed session picks the target from its local and remote-tracking branches, and any other session types the ref.
- A Changes-screen banner that carries Continue (disabled while a conflict remains) / Skip… / Abort… when `repo.rebase` is set. A `git am` session or an older agent instead gets a banner pointing to the terminal.

A conflict row's confirmed *Mark resolved* action stages a fixed file. The agent refuses it while the file still holds `<<<<<<<`/`>>>>>>>` markers. It is advertised as `capabilities.git.resolve` alongside `stage`.

### Conflict resolver

The same session-scoped prefix also answers `GET …/git/conflict` and `POST …/git/conflict/resolve|take|restore` for one conflicted path at a time. A client can read its markers and the two sides' labels, write a resolution or a draft, take a whole side, or rebuild the conflict. `capabilities.git.conflicts` also adds `resolved_paths` to `GET …/git/status`.

With it, all three clients open a full-file resolver from a conflict row instead of the `HEAD` diff:

- The two sides are named by label and role, never "ours"/"theirs".
- A conflicts-left counter.
- Apply & mark resolved / Save draft.
- Whole-file / Start over… picks behind a menu.
- A file resolved this way lists under a Resolved section, with a confirmed Restore conflict….

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

**Revoking.** Users can revoke any device with a confirmation dialog. Revocation cuts the device off at once:

- Its access tokens stop working.
- All its refresh tokens across agents are revoked.
- Its live connections are closed.

**API.** The central server exposes `GET /api/devices` and `DELETE /api/devices/{device_id}` (user-scoped; cross-tenant ownership is enforced).

**Device identity.** Each install is identified by a client-generated UUID v4, persisted in a DataStore excluded from Android Auto Backup and device-transfer.

## Session Notifications

When the Android app is backgrounded, a foreground service holds the events socket and surfaces a system notification whenever a Claude session needs attention. Tapping the notification deep-links directly to the relevant session.

| Reason | When | Raised by |
|---|---|---|
| `awaiting_permission` | A permission prompt is awaiting | Claude Code hooks |
| `awaiting_os_permission` | An OS file-access prompt is blocking on the Mac: "Action needed on your Mac" | The agent's own startup-stall watchdog |
| `turn_finished` | The turn finished | Claude Code hooks, through the attention gate below |
| `ended` | The session ended | Claude Code hooks |

The hooks are `Notification`, `Stop`, `UserPromptSubmit` and `SessionEnd`. They post to the agent's local `POST /internal/hook` endpoint.

**One alert per stop.** `Stop` fires once per model turn, including every re-invocation a completing background subagent causes, so "turn finished" is not raised from it directly. An attention gate on the agent:

- Drops a `Stop` whose payload still reports in-flight background tasks or scheduled wakeups.
- Dedupes repeat stops within a turn.
- Debounces the rest.

So you get one alert when Claude actually stops rather than one per subagent.

**Delivery per platform.**

- **Android:** no FCM or external push service is required.
- **iOS:** the client cannot hold a socket while suspended. It receives these attention alerts as APNs pushes sent by the central server to every registered device, except those already holding a live events WebSocket of their own. Gating is per device, not per account, so Android's always-on foreground-service socket no longer silences pushes to a signed-in iPhone (see [docs/ios-client.md](ios-client.md)).

## Help & Support

An in-app screen, reachable from the Login screen and from Settings, and available even when signed out. It offers two channels:

| Channel | What it does |
|---|---|
| "Ask the community" | Opens the project's public GitHub Discussions in a Chrome Custom Tab |
| "Email support" | Opens a prefilled `mailto:` (app version, device model, anonymous device id, and the signed-in email when available). Falls back to copying the support address to the clipboard when no email app is installed |

The two destinations are build-time `buildConfigField`s `SUPPORT_GITHUB_URL` and `SUPPORT_EMAIL`, overridable via environment variables or `android/local.properties` (same resolution order as `PRIVACY_POLICY_URL`).
