# Mobaide feature reference

The detailed behaviour of each feature: the limits, the per-platform differences, and the main settings.

For the short overview, see the [README](../README.md). The wire contract itself lives in [PROTOCOL.md](../PROTOCOL.md).

A **workspace** is a named folder that the agent starts sessions in. The wire and storage names still say "project" for compatibility (`/api/projects…`, `projects.changed`, `project_name`, `projects.json`).

**On this page**

| Area | Features |
|---|---|
| Sessions | [Chat Sessions](#chat-sessions) · [Remote Terminal](#remote-terminal) · [Multi-Session](#multi-session) · [Managed CLI agents](#managed-cli-agents) · [Terminal Sessions](#terminal-sessions) · [Custom Path Sessions](#custom-path-sessions) · [Reorderable New-Session Lists](#reorderable-new-session-lists) · [Auto Scan Mode](#auto-scan-mode) · [1-hour Prompt Cache](#1-hour-prompt-cache) |
| Terminal input and display | [Terminal Display Modes](#terminal-display-modes) · [Explicit Keyboard & Direct TUI Input](#explicit-keyboard--direct-tui-input) · [Prompt Templates](#prompt-templates) · [Reconnection](#reconnection) |
| Git | [Git Management](#git-management) · [In-session Git Changes](#in-session-git-changes) |
| Account and platform | [Secure Access](#secure-access) · [Cross-Platform](#cross-platform) · [Keep the Computer Awake](#keep-the-computer-awake) · [Account Self-Deletion](#account-self-deletion) · [Device Management](#device-management) · [Session Notifications](#session-notifications) · [Help & Support](#help--support) |

## Chat Sessions

Claude Code, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie and Pi sessions open in a **chat view** by default: messages, tool calls, diffs, plans, permission prompts and questions are native UI on Android, iOS and the web, instead of a terminal.

- **How it works.** The agent runs the coding agent over the [Agent Client Protocol](https://agentclientprotocol.com) (ACP) and streams a structured timeline. Claude Code uses the ACP adapter `@agentclientprotocol/claude-agent-acp`, which drives the PC's own `claude` binary. Codex uses `@agentclientprotocol/codex-acp`, which drives the PC's own `codex app-server` (`CODEX_PATH`). Antigravity uses the ACP server built into its CLI, `agy acp`: no adapter and no Node.js. OpenCode does the same with `opencode acp`, GitHub Copilot with `copilot --acp`, and Cursor with `cursor-agent acp`. Pi has no ACP server, so the agent runs its own bridge (`mobaide_agent/mobaide/acp/pi_bridge.py`) over Pi's RPC mode (`pi --mode rpc`). Login, settings and CLI version are the same as in a terminal session.
- **Agent-agnostic.** The chat UI renders only ACP concepts (messages, tool kinds, config options, slash commands). Another ACP agent needs only a launch spec in `mobaide_agent/mobaide/acp/agents.py`.
- **Requirements on the PC.** For Claude Code and Codex: Node.js 22+, or an adapter command on PATH. For Antigravity: an `agy` that supports `agy acp` (the agent checks `agy help acp`). For OpenCode: an `opencode` binary (no auth probe, no Node.js). For GitHub Copilot: a `copilot` binary (CLI 1.0.106 or later). For Cursor: a `cursor-agent` binary. For Junie: a `junie` binary (no Node.js). For Pi: a `pi` binary. Otherwise the wizard shows why and creates a terminal session. `MOBAIDE_ACP_CLAUDE_COMMAND`, `MOBAIDE_ACP_CODEX_COMMAND`, `MOBAIDE_ACP_ANTIGRAVITY_COMMAND`, `MOBAIDE_ACP_OPENCODE_COMMAND`, `MOBAIDE_ACP_COPILOT_COMMAND`, `MOBAIDE_ACP_CURSOR_COMMAND`, `MOBAIDE_ACP_JUNIE_COMMAND` and `MOBAIDE_ACP_PI_COMMAND` in the agent's env file override the launch commands. The Pi override must speak ACP.
- **Antigravity probe.** A yes or no answer is kept until the `agy` binary changes. A missing `agy` or a failed probe is checked again after 30 s. The agent warms the probe at start.
- **Adapter install.** The agent installs each adapter once, at a pinned version, into `<state prefix>/acp-adapters/`. It uses `npm install --ignore-scripts --omit=optional --before=<release date>` and runs the adapter with `node`, not `npx`. The first chat shows "Setting up the ... chat on your PC" until the install ends, then the notice goes away. Uninstalling the agent removes `acp-adapters`.
- **Codex modes.** Read-only maps to the read-only mode. Auto (workspace-write) maps to Workspace access, and a person approves anything outside it. Auto review (`auto_review`) maps to the Auto review mode (`agent`): a Codex reviewer approves each request outside the workspace sandbox. Full access maps to full access only with `full_auto`, or `danger-full-access` with approval `never`. The mode is required: if the agent cannot apply it, the session does not start.
- **Codex questions.** When Codex asks a question without waiting (`request_user_input_async`), the chat shows the question text and a form, while Codex keeps working. An answer is sent into the running turn. An answer sent after the turn ends is not delivered, and the form closes when the turn ends.
- **Folder trust.** Before a chat session opens a folder Claude Code has not trusted, the chat asks **Trust this folder?**. Trust is recorded in `~/.claude.json`, like the CLI. For Codex, the chat reads and writes `[projects."<dir>"] trust_level` in `$CODEX_HOME/config.toml`, with the same lookup order as Codex, including linked worktrees. For the home folder, a folder above it, or a root, trust lasts for this session only. Parent folders count only up to the enclosing git repo root. Declining ends the start; Restart offers the choice again. `agy acp` skips the CLI's trust prompt, so for Antigravity the chat asks too. Trust is recorded as `trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json`, for the exact folder only. OpenCode has no folder-trust gate, so its chat does not ask. `copilot --acp` also loads a folder's hooks and MCP servers without asking, so the chat asks for GitHub Copilot too. Trust is recorded in Copilot's own `trustedFolders` list in `$COPILOT_HOME/config.json` (default `~/.copilot/config.json`). A listed folder also covers the folders inside it. For the home folder, a folder above it, or a root, trust lasts for this session only. `cursor-agent acp` reads a folder's rules, hooks and MCP servers from its working directory, so the chat asks for Cursor too. Trust is recorded the way Cursor does it, as a `.workspace-trusted` file in `<CURSOR_DATA_DIR or ~/.cursor>/projects/<folder slug>/`. A parent folder's record also covers the folders inside it, except the home folder, the folders above it, and folders fewer than three levels deep. For Pi, the chat asks only when the folder has resources Pi protects (`.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions`, `.pi/skills`, `.pi/prompts`, `.pi/themes`, `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, or `.agents/skills` in the folder or above), Pi's `defaultProjectTrust` is `ask`, and Pi's `trust.json` has no decision. Trust writes `true` into `trust.json`, also for the home folder: Pi has no "too broad" rule.

### What the chat view offers

| Area | Behaviour |
|---|---|
| Timeline | Markdown answers, collapsible thinking, one compact card per tool call (icon by kind, status, `+N −M` for edits; tap for the diff, output and input). Conversation text uses the system font, not the pixel font. On Android and iOS, a tap on the timeline hides the keyboard; drags, long presses and row taps work as before. On iOS, a long press on chat text selects a word, handles extend the selection, and Copy takes only the selection; selection stays inside one block (paragraph, heading, code block), and a tap clears it. |
| Focus view | On by default; toggle it in the chat's ⋯ menu (one setting per device or browser, for every chat). It shows your messages, the agent's final answer of each turn, approvals and questions, notices and errors. The thinking, tool calls and in-between messages of a run fold into one "N steps" row that shows the current step while the agent works; tap it to open the steps in place. |
| Approvals | Permission prompts and multiple-choice questions appear as a card above the composer, with the agent's own options. The first answer from any device wins. If the agent cancels the request, the card leaves on all devices. |
| Composer | Slash-command suggestions, `@` file mentions (see below), image attachments (when the agent accepts images), voice input (on your computer or through Mobaide Cloud), prompt templates (inserted at the cursor), save as template, a full-screen editor on Android and iOS, and Claude's suggested next prompt (tap to fill the composer; Claude Code only, from the second turn). Messages sent while the agent works are queued. You can remove one, or tap **Send now** to steer the agent with it. |
| Controls | Stop, and chips for the mode (shield), model (chip) and effort (brain) and other agent options; the chips wrap onto a second line instead of scrolling. Context usage and cost in the top bar, and a full-screen button on the web. Plan progress above the composer. |
| New conversation | **New conversation** in the chat's ⋯ menu clears the context and starts over in the same window, with the same folder and settings. A confirm dialog appears only when a running turn stops or queued messages are removed; an idle chat starts over at once. The session, its folder or worktree and the folder trust stay. If the old conversation was stored, a notice says you can reopen it with **Continue a session**. The `/clear` slash command does the same at once, without the dialog (unless the agent has its own `/clear`). |
| Fork a chat | Copy the conversation into a new chat and keep this one as it is. **Fork chat** in the ⋯ menu copies all of it (Claude Code, Codex, OpenCode, Junie). **Fork from here** on the final answer of a finished turn copies the conversation up to that answer and drops every later turn (Claude Code and Codex only; not on a message in the middle of a turn, nor on a stopped or failed turn. After the chat reloads, a stopped Codex turn or a failed turn can offer it, as the stored conversation does not say how a turn ended; a Codex turn stopped in the Codex app on the PC can offer it too, as Mobaide only sees that it ended). It is a button under the answer on Android, iOS and web. A confirm dialog comes first, then the new chat opens with that history. It uses the same folder (a worktree too), workspace and settings, its title starts with "Fork:", and it appears in **Continue a session**. While it is made, the status line reads "Forking the chat…". Files on the PC are not changed back: both chats work in the same folder. While a turn runs or the chat starts, the fork actions are disabled (the PC agent also refuses them): wait for the turn to end, or stop it, then fork. |
| Command output | Command output shows as a console block, with a live tail while the command runs. |
| Code Mode calls | A Code Mode tool call (Pi `codemode`, OpenCode `execute`) shows as a **Run script** card. The subtitle lists the tools the script names ("Calls read, bash"). The card shows the output, then the script as a JavaScript code block. The raw input JSON is hidden. |
| Sign-in | A **Sign-in needed** card appears when the agent is signed out, or when you send `/login` (also in the slash-command suggestions). Pick a method. The PC agent runs the coding agent's own login command in a hidden terminal. Claude Code: Claude account or Console, then paste the code the page shows. Codex: open the page and enter the one-time code in the card, or use an OpenAI API key. While Codex's shared app-server runs on the PC, the Codex sign-in goes through it, because it does not read a login that `codex login` saves. Antigravity: Google sign-in, then paste the code or the address of the page you land on. OpenCode has no sign-in card: on a provider auth failure the chat says "OpenCode could not sign in to the model provider. In New session, pick OpenCode and use Connect a provider, or run `opencode auth login` on the PC. Then tap Restart." GitHub Copilot: **GitHub account**, which runs `copilot login --device-code` on the PC and shows GitHub's device page and a one-time code. Cursor: **Cursor account**, which runs `cursor-agent login` on the PC and shows a sign-in page that works on any device; there is no code to type. On success the agent restarts and sends the refused messages again. A failed login, or one that takes over 20 min, returns to the method choice with the reason. Pi: **ChatGPT account** (`openai-codex`, a device code) or **Claude account** (`anthropic`, a code from the page), through `pi auth login <provider> --no-browser`, or an **OpenRouter**, **Gemini**, **OpenAI** or **Anthropic API key**, which the PC agent types at the prompt of `pi auth login <provider> --api-key`. Pi does not check the key: if your first message fails with it, the card appears again, and a new key replaces the old one. An Anthropic API key replaces a Claude account login. Set up other providers with `/login` in Pi. |
| Sync | Every open device shows the same state; a reconnect fetches only what it missed. |

**`@` file mentions.** Type `@` at the start of the message or after a space, then part of a name. Up to 20 files and folders of the session's folder appear in the suggestion list (on the web, pick with the arrow keys and Enter or Tab; Esc closes the list). The PC agent ranks them, so all apps show the same order: names that start with what you typed, then names that contain it, then paths. In a git repository the list honours `.gitignore`; elsewhere dot folders, `node_modules` and `__pycache__` are skipped. A pick puts `@path ` in the message. A folder puts `@folder/` and the list goes on inside it. The text keeps the path for every agent. Each picked file whose `@path` is still in the text when you send (at most 10) also goes to the agent as a file link, and your message shows a file chip for it, also after **Continue a session**. How agents use the link: Claude Code and Codex see it as `[@name](file://…)`, and GitHub Copilot and Antigravity as a link; all four read the file with their own tools. OpenCode reads the file (or lists the folder) into the message. Cursor adds up to 20 000 characters of a file inside the project. Junie attaches the file. Pi gets the link as a line of text and reads the file. A large file can make the message large for OpenCode and Cursor. The suggestions are on Android, iOS and the web, in the inline composer only (not the full-screen editor). An older PC agent shows no suggestions.

The terminal's control-key bar and custom keyboard shortcut buttons are not part of the chat view: they send raw terminal bytes. Templates and slash commands work in the chat view. Stop and the mode chip replace Esc / Ctrl+C and Shift+Tab.

### Open sessions in and continuing sessions

- **Open sessions in.** The Claude Code, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie and Pi pages of the new-session wizard start with an **Open sessions in** choice: **Chat** (left, the default) or **Terminal** (right). The choice is remembered. Chat is disabled, and Terminal is held, when the PC has no chat view for the agent. The Auto Scan wizard has the same choice, also Chat by default. Terminal runs the session in the classic terminal (needed for TUI-only features such as Rewind; a chat can fork from an earlier answer instead). The wizard's Focus view applies only to terminal sessions; a chat session has its own Focus view in its ⋯ menu. Debug logging works in both: a terminal session prints the diagnostics into the terminal, and a chat session writes them to `~/.claude/debug/` on the PC.
- **Continue a session.** The same pages offer **Continue a session**: a list of the Claude Code, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie or Pi sessions stored on the PC, for this folder or all folders, including sessions started outside Mobaide (for example by running `claude` in a terminal). Picking one opens it in the chat view with its history. Sessions in the agent's own `voice-cleanup` folder are never listed.
- **Delete a stored session.** When the agent supports ACP `session/delete` (Claude Code, Codex, Junie), each row of **Continue a session** has a Delete action: a trash button on Android, a swipe action or context menu on iOS, a Delete button on the web. A confirm dialog comes first. Claude Code removes the session from the PC, also from its terminal history, and this cannot be undone; Codex moves it to its archive. A row that is open in a Mobaide chat or in another app on the PC cannot be deleted. Close a session that is still open in a terminal first: Mobaide cannot detect it there.
- **Live Codex sessions.** When the Codex shared app-server runs (the daemon that `codex` in a terminal starts), chat sessions join it through `mobaide/acp/codex_bridge.py`. A session open in a terminal and on the phone stay in sync both ways. This works on macOS and Linux, not on Windows, and not for workspaces with a conda env. A message typed in another app shows as **Message sent from another app on your PC**, followed by its answer. The adapter does not forward the text of that message. Closing the chat cancels only its own turn.
- **Antigravity sessions.** Each chat runs its own `agy acp` process in the session folder. Continue uses `session/list` and `session/load`, with history. It is not live: `agy` has no shared local server, so there is no sync with a terminal. **Send now** works: the message goes into the running turn as a second prompt. While the turn asks for a permission or an answer, answer it first.
- **Antigravity conversation lock.** The chat holds the lock file that `agy` uses (`~/.gemini/antigravity-cli/presence/<id>.lock`). An `agy` terminal then warns "Conversation already open". When another app holds the lock, the Continue list shows "Open in another app on your PC. Close it there first.", and the chat shows a warning notice. Two processes that write one conversation overwrite each other. After the other app closes the conversation, the next message first reopens it, so the chat shows what was sent there ("Loaded the latest messages"), and then goes out.
- **OpenCode sessions.** Each chat runs its own `opencode acp` process, apart from the server the OpenCode TUI uses. It is not live: there is no sync between a chat and a terminal on the same session. Continue lists the sessions stored per folder (`GET /api/chat/opencode/sessions`) and replays the history. The config chips come from OpenCode: model (`provider/model`), mode (Build or Plan) and, for models with variants, effort. With **Auto-approve permissions**, the agent answers each permission request with the "allow once" option and shows no card.
- **GitHub Copilot sessions.** Each chat runs its own `copilot --acp` process. It starts in the home folder and takes the session folder from `session/new`. Continue uses `session/list` (filtered by folder) and `session/load`, with history. It is not live: there is no attach to a session open in Copilot's terminal UI.
- **GitHub Copilot options.** The wizard's allow-all choice sets Copilot's ACP option `allow_all` to `on`. The chat shows it, and you can turn it off. The mode chip offers Agent, Plan and Autopilot. A model chip and an effort chip appear only when the account's Copilot plan offers more than the Auto model. The wizard has no model or effort picker for Copilot.
- **Cursor sessions.** Each chat runs its own `cursor-agent acp` process, started in the session folder. Continue lists Cursor chat sessions only: Cursor keeps them apart from the sessions of its terminal UI, so a session started in Cursor's terminal UI cannot be continued in the chat.
- **Cursor options.** Mode (Agent, Plan or Ask), the model, and the picked model's settings (effort, context, fast) are chips inside the chat. The wizard has no model picker for Cursor. A shell command that is not in Cursor's allowlist asks for approval (**Allow once**, **Allow always** or **Reject**). File edits do not ask; that is Cursor's own behaviour. Cursor's questions show as a form. A Cursor plan shows as a message plus a card with **Accept plan** and **Reject plan**; in Plan mode the card also offers **Accept and start**, which switches to Agent mode and sends "Implement the plan.". Cursor's task list shows as the plan chip. The wizard's one launch option is **Run everything without asking (dangerous)**, which starts `cursor-agent --force` (Cursor's Run Everything; commands that Cursor's settings deny still do not run). The worktree section and the **Open sessions in** choice also apply. Terminal sessions run the Cursor TUI.
- **Junie sessions.** Each chat runs its own `junie --acp=true` process (JetBrains Junie CLI), started in the session folder. Continue lists Junie sessions, including ones started in Junie's own terminal UI. The chat asks **Trust this folder?** before it opens a folder Junie has not trusted, because Junie loads a folder's MCP servers, skills and commands without asking over ACP. A folder you trusted in Junie's terminal UI needs no question. The chat has no sign-in: sign in from a Junie terminal session with **Open sessions in** set to **Terminal**, then restart the chat. If no model can answer, the chat shows an error that says how to get one: Junie Lite works only in Junie's terminal UI and in JetBrains apps.
- **Junie options.** Mode (Default, Plan or Goal), the model, the effort and Brave mode (Auto, Brave or Safe) are chips inside the chat. The wizard has no model picker for Junie. The wizard's launch options are **Plan mode** and **Brave mode (dangerous)**. Plan mode starts the session in Junie's Plan mode: Junie prepares a plan and asks before it changes files. Brave mode runs every command without asking, for this session only. The worktree section and the **Open sessions in** choice also apply. Terminal sessions run the Junie TUI. Junie's voice cleanup runs on Junie Lite and is in voice-input.md.
- **Pi sessions.** Each chat runs its own `pi` process (RPC mode) in the session folder, through the agent's bridge `mobaide_agent/mobaide/acp/pi_bridge.py`. Continue lists Pi's own session files (`~/.pi/agent/sessions`), the same as `pi --resume`. It is not live: there is no sync with a session open in Pi's terminal UI. A project-level `sessionDir` from `.pi/settings.json` is not read for the list.
- **Pi chat options.** Chips: Model (the models of all signed-in providers) and Thinking. **Send now** works (Pi's `steer`). `/compact [what to keep]` is supported. Pi's extension commands, prompt templates and skills are in the commands list. Pi's built-in terminal commands (`/model`, `/settings`, and so on) are not available in the chat. Extension dialogs become approval cards or forms. Context use is shown. The session cost (Pi's own total, as in its footer) is shown for a provider with an API key or another pay-per-token login. It is not shown for a subscription login (ChatGPT, Claude, GitHub Copilot and others): Pi prices those turns at list prices, which is not what you pay. The cost is the total of the whole session: after a switch from a subscription model to a pay-per-token model, it also counts the earlier subscription turns at list price.
- **Pi wizard.** The Pi page shows "Pi runs commands and edits files without asking. Pick the model and the thinking level inside the session.", the worktree section and the **Open sessions in** choice. There is no model or thinking picker in the wizard.
- **Code Mode.** Code Mode lets the coding agent write JavaScript that makes several tool calls in one step. The wizard has a **Code Mode** toggle for three agents. The choice is remembered per device. Continue never uses it. The Auto Scan wizard has the same toggle for these agents (see Auto Scan Mode).
  - **Claude Code.** Under Advanced. Default off. It needs Claude Code 2.1.323 or later (native build, Claude subscription or Console account). It shows only when **Open sessions in** is **Terminal**, like Focus view: Claude Code offers Code Mode neither through the Agent SDK nor through ACP. With **Chat**, the toggle is hidden, the choice is kept, and the session starts without Code Mode. The launcher sets `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`. On first use, Claude Code asks to confirm and relaunches itself; the session stays open. Mobaide's Claude hooks get `"onFailure": "continue"` in these sessions, because a failed hook would refuse tool calls. A `0` in your `settings.json` or managed settings still wins.
  - **Pi.** Default off. It needs Pi 1.1.0 or later. The launcher adds `--tools +codemode` (Pi's built-in `codemode` tool), in terminal sessions and in the chat bridge.
  - **OpenCode.** Default on, for OpenCode 2 (the `execute` tool for MCP tools). Off appends the rule `{"action":"execute","resource":"*","effect":"deny"}` to `OPENCODE_CONFIG_CONTENT`, after your own rules. In the terminal UI it also starts `opencode --standalone`, so the rule stays with that session and not with the shared background service. OpenCode 1 has no Code Mode: nothing changes there. On Android the OpenCode and Pi toggles sit under a **Tools** heading.
- **No memory (Claude Code).** Under Advanced in the wizard, for new sessions and Auto Scan. It turns off Claude Code's auto memory: Claude does not load `MEMORY.md` and does not write notes to `~/.claude/projects/<repo>/memory/`. `CLAUDE.md` files still load. The launcher sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` for terminal and chat sessions. With the toggle off, Mobaide sets nothing, so your own setting stays in control. A `0` in your `settings.json` or managed settings still wins.
- **Ultracode (Claude Code).** Under Advanced in the wizard, for new sessions and Auto Scan. It turns on Claude Code's dynamic workflows (the `ultracode` setting in a terminal, the `ultracode` option in a chat). It does not change the effort: the session runs at the effort you pick, as with `/effort` in Claude Code 2.1.284 or later.
- **Pi terminal sessions.** The session runs `pi`. "Your turn" alerts come from OSC 7501 reports (`app=pi`): Pi sends the support query and the agent answers it. Pi's voice cleanup is in voice-input.md.
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

A running session that is mid-turn shows an animated accent square and a "Working" chip in the session list and sessions panel. A muted "Background" chip shows while the main agent is parked and only passive background work runs (a background shell, a monitor, a scheduled wakeup). "Needs permission" outranks both, and they outrank "Ready". The agent reports this as the session's `activity` (`working`, `waiting` or `idle`) for Claude, Auto Scan and Junie terminal sessions (Junie: `working` or `idle`, when its hooks are on) and for Claude, OpenCode, GitHub Copilot and Antigravity chat sessions, and `working` or `idle` for the other chat sessions. Codex, Antigravity, OpenCode, GitHub Copilot, Pi, Terminal and custom terminal sessions take `working` or `idle` from the OSC 7501 status reports their program writes; a Cursor terminal session or a plain shell writes none and shows no Working state. In a Claude terminal session, interrupting a turn with Esc or Ctrl+C clears "Working" within a second, with no alert. In a Junie terminal session, Esc does the same. An Esc that only closes a menu or clears the input changes nothing.

When a Claude chat meets a usage limit (for example the 5-hour limit), it continues by itself after the limit resets. The queue shows an **Auto-continue** row with the time in your local time; tap its Cancel button to stop it. A message you send meanwhile cancels the wait and goes out at once. There is no push alert while the chat waits. The chat waits only for a reset at most 6 hours away and continues at most 3 times in a row without a message from you. It needs `claude-agent-acp` 0.107.0 or later, which sends the reset time. Wire details: the chat state key `limit_wait` in PROTOCOL.md.

In a Claude chat, a turn can end while work it started still runs. The chat header then shows "Working in background" (a workflow or a sub-agent) or "Background task" (only a background shell, a monitor or a scheduled wakeup) in place of "Ready". You can still send a message. "Ready" comes back when that work ends. Wire details: the chat state key `background` in PROTOCOL.md.

OpenCode, GitHub Copilot and Antigravity chats follow background work too, from each agent's own signal:

- **OpenCode 2.3+.** While a background subagent runs, the header shows "Working in background". Its tool calls show in the chat, with the subagent's name before the title.
- **GitHub Copilot.** While the reply is done but an attached background shell still holds the prompt, the header shows "Background task" with the elapsed time. Messages you send wait in the queue until the prompt ends.
- **All three.** When finished background work (a subagent, a shell) wakes the agent after its answer, the chat shows that turn as running. You can stop it, and messages you send wait for it. Copilot reports the end of such a turn. For OpenCode and Antigravity, the turn ends after 20 s with no new output and no tool still running. After 10 minutes with no new output it ends even with a tool still running.

Claude Code, Codex and Cursor chats show each subagent as a card in the chat: its title, state (Running, Needs you, Done, Stopped or Failed), the task it got, its tool calls, its checklist progress and its answer. The subagent's tool calls show inside its card, not between the main agent's steps, and its checklist no longer replaces the plan chip. A card starts collapsed: its title, state and one line (the newest tool call while it runs, then the first line of its answer); tap it to see the rest. Two or more subagents in a row (only thinking and tool calls between them) share one group card, for example "12 subagents · 3 running · 9 done", which starts collapsed too, so a long fan-out takes one line of the chat. In an open group the subagents still working come first, then the failed, stopped and done ones, each in launch order. A subagent that needs you (or has a subagent that needs you) is never hidden: it leaves its group and stays open until it is answered. A permission request from a subagent shows as usual, with the subagent's name before the title. **Stop** on a card stops only that subagent, and the main agent goes on; it shows for running Claude subagents and for Codex subagents while they can be interrupted, never for Cursor or for a continued session's old subagents. In a Codex chat, a subagent that still runs after the answer shows "Working in background". A Cursor turn waits for its background subagents and includes their results, so the chat reads Working until they end, and Stop ends them too. Wire details: the `subagent` item in PROTOCOL.md.

## Managed CLI agents

The agent runs Claude Code by default. It can also launch the [OpenAI Codex CLI](https://developers.openai.com/codex/cli), the [Antigravity CLI](https://antigravity.google/docs/cli) (binary `agy`), [OpenCode](https://opencode.ai/docs/), the GitHub Copilot CLI (binary `copilot`), the [Cursor CLI](https://cursor.com/docs/cli/overview) (binary `cursor-agent`), [Junie](https://junie.jetbrains.com) (binary `junie`) or [Pi](https://pi.dev) (binary `pi`) as additional managed coding agents.

### Agent kinds at a glance

| Kind | `cli` value | Options block | Executable setting | If the binary is missing |
|---|---|---|---|---|
| Claude Code | `"claude"` | | `MOBAIDE_CLAUDE_BIN` | |
| Codex CLI | `"codex"` | `codex_options` | `MOBAIDE_CODEX_BIN` | 503 with `codex_cli_missing` |
| Antigravity CLI | `"antigravity"` | `antigravity_options` | `MOBAIDE_AGY_BIN` | 503 with `antigravity_cli_missing` |
| OpenCode | `"opencode"` | `opencode_options` | `MOBAIDE_OPENCODE_BIN` | 503 with `opencode_cli_missing` |
| GitHub Copilot | `"copilot"` | `copilot_options` | `MOBAIDE_COPILOT_BIN` | 503 with `copilot_cli_missing` |
| Cursor | `"cursor"` | `cursor_options` | `MOBAIDE_CURSOR_BIN` | 503 with `cursor_cli_missing` |
| Junie | `"junie"` | `junie_options` | `MOBAIDE_JUNIE_BIN` | 503 with `junie_cli_missing` |
| Pi | `"pi"` | `pi_options` | `MOBAIDE_PI_BIN` | 503 with `pi_cli_missing` |
| Custom agent type | `"custom_<id>"` | | The stored launch command | |

`MOBAIDE_CODEX_BIN`, `MOBAIDE_AGY_BIN`, `MOBAIDE_OPENCODE_BIN`, `MOBAIDE_COPILOT_BIN`, `MOBAIDE_CURSOR_BIN`, `MOBAIDE_JUNIE_BIN` and `MOBAIDE_PI_BIN` all mirror `MOBAIDE_CLAUDE_BIN`. The operator uses them to point the agent at each executable. Without `MOBAIDE_COPILOT_BIN`, the agent looks in PATH, then the npm fallback folders, then (on Windows) the WinGet link. On Windows, `deploy/install.ps1` finds the `copilot` command and writes `MOBAIDE_COPILOT_BIN` (see [deployment.md](deployment.md)). On other systems, set `MOBAIDE_COPILOT_BIN` by hand if the agent cannot find the binary. Without `MOBAIDE_CURSOR_BIN`, the agent looks in PATH, then `~/.local/bin`, then (on Windows) `%LOCALAPPDATA%\cursor-agent`. On Windows, `deploy/install.ps1` finds `cursor-agent` and writes `MOBAIDE_CURSOR_BIN`; on other systems, set it by hand if the agent cannot find the binary. Without `MOBAIDE_JUNIE_BIN`, the agent looks in PATH, then `~/.local/bin/junie` (on Windows `%USERPROFILE%\.local\bin\junie.bat`), where Junie's install script puts its launcher. On Windows, `deploy/install.ps1` finds `junie` and writes `MOBAIDE_JUNIE_BIN`. Without `MOBAIDE_PI_BIN`, the agent looks in PATH, then `~/.pi/agent/bin` (Pi's own installer), then the npm fallback folders. On Windows, `deploy/install.ps1` finds `pi` and writes `MOBAIDE_PI_BIN`. Install Pi as in the [Pi quickstart](https://pi.dev/docs/latest/quickstart).

### Create-session contract

The create-session wire contract is REST `POST /api/sessions` and the relay `create_session` control message. It takes an optional `cli` discriminator and a matching options block:

| Options block | Fields |
|---|---|
| `codex_options` | model, sandbox, approval, reasoning effort, full-auto, auto review (`--approve-for-me`, Codex 0.147.0 or later), initial prompt |
| `antigravity_options` | model, skip-permissions, sandbox, add-directories, worktree/branch |
| `claude_options` | includes `code_mode` (default `false`; terminal UI only) |
| `opencode_options` | auto-approve, initial prompt (up to 32000 characters; no client sends it), worktree/branch/base branch, connect (sign in to a provider), `code_mode` (default `true`) |
| `copilot_options` | allow-all, worktree, branch name, base branch |
| `cursor_options` | force (run everything without asking), worktree, branch name, base branch |
| `junie_options` | plan mode, brave mode, worktree, branch name, base branch |
| `pi_options` | worktree, branch name, base branch, `code_mode` (default `false`) (no approval switch: Pi runs its tools without asking) |

In `antigravity_options`, skip-permissions and sandbox are mutually exclusive, and the server rejects the combination.

`ui: "chat"` with `claude_options.code_mode: true` answers 422 ("Code Mode for Claude Code works only in the terminal UI"). A Code Mode launch on a CLI that is too old answers 400 ("Code Mode needs <CLI> <min> or later. This PC has X: run `<cli> update` there, or turn Code Mode off.").

### Custom agent types

Users can define up to 20 account-scoped custom agent types (display name + launch command) through the central server's `/api/custom-agent-types` REST resource. Types sync live across the user's devices.

- **Managing them.** The create-session mode picker has one card per type, plus an "Add custom agent…" card. Long-press a custom card for Edit/Delete.
- **Launching one.** The client sets `cli` to the type's `custom_<id>` kind and never sends the command. The relay resolves the stored command server-side, and the agent spawns it via the user's login shell as a plain terminal session. The create flow keeps the "Create worktree" toggle for git workspaces.
- **Deleting one.** This soft-deletes its kind-scoped saved shortcuts in the same transaction.
- **Older agents.** Pre-v7 agents are gated with a 409 `agent_update_required` on custom launches.

The wire details are in [PROTOCOL.md](../PROTOCOL.md).

### Codex and Antigravity sign-in

**Codex (terminal sessions).** When Codex is installed but not signed in, the agent detects this before spawning. It returns a `codex_auth_required` marker (HTTP 401) instead of hanging on Codex's interactive login. A Codex chat session does not return this marker: it shows the in-chat **Sign-in needed** card instead. For a terminal session, the app then prompts for an OpenAI API key and posts it to `POST /api/codex/login`, before retrying the create. That route runs `codex login --with-api-key`, with the key fed on stdin and never persisted by the agent.

**Antigravity (terminal sessions).** There is no auth probe. An unauthenticated `agy` shows its Google Sign-In prompt in the PTY, so the operator pre-authenticates `agy` on the host before launching a session. There is no API-key env var and no in-app Antigravity login route for terminal sessions. Chat sessions sign in from the chat card.

**OpenCode.** There is no auth probe: OpenCode starts without an account and has free models. Sign in from the app with **Connect a provider** on the OpenCode options screen, or on the PC with `opencode auth login`.

### OpenCode terminal sessions

- **Wizard.** OpenCode is the sixth built-in mode, after Antigravity (default order: Claude, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie, Pi, Auto Scan, Terminal). Options: **Auto-approve permissions (dangerous)**, the worktree section and the **Open sessions in** choice. Launch options are remembered per device. The card is disabled with "Update Mobaide on your PC to use OpenCode." when the agent lists no `opencode` CLI, and with "OpenCode CLI is not installed on the agent." when the binary is missing.
- **Launch.** `opencode [--auto]` runs in the session folder. `--auto` is the **Auto-approve permissions** option: OpenCode accepts every request its rules do not deny.
- **No model picker.** The OpenCode 2 TUI has no `--model` or `--agent` flag. Pick the model and the Build or Plan agent inside the session.
- **Tabs off.** The launcher starts the TUI with `OPENCODE_CLI_CONFIG_CONTENT={"tabs":{"mode":"off"}}`, merged into your own value. Your own `tabs` key wins. OpenCode shares one tab set per folder, and its status report covers all tabs. With tabs on, a request in one session would alert every session of the project.
- **Binary lookup.** `MOBAIDE_OPENCODE_BIN`, then PATH, then `~/.opencode/bin/opencode`, npm-global, Homebrew and `~/.bun/bin`. On Windows: `%USERPROFILE%\.opencode\bin\opencode.exe`, npm shims and Bun. The Windows installer does not detect OpenCode.
- **Connect a provider.** The OpenCode options screen has a **Connect a provider** card (web: **Connect a provider…**). It starts a terminal session that runs `opencode auth login`, with no worktree and the other options ignored. When the command exits, the session ends as a normal stopped session and leaves the list. The launch options are not remembered for it.
- **Voice cleanup.** Available. It uses OpenCode's default model, because OpenCode names no cheapest one. It needs OpenCode 2.2.6 or later; an older one asks you to run `opencode upgrade`. Your `~/.claude/CLAUDE.md` is not used. Your global OpenCode config still applies: its `AGENTS.md` joins the prompt and its MCP servers start on each cleanup (up to about 3 s more). Each run is archived, so it stays out of OpenCode's own session picker and `--continue`. Details are in voice-input.md.

### On-screen control-key bar

The terminal's on-screen control-key bar is CLI-aware and sends raw PTY bytes. It is Android/iOS only, together with the chord picker and saved shortcuts. The web client removed them because a desktop has a physical keyboard, so every chord types directly into the terminal instead (see [docs/web-client.md](web-client.md)).

| Session kind | Keys | Layout |
|---|---|---|
| Claude, shell, auto-scan | 14 | Mode `Shift+Tab`, Rewind `Esc Esc`, Esc, Ctrl+C, Enter, Tab, Backspace `DEL 0x7f`, arrow keys, `Ctrl+O`, `Ctrl+R`, `Ctrl+L` |
| Codex | 16 | The same bar, with the approval answers Approve `y` / Reject `n` prepended |
| OpenCode | 11 | Mode `Shift+Tab` (cycles the agent), `Ctrl+P` (command palette), then the neutral set: Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys |
| Antigravity | 15 | `Ctrl+K`, `Ctrl+G`, `Ctrl+D`, then Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys, `Ctrl+O`, `Ctrl+R`, and `Ctrl+L` |
| GitHub Copilot | 14 | The same as Claude Code: Mode `Shift+Tab`, Rewind `Esc Esc`, Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys, `Ctrl+O`, `Ctrl+R`, `Ctrl+L` |
| Cursor | 15 | Mode `Shift+Tab` (Agent / Plan / Ask), the approval answers Approve `y` / Reject `n`, then Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys, `Ctrl+O`, `Ctrl+R`, `Ctrl+L`; no Rewind |
| Junie | 13 | Mode `Shift+Tab` (toggles Plan), `Ctrl+P` (opens the plan), then Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys, `Ctrl+O`, `Ctrl+R`. No approval answers (Junie asks with a list), no Rewind and no `Ctrl+L`. |
| Pi | 13 | `Shift+Tab` Thinking (cycles the thinking level), `Ctrl+P` (next model), `Ctrl+L` (model list), `Ctrl+O` (expand tool output), then the neutral set: Esc, Ctrl+C, Enter, Tab, Backspace, arrow keys. No Rewind and no `Ctrl+R`: two taps on Esc open Pi's session tree. |
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

The third tile on the create-session screen opens a raw shell anchored to the selected workspace's cwd. Session cards show the `Terminal` kind label.

| Platform | Shell |
|---|---|
| Windows | `cmd.exe` |
| Linux/Unix | The user's login shell: resolved `$SHELL`, falling back to the account's password-database shell, then `/bin/sh` |

For stored git workspaces, tapping the tile first shows a "Terminal session" config page with a "Create worktree" toggle and an optional branch name, mirroring the Claude Standard config page. Non-git workspaces and custom paths skip the config page and start the shell immediately.

## Worktree Base Branch

When "Create worktree" is on for a git workspace, the config page shows a "Base branch" picker. The new worktree branch starts from the selected branch. This works for Claude, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie, Pi and Terminal sessions.

- **List.** The picker lists the local branches and the remote-tracking branches. Before it lists, the agent runs a best-effort fetch of all remotes, so new remote branches appear and deleted ones disappear. The fetch has a 10 s limit, never asks for a sign-in, and is reused for 30 s. Offline, the picker shows the last fetched branches. It has a search field. The default branch is first, then the newest branches.
- **Default.** The agent picks `origin/HEAD` first, then `origin/main`, then the local `main`.
- **Memory.** The agent remembers the last base branch for each workspace. Every client and device preselects it the next time. A pick of "Default branch" clears it. If that branch is gone, the picker selects the default.
- **Fresh start.** For a remote base, the agent fetches that remote before it creates the worktree. The fetch has a 10 s time limit and never asks for a sign-in. If the remote does not answer in time, the worktree starts from the last fetched state, and the next minute of creates in that workspace skips the fetch.
- **Older agents.** Clients hide the picker without `capabilities.git.worktree_base`.
- **Location.** New worktrees go to `<MOBAIDE_WORKTREES_DIR>/<workspace folder name>/<slug>`, outside the workspace, so no `.gitignore` entry is needed. The default is `~/.mobaide/worktrees` (`%USERPROFILE%\.mobaide\worktrees` on Windows). `~` and environment variables are expanded, and a relative path starts at the home folder. A workspace sub-folder holds a `.mobaide-project` file that names its repo; a second repo with the same folder name gets `<name>-<8 hex>`. A linked worktree that already exists at `<workspace>/<WORKTREE_BASE>/<slug>` (default `mobaide_worktree`) is still reopened, but new ones never go there. Uninstall does not delete the worktrees folder. Chats for GitHub Copilot and Cursor ask for folder trust once for each new worktree, because trust on the workspace folder does not cover a folder outside it.

Wire details are in PROTOCOL.md, *Delta 2026-11-09: worktree base branch*.

## Custom Path Sessions

A "Custom path…" entry in the workspace picker launches a one-off Claude or Terminal session in any folder on the agent machine, without adding it to `projects.json`. The agent validates the path (absolute, exists, is-directory) and returns a clean 400 on bad input.

The last 5 per-agent paths are offered again, and each can be forgotten. A path is recorded only after the session is actually created. The list is device-local, and it is cleared on sign-out and account deletion, since the paths name the user's own workstation.

## Reorderable New-Session Lists

In the new-session wizard, users can drag workspaces and agent modes into the order they want. For example, the most-used workspace or agent goes to the top.

| List | Where the order is saved | Scope |
|---|---|---|
| Workspaces | The agent's `projects.json`, through `PATCH /api/projects/order` | Per agent. It is the same order as on the Workspaces screen. |
| Agent modes (built-in and custom) | The central server, through `PUT /api/session-mode-order` | Per account. |

- **Sync.** Every client shows the saved order, also in later sessions. A change reaches the other open clients live: `projects.changed` for workspaces, `session_mode_order.updated` for modes.
- **Cost.** One request per drop, never per drag step. Saves of one list run one at a time, in drop order. A drop that changes nothing sends nothing.
- **Fixed rows.** "Custom path…", "Create new workspace…" and "Add custom agent…" stay at the bottom.
- **New items.** A custom agent created after the last reorder goes below the ordered ones.
- **Failure.** If a save fails, the list goes back to the saved order and a "Reorder failed — reverted." message shows.

| Platform | How to reorder |
|---|---|
| Android | Drag the handle left of a card. The list scrolls near its edges. TalkBack offers "Move up" / "Move down" actions on each card. |
| iOS | Tap **Reorder**, drag the ≡ handles, then tap **Done**. |
| Web | Drag the ⠿ handle with a mouse, finger or pen. Or focus the handle and press ArrowUp / ArrowDown. |

The wire details are in [PROTOCOL.md](../PROTOCOL.md).

## Auto Scan Mode

Auto Scan watches an issue source for matching issues. It creates a git worktree for each issue and launches an AI agent session to work on it. The wire contract is in [PROTOCOL.md](../PROTOCOL.md) ("Delta 2026-11-18: Auto Scan sources and agents").

- **Issue source.** The wizard is four numbered sections: 1 Where to find issues, 2 Which issues, 3 AI agent, 4 Instructions and schedule. A "How Auto Scan works" callout and a Templates card sit above them.
- **GitHub.** The scan reads the open issues of the workspace's own GitHub repository. An issue matches when it has any trigger label (case-insensitive). Starting work removes the trigger labels and adds an optional "label to add". The label to add must not be one of the trigger labels: the agent rejects such a filter with `422`, and the clients block Start. A closed issue counts as done, and its worktree is removed without `--force`. The wizard says that each issue runs in its own git worktree, and that the worktree is removed when the issue is closed (GitHub) or reaches Done (Linear).
- **How issues are picked up.** The wizard shows tips: use a dedicated label such as `auto-scan`, avoid common labels, keep the label out of issue templates and workflows, and use one label per kind of work with a template for each. **Hide tips** / **Show tips** toggles them, and the choice is remembered per device.
- **Issue types.** A GitHub filter can also match issue types (Bug, Task, Feature and so on). The agent lists the types of the repository with the labels.
- **Templates.** The wizard saves the Auto Scan settings as named templates and applies them later. The agent stores them in `autoscan_templates.json` (up to 50, shared by all workspaces).
- **GitHub CLI.** The wizard shows the `gh` status. **Install GitHub CLI** and **Sign in to GitHub** open a setup terminal on top of the wizard. The agent runs the installer (brew, winget, apt-get, dnf, pacman or zypper) or `gh auth login`.
- **Linear.** The scan uses the workspace's Linear API key. The wizard has a **Save key** action and a Linear filter.
- **Filters.** Filters start empty, with no built-in defaults. The last choices are remembered per workspace on the agent (`auto_scan` in the workspace record).
- **AI agent.** The user picks the agent that runs each issue: Claude Code, Codex, Antigravity, OpenCode, GitHub Copilot, Cursor, Junie or Pi. Before the first claim the agent checks that the CLI is installed, and that Codex is signed in.
- **Time between scans.** Step 4 sets the scan interval, from 1 minute to 24 hours (default 5 minutes). It is saved per workspace and in templates.
- **Open sessions in.** **Chat** (the default) or **Terminal**. Chat opens each issue's session in the chat view, with the issue as the first message. It is offered for agents listed in `capabilities.chat`.
- **Worktree trust.** Auto Scan trusts each worktree it creates, so agents do not ask to trust the folder. Chat sessions skip the question. Before a terminal or chat session starts, the scan writes the agent's own trust record: Claude Code (`~/.claude.json`), Codex (`config.toml`, for that worktree only), Antigravity (`settings.json`), Copilot (`config.json`), Cursor (marker file) and Pi (`trust.json`). Mobaide cannot write Junie's records, which are keychain-signed, but Junie trusts a worktree whose main checkout it trusts. A non-git workspace gets no record, and a chat there still asks.
- **Initial prompt.** The first prompt is the instruction plus the issue title, URL and description.
- **Remembered choices.** The wizard remembers the last-used launch options on-device, in a bucket separate from the Claude Code sheet.
- **Permissions.** Claude Code has its own options, with Skip permissions as a live toggle (default ON). Codex reasoning effort offers **Agent default**. Codex also offers **Auto review** (when the PC agent advertises it): a Codex reviewer approves safe requests, so the scan keeps the workspace sandbox, but a denial can still stop it for a person. Each agent shows the same options as its normal New session screen, without the worktree checkbox, because every issue gets a worktree and the session starts at once. The options are remembered per workspace on the agent (`auto_scan.<kind>_options`). Where the permission option is switched OFF, the scan pauses at permission prompts and raises the normal attention notification.
- **Code Mode.** The Auto Scan wizard shows the **Code Mode** toggle for Claude Code, OpenCode and Pi, with the same defaults as a new session. It is saved per workspace and in templates (`code_mode` in `claude_options`, `opencode_options` and `pi_options`). For Claude Code it sits under Advanced and works like the new-session wizard: it shows only with **Terminal** in **Open sessions in**, and a chat scan starts without it. The agent ignores Claude Code Mode in a chat scan. A start with Code Mode on and a Claude Code or Pi that is too old fails with the version message before any issue is claimed.
- **Card and detail.** In the New session list, the Auto Scan mode has a radar icon and the Terminal mode a terminal icon. A running scan's card and detail screen show "Source · Agent", plus "· Chat" when its sessions open in the chat view. The detail screen counts down to the next scan, and a failed scan cycle also sets the new time. After Stop, or when the scan is no longer running on the PC, the detail screen shows "Stopped" and hides the countdown, **Run now** and **Stop**. If the same workspace is scanned again from another place while the detail screen is open, the screen starts over on the new run: it clears the log, phases and issues, and shows the new run live.
- **Older agents.** An agent without `capabilities.autoscan` keeps the Linear and Claude panel, with an "Update Mobaide on your PC" note. The workspace form shows the Auto Scan filter section only for those agents.

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

## Keep the Computer Awake

An opt-in setting per agent stops the agent's computer from going to sleep while it is idle, so its sessions stay reachable from a phone. It is mostly useful on a laptop. The default is off.

- **Where.** Android and iOS: **Settings → Keep awake**, for the connected agent. With no agent selected, the section stays visible and tells you to connect to one. Web: **Agents → Settings** on an online agent.
- **Storage.** The agent stores the choice in `agent_settings.json` in its state folder, so every client of the agent sees one setting. A change on one device shows on the others at once (`agent_settings.changed`).
- **No restart.** The agent applies a change before it answers. After an agent restart (for example, an update), it applies the stored choice again as its first step. During an update restart, the old process keeps the computer awake until the new process holds its own lock: for 2 minutes on macOS and Linux, and for one full sleep timeout on Windows. On Linux this needs a signed-in user.
- **Limits.** The screen can still turn off, except on a Windows laptop with Modern Standby on battery (see below). Closing a laptop lid still puts it to sleep on every OS.
- **Windows laptops with Modern Standby.** On battery, Windows ends the normal power request 5 minutes after the sleep timeout. Only a request that keeps the screen on stops this. So while such a laptop runs on battery, the agent also keeps the screen on, and the screen does not lock by itself. On mains power the screen turns off as usual. The agent checks the power source every 30 seconds. The clients show a note about this under the checkbox.
- **Per OS.**

  | OS | How | Check it |
  |---|---|---|
  | macOS | IOKit `PreventUserIdleSystemSleep` assertion | `pmset -g assertions` |
  | Windows | `PowerRequestSystemRequired` power request, plus `PowerRequestDisplayRequired` on battery on a Modern Standby laptop | `powercfg /requests` (as admin) |
  | Linux | `systemd-inhibit` (`sleep`, else `idle`), plus `gnome-session-inhibit --inhibit suspend` on the user's session bus | `systemd-inhibit --list` |

  The OS releases each lock when the agent process ends, also after a crash.
- **Linux details.** The agent runs as a system service. Before anyone signs in to the desktop, polkit gives it only logind's `idle` lock. The automatic suspend of GNOME and KDE ignores that lock, and KDE reads it as "keep the screen on". After a desktop sign-in, polkit usually allows logind's `sleep` lock, which blocks suspend on every desktop. So within a minute of a sign-in the agent swaps `idle` for `sleep`, and the screen can lock again. On GNOME it also takes a GNOME session lock. It tries a missing lock again every minute. After a log-out and a new sign-in, it takes the GNOME lock again in the new session within a minute. With nobody signed in since boot, the login screen can still suspend the computer.
- **Status.** When the agent cannot keep the computer awake, the clients show why under the checkbox. When the agent has a note on how it keeps the computer awake (the Windows screen case above), they show it there as plain text. An agent inside WSL reports `unsupported`: Windows decides when that computer sleeps, so turn the setting on for an agent installed on Windows itself.
- **Older agents.** An agent without `capabilities.agent_settings.keep_awake` shows "runs a Mobaide agent without this setting".

## Git Management

An in-app screen, reachable from Edit Workspace on git-backed workspaces and from a session's ⋯ → Git → Branches…

- **Browse.** Local branches, worktrees and remote-tracking branches appear as an alphabetized folder tree, with each tip commit's subject and date.
- **Delete.** Multi-select and batch delete, with undo and force-delete confirmation. One force confirmation removes both the worktree and its unmerged branch. With `capabilities.git.delete_jobs`, a batch runs as a background job that the client polls every second ("Deleting N item(s)…", then "Deleting… c of t done"). The client allows one batch at a time per screen. It reloads the list after every batch, because a reconnect can drop the agent's `git.changed`. It also stops waiting on a 404, or after 2 minutes without a successful poll, and then reloads the list. Deleting on the remote too needs a confirmation and `capabilities.git.delete_remote`.
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

- An in-terminal picker bottom-sheet (recents / favorites / all sections, with search) inserts a template into the input bar. On Android and iOS, chat and terminal composers insert it at the cursor (replacing a selection, keeping the rest of the draft). iOS 17 inserts at the end.
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

The apps raise a notification whenever an agent session needs attention. This covers chat sessions and every terminal session kind. Tapping it opens the relevant session.

On Android and iOS, a chat permission alert has **Allow** and **Deny** buttons when one plain tool request is open. A form, the folder trust, a plan approval or a second open request gets no buttons. The buttons answer in the background. Allow needs an unlocked phone. Deny also works on the lock screen, and a paired Apple Watch shows the iOS buttons. The local alert names the kind of action and shows the request title. APNs pushes name only the kind of action, never the title. If an answer fails, the alert becomes "Could not answer" with the reason. The web alert shows the title but has no buttons, because a browser cannot add them.

| Reason | When |
|---|---|
| `awaiting_permission` | A permission prompt is waiting |
| `awaiting_os_permission` | An OS file-access prompt is blocking on the Mac: "Action needed on your Mac" |
| `turn_finished` | The agent finished its turn |
| `turn_failed` | The turn ended in an error: "Turn failed", chip "Failed" |
| `ended` | The session ended |

- **Source.** One source per session, so nothing alerts twice.
  - Claude and Auto Scan terminal sessions: Claude Code hooks post to the agent's local `POST /internal/hook` endpoint.
  - Claude chat sessions: the same Claude Code hooks, passed through the chat adapter.
    - "Ready" comes 2 s after the turn ends, and only when no background work is left (a workflow, a sub-agent, a shell).
    - A turn that Claude starts by itself (after a background task ends) shows as running in the chat. You can stop it, and **Send now** puts a queued message into it.
    - Without hooks (an old CLI, a policy that blocks them), the turn result raises the reason, as for the other chat sessions.
  - OpenCode, GitHub Copilot and Antigravity chat sessions: the turn result raises the reason, and a turn the agent starts by itself after a background task ends raises `turn_finished` at its own end. In OpenCode, `turn_finished` waits until no background subagent runs: the parent wakes when they end, and that turn alerts. Stopping such a turn raises nothing.
  - Other chat sessions: the agent raises the reason from the turn result. An RPC error on your own prompt gives `turn_failed`.
  - Codex, Antigravity, OpenCode, GitHub Copilot, Pi, Terminal and custom terminal sessions: the CLI writes OSC 7501 program-status reports. The agent maps them to reasons after a 1.5 s quiet window, and one session alerts at most every 5 s.
  - Cursor terminal sessions raise no "your turn" alert and show no activity state, because Cursor's terminal UI does not report its state. Cursor chat sessions raise the alerts.
  - Junie terminal sessions: Junie CLI command hooks run a small script that posts to the same `POST /internal/hook` endpoint, through the same attention gate as Claude.
    - The agent passes a per-session hook file with `--config-location`. Junie adds it to your own `~/.junie/config.json`, so your hooks and MCP servers still load. The file holds no token; the script reads it from the session's environment.
    - Every hook is `async`, so Junie never waits for it and still shows its own approval dialog. A sync `PermissionRequest` hook that exits 0 would approve the action with no dialog.
    - A question form from Junie (its `AskUserQuestion` tool) raises `awaiting_permission`. A turn that ends on an LLM or API error (`StopFailure`) raises `turn_failed`. Esc that interrupts a turn sets the activity to `idle` with no alert, from the `CancelAgentEvent` line in Junie's session `events.jsonl`.
    - No `SessionEnd` hook: Junie fires it on `/restart` too, and then relaunches in the same terminal with the same hook file. The agent deletes the file when the session goes.
    - A Junie whose `--help` does not list `--config-location` starts with no hooks, and so does every Junie session on Windows. A negative answer is checked again after 5 minutes, because a Junie update does not change the `junie` path. Junie chat sessions raise the alerts from the turn result.
  - OpenCode 2 reports only `idle`, `working` and `blocked`. A `working` to `idle` change is the turn end (`turn_finished` after the quiet window), unless client input arrived within 2 s before it (an interrupt). `blocked` alerts (`awaiting_permission`) only after it stands 0.5 s, because with `--auto` OpenCode reports `blocked` for about 10 ms per request. OpenCode raises no `turn_failed`. OpenCode 1.x reports nothing, so it has no terminal alerts.
  - `awaiting_os_permission` comes from the agent's own startup-stall watchdog.
- **Notification label.** With no title and no workspace, the label names the session kind, for example "Codex session". APNs pushes show "Agent session" for custom kinds.
- **Known limits.** Claude terminal sessions do not raise `turn_failed`. OSC 7501 on Windows ConPTY is unverified. Older clients show nothing for `turn_failed`. Junie terminal alerts are off on Windows until a real Windows run is checked, and Junie labels its hooks Early Access.
- **Shared Codex thread.** A Codex chat and a Codex terminal session in the same folder can see the same turn. They alert once within 6 s.
- **One alert per stop.** An attention gate on the agent holds back `Stop` while background tasks are still running, so you get one alert rather than one per subagent.
- **Android.** A foreground service holds the events socket. No FCM or external push service is required.
- **iOS.** The central server sends APNs pushes to every registered device that has no live events WebSocket of its own (see [docs/ios-client.md](ios-client.md)).

## Help & Support

An in-app screen, reachable from the Login screen and from Settings, even when signed out. It offers "Ask the community" (the project's public GitHub Discussions) and "Email support" (a prefilled `mailto:` with app version, device model, anonymous device id, and the signed-in email when available).

On Android, the destinations are the build-time values `SUPPORT_GITHUB_URL` and `SUPPORT_EMAIL`.
