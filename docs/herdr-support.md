# Herdr support

**Status (2026-11-15):** first release. Dashboard, alerts, actions and the live pane view work on Android, iOS and web, for both herdr products.

This is the umbrella document for Herdr support. The wire records are in [PROTOCOL.md](../PROTOCOL.md), *Delta 2026-11-15: Herdr dashboard*, and are not repeated here.

**On this page**

- [1. What it is](#1-what-it-is)
- [2. Decisions](#2-decisions)
- [3. How it works](#3-how-it-works)
- [4. Safety rules](#4-safety-rules)
- [5. The pane view](#5-the-pane-view)
- [6. Alerts and "seen"](#6-alerts-and-seen)
- [7. Configuration](#7-configuration)
- [8. File map](#8-file-map)
- [9. Testing](#9-testing)
- [10. Facts about herdr that the code depends on](#10-facts-about-herdr-that-the-code-depends-on)
- [11. Limits and open items](#11-limits-and-open-items)

## 1. What it is

Herdr is a terminal workspace manager for AI coding agents. A user starts it on a computer and runs many agents in its panes.

Mobaide shows the herdr servers of the agent's computer in the apps:

- **Dashboard.** Every workspace, tab and pane, with the status of each agent.
- **Alerts.** A notification when an agent in a pane needs the user.
- **Actions.** New workspace, new tab, split, start an agent, rename, close.
- **Pane view.** One pane as a live terminal, with typing.

Two products have the name. Both are supported behind one adapter:

| Product | Binary | Server id | Notes |
|---|---|---|---|
| Herdr TUI | `herdr` | `tui:<session>` | Minimum version 0.9.2. An older server shows as `outdated`. |
| herdr-core | `hc` (with the Herdr desktop app) | `core:<session>` | No workspaces or tabs of its own. The tree comes from labels. |

## 2. Decisions

| Topic | Decision | Reason |
|---|---|---|
| Start of a server | Attach only. Mobaide never starts a herdr server. | A server that the agent service starts stops with that service (Linux cgroup, Windows Job Object). |
| Pane content | A pane is a Mobaide terminal session that exists only while a client has it open. | The terminal screen, the composer and the keys work without new client code paths. |
| Two viewers | One shared stream per pane. Watch mode uses the pane's real size. Control mode has one controller. | A phone must not resize a pane that a user reads at the desktop. |
| Approvals | No separate approval card. | The live terminal is where the user answers. |
| "Seen" state | Kept on each device, keyed by the pane's `turn`. | The herdr call that marks a pane seen also moves the view of a user at the desktop. |
| Alert kind | One kind, `herdr.attention`, for all panes. | One notification channel and one setting are sufficient. |
| Session list | Pane sessions are hidden from `sessions.changed`. | They are not counted against the session cap and do not block an agent update. |
| Card visibility | The Herdr card shows only while a herdr server runs. | A user without herdr sees nothing new. |
| Windows | The code is complete. The dashboard is off by default there in this release. | The named-pipe transport has not run on a real Windows computer. See section 11. |
| Roles | One permission role. | The same user owns the computer and the apps. |
| Chat view | Not in this release. The composer on the terminal is the chat input. | A transcript view needs a per-agent parser. |

## 3. How it works

```
client ── relay ── agent ──┬── TuiBackend ── herdr.sock (NDJSON)
                           │                └─ `herdr terminal session observe|control` (pane stream)
                           └── CoreBackend ── herdr-core socket (JSON-RPC, framed channels)
```

- `HerdrService` finds servers every 5 seconds and keeps one link per server.
- A link holds a mirror of the server's tree and subscribes to its events.
- A change sends the whole state as one `herdr.changed` event. A client replaces its state; it does not merge.
- Clients use Mobaide's own records. A client never sees a herdr method name. An upstream change needs an agent release only.
- `GET /api/version` carries `capabilities.herdr = {"dashboard": true}`. A client shows no Herdr UI without it.

## 4. Safety rules

The herdr socket has no authentication. Access to it is equal to a shell.

- Only the Mobaide agent opens the socket. The relay and the clients never do.
- The agent calls a fixed list of methods. These are never reachable: `server.stop`, `server.reload_config`, `server.live_handoff`, `layout.apply`, `plugin.*`, `integration.*`.
- Nothing stops a server, a pane or an agent without a clear user action. "Close" asks first on every client and says what stops.
- Pane text is not stored on the relay. A push notification carries the pane title and the reason, not the pane content.
- A new workspace folder must exist on the agent's computer. The agent checks it.
- The agent's `stop()` of a pane session only detaches. It never closes the pane.
- No session route ends a pane view. `DELETE /api/sessions/{id}` answers 404 for a pane session id.
- Each Herdr action writes an audit record on the agent: `audit.herdr.created`, `audit.herdr.closed`, `audit.herdr.renamed` (without the label), `audit.herdr.agent_started`, `audit.herdr.control`.
- A label must not contain control characters. Herdr draws it on the user's desktop terminal.

Privacy:

- An iOS push for a pane sends the pane title and the herdr server, pane and agent ids to Apple. The pane title can be a name that the user chose.
- Each client stores, on the device only, which finished turns the user has seen.
- `central_server/legal/privacy.md` states both. Change that file when the alert content changes.

## 5. The pane view

`POST /api/herdr/pane/open` returns a session id. The client opens the usual terminal WebSocket with it. The id is the same for the same pane, so two clients share one stream.

| Mode | Size | Typing |
|---|---|---|
| Watch (default) | The pane's real size. The state frame carries `pane: {mode: "watch", cols, rows}`. | The composer always works. Raw keys work when `features.type_in_watch` is true. |
| Control | The controller's size. The state frame carries `pane: {mode: "control", controller, cols, rows}`. | All keys. |

Rules for a client that draws a dictated grid (every client in watch mode, and every client but the controller in control mode):

- The emulator has exactly the pane's grid. Pane frames address cells by absolute position.
- The text shrinks until the columns and the rows fit, down to a smallest size.
- A pane that is still wider pans horizontally. On Android and iOS, a pane that is still taller is anchored to its bottom; on web it scrolls.
- The client sends no resize frame.

Control mode:

- "Take control" resizes the real pane to the client's screen. The notice on the screen says so.
- One client controls a pane. Each app process and each browser tab has its own `client_id`, and the state frame names the controller.
- A second client that watches the same pane draws the controller's grid. Its notice says that another device controls the pane. It can take control itself.
- "Release", or leaving the screen, gives the pane back. The agent restores the previous size. Only the controller can release.
- Control ends when the last client detaches.

Lifetime:

- A pane stream that fails is opened again after 1, 2, 5, 15 and then 30 seconds. A stream that worked for 30 seconds starts this sequence again.
- The agent ends a pane session that nobody watched for 30 minutes, and one whose server restarted.
- The client then gets a `stopped` state frame. It asks for the pane again once and attaches again.
- A pane that is closed answers `pane_not_found`. The client leaves the screen.
- When the link to a herdr server drops, its pane views end at once. The open call waits up to 4 seconds for the new link, so the view comes back. In that time it looks for the server again every 0.5 seconds. A server that was never seen gets an answer at once.

"History" reads the last 1000 lines of the pane as text (`GET /api/herdr/pane/text`). The live view shows only the screen.

## 6. Alerts and "seen"

| Pane status | Chip | Alert reason |
|---|---|---|
| `blocked` | Needs you | `awaiting_permission` |
| `failed` | Failed | `turn_failed` |
| `done` | Done | `turn_finished` |
| `working` | Working | none |
| `idle` | Idle | none |

- The agent times alerts with the same gate as session alerts. A `done` must hold for 3 seconds, because herdr can report it while an agent only paused.
- A new `turn` is a new finished turn, also when the agent never read `working` in between. A short turn can start and end between two reads.
- A client shows no alert for the pane that is on screen.
- A tapped alert opens the dashboard, which opens the pane.
- A `done` pane whose `turn` the device has already seen shows as Idle. Opening the pane, or "Mark as seen", stores the turn.
- The relay sends the push with keys `herdr_server_id`, `herdr_pane_id` and `agent_id`.

## 7. Configuration

Agent `.env` (see `mobaide_agent/.env.example`):

| Variable | Default | Use |
|---|---|---|
| `MOBAIDE_HERDR_ENABLED` | on (off on Windows) | `0` switches the dashboard off. The agent then sends no `herdr` capability. `1` switches it on, also on Windows. |
| `MOBAIDE_HERDR_BIN` | found on `PATH` | Path of a `herdr` binary of the same version as the server, for the pane stream. |
| `MOBAIDE_HERDR_CONFIG_DIR` | `~/.config/herdr` | The folder of the Herdr TUI sockets, when the agent service has no `XDG_CONFIG_HOME`. |
| `MOBAIDE_HERDR_FAKE` | off | Tests only. A scripted server replaces the real backends. |

All four are fields of `AgentConfig`, so a line in the agent's `.env` works on every platform.

There is no client setting.

## 8. File map

| Component | Files |
|---|---|
| Agent | `mobaide_agent/mobaide/herdr/`: `service.py` (discovery, alerts, pane sessions), `tui.py`, `core.py` (the two backends), `pane_session.py`, `transport.py`, `models.py`, `dispatch.py`, `router.py`, `fake.py` |
| Agent wiring | `core/session_manager.py` (external sessions), `relay/client.py`, `agent/ws/session_io.py`, `agent/ws/events.py`, `agent/app.py`, `agent/config.py` |
| Relay | `central_server/central_server/relay/agent_ws.py` (alert allow-list), `push/dispatcher.py`, `push/content.py`, `push/sender.py` |
| Web | `web/src/routes/herdr/`, `web/src/herdr/`, `web/src/api/herdrRepository.ts`, `web/src/protocol/dto/herdr.ts`, `web/src/terminal/sessionModel.ts` (dictated grid) |
| Android | `ui/herdr/`, `data/repository/HerdrRepository.kt`, `data/network/dto/HerdrDto.kt`, `data/local/HerdrSeenStore.kt`, `ui/terminal/TerminalSurfaceView.kt` (dictated grid), `terminal-view/.../TerminalView.java` (forced rows) |
| iOS | `UI/Herdr/`, `Data/Repository/HerdrRepository.swift`, `Models/HerdrDto.swift`, `Data/Local/HerdrSeenStore.swift`, `UI/Terminal/TerminalPanScrollView.swift` (dictated grid), `UI/Terminal/TerminalPaneState.swift` |
| Fixtures | `tests/protocol_fixtures/dto/agent/HerdrSnapshot.json`, `HerdrAttention.json` |

## 9. Testing

`tests/e2e/manual_stack.py up` has two flags (see [manual-e2e-testing.md](manual-e2e-testing.md)):

| Flag | Effect |
|---|---|
| `--fake-herdr` | A scripted server `tui:demo` with two workspaces, a working agent, a blocked agent and a shell. No herdr install is necessary. A message to an agent makes it work and then finish. |
| `--herdr` | The agent attaches to the real herdr servers of the computer. |

Without a flag the stack has no Herdr dashboard, so a test does not touch the operator's own herdr by accident.

To test with a real server and not touch your own, start an isolated one:

```bash
export XDG_CONFIG_HOME=/tmp/herdr-test          # keep the path short: a socket path has a length limit
herdr --session spike                           # a separate server with its own socket
MOBAIDE_HERDR_CONFIG_DIR=/tmp/herdr-test/herdr python tests/e2e/manual_stack.py up --herdr ...
```

## 10. Facts about herdr that the code depends on

Herdr TUI (verified with 0.9.5, protocol 22):

- One request per socket connection. Only `events.subscribe` stays open.
- An agent status change arrives only on a subscription scoped to that pane (`pane.agent_status_changed {pane_id}`).
- With a desktop client attached, the API reports `idle`, not `done`. `completion_seq` marks a finished turn.
- The layout rect of a pane includes the border and the scrollbar. The real size comes from the pane's TTY.
- `pane.rename` sends no event. The link reads its mirror again after each action.
- The bridge binary must have the same version as the server.
- `pane.send_text` types raw bytes and does not resize.

herdr-core (verified with `hc` 0.1.21):

- JSON-RPC 2.0, then framed channels after `hello {framed: true}`.
- Frames can arrive before the reply that names their channel. The connection buffers them.
- A control attach keeps the last viewer's size. The stream restores the old size before it closes.
- `terminal.keys` accepts key names only, so raw typing in watch mode is limited. `features.type_in_watch` is false.
- Space and tab names come from the labels `herdr.space` and `herdr.tab`, and from a document of the desktop app. The read of that document is tolerant.

Windows:

- The agent runs a `SelectorEventLoop`, which has no asyncio subprocess or pipe support. `transport.py` uses threads that feed a `StreamReader`.
- Sockets are named pipes.

## 11. Limits and open items

- **Windows is off by default, and not verified on a real computer.** `MOBAIDE_HERDR_ENABLED=1` switches it on. Do these checks on Windows before the default changes:
  - The named pipe uses overlapped I/O (`transport.py::_OverlappedPipe`), because a synchronous pipe handle serializes reads and writes and would deadlock. This code has not run.
  - The agent does not check which account serves the pipe. Another local account could create the pipe name while herdr does not run. The pipe is opened with `SECURITY_IDENTIFICATION`, so that account cannot act as the agent's user. An owner check (`GetNamedPipeServerProcessId` and a token compare) is still open.
  - The bridge on native Windows has no public documentation. The pane view falls back to a poll of `pane.read` when the bridge gives no frame.
- **Update the relay before the agents.** The relay sends the number of clients with each attach and detach. An agent on an older relay counts one step for each message, and after a tunnel reconnect it can count two viewers of one pane as one.
- **A message that herdr-core refuses or does not take in time is only logged.** The user gets no sign in the app.
- **A create with an agent can make a pane without the agent.** The answer then has `agent_error`, and the clients show it and open the pane.
- **herdr-core tree names** depend on an undocumented document. Terminals without labels are in one "Terminals" group.
- **Transient size after a herdr-core release.** A pane can show 66x22 for a moment before the old size returns.
- **A stalled tunnel can drop a diff frame.** The next repaint or reconnect corrects the view.
- **No transcript chat view.** It is the next step if users ask for it.
- **No start of a herdr server from the apps.** See the decision in section 2.
