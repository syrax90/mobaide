# Wire-protocol versions

The Mobaide system uses a single **integer** ``protocol_version`` as the authoritative compatibility signal between the Android app, ``mobaide_agent``, and ``central_server``. The three human-facing semvers (``android-vX.Y.Z``, ``agent-vX.Y.Z``, ``server-vX.Y.Z``) are independent and ship on their own cadence; they do **not** gate compatibility.

**Naming note.** The product calls a named launch folder a **workspace**. The wire and storage names still say "project" for compatibility: the ``/api/projects…`` routes, ``projects.changed``, JSON keys such as ``project_name``, and the agent file ``projects.json``.

On this page:

* [proto-v7 (current)](#proto-v7-current)
* [proto-v6 (superseded by proto-v7)](#proto-v6-superseded-by-proto-v7)
* [proto-v5 (superseded by proto-v6)](#proto-v5-superseded-by-proto-v6)
* [proto-v4 (superseded by proto-v5)](#proto-v4-superseded-by-proto-v5)
* [proto-v3 (superseded by proto-v4)](#proto-v3-superseded-by-proto-v4)
* [proto-v2 (superseded by proto-v3)](#proto-v2-superseded-by-proto-v3)
* [Application-level WebSocket close codes](#application-level-websocket-close-codes)
* [proto-v1 (superseded by proto-v2)](#proto-v1-superseded-by-proto-v2)
* [How to bump](#how-to-bump)

## proto-v7 (current)

### Version constants and rollout order

Introduced: 2026-07-27 (CLA-153).

| Component | Setting |
|-----------|---------|
| Python support: both ``mobaide_agent`` and ``central_server`` | ``PROTOCOL_VERSION = 7``, ``SUPPORTED_PROTOCOL_VERSIONS = (6, 7)``, ``MIN_CLIENT_PROTOCOL_VERSION = 6`` |
| Android | ``APP_PROTOCOL_VERSION = 7`` — Android advanced to ``7`` with its client half (CLA-154) |
| iOS | ``APP_PROTOCOL_VERSION = 7`` — iOS advanced with its half (CLA-155) |

Old mobile builds remain accepted meanwhile.

> [!WARNING]
> **Deployment order matters**: a proto-v7 agent handshaking against a proto-v6-only relay is closed with ``4002``, so the central server must deploy before any agent rollout.
> Old (v6) agents keep connecting — the relay supports both versions — but cannot launch custom agent types (see the ``409`` gate below).

### Custom agent types overview

Change vs proto-v6: account-scoped **custom AI agent types** — a name + launch command registry on the central server, launched as plain terminal sessions with an **open session kind**.

The version is bumped because the kind vocabulary stops being a closed enum: clients must **preserve unknown ``kind`` strings verbatim** instead of coercing them to ``claude``, and a detectable version boundary is needed so the relay can refuse custom launches against old agents that would silently ignore the new fields and open a bare shell.

### Open session kind

**Open session kind**: ``kind`` (in ``session_to_wire_dict`` and every session list/event envelope) may now be the raw string ``custom_<12hex>`` (pattern ``^custom_[0-9a-f]{12}$``; the 12-hex id is the registry row id).

Built-in kinds are unchanged. The session wire dict also gains nullable ``kind_label`` — a snapshot of the type's display name taken at session creation, so lists render correctly after the type is renamed or deleted (null for built-in kinds).

### Custom-type session create

**Session create** (``POST /api/sessions`` proxied over ``CH_CONTROL``, and the equivalent ``create_session`` control message): the **existing ``cli`` discriminator** carries the custom kind — ``cli: "custom_<12hex>"``. Clients never send the command; the client-supplied ``command``/``env`` hints stay discarded exactly as on the Terminal path.

* The relay **strips** ``resolved_command`` / ``resolved_name`` from every client body on this route, then — for a valid custom ``cli`` — looks the id up in the caller's registry and **injects** the stored command + display name into the forwarded body.

  These two fields are therefore server-only: on the agent side a non-null value is always relay-authored (mobile clients have no path that reaches the agent's control channel with arbitrary JSON).

* Relay-side failures answer without forwarding: unknown/deleted id → ``404``; malformed ``custom_*`` id → ``400``; **live tunnel's** negotiated ``protocol_version`` ``< 7`` (or unset) → ``409`` with body ``{"agent_update_required": true, "detail": ...}``.

  Agent **offline** → the standard proxy ``503`` (the version gate applies only to a live tunnel — the relay never answers ``agent_update_required`` from a persisted handshake row, which overlapping tunnels can leave describing a different connection).

* Agent-side: a custom ``cli`` without ``resolved_command`` (agent-direct REST, or a pre-v7 relay) fails cleanly — ``400`` on the REST/proxy paths, ``session_create_failed`` on the control path — never a silent bare-shell downgrade. The agent-direct REST API always rejects custom kinds (``400``): it has no registry access.

* Spawn contract: POSIX ``[login_shell, "-l", "-c", command]`` with the stored command as **one argv element** (no interpolation, no quoting layer); Windows ``["cmd.exe", "/c", command]``. The login shell resolves ``$SHELL`` → ``pw_shell`` → ``/bin/sh``, falling back to ``/bin/sh`` when the basename isn't a known POSIX shell (bash / zsh / sh / dash / ksh).

  Plain Terminal sessions use the same resolver. Custom sessions are plain-terminal semantics end to end: the ``worktree`` / ``branch_name`` toggle works unchanged, and there is no managed-CLI machinery (no options block, no attention hooks, no autoscan).

### Custom-type REST registry

The registry lives on the central server. It takes the dashboard/user JWT, non-admin.

| Route | Body | Result and notes |
|-------|------|------------------|
| ``GET /api/custom-agent-types`` | none | List. |
| ``POST /api/custom-agent-types`` | ``{"name", "command"}`` | ``201``. |
| ``PATCH /api/custom-agent-types/{id}`` | ``{"name"? , "command"?}`` | At least one field; provided-but-blank → ``422``; there is **no** empty-string-clears form — both attributes are mandatory. |
| ``DELETE /api/custom-agent-types/{id}`` | none | ``204`` (soft-delete). |

Validation:

* ``name``: 1–60 chars trimmed, unique per account case-insensitively. Only ASCII case folding applies (SQLite ``NOCASE``); non-ASCII characters compare byte-wise, so ``Ågent`` and ``ågent`` are distinct names.
* ``command``: 1–1000 chars trimmed.
* An embedded NUL byte in either field → ``422``.
* The per-account cap is **20** live types, enforced atomically → ``409``.
* A name conflict → ``409`` (distinct ``detail`` strings).
* Ownership violations and repeat deletes → ``403`` (same non-idempotent-DELETE caveat as shortcuts).

The row shape is ``{"id", "name", "command", "created_at", "updated_at"}``. ``command`` is returned to its owner (the editor needs it); only session-create requests never carry it.

### Custom-type user events

**New user-event kinds** (server-originated, same envelope as ``template.*`` / ``shortcut.*``): ``custom_type.created`` / ``custom_type.updated`` (full row) and ``custom_type.deleted`` ``{"id"}``.

Deleting a type also soft-deletes the owner's saved shortcuts whose ``kind`` equals ``custom_<id>`` **in the same transaction, with only the one ``custom_type.deleted`` event** — no companion ``shortcut.deleted`` events; Android and iOS purge their local shortcut caches for that kind on receipt (the web client removed its shortcuts UI and ignores the cascade).

Connect-time reconcile is the usual REST refresh (``GET /api/custom-agent-types``). Per-type shortcut scoping needs no server change: ``custom_<12hex>`` already satisfies the shortcuts ``KIND_PATTERN`` and the server stores kinds opaquely.

### First-pairing account event

**First-pairing account event** (additive, **no version bump**; neither ``envelope.py`` copy changes).

New user-event kind ``account.first_agent_paired`` ``{"agent_id"}``, broadcast once per account from ``_complete_pairing`` when the ``users.first_paired_at`` compare-and-set wins, and a new REST route ``GET /api/auth/me`` → ``{"user_id", "email", "role", "created_at", "scheduled_deletion_at"?, "first_paired_at"?}`` (dashboard/user JWT, any role).

The mobile apps log their Firebase ``agent_paired`` conversion exactly once per account — live off the event, or on the next user-events connect off the ``/me`` stamp (see docs/app-conversion-measurement.md).

Older clients ignore the kind (``else -> Unit`` / ``default: .none`` / ``{kind: 'ignored'}``); the web client mirrors both shapes and invalidates its agent list on the event.

### Agent self-uninstall

**Agent self-uninstall** (additive, **no version bump**; 2026-10-14). Removing an agent can now also make it uninstall itself from its host.

* Agent → relay ``auth`` carries ``capabilities: {"uninstall": true}`` when the build can do it (an installed release, or ``MOBAIDE_ENV=test``, which deletes nothing). The relay stores it as ``agents.supports_uninstall`` (rewritten on every handshake) and ``GET /api/agents`` returns it as ``supports_uninstall`` (default ``false``); clients offer the option only when it is ``true``.

* ``DELETE /api/agents/{agent_id}?uninstall=true`` (still ``204``) records an uninstall request — ``agent_uninstall_requests``, keyed by agent id + the credential hash, 30-day TTL, removed by the periodic token sweep — before the usual cascade delete.

* Live agent: the relay writes a ``CH_CONTROL`` ``{"type": "agent_uninstall"}`` envelope straight on the socket (not through the send queue, which the close tears down) and then closes ``4010`` with reason ``agent_uninstalled``. Paired mobiles still get ``4010`` ``admin_revoked``.

* Offline agent: when it next authenticates with the removed agent's credentials and a live request matches, the relay replies to ``auth`` with the text frame ``{"type": "agent_uninstall"}`` instead of ``auth_ok`` and closes ``4001`` ``Agent removed``. Delivery is idempotent, so the request stays until it expires.

* Older agents ignore the unknown control type and treat a non-``auth_ok`` reply as an ordinary auth failure, exactly as before.
* The agent side (disarm credentials, unregister the supervisor, delete the install, exit 0) is described in docs/deployment.md *Uninstalling the agent*.

### Protocol fixtures and the web client

**Since CLA-191 (WEB-04) the web client is a second consumer**: ``web/src/protocol/`` reads the same JSON off disk under Vitest, and ``protocol-integrity`` runs that suite on every PR.

A fixture edit must therefore satisfy both arms — and note that Swift's ``Codable`` drops unknown keys on re-encode while the web client's ``z.looseObject`` preserves them, so an unknown-extra-key case added to a shared fixture would fail iOS. Web coverage is deliberately partial and declared in ``web/src/protocol/__fixtures__/coverage.ts``, which fails if a new fixture file is neither asserted nor assigned an owner.

### Delta 2026-11-27: answer a chat permission from the notification

Additive delta within proto-v7 (2026-11-27): **one session field, one agent route, two user-event fields and one APNs category. No version bump.**

| Where | Change |
|-------|--------|
| Session wire dict `attention_request` | `{id, title, tool_kind, choices}` or `null`. Set only on a chat session whose `attention` is `awaiting_permission`, while exactly one request is open (no sign-in request either) and that request: is a `permission` request with a `tool_call_id`; is not a decision (Cursor's plan card) and not a mode switch (tool kind `switch_mode`, such as Claude's plan approval); has a standard ACP kind on every option the agent sent; and has exactly one `allow_once` and one `reject_once` option. `title` is one line, at most 120 characters. `tool_kind` is the ACP tool kind. `choices` is `["allow_once", "reject_once"]`. |
| `POST /api/sessions/{session_id}/chat/respond` | Body `{request_id, choice}`; `choice` is `allow_once` or `reject_once`. Answers the request with its single option of that kind, as the `respond` command does (the first answer wins). `200 {answered: true}`; `404` no such chat session; `409` the request is not pending (answered, cancelled or an old id); `422` an invalid body, or a request a notification cannot answer (a form, the folder trust, a plan decision, or options that break the rules above). It answers by id, so it also works while a second request is open. Mobile clients reach it through `/proxy/{agent_id}/…`; the relay has no new route. |
| Slim `session.attention` user event | Optional `request_id` and `request_tool_kind`, copied from `attention_request`. **Never the request title**: it can hold the command, and the APNs alert is built from this payload. The server relays `request_id` only when it matches `[A-Za-z0-9._:-]{1,64}` and `request_tool_kind` only when it matches `[a-z_]{1,32}`. |
| APNs payload | For an `awaiting_permission` event with a `request_id`: `aps.category = "CHAT_PERMISSION"` and a custom `request_id` key. The body names the kind of action: `<label> wants to run a command` (`execute`), `wants to edit files` (`edit`), `wants to delete files`, `wants to move files`, `wants to read files`, `wants to search files`, `wants to fetch from the web` (`fetch`). Any other kind keeps `<label> is waiting for your approval`. Without a `request_id` the payload is unchanged. |
| Chat request ids | `r:<n>:<6 hex>`. The random part makes sure that an old notification action never names a later request, also after the agent restarts. Clients treat ids as opaque. |

* Attention: while the session stays `awaiting_permission`, a change of the request that `attention_request` names raises `session.attention` again. The new alert replaces the old one (the collapse key is the session id), so a second request gets its own alert after the first one is answered. A change to `null` (a second request opened) publishes only `session.updated`. Clients keep an alert they already show: its actions still answer the request it named, by id (`409` once that request is gone).
* Android and iOS add **Allow** and **Deny** to the alert when `attention_request` is set and its `choices` hold both values. The local alert body is the kind phrase above, then the request title on a second line. Allow needs an unlocked device: iOS `.authenticationRequired`; Android sets `setAuthenticationRequired` (12+), and its receiver also refuses Allow while the device is locked on every version (the flag holds only for a tap in the shade) and posts the alert again with "Unlock your phone first, then tap Allow." On Android 12+ the receiver first waits up to 2 s for the keyguard to go away, so Allow from the lock screen works after the system unlock. An Android alert from an agent that is no longer the connected one answers nothing and says so. Deny works on the lock screen and on a paired Apple Watch. Both answer in the background. On success the alert goes away; on failure the app replaces it with "Could not answer" and the reason ("The request was already answered" for `409`). Neither removes nor replaces a newer alert of the same session (the next request's). iOS registers the `CHAT_PERMISSION` category at launch; for an APNs alert from another agent of the account it mints a token for that agent with `POST /api/agents/{agent_id}/token` and does not keep it.
* Web: a page `Notification` cannot carry actions (a browser constraint), so the web alert has none. Its body adds the request title when the tab's cached session row names the same request. If the row has not arrived yet, the alert waits up to 750 ms for it.
* Compatibility: an old agent sends no `attention_request`, an old server drops the two user-event fields, and an old app ignores the category. Each gives the plain alert.
* Golden fixture: `dto/agent/SessionResponse.json` case `chat_attention_request`.

### Delta 2026-11-27: suggested next prompt in chat

Additive delta within proto-v7 (2026-11-27): **one chat state key. No version bump; `central_server` is untouched.**

| Where | Change |
|-------|--------|
| State key `prompt_suggestions` | `[{text}]`, most likely first, at most 3, each `text` at most 2 000 characters. `[]` when there is none. |

* ACP: the agent advertises `clientCapabilities.session.promptSuggestions: {}` to every chat agent (unstable ACP "Prompt suggestions"). The stored-session catalog still sends `{}`. An agent that adopts it sends `session/update` `prompt_suggestions_update` with `suggestions: [{prompt: ContentBlock[]}]`; each update replaces the list. `claude-agent-acp` 0.109.0 or later sends one suggestion after a turn (not after the first turn of a session, not in plan mode, not after an API error).
* The agent keeps a suggestion only when all its blocks are text; it joins and trims them and skips a blank one. A malformed update is ignored. A non-empty list that comes while the chat starts or loads is ignored. No timeline item, no attention, no self turn (`wake.py` `CONTENT_UPDATES` does not list it), and `status` does not change.
* The agent clears the key when it dispatches a prompt, when a steer is injected, when a turn from another app or the agent's own wake starts, when the agent process starts (`restart`, the restart after a sign-in, `new_conversation`, `/clear`, `session/load`) and on an agent exit.
* The user's off switch on the PC wins: Claude Code ANDs the adapter's SDK option with `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION` or the `promptSuggestionEnabled` setting.
* Clients show the first suggestion as one chip above the composer, only while the composer has no text and no images, `status` is `ready`, no request card shows, the session is live and slash suggestions are not open. A tap fills the composer with the full text and focuses it. **It never sends**: a suggestion is model output and can carry injected text. The chip does not come back for the same text after the user typed or cleared the composer. Older clients ignore the key.

### Delta 2026-11-27: Send now in Antigravity chats

Behaviour delta within proto-v7 (2026-11-27): **no wire change, no version bump; `central_server` and the clients are untouched.**

The state key `steering` (Delta 2026-11-08) is now also `true` for an agent that steers a running turn by a second `session/prompt`, though its `initialize` has no `_meta.steering`. Only Antigravity (`agy acp`) does so. An agent that advertises `_meta.steering` keeps `_session/steering`.

* `steer` during an Antigravity turn sends the queued message as a second `session/prompt`. `agy` answers the running prompt `end_turn` at once and goes on with the same reply under the new prompt. The PC agent ignores that first answer: the reply item stays open, open requests stay open, `status` stays `running`, `turn_started_at` stays, and `turn_finished` fires once, at the real end. `data: {steered: true}`; the message leaves the queue and shows as a `user` item.
* `agy` refuses the prompt while the turn waits for a permission or a question answer. `steer` then fails with "Answer the open request first, then send the message" (or the agent's error), and the message stays in the queue. The turn goes on.
* `{steered: false}` when the turn's prompt is not sent yet, after Stop, or while another message is being handed over: the message runs next from the queue.
* `cancel` after a handover sends `session/cancel` as usual; the new prompt answers `cancelled`.

### Delta 2026-11-25: subagent cards in chat

Additive delta within proto-v7 (2026-11-25): **one chat item type, one optional tool item field, one chat command. No version bump; `central_server` is untouched.**

Claude (`claude-agent-acp`), Codex (`codex-acp`) and Cursor (`cursor-agent acp`) chats now declare `clientCapabilities.subagents: {}` in `initialize` (ACP Subagents RFD, stable in ACP v1 since 2026-11-20). The agent then reports each subagent as a child session: Claude and Codex with `subagent_update` and `session_message`, Cursor with the earlier draft `subagent_spawned` and `subagent_state_update`. The other agents' `initialize` is unchanged.

| Where | Change |
|-------|--------|
| Item type `subagent` | Id `sa:<child session id>`. Fields: `subagent_id`, `parent_subagent_id` (the subagent that started it, or `null`), `title` (never empty: the agent's title, else its role, else "Subagent"), `description` (the role, or `null` when it is the title), `task` (the prompt the parent sent, or `null`; ≤ 32 KB), `state` (`running`, `requires_action`, `idle`, `unknown`), `stop_reason` (only with `idle`: `end_turn`, `cancelled`, `error`, `max_tokens`, … or `null`), `result` (the subagent's answer, or `null`; ≤ 64 KB), `can_cancel`, `plan` (its checklist, plan entries as in the state key `plan`). Upserted on each change. |
| Tool item field `subagent_id` | Optional. The subagent that owns the tool call. Its id is `<child session id>:<tool call id>` and its `title` starts with `"<subagent title>: "`. On Cursor, a tool call of the main agent that launched a subagent carries that subagent's id (its own id is not namespaced); a subagent's tool call that launched a nested subagent keeps its own subagent's id. |
| `'c'` command `cancel_subagent` | `subagent_id`. Sends `session/cancel` with the child session id: only that subagent stops, the parent's turn goes on. `data: {cancelled: true}`. Refused with "Unknown subagent", "This subagent cannot be stopped" (`can_cancel` is false) or "The agent is not running". The subagent's `idle` / `cancelled` update confirms it. |

* Clients show a `subagent` item as a card: title, state, task, the tool items with its `subagent_id` (inside the card, not in the main flow; the title prefix is cut), the checklist progress, the result, and **Stop** while `can_cancel`. State labels: `running` Running, `requires_action` Needs you, `idle` Done (`cancelled`: Stopped; `error`: Failed), `unknown` none. A card nested under another subagent (`parent_subagent_id`) is shown inside that card.
* Of a subagent's own updates, the agent keeps its tool calls, its checklist (never the chat's `plan` state: a subagent's TodoWrite no longer replaces the plan chip), its own subagents and its messages. Its message text is not shown; when no answer comes as a `session_message`, `result` is its last message (Codex) or, on Cursor, the output of the tool call that launched it (the main agent's or its parent subagent's), once it ends. The agent keeps at most 500 subagents per chat and drops the oldest idle one first.
* Requests (`session/request_permission`, `elicitation/create`) from a subagent's session get `tool_call_id` `<child id>:<tool call id>`, so the card attaches to that subagent's tool item; auto-approve applies as for the parent. A request that names an unannounced session gets `-32602`. A subagent's open requests stay open until it reports `idle`: also when the parent's turn ends or is cancelled while the subagent goes on (Claude's background subagents), as the ACP v1 subagents page requires.
* `can_cancel` is true only while the subagent runs and the agent grants `capabilities.cancel`: live Claude subagents, Codex subagents while their turn can be interrupted, never Cursor's. A replayed subagent (`session/load`, Continue) never offers Stop, and one the replay leaves running reads `unknown` at the `session/load` reply, until the agent reports its current state after it.
* `cancel` (the turn's Stop) leaves the subagents' tool calls alone; a subagent that ends as `cancelled` or `error` ends its running tool calls as `cancelled` or `failed`. Restart, New conversation, `/clear` and an agent exit end every card: `running` turns `unknown`, and Stop goes away.
* `background`: Claude keeps its hook-driven value. For Codex (no hooks), a subagent still running after the prompt's answer sets `background: "working"` until it ends. A Cursor prompt waits for its background subagents and includes their results in the same turn, so `status` stays `running` and `turn_finished` fires once, after them; Stop ends the prompt and its subagents.
* Older clients skip the `subagent` item and ignore `subagent_id`: they still show the subagent's tool calls as flat tool cards, with its title before the tool title. They no longer see Claude's Agent/Task tool call, which the adapter replaces with the subagent session.
* Claude's subagent replay after `session/load` needs `claude-agent-acp` 0.109.1 or later.

### Delta 2026-11-25: Codex Auto review

Additive delta within proto-v7 (2026-11-25, Codex Auto review — agent, web, Android and iOS): **one boolean in `codex_options` and one capability key. No version bump; `central_server` is untouched.**

| Where | Change |
|-------|--------|
| `codex_options.auto_review` | `bool`, default `false`, on `POST /api/sessions`, `POST /api/autoscan/start`, workspace `auto_scan.codex_options` and template `settings.codex_options`. Terminal: `codex --approve-for-me` in place of `--sandbox` and `--ask-for-approval` (the flag sets the workspace-write sandbox and the `on-request` policy itself, and a Codex reviewer agent answers each approval request). Chat: the codex-acp mode `agent` (Auto review). Needs Codex 0.147.0 or later. |
| `capabilities.cli.codex.auto_review` | `true` on `GET /api/version` (both transports). Clients show the **Auto review** preset only when the key is `true`. |

* `auto_review` together with `full_auto` answers `422`.
* A start with `auto_review` on a Codex older than 0.147.0 answers `400` ("Auto review needs Codex 0.147.0 or later. This PC has X: run `codex update` there, or pick another preset."), before a worktree is created. An Auto Scan start checks it before any issue is claimed. A version that cannot be read does not block the start.
* Clients send `sandbox: "workspace-write"` and `approval: "on-request"` with `auto_review: true`, so an older agent, which drops the unknown field, asks a person instead of failing or bypassing the sandbox. Old clients send no `auto_review`: nothing changes.
* On a denial, Codex takes a safer path or stops and asks the user, so an Auto Scan session with Auto review can still wait for a person. Every reviewer call uses Codex usage.
* Golden fixtures: `auto_review` in `CodexOptionsModel.json` and the `cli_feature_flags` case in `VersionInfoResponse.json`.

### Delta 2026-11-23: keep the computer awake

Additive delta within proto-v7 (2026-11-23, keep awake — agent, web, Android and iOS): **two REST routes, one capability key and one agent event. No version bump; `central_server` is untouched (it already forwards `PUT`).**

An opt-in setting per agent (default off) stops the agent's computer from going to sleep while it is idle, so its sessions stay reachable from a phone. The screen can still turn off, except on a Windows laptop with Modern Standby on battery. Closing a laptop lid still puts the laptop to sleep.

* `capabilities.agent_settings` is `{"keep_awake": true}` on `GET /api/version` (both transports). Clients show the setting only when the key is `true`.
* `GET /api/agent/settings` → `AgentSettingsResponse {"keep_awake": {"enabled", "state", "message"}}`. Every key is always present.
  * `enabled`: the stored choice.
  * `message`: `null`, or text for the user. With `active` it is a note on how the computer stays awake, which clients show under the switch as plain text, not as an error. Today only a Windows laptop with Modern Standby sends one: its screen stays on while it runs on battery.
  * `state` is an open vocabulary. A client reads a value it does not know as "not active" and shows `message`.

  | `state` | Meaning |
  |---|---|
  | `off` | The choice is off. |
  | `active` | The computer stays awake now. `message` is `null` or a note. |
  | `failed` | The choice is on, but the OS refused. `message` says why. |
  | `unsupported` | This computer has no way to stay awake, whatever the choice (for example, an agent inside WSL). `message` says why. Clients disable the switch. |

* `PUT /api/agent/settings/keep_awake` `{"enabled": bool}` (strict bool) → the full `AgentSettingsResponse`. The agent stores the choice in `<state dir>/agent_settings.json` and applies it before it answers, with no restart. A failure to apply is a `200` with `state: "failed"`, not an error. `500 storage_error` when the file cannot be written (then nothing changes); `422` validation. Both transports answer identically. The relay runs these routes off the tunnel reader.
* The agent event `agent_settings.changed` carries the full `AgentSettingsResponse`. The agent publishes it after every `PUT`, when a lock ends or comes back by itself (Linux), and as a snapshot on every events-channel connect (direct `/ws/events` and the relay's events snapshot).
* At start, the agent applies the stored choice again. It does this first, in the background, so boot does not wait for it.
* An update restart leaves a gap before the new process holds its own lock. The old process covers it, first thing in its shutdown: for 120 s on macOS and Linux, for one sleep timeout on Windows:
  * macOS: `caffeinate -i -t 120`, in a session of its own so launchd does not stop it.
  * Windows: `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` once, which resets the idle timer. While the agent holds a display request, it adds `ES_DISPLAY_REQUIRED`, so the screen also stays on for one screen timeout.
  * Linux: each held lock again, with `sleep 120` as the holder, through `systemd-run --user`. systemd stops every process of the agent's unit, so this needs the user's own service manager (a desktop login has one).
* How each OS stays awake. The OS releases each lock when the agent process ends, also after a crash:
  * macOS: an IOKit `PreventUserIdleSystemSleep` assertion (`pmset -g assertions` lists it).
  * Windows: a `PowerRequestSystemRequired` power request (`powercfg /requests` lists it). On a Modern Standby laptop (`AoAc` and a system battery in `SYSTEM_POWER_CAPABILITIES`), Windows ends that request on battery 5 minutes after the sleep timeout. So there the agent reads `GetSystemPowerStatus` at once and every 30 s, and while the laptop runs on battery it also holds `PowerRequestDisplayRequired` on the same request. When it takes it, it resets the idle timers once (`ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED`), so a screen that already turned off turns on again. A display request also stops the screen from locking by itself.
  * Linux: `systemd-inhibit --what=sleep --mode=block`. When polkit refuses `sleep` (it does for a system service outside a login session), the agent takes `--what=idle` instead. `sleep` blocks suspend on every desktop and leaves the screen lock alone. KDE reads an `idle` lock only as "keep the screen on", so its automatic suspend ignores it. polkit grants `sleep` to a process outside a login session while its user has a graphical session (the `DISPLAY=` session in `/run/systemd/users/<uid>`). So while it holds `idle`, the agent checks that file each minute, tries `sleep` once for each new graphical session and, when it gets it, drops the `idle` lock. GNOME's automatic suspend ignores logind's idle lock, so the agent also runs `gnome-session-inhibit --inhibit suspend` on the user's session bus when that bus exists. Each lock is a child process that holds a pipe from the agent, so it ends with the agent. While the setting is on, the agent tries a missing lock again every minute: a desktop login after the agent started also gets its lock. `gnome-session-inhibit` keeps running after a log-out, but its lock ended with the session. So each minute the agent also checks the session bus socket and the session manager process that the lock belongs to (its pid from `busctl`, its start time from `/proc/<pid>/stat`). When either is gone, it ends that holder and takes the lock again in the new session. Each lock process runs in a process session of its own (setsid), so polkit never asks for a password on the terminal an agent was started from. `state` is `active` when at least one lock is held.
* Golden fixtures: `AgentSettingsResponse.json` (also the event payload) and `KeepAwakeSettingsRequest.json`; the `agent_settings_capability` case in `VersionInfoResponse.json`.

### Delta 2026-11-23: activity from OSC 7501

Additive delta within proto-v7 (2026-11-23): **the session `activity` (Delta 2026-11-11) now also comes from OSC 7501 reports. No version bump; no wire shape changes; the clients are untouched.**

* Every terminal session that listens to OSC 7501 (Delta 2026-11-10: all terminal sessions except Claude Code, Auto Scan and Junie) sets `activity` from each report at once, with no quiet window: `state=working` → `working`; `idle`, `done`, `error` and `clear` → `idle`; `blocked` keeps the current value, because `awaiting_permission` already shows that the turn waits for the user.
* So Codex, Antigravity (`AGY_CLI_OSC7501=on`), OpenCode 2, GitHub Copilot and Pi terminal sessions show "Working" while a turn runs. A session whose program writes no report (Cursor, a plain shell) keeps `null`.
* Attention alerts and their timing do not change. Claude Code, Auto Scan, Junie and chat sessions do not change.

### Delta 2026-11-22: cloud voice input

Additive delta within proto-v7 (2026-11-22, cloud voice input — central server, web, Android and iOS): **six REST routes and one user event on the central server. No version bump; the agent is untouched.**

A user can dictate without a speech model on the computer. The client uploads the recording to the central server, and the central server has a hosted speech service turn it into text. The agent's on-computer voice input (delta 2026-10-14) stays, and the user chooses per agent which one transcribes.

All routes take the user token (`Authorization: Bearer`, the same token as `/api/agents`). A server from before this delta answers `404`; a client reads that as "not offered". The feature is on only when the server's configuration names a speech service (docs/deployment.md, *Cloud voice input*).

#### Cloud voice status

`GET /api/voice/cloud` → `CloudVoiceStatus {"state", "limits": {"sample_rate", "max_seconds", "max_chunk_bytes"}, "usage": {"used_seconds", "limit_seconds", "resets_at"}, "engines": {"<agent_id>": "cloud" | "local"}}`, every key always present. `usage.limit_seconds` is `0` when the server sets no daily limit for an account (the default); clients then show no daily allowance and no "Used today" row.

| `state` | Meaning |
|---|---|
| `off` | Not offered to this account: the feature is off, or the account is not in the rollout. Clients behave as before this delta. |
| `ready` | A dictation can start. |
| `unavailable` | The speech service does not answer, or the limit for all users is used up. Clients say so and offer on-computer voice. |
| `limit_reached` | The caller used today's allowance (`usage.limit_seconds`, counted per UTC day). It is back at `usage.resets_at`. |

`state` is an open vocabulary: a client reads a value it does not know as `off`. `limits` has the role of `capabilities.voice` for cloud dictations. `engines` holds the caller's choice per agent; an agent without an entry has no choice yet, and a value a client does not know reads as no choice.

When the last answer of the speech service is older than five minutes, this route asks it again (one small request, at most one at a time) and waits up to 2 s for the answer. After that it answers from the last known state.

#### Voice engine choice

`PUT /api/voice/cloud/engines/{agent_id}` `{"engine": "cloud" | "local"}` → the full `CloudVoiceStatus`. It stores where that agent's dictations are transcribed, for every device of the account. `404 agent_not_found` when the agent is not the caller's; `403 cloud_disabled` for `cloud` while cloud voice input is not offered.

A change publishes the user event `cloud_voice.changed` `{"engines": {…}}` (the whole map) on `/ws/user-events`. Clients refresh their status on it.

How a client resolves an agent:

1. `state` is `off` or unknown: on-computer voice, as before.
2. The agent has an entry in `engines`: that engine.
3. No entry, and the agent's own voice input is enabled (`VoiceStatusResponse.enabled`): on-computer voice.
4. Otherwise the first mic tap opens a chooser. Cloud is preselected there, and nothing is recorded before the user confirms.

Because cloud voice input needs nothing from the agent, the mic is also shown for an agent without `capabilities.voice`.

#### Cloud dictation

The four routes mirror the agent's dictation routes (delta 2026-10-14) body for body, so a client uploads to either with the same code:

1. **Open.** `POST /api/voice/cloud/dictations` → `{"dictation_id", "sample_rate", "max_seconds", "max_chunk_bytes"}`. An account has one open dictation: a new one replaces the previous one.
2. **Upload audio.** `POST /api/voice/cloud/dictations/{id}/audio` `{"seq", "pcm16_b64"}` → `{"seq", "duration_ms"}`. Same rules as on the agent: 16 kHz mono little-endian PCM16, `seq` from 0, one request in flight, a repeated `seq` is acknowledged without being appended again.
3. **Finish.** `POST /api/voice/cloud/dictations/{id}/finish` `{"last_seq"}` → `{"dictation_id", "state", "text", "duration_ms"}`. `state` is `done` or `transcribing` (call again; the server waits about 20 s per call for the text). A recording with no speech answers `done` with an empty `text` and is not sent to the speech service.
4. **Abort.** `DELETE /api/voice/cloud/dictations/{id}` (204, idempotent).

A failed `finish` keeps the audio on the server, so calling `finish` again tries the speech service again. The server spaces these attempts (2, 4, 8, then 16 s): a `finish` that comes sooner gets the previous refusal again. A finished result is kept for 2 minutes. An open dictation expires after 60 s without a request, or 5 minutes after it was opened.

Refusals use the agent's envelope `{"detail": {"code", "message", …}}` and never `401`:

| Status and code | When |
|---|---|
| `403 cloud_disabled` | Cloud voice input is not offered to this account. |
| `503 cloud_unavailable` | The speech service does not answer, or it refused this recording. Checked when a dictation opens (so the user learns it after a few words, not after the whole recording) and when it finishes. The client keeps the audio and offers Retry. |
| `429 cloud_limit_reached` | Today's allowance is used up; `resets_at` says when it is back. A dictation that was already open may still finish. |
| `503 voice_busy` | Too many requests at once, or more than 30 dictations in a minute. Clients retry it by themselves, with backoff, as they do for the relay's `503 voice_busy`. |
| `404 dictation_not_found`, `409 seq_gap` (`expected_seq`), `409 dictation_finished`, `409 too_long` (`max_seconds`), `413 chunk_too_large`, `422 bad_audio` | As on the agent. |

Cloud transcripts skip the transcript cleanup (delta 2026-11-03): the speech service already returns clean text. The central server keeps the audio in memory only, until the speech service has answered, and stores neither audio nor text; it stores the seconds of audio per account and day for the limits. The seconds are counted when the recording is sent, and given back only when the speech service refuses it or cannot be reached, so an aborted `finish` and a request that is not answered in time still count. `central_server/legal/privacy.md` says what the speech service keeps.

#### Cloud voice golden fixtures

`tests/protocol_fixtures/dto/central/CloudVoiceStatus.json` and `CloudVoiceChangedPayload.json`. The dictation bodies are the agent's (`VoiceDictation*.json`).

### Delta 2026-11-21: delete a stored chat session

Additive delta within proto-v7 (2026-11-21): **one route, one response field. No version bump; `central_server` is untouched (it already forwards `DELETE`).**

| Where | Change |
|-------|--------|
| `GET /api/chat/{kind}/sessions` | `ChatStoredSessionsResponse` gains `can_delete` (bool, default `false`): the agent advertises ACP `sessionCapabilities.delete`. A missing field (older agent) means `false`. |
| `DELETE /api/chat/{kind}/sessions/{session_id}` | New. Sends ACP `session/delete` and answers `204`. |

* Clients show Delete only on a page with `can_delete: true`, and only for a row without `open_session_id` and without `open_elsewhere`. They ask for confirmation first. On `204` they remove the row locally and do not reload page 1, so the cursor stays valid. On an error they show `detail` and keep the row.
* The agent picks a soft or a hard delete. Claude Code removes the transcript, also from its terminal history, and this is permanent. Codex moves the thread to its archive.
* Errors carry `{"detail"}`: `400` invalid id (same rule as `chat_resume_id`), `404` no chat agent of that kind, `409` the agent cannot delete, or a Mobaide chat has the session open, or another app on the PC has it open (presence lock, Antigravity only), `502` the agent failed, `503` it cannot run here. The agent re-checks the open Mobaide chats right before it sends `session/delete`.
* An agent answer `-32002` (not found) counts as deleted: `204`.
* Claude Code and Codex have no presence signal, so a session open in their terminal app cannot be blocked. Delete of an active session is implementation-defined in ACP. The confirm text asks the user to close it there first.
* On the relay it runs aside from the tunnel reader, like the list: its first call can start the agent process.

### Delta 2026-11-21: Claude chat continues after a usage limit

Additive delta within proto-v7 (2026-11-21): **one chat state key. No version bump; `central_server` is untouched.**

| Where | Change |
|-------|--------|
| State key `limit_wait` | `{queue_id, resets_at, continues_at, limit_type}` or `null`. Set while an automatic continue waits in the queue for a usage limit to reset. `resets_at` and `continues_at` are ISO 8601 UTC; `limit_type` is the adapter's `rateLimitType` (`five_hour`, …) or `null`. |

* Trigger: the agent answers the user's prompt with an RPC error whose `data` has `errorKind: "rate_limit"` and a number `rateLimit.resetsAt` (epoch seconds; `claude-agent-acp` 0.107.0 or later). Without that data (an older adapter, another agent) nothing changes.
* The chat puts a continue ("Continue from where you stopped. The usage limit has reset.") first in `queue` and holds the whole queue until `continues_at` (`resets_at` + 60 s). The time is checked on the wall clock every 30 s, so a PC that slept through the reset sends it on wake. Claude Code is expected to keep the refused message in the conversation, so it is not sent again (to be checked against the real adapter with the bump to 0.108.0, #897).
* No wait when the reset is more than 6 h away (a weekly limit) or after 3 automatic continues in a row without a prompt from the user. The error text from Claude Code says when the limit resets.
* `dequeue` of `queue_id` cancels the wait; the messages queued behind it then go out. A `prompt` during the wait cancels it and goes out at once. `new_conversation`, `/clear`, `restart`, the restart after a sign-in and an agent exit drop the wait. `steer` of the held continue fails ("This message waits for the usage limit to reset").
* Attention: a turn that arms a wait raises no `turn_failed`. The continued turn raises `turn_finished` or `turn_failed` as usual.
* Clients show the queued continue as an **Auto-continue** row, "Usage limit reached. Continues automatically at <local time>.", with a Cancel button that sends `dequeue`. Older clients ignore the key and show the continue as an ordinary queued message they can remove.

### Delta 2026-11-20: Code Mode in Auto Scan

Additive delta within proto-v7 (2026-11-20): **Auto Scan keeps `code_mode`, and Pi gets an options object. No version bump; `central_server` is untouched.**

| Where | Change |
|-------|--------|
| `claude_options.code_mode` | Now kept by Auto Scan (workspace `auto_scan.claude_options`, templates). Used only with `ui: "terminal"`; a chat ignores it. |
| `opencode_options.code_mode` | Now kept by Auto Scan. Default `true`, as before. |
| `pi_options` | New Auto Scan options object `{code_mode}` (default `false`) on `POST /api/autoscan/start`, workspace `auto_scan.pi_options` and template `settings.pi_options`. |

* `POST /api/autoscan/start` with Code Mode on and a Claude Code (terminal UI) or Pi that is too old answers `400` with the Code Mode version message, before any issue is claimed. A version that cannot be read does not block the start.
* Old agents ignore `pi_options` and keep `code_mode` out of the saved choices. Old clients send no `code_mode`: Claude and Pi scans run without Code Mode and OpenCode scans with it, as before.

### Delta 2026-11-20: Code Mode

Additive delta within proto-v7 (2026-11-20): **one boolean in three options blocks. No version bump; `central_server` is untouched.**

| Where | Change |
|-------|--------|
| `claude_options.code_mode` | `bool`, default `false`. Sets `CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`. Needs Claude Code 2.1.323 or later. Terminal UI only. |
| `opencode_options.code_mode` | `bool`, default `true`. `false` appends `{"action":"execute","resource":"*","effect":"deny"}` to `OPENCODE_CONFIG_CONTENT` and, in the terminal UI, runs `opencode --standalone`. OpenCode 1 is unchanged. |
| `pi_options.code_mode` | `bool`, default `false`. Adds `--tools +codemode`, in terminal sessions and in the chat bridge. Needs Pi 1.1.0 or later. |

* `ui: "chat"` with `claude_options.code_mode: true` answers `422` ("Code Mode for Claude Code works only in the terminal UI").
* A Code Mode launch on a CLI that is too old answers `400` ("Code Mode needs <CLI> <min> or later. This PC has X: run `<cli> update` there, or turn Code Mode off."). The relay proxy now maps a launcher `ValueError` to `400` (it was `500`).
* In Code Mode sessions, Mobaide's Claude hooks carry `"onFailure": "continue"`. A `0` in the user's `settings.json` or managed settings still wins.
* Clients show a **Code Mode** toggle per agent and remember it per device (launch prefs key `codeMode`). Old agents ignore the unknown field.

### Delta 2026-11-19: remove a session's worktree when it stops

Additive delta within proto-v7 (2026-11-19): **one session field, one stop query parameter. No version bump; `central_server` is untouched.**

| Where | Change |
|-------|--------|
| `SessionResponse.worktree_branch` (REST list, `sessions.changed`, `session.updated`) | `str \| null`. The local branch of the linked worktree the agent made (or reused) for a `worktree: true` create, or the one an Auto Scan session runs in. `null` when the session runs anywhere else, when the create fell back to the workspace root, or when the worktree HEAD was detached. |
| `DELETE /api/sessions/{id}?remove_worktree=true` (both transports) | After the stop, the agent removes that worktree and its local branch with `force` (uncommitted changes and unmerged commits are lost). It deletes the `worktree_branch` the client showed, even when the worktree has another branch checked out by then, so a branch the user did not confirm is never deleted. The removal runs as a background delete job (see *workspace Git delete jobs*) and publishes `git.changed`. The answer stays `204` and does not wait for the job. |

* The removal is best effort: the stop has already succeeded. When the busy guard refuses (another session works in the same worktree) or 8 jobs already run, the agent logs it and keeps the worktree. The user's confirmation covers the auto-scan item that owns the worktree, so an Auto Scan session's removal skips that check and then makes the scan stop tracking the worktree. It never removes the workspace root. For every workspace Git delete (this one and the Git screen's), a session or auto-scan item in a sub-folder of a worktree now counts as a holder too, because it loses its working directory when the worktree goes.
* Clients offer the removal in the Stop / Force Stop confirmation only when `worktree_branch` is set, as a checkbox that is never pre-checked (iOS: a sheet, because a confirmation dialog cannot host a toggle). While another session in the list has the same `cwd`, the checkbox is disabled and says why, because the busy guard would refuse the removal. Older agents never send the field, so no client offers it to them.

### Delta 2026-11-18: Auto Scan sources and agents

Additive delta within proto-v7 (2026-11-18, extended 2026-11-19 before release): **one capability, six routes, new optional fields. No version bump; `central_server` is untouched** (its proxy forwards any `/api/...` path).

Auto Scan can now scan the open issues of the workspace's **GitHub** repository (through the GitHub CLI, `gh`) as well as Linear, and run **any built-in agent** on each issue, not only Claude Code, with that agent's own options. The wizard chooses every option; the agent remembers the choices per workspace, and can save them as named **templates**.

| Where | Change |
|-------|--------|
| `capabilities.autoscan` | `{"sources": ["linear", "github"], "clis": [8 kinds], "github_setup": true}`. An agent without it scans Linear only, runs Claude Code only and has none of the routes below. |
| `POST /api/autoscan/start` | New optional fields: `source` (`linear` / `github`), `cli` (`claude`, `codex`, `antigravity`, `opencode`, `copilot`, `cursor`, `junie`, `pi`; absent = `claude`), the chosen agent's options (see **Agent options**), `ui` (`terminal` / `chat`; absent = `terminal`), `interval_minutes` (1–1440; absent = the agent's default, 5 minutes), `initial_prompt` (≤ 32,000 chars; `""` or a blank text clears the saved prompt), `linear_filter` (the `AutoScanFilter` shape), `github_filter`, `linear_api_key`. A missing filter or Linear key, or a source or agent that cannot be used (not installed, Codex not signed in, no chat view for it on this PC with `ui: "chat"`), answers `400` with a `detail` to show. |
| Workspace dict (`GET /api/projects`, `projects.changed`) | New `auto_scan`: `{"source", "cli", "ui", "interval_minutes", "linear_filter", "github_filter", "claude_options", "codex_options", "antigravity_options", "opencode_options", "copilot_options", "cursor_options", "junie_options", "pi_options"}`, each `null` until chosen. `<kind>_options` are the options of the last start with that agent. `auto_scan_filter` stays for older clients (legacy defaults while unset). |
| `AutoScanInstanceDto` | New `source` (default `linear`), `cli` (default `claude`) and `ui` (default `terminal`). `scan_interval_seconds` is the scan's own wait between cycles. |
| `GET /api/autoscan/github/status?project_name=` | `{installed, version, signed_in, account, host, repo, repo_url, problem, can_install, install_command}`. `problem` is the first blocking reason in plain words, `null` when a scan can start. `host` is the host to sign in to; it is `null` for a folder that is not a git repository or has no remote, where signing in cannot help. Clients offer **Sign in to GitHub** only when `signed_in` is false and `host` contains `github`. `404` for an unknown workspace. |
| `GET /api/autoscan/github/labels?project_name=` | `{"labels": [{"name", "color"}], "issue_types": [{"name", "color", "description"}]}` of the repository (up to 200 labels; a label whose name has a comma is left out, since `gh` cannot filter on it). `issue_types` are the repository's enabled GitHub issue types (Bug, Task, Feature…; `color` is GitHub's color name such as `RED`); empty when it has none or GitHub did not say. `400` with `detail` when `gh` cannot read the labels. |
| `GET /api/autoscan/templates` | `{"templates": [AutoScanTemplate]}`, sorted by name. Templates are saved on the PC, for every workspace. |
| `POST /api/autoscan/templates` | `{name, settings}` → `200` with every template. `name`: 1–60 characters after runs of white space become one space. A template with the same name (ignoring case) is replaced and keeps its `id`. `409` when a new name would pass 50 templates, or the list would pass 8 MiB as JSON; `422` for invalid settings. |
| `DELETE /api/autoscan/templates/{id}` | → `200` with every template left; `404` for an unknown id. |
| `POST /api/autoscan/github/setup` | `{project_name, action: "install" \| "sign_in", rows?, cols?}` → `201` with a session dict. The session is a `shell` terminal in the workspace folder that runs the installer or `gh auth login`, and ends with the command (`exits_when_done`). `400` when no installer is known (`install`) or `gh` is missing (`sign_in`); `409` at the session cap. |

**`github_filter`** is `{labels: [1–20 names], assignee: string | null, active_label: string | null, issue_types: [0–20 names]}`. Label names are trimmed, deduplicated, at most 100 characters and contain no comma (`gh` splits option values on commas). A blank `assignee` or `active_label` is `null`. `active_label` must not be one of `labels` (ignoring case): adding it would make the issue match again, so such a filter answers `422`, and a scan with an older stored filter does not add it. `issue_types` (default `[]`) are GitHub issue type names, trimmed and deduplicated ignoring case; when not empty, only an issue whose type is one of them (ignoring case) is taken, and an issue with no type is not. The scan asks GitHub for each trigger label and chosen type (`gh issue list --label <l> --type <t>`, which uses GitHub search), so issues of other types do not fill the 50-issue window; above 10 such pairs it asks once per label for up to 300 issues and drops the other types itself. This needs a `gh` that knows `--type` and the `issueType` JSON field; with an older one the cycle fails with an error that says to update `gh`.

**Agent options.** `claude_options` (the session `ClaudeOptionsModel`), `codex_options`, `antigravity_options`, `opencode_options`, `copilot_options`, `cursor_options`, `junie_options`, `pi_options`. Only the chosen agent's object is used. Each has the fields of the session options of that agent that an Auto Scan keeps — never the worktree, branch, prompt or sign-in fields, since each issue gets its own worktree and starts at once with its prompt:

| Agent | Fields (same names, values and checks as in `POST /api/sessions`) |
|-------|------|
| Claude Code | `model`, `effort`, `plan_mode`, `skip_permissions`, `no_memory`, `fast_mode`, `ultracode`, `append_system_prompt`, `debug`, `focus_mode`, `code_mode` |
| Codex | `model`, `reasoning_effort`, `sandbox`, `approval`, `full_auto`, `auto_review` |
| Antigravity | `model`, `skip_permissions`, `sandbox`, `add_dirs` (not both `skip_permissions` and `sandbox`) |
| OpenCode | `auto_approve`, `code_mode` |
| GitHub Copilot | `allow_all` |
| Cursor | `force` |
| Junie | `plan_mode`, `brave` |
| Pi | `code_mode` |

`claude_options.no_memory` (`bool`, default `false`, here and in `POST /api/sessions`) sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` for terminal and chat sessions: Claude Code does not load or write auto memory, and `CLAUDE.md` files still load. With `false` the agent sets nothing.

A start without the chosen agent's object runs it without asking: Codex `full_auto`, Antigravity `skip_permissions`, OpenCode `auto_approve`, Copilot `allow_all`, Cursor `force`, Junie `brave`. The start saves the object on the workspace (`auto_scan.<kind>_options`) with the other choices. An object a newer agent no longer accepts reads as `null`.

**`AutoScanTemplate`** is `{id, name, updated_at, settings}`. `updated_at` is ISO 8601 UTC. `settings` has `source`, `cli`, `ui`, `interval_minutes`, `initial_prompt` and the eight `<kind>_options`, each possibly `null`, plus `linear_filter` and `github_filter` as drafts: the same fields, but any may still be empty (a GitHub filter without labels, a Linear filter with blank states), so a template can hold a half-made filter. A template never holds a Linear API key. The templates live in `autoscan_templates.json` next to the agent's workspaces file. An entry this agent does not accept (one a newer agent wrote, for example) is left out of the answers but kept in the file; a file that is not a JSON list is moved to `autoscan_templates.json.bak` before the next save.

* A GitHub scan takes the open issues that carry any of `labels` (and, when `assignee` is set, are assigned to it; `@me` is the signed-in `gh` account). Label names match without regard to case, as on GitHub. Taking an issue adds `active_label` (the `state_update` step) and removes the trigger labels it has, in the issue's own spelling (the `label_removal` step), so it is not taken again; an issue whose current labels no longer include a trigger label is skipped even when the label search still lists it. When GitHub refuses the removal, the scan skips the issue until it restarts. A **closed** issue is done: its worktree is removed without `--force`, so git keeps a worktree with uncommitted changes and the scan tries again next cycle (anyone who opened an issue can close it).
* Issue events keep their shape. GitHub identifiers are `#<number>`; worktree folders and branches use `gh-<number>-<title words>`.
* The repository is the one `gh repo view` resolves in the workspace folder (it honours `gh repo set-default` and ranks `upstream` before `origin`); its host is the one checked for sign-in. Remote hosts are read like `gh` reads them: an ssh alias resolves through `ssh -G`, and `*.github.com` (such as `ssh.github.com`) counts as `github.com`. Sign-in is checked with `gh auth status --active`, so a stale second account does not block. The `gh` binary: `MOBAIDE_GH_BIN`, then PATH, then the Homebrew, Linux and Windows install folders. It is looked up on every call, so an install made from the app is found at once. `gh` calls run on their own 4-thread pool.
* Over the relay, the three GitHub routes, `POST /api/autoscan/start` and `GET /api/projects/{name}/linear/(labels|states)` run outside the tunnel reader (at most 4 at once, else `503`), and answer `504` after 25 s. The template routes are local file reads and writes, answered inline.
* Installers: Homebrew where present (`brew install gh`); on Windows `winget install --id GitHub.cli`, run directly; on Linux without Homebrew `apt-get`, `dnf`, `pacman` or `zypper` (with `sudo` unless the agent runs as root). Sign-in runs `gh auth login --hostname <host> --git-protocol https` for the host of the workspace's GitHub remote, so the user can sign in with a browser code or paste a token.
* Before an issue is taken, the scan checks that the agent is installed and, for Codex, signed in; otherwise the cycle stops and the issue keeps its labels or state. A GitHub scan launches the agent without the workspace's Linear key (no `.mcp.json`, no `LINEAR_API_KEY`), and with the folder of the `gh` it uses added at the end of the session's PATH, so the agent can run `gh issue view` even when `gh` was found outside PATH.
* Agents: each issue gets the same per-issue worktree Claude Code gets. A non-Claude agent runs as a session of its own kind (`codex`, `copilot`, …), not `auto_scan`, so its attention and keys work as in a normal session. Its options map to the same flags as in a normal session; the "run without asking" switches are Codex `full_auto` (`--dangerously-bypass-approvals-and-sandbox`), Antigravity `skip_permissions` (`--dangerously-skip-permissions`), OpenCode `auto_approve` (`--auto`), Copilot `allow_all` (`--allow-all`), Cursor `force` (`--force`), Junie `brave` (`--brave`). Pi has none. Claude Code keeps `claude_options`.
* The first prompt: the client's prompt (or the saved one, or the source's default), then a blank line, `<Source> issue <id>: <title>`, the issue URL and the description. Control characters other than line feed and tab are removed from the issue text. The issue line and URL are never cut. A prompt too long for the launch is cut in its instruction first (with `[…]`), which keeps room for the first 1,000 characters of the description, and then in the description:
  * A GitHub description is cut at 4,000 characters, since its agent can read the rest with `gh issue view`. A Linear description has no such cut, since only Claude Code can read Linear.
  * Any description is then cut to fit the launch: 32,000 characters, or 24,000 on Windows. A cut description ends with `[…]`.
  * The prompt is passed as Claude `-- <p>`, Codex `-- <p>` (every Codex terminal session now puts its first prompt after `--`), OpenCode `--prompt=<p>`, Antigravity `--prompt-interactive=<p>`, Copilot `--interactive=<p>`, Cursor `-- <p>`, Junie `--prompt=<p>`, Pi `-- <p>`.
  * Through a Windows `.cmd`/`.bat` shim, line breaks become spaces, the cmd.exe characters `" % ! ^ & | < >` are replaced, and the description is cut so the prompt fits 6,000 characters.
* `ui: "chat"` opens each issue's session in the chat view (`ui: "chat"` on the session, as for `POST /api/sessions`), in the same worktree. Only the agents in `capabilities.chat` can; clients offer the choice for those only. The chat sends the first prompt as its first message, so the command-line limits below do not apply; the prompt is still at most 32,000 characters (a GitHub description is still cut at 4,000). A Claude Code chat session has kind `claude`, not `auto_scan`. Every chat session now sends a first prompt it was given, for every agent (before, only Claude Code, Codex and OpenCode did).
* Folder trust: each issue's worktree holds the workspace's own files, so the scan trusts it and no agent asks. A chat session starts without the trust question. Before a terminal or chat session starts, the scan writes the agent's own trust record for the worktree when it has none: Claude Code (`~/.claude.json`), Codex (`config.toml`, for the worktree itself, not the main checkout; nothing is written when the folder or its main checkout already has a record, such as an explicit `untrusted`), Antigravity (`settings.json`), Copilot (`config.json`), Cursor (its `.workspace-trusted` file) and Pi (`trust.json`). Junie signs its records with a keychain key, so Mobaide cannot write one; Junie trusts a worktree whose main checkout it trusts. If the scan cannot record the trust (the agent's file changed under it three times), the scan log says that the session may ask. A folder that is not a git repository gets no record, and a chat there still asks. Chat sessions get the `gh` folder at the end of their PATH too.
* `interval_minutes` sets the wait after each cycle for this scan only; `run-now` still starts a cycle at once.
* Remembered choices: a start saves `source`, `cli`, `ui`, `interval_minutes`, the chosen agent's options, the sent filter and `linear_api_key` on the workspace before the scan starts, also when the start then fails; a start that `409`s or `429`s saves nothing. The prompt is saved once the scan runs. A workspace created without a filter has `auto_scan.linear_filter: null`. An earlier agent wrote the legacy defaults into every workspace; those load as `null` unless the workspace has a Linear key or its last scan was a Linear one.
* An older client (no `source`) starts a Linear scan with the stored filter, or the legacy defaults, and Claude Code, as before.

### Delta 2026-11-18: workspace Git delete jobs

Additive delta within proto-v7 (2026-11-18): **two workspace Git routes, one capability key, one earlier `force_required`. No version bump; `central_server` is untouched.**

`POST …/git/items:delete` removes the worktrees one by one inside one request. A large batch outlasted the relay's 30 s budget: the client lost the results, and with them the force prompt for unmerged branches. The relay reader also awaited the batch inline, so the whole tunnel (even the Git screen's own list) froze until it ended.

| Where | Change |
|-------|--------|
| `POST /api/projects/{name}/git/delete-jobs` | Body `GitDeleteRequest`. The busy guard runs first and answers the same `409 {detail: {busy_by}}` as `items:delete`. Then `202 GitDeleteJobResponse` at once. `409 {detail: {code: "too_many_jobs"}}` while 8 jobs run on the agent. |
| `GET /api/projects/{name}/git/delete-jobs/{job_id}` | `200 GitDeleteJobResponse`. `404 {detail: {code: "job_not_found"}}` for an unknown id (the agent restarted, or the job finished more than 10 minutes ago; at most 100 finished jobs are kept). |
| `GitDeleteJobResponse` | `job_id`, `status` (`running` \| `done`), `total`, `completed` (items processed so far), `results` (`null` while running, then the same per-item results as `items:delete`, in request order). A batch that fails unexpectedly ends `done` with an `error` result for every item, and `git.changed` is published so the list shows the truth. |
| `capabilities.git.delete_jobs` | `true` on agents with the two routes. Clients use the job and poll it every second. They reload the list when the job ends `done` (a relay reconnect can drop the agent's single `git.changed`). They give up at once on `404`, or after 2 minutes with no successful poll (a relay reconnect must not lose the results), and then reload the list too. Without the key they keep `items:delete`. One batch runs at a time per screen. |
| `branch_with_worktree` without `force` | The agent checks whether `git branch -d` would refuse the branch (git's own rule: merged into its upstream when that ref resolves, else into `HEAD`) **before** it removes the worktree. An unmerged branch answers `force_required` and leaves the worktree in place, so the force confirmation removes both. Earlier agents removed the worktree first and kept the branch. |

* The job runs `items:delete`'s batch logic unchanged: the same order, audit events and one `git.changed` when anything changed. It is not cancellable.
* The relay now runs `POST …/items:delete` aside from the tunnel reader (at most 4 at a time, else `503`), so older clients no longer freeze the tunnel. Their request can still time out at the central server; the agent finishes the batch and publishes `git.changed`.
* Golden fixture: new ``tests/protocol_fixtures/dto/agent/GitDeleteJobResponse.json``.

### Delta 2026-11-18: new conversation in a chat session

Additive delta within proto-v7 (2026-11-18): **one chat command, one chat state key, one agent slash command. No version bump; `central_server` is untouched.**

ACP has no "clear" method, and `claude-agent-acp` hides `/clear`. A user who wanted an empty chat had to open New session and go through the wizard again.

| Where | Change |
|-------|--------|
| `'c'` command `new_conversation` | No fields. Starts an empty conversation in the same Mobaide session: a new agent process, then `session/new` with the same folder, extra folders, hooks and system prompt. The config values in use apply again, as for `restart` (required ones stay required). `data: {}`. Refused with "The session is closing" or "The agent is already starting" (also while a `restart` runs). |
| State key `new_conversation` | `true` when the agent takes the command. Clients offer **New conversation** only then. |
| Slash command `clear` | The agent adds `/clear` to `commands` unless the agent itself lists one. Sending `/clear` runs `new_conversation` and answers like a prompt (`queued: false`). |

* What the command does: it cancels a running turn of this session (`session/cancel`, then the process closes), answers open requests as cancelled, drops the queue and the messages kept for a sign-in retry, and never sends the wizard's first prompt again. Then it starts a new epoch with an empty timeline. The state resets `acp_session_id`, `usage`, `plan`, `queue`, `background` and `title` (back to the name from the wizard, or `null`).
* The Mobaide session id, the session row, the folder or worktree, and the folder trust stay. A message sent while the new conversation starts waits in the queue and goes into it.
* The agent keeps the old conversation. The new timeline starts with an info `notice` "Started a new conversation". When the old one was stored (a turn finished, or it was a continued session) and the agent can load sessions, the notice text says how to open it again (Continue a session); otherwise the text is empty.
* On a shared host (live Codex), the agent waits up to 3 s for this session's own turn to stop before the process closes, as closing the session does. A turn that another app started goes on there.
* Clients confirm first, and the dialog says when a running turn stops or queued messages are removed. Older clients ignore the key; on an older agent the command answers "Unknown command".

### Delta 2026-11-16: Junie sessions

Additive delta within proto-v7 (2026-11-16): **one session kind, one options block, three capability entries, one push label. No version bump; `central_server` gained only the "Junie session" push label.** An older agent does not list `junie`, and the clients then disable the card with "Update Mobaide on your PC to use Junie."

Junie CLI (`junie`, JetBrains) is a managed agent of kind `junie`, for terminal and chat sessions.

| Where | Change |
|-------|--------|
| `POST /api/sessions` and relay `create_session` | `cli: "junie"` with `junie_options` (`plan_mode`, `brave`, `worktree`, `branch_name`, `base_branch`). A missing binary answers `503` with `junie_cli_missing`. |
| `capabilities.cli` | A `junie` entry. |
| `capabilities.chat` | A `junie` entry (`resume: true`, `live: false`). |
| `capabilities.voice.cleanup_kinds` | The id `junie`. |
| Session mode order | The id `junie`. Default position: after `cursor`. |

* Binary: `MOBAIDE_JUNIE_BIN`, then PATH, then `~/.local/bin/junie` (on Windows `%USERPROFILE%\.local\bin\junie.bat`), where Junie's install script puts its launcher. `MOBAIDE_ACP_JUNIE_COMMAND` overrides the chat command.
* `plan_mode` starts in Junie's Plan mode: Junie prepares a plan and asks before it changes files. `brave` is Brave mode for this session only: Junie runs every command without asking. Junie's own saved Brave setting is not changed.
* Terminal sessions run the Junie TUI, `junie [--plan] [--brave]`. They raise attention alerts and set `activity` (`working` or `idle`) from Junie CLI command hooks, not OSC 7501: the agent passes a per-session hook file with `--config-location`, and a small script posts each hook to `/internal/hook`. A Junie without `--config-location` in its `--help`, and every Junie session on Windows, starts with no hooks and raises no alert.
* Chat sessions run the ACP server built into the CLI, `junie --acp=true` (`--brave` added for `brave`), with no adapter. The server takes the session folder from `session/new`.
* Config options: `mode` (Default, Plan, Goal; no category), `model` (grouped by provider), `effort` (`thought_level`) and `brave_mode` (Auto, Brave, Safe; category `mode`). `plan_mode` applies as `mode` = `plan`. A change of `brave_mode` in the chat changes Junie's saved setting, as in its TUI; a Restart does not apply it again (the new process reads the saved value).
* Plan mode: the plan arrives as an agent message, then a permission request "Implement this plan?". After **Confirm and implement**, Junie switches `mode` back to Default and does the work in the same turn; the delivery steps become the timeline `plan`. This request (tool call id `junie-plan-review:…`) is never auto-approved, also not when the chat accepts every other request, and its tool call updates never become tool items.
* `ask_user` and the Goal mode setup arrive as `elicitation/create` form requests. Approvals have the options Yes and No.
* Command output: a finished command becomes a `console` part with the whole output (`rawOutput.output`) and the exit code. Junie's own `content` holds a shortened copy.
* Not live: Continue uses `session/list` and `session/load`. The list holds sessions started in Junie's terminal UI too. Junie matches the `cwd` filter to the exact real path, so the PC agent sends `realpath(cwd)` and gives each row of that folder back the `cwd` as the client sent it.
* Folder trust: over ACP Junie loads the folder's MCP servers, skills, commands and settings without its trust question, so the PC agent asks in the chat. Junie signs its own trust markers (`<JUNIE_HOME or ~/.junie>/trust/*.json`) with a key in the system keychain, so the PC agent only reads them: a folder trusted in Junie's TUI, or inside a folder trusted with "Trust all projects in …", needs no question. A trust given in the chat is kept in `junie-trusted-folders.json` in the agent's state folder, for that folder only.
* Sign-in: none from the chat. Junie signs in only on the welcome screen of its TUI, so a chat that is not signed in (`-32000` on `session/new`) says to sign in from a Junie terminal session, then Restart.
* No model: Junie offers its free Junie Lite model only in its TUI and in JetBrains apps. A prompt that no model can take answers `-32002` with `data.reason` `model_not_available`; the chat shows an error item that says how to get a model.
* Voice transcript cleanup: `capabilities.voice.cleanup_kinds` lists `junie`, and `POST /api/voice/cleanup` accepts `kind: "junie"`. The run is `junie --output-format json --model junie-lite --allowed-tools=submit --skip-update-check --config-default-locations=false --mcp-default-locations=false --skill-default-locations=false --agent-default-location=false --command-default-location=false --extensions-default-location=junie-extensions` (docs/voice-input.md, *Transcript cleanup*), with the user's `config.json`, MCP servers, skills, custom agents, commands and extensions turned off; the run's environment drops the `JUNIE_*` variables that would add them back or move the run to another folder or session, and `JAVA_TOOL_OPTIONS` gets `-Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8`, because Junie's JVM writes piped output in the system code page. It runs on Junie Lite, JetBrains' free model, the only model of a Lite-only account. `--allowed-tools` leaves only `submit`, the tool that ends a run, so the model can run no command. The rules and the transcript are the task, on stdin: with the transcript alone as the task, Junie Lite tried to carry it out. An answer that reports on the cleanup (Markdown headings, list items, bold, or the word "transcript" when the dictated text has none) is a `cleanup_failed`. For Junie Lite, Junie refuses once per run a `submit` that changed no file when it judges the task to need code changes, so dictated orders to create documentation or config files (a README, `.gitignore`, a license file) fail after 7–13 s and keep the text as heard. The user's `~/.junie/AGENTS.md` still applies, and Junie keeps each run in its session history; the chat's session list hides the `voice-cleanup` work folder.
* Not supported for Junie: a model or effort picker in the wizard (`junie list-models` lists every model, also those the account cannot use), sign-in from the chat.
* Test mode runs the fake agent with `--flavor junie`.

### Delta 2026-11-21: OpenCode, Copilot and Antigravity chats follow background work

Agent-side delta within proto-v7 (2026-11-21): **no new key, no version bump; `central_server` is untouched.** One value combination is new: `background: "waiting"` while `status` is `running`.

These three agents can finish work after their `session/prompt` answer, or hold the answer while only background work runs. Before, the chat read "Ready" too early, and the real end raised no alert. They run no Claude Code hooks, so the agent reads each one's own signal:

| Agent | Signal (opted in with `clientCapabilities._meta` in `initialize`) | Effect |
|-------|------|--------|
| OpenCode 2.3+ | `{"opencode/child-session-updates": true}`. `opencode/session/child_update` notifications: `type: "status"` (`created`, `running` → in flight; `completed`, `failed`, `interrupted` → ended) and `type: "update"` (the subagent's own update) | While a subagent is in flight and `status` is `ready`: `background: "working"`, session `activity` `working`, and the finished turn's `turn_finished` waits. When the last one ends, the parent's woken turn alerts at its end, or the alert comes after 20 s when no turn follows. Of the subagent's updates, only `tool_call` and `tool_call_update` show, as tool items with id `<child id>:<tool call id>` and the subagent's title before the tool title. With the opt-in, OpenCode no longer sends them in the parent stream. An older OpenCode ignores the key. |
| GitHub Copilot | `{"github.com/copilot": {"events": ["assistant.turn_start", "assistant.idle", "session.idle"]}}`. `github.com/copilot/sessionEvent` notifications; events with `agentId` (a subagent's) are ignored | `assistant.idle` (not `aborted`) during a turn: `background: "waiting"` and `activity` `waiting` while `status` stays `running`, until the next `assistant.turn_start` or the turn's end. With no turn running, `assistant.turn_start` starts a self-started turn, and `session.idle` ends it with `turn_finished`. |
| Antigravity 1.5.10+ | None (agy sends no busy marker) | The fallback below. |

* Fallback for all three: a content update (`agent_message_chunk`, `agent_thought_chunk`, `tool_call`, `tool_call_update`) that comes while `status` is `ready` starts a self-started turn: `status: "running"` with no "Message sent from another app" notice, queued messages wait, and `cancel` sends `session/cancel` and ends it with no alert. It does not start within 1 s of a prompt's answer, during `loading`, or from metadata updates. The turn ends with `turn_finished` after 20 s with no content update, no open request and no tool call of the turn still `pending` or `in_progress`. A turn that Copilot opened with `assistant.turn_start` ends at its `session.idle` instead. Either way, 10 minutes with no content update and no open request end the turn, even with a tool still running.
* An OpenCode child status other than the five above counts as an end.
* Copilot: a second `session/prompt` while the first waits on an attached shell makes Copilot answer the first one `cancelled` (measured on 1.0.112). So queued messages still wait for the prompt's answer.
* Clients: the chat header shows "Background task" in place of "Working" while `status` is `running` and `background` is `"waiting"`. Older clients keep showing "Working".

### Delta 2026-11-16: chat state `background`

Additive delta within proto-v7 (2026-11-16): **one chat state key. No version bump; `central_server` is untouched.**

After the 2026-11-15 delta, a Claude chat session row reads `working` or `waiting` while a turn's background work runs, but the chat screen read only the timeline `status`, so its header showed "Ready" for that whole time.

| Where | Change |
|-------|--------|
| State key `background` | `"working"` (a workflow or a sub-agent runs), `"waiting"` (only a background shell, a monitor or a scheduled wakeup), or `null`. Not `null` only while `status` is `ready` and the last `Stop` hook listed work in flight (Delta 2026-11-21 adds `"waiting"` while `status` is `running`). It is then the session row's `activity`. |

* Clients show it in the chat header in place of "Ready": "Working in background" for `working`, "Background task" for `waiting`. The composer stays usable, because the main agent waits for input.
* `null` in the quiet window after a turn that left nothing in flight (the session row reads `working` there), and for every chat session without Claude Code hooks (other agents, a CLI that runs no hooks).
* The patch that sets `background` rides in the same `events` frame as the `status` change that causes it, so a client never sees "Ready" in between.
* Older clients ignore the key and keep showing "Ready".

### Delta 2026-11-15: Claude chat follows background work

Agent-side delta within proto-v7 (2026-11-15): **no wire change, no version bump. No client change: every value below already exists.**

Before, a Claude chat session took its `activity` and its `turn_finished` alert from the `session/prompt` answer alone. `claude-agent-acp` answers a prompt while a workflow, a background shell or a monitor still runs, and a turn that Claude Code starts by itself (a task notification, a scheduled wakeup) has no prompt. So the session read `idle` and alerted "Ready" while work was in flight, and the real end raised nothing.

* The agent passes Claude Code hook settings in `session/new` and `session/load` (`_meta.claudeCode.options.settings`): `Stop`, `StopFailure`, `UserPromptSubmit` and `PreToolUse` `http` hooks to `POST /internal/hook`. No `Notification` and no `SessionEnd` hook: the adapter reports a permission request and the agent's exit.
* The adapter gives the settings to the CLI as a process argument, so the hook URLs carry no token. Each hook sends the per-agent token in the `X-Mobaide-Hook-Token` header, which the CLI fills from `MOBAIDE_HOOK_TOKEN` in its environment. `POST /internal/hook` accepts the token from the query or from this header.
* `activity`: `working` while the timeline `status` is `running`. Otherwise the attention gate's value from the last `Stop` payload, as for a Claude terminal session: `working` (a running workflow or sub-agent), `waiting` (only a shell, a monitor or a scheduled wakeup), or `idle` when the quiet window ends.
* `turn_finished`: the gate raises it, 2 s after a `Stop` that lists nothing in flight (a terminal session waits 8 s). The `Stop` of the user's own prompt reaches the gate at the prompt's answer, so no alert comes while that turn runs.
* A turn Claude Code starts by itself shows on the chat screen as a running turn: `status: "running"` from its `UserPromptSubmit` hook to its `Stop` hook, with no "Message sent from another app" notice. Messages sent meanwhile wait in the queue, and `cancel` stops the turn. `steer` on a queued message during such a turn sends it at once as a `session/prompt`, because the adapter steers only a turn it got as a prompt; Claude Code adds it to the running turn, the result is `{steered: true}`, and the turn becomes this session's own. `StopFailure` in such a turn raises `turn_failed`.
* A stopped or failed turn fires no `Stop`. The agent then sets `idle` itself, with no `turn_finished`.
* Fallback: until the first hook of an agent process arrives, the session works as before (the prompt answer raises `turn_finished` at once). This covers a CLI that runs no hooks and a policy that blocks them.
* Other chat kinds are unchanged.

### Delta 2026-11-13: Pi sessions

Additive delta within proto-v7 (2026-11-13): **one session kind, one options block, two capability entries, one push label. No version bump; `central_server` gained only the "Pi session" push label.**

The Pi coding agent (`pi`, pi.dev) is a managed agent of kind `pi`, for terminal and chat sessions.

| Where | Change |
|-------|--------|
| `POST /api/sessions` and relay `create_session` | `cli: "pi"` with `pi_options` (`worktree`, `branch_name`, `base_branch`). A missing binary answers `503` with `pi_cli_missing`. |
| `capabilities.cli` | A `pi` entry. |
| `capabilities.chat` | A `pi` entry (`resume: true`, `live: false`). |
| Session mode order | The id `pi`. Default position: after `copilot`. |

* Binary: `MOBAIDE_PI_BIN`, then PATH, then the folder of Pi's own installer (`~/.pi/agent/bin`), then the npm fallback folders. `MOBAIDE_ACP_PI_COMMAND` overrides the chat command; it must speak ACP.
* `pi_options` has no approval switch: Pi runs its tools without asking, in the terminal and in the chat. The model and the thinking level are picked inside the session.
* Terminal sessions run `pi`. They raise attention from OSC 7501 reports (`app=pi`), with no hooks (see Delta 2026-11-10). Pi sends the support query first; the agent answers it.
* Chat sessions: Pi has no ACP server. The PC agent runs its own bridge (`mobaide/acp/pi_bridge.py`), which speaks ACP to the chat session and Pi's RPC mode (`pi --mode rpc`, JSON lines) to one `pi` process per session, started in the session folder.

| Pi RPC | Chat |
|--------|------|
| `get_available_models`, `set_model` | Config option `model` (`provider/id`; the models of every signed-in provider, grouped by provider). |
| `get_available_thinking_levels`, `set_thinking_level` | Config option `thought_level`, when the model has more than one level. |
| `prompt`, `abort`, `agent_settled` | `session/prompt`, `session/cancel`, the stop reason. |
| `steer` | `_session/steering` ("Send now" for a queued message). |
| Message and tool events | `agent`, `thought` and `tool` items. `bash` output is a `console` part with the exit code; `edit` and `write` carry a diff. |
| `compact`, compaction events | The command `/compact [what to keep]` and a `compaction` item. |
| `get_commands` | The `commands` list (extension commands, prompt templates, skills). Pi's built-in terminal commands (`/model`, `/settings`, …) are not listed: RPC mode does not run them. |
| `extension_ui_request` | `confirm`, and `select` with at most 4 short options: a `permission` request with one button per option. A longer `select`, `input` and `editor`: a `form` request. `notify`: a `notice` item. |
| Session files (`~/.pi/agent/sessions`) | `session/list` and `session/load`: the same sessions that `pi --resume` lists. Pi stores a session with its first message, so a `session/load` of a session with no file (a Restart before the first message) starts it again under the same id (`--session-id`). |

* Not live: a session open in Pi's terminal UI and in the chat is not kept in sync.
* Usage: the context use of the last answer. No cost: Pi computes it from list prices, also for subscription logins.
* Folder trust: without a terminal Pi skips a folder's own resources instead of asking. So the PC agent asks in the chat when the folder has resources that Pi protects (`.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions`, `.pi/skills`, `.pi/prompts`, `.pi/themes`, `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, or `.agents/skills` in the folder or above it), Pi's `defaultProjectTrust` setting is `ask`, and `trust.json` in Pi's folder holds no decision for the folder or a parent. `trust` writes `true` for the real path, as Pi's `/trust` does. A saved "do not trust" is kept: the session starts and Pi skips the resources.
* Sign-in: the account methods `openai-codex` and `anthropic`, and the API-key methods `openrouter-key`, `google-key`, `openai-key` and `anthropic-key` (see Delta 2026-11-11, sign-in from the chat). It is offered when no provider has credentials (`get_available_models` is empty) or Pi refuses a prompt for a missing key, a wrong key or a login it could not renew. Other providers are set up in Pi itself (`/login`).
* Voice transcript cleanup: `capabilities.voice.cleanup_kinds` lists `pi`, and `POST /api/voice/cleanup` accepts `kind: "pi"`. The run is `pi --mode json --no-session --no-tools --no-extensions --no-skills --no-prompt-templates --no-context-files --no-mcp --no-themes --no-approve --offline --thinking off --system-prompt cleanup-prompt.md`, on Pi's default model, with the transcript on stdin (docs/voice-input.md, *Transcript cleanup*). Nothing is stored in Pi's session history.
* The sign-in request `text` can come from the agent's launch plan: Pi's says where to set up the other providers.
* Not supported for Pi: a model or thinking-level picker in the wizard.
* Test mode runs the fake agent with `--flavor pi`.

### Delta 2026-11-12: OpenCode sessions

Additive delta within proto-v7 (2026-11-12): **one session kind, one create-options block, two capability entries, one push label. No version bump (`PROTOCOL_VERSION` is unchanged).** `central_server` gained only the "OpenCode session" push label.

| Where | Change |
|-------|--------|
| Create request | `cli: "opencode"` with `opencode_options` = `{auto_approve: bool = false, initial_prompt: str \| null (up to 32000 chars; no client sends it), worktree: bool = false, branch_name, base_branch, connect: bool = false}`. `ui` is `"chat"` or `"terminal"` as for the other agents. |
| Missing binary | 503 with `opencode_cli_missing`, like `codex_cli_missing`. There is no auth probe: OpenCode starts without an account. |
| `capabilities.cli` | `opencode: {"available": bool}`, with no `models`. A client treats an agent whose list has no `opencode` as too old (it would open a plain shell). |
| `capabilities.chat` | `opencode: {"resume": true, "live": false}`. |
| `capabilities.voice.cleanup_kinds` | Lists `opencode`: `POST /api/voice/cleanup` accepts `kind: "opencode"`. |

* Binary lookup: `MOBAIDE_OPENCODE_BIN`, then PATH, then `~/.opencode/bin/opencode`, npm-global, Homebrew and `~/.bun/bin` (Windows: `%USERPROFILE%\.opencode\bin\opencode.exe`, npm shims, Bun). The Windows installer does not detect OpenCode.
* Terminal sessions run `opencode [--auto]` in the session folder. OpenCode 2 has no `--model` or `--agent` flag, so there is no model option. The launcher sets `OPENCODE_CLI_CONFIG_CONTENT={"tabs":{"mode":"off"}}`, merged into the user's own value (the user's `tabs` key wins), because OpenCode shares one tab set per folder and its status report covers all tabs.
* Terminal attention comes from OSC 7501 (`app=opencode`), which reports only `idle`, `working` and `blocked`. A `working` to `idle` change is the turn end: `turn_finished` after the usual quiet window, unless client input arrived within 2 s before it (an interrupt). `blocked` gives `awaiting_permission` only after it stands 0.5 s (with `--auto`, OpenCode reports `blocked` for about 10 ms per request). There is no `turn_failed`. OpenCode 1.x reports nothing. `activity` follows the reports (Delta 2026-11-23): `working` → `working`, `idle` → `idle`.
* Chat sessions run `opencode acp`, OpenCode's built-in ACP server: no adapter and no Node.js. It runs a server of its own, apart from the TUI's, so a chat and a terminal on one session do not sync. `GET /api/chat/opencode/sessions` lists the stored sessions of a folder, and Continue replays the history. There is no folder-trust gate.
* Chat config options come from OpenCode: `model` (`provider/model`), `mode` (Build or Plan) and `effort` for models with variants. With `auto_approve`, the agent answers each permission request with its "allow once" option and shows no card.
* `opencode_options.connect: true` starts a terminal session that runs `opencode auth login`, OpenCode's interactive provider sign-in. `ui` is forced to `terminal`, no worktree is created and the other options are ignored. The command ends the session as a normal `stopped` session (`SessionConfig.exits_when_done`): never `crashed`. Liveness cleanup removes its row and publishes `sessions.changed`, as for a user stop. Clients show a "Connect a provider" entry on the OpenCode options screen and do not remember the launch options for it.
* The launcher sets `PWD` to the session folder, because OpenCode takes its folder from `PWD` when it is set.
* A provider auth failure in a chat is not a sign-in card. The chat shows "OpenCode could not sign in to the model provider. In New session, pick OpenCode and use Connect a provider, or run `opencode auth login` on the PC. Then tap Restart."
* Voice transcript cleanup runs `opencode run --standalone --agent mobaide-voice-cleanup --title Voice-cleanup --archived --format json` with the transcript on stdin, on OpenCode's default model (one request per cleanup). The env drops `PWD` and sets `OPENCODE_DISABLE_PROJECT_CONFIG=1` and `OPENCODE_DISABLE_CLAUDE_CODE=1` (no `~/.claude/CLAUDE.md`). The cleanup agent (`system` = the shared cleanup prompt, deny-all permissions) goes inline in `OPENCODE_CONFIG_CONTENT`, merged into the PC's own value, with a top-level deny-all rule last and `share: "disabled"`. No file is written. An `error` event fails the cleanup even at exit code 0, and any tool event discards the run. OpenCode 1.x has no `--standalone` and OpenCode 2.2.0–2.2.5 have no `--archived`: `cleanup_outdated`. An `OPENCODE_CONFIG_CONTENT` that is not a plain JSON object: `cleanup_failed`. The user's global OpenCode config still applies (its `AGENTS.md`, its MCP servers). Each run (title `Voice-cleanup`) is archived, so it stays out of OpenCode's session picker and `--continue`.
* `GET /api/chat/{kind}/sessions` never lists rows whose folder is the agent's `voice-cleanup` work dir. A page holding only such rows is read on (up to 10 agent pages), so a client never gets an empty page with a cursor.
* The env file keys `MOBAIDE_OPENCODE_BIN` and `MOBAIDE_ACP_OPENCODE_COMMAND` override the binary and the `opencode acp` command.

Golden fixtures: new `tests/protocol_fixtures/dto/agent/OpenCodeOptionsModel.json`; `CreateSessionRequest.json` gains an `opencode_launch` case. Both carry `connect`; `VersionInfoResponse.json` and `VoiceCleanupRequest.json` list `opencode`.

### Delta 2026-11-12: GitHub Copilot sessions

Additive delta within proto-v7 (2026-11-12): **one session kind, one options block, two capability entries. No version bump; `central_server` is untouched.**

GitHub Copilot CLI is a managed agent of kind `copilot`, for terminal and chat sessions.

| Where | Change |
|-------|--------|
| `POST /api/sessions` and relay `create_session` | `cli: "copilot"` with `copilot_options` (`allow_all`, `worktree`, `branch_name`, `base_branch`). A missing binary answers `503` with `copilot_cli_missing`. |
| `capabilities.cli` | A `copilot` entry. |
| `capabilities.chat` | A `copilot` entry (`resume: true`, `live: false`). |
| Session mode order | The id `copilot`. Default position: after `opencode`. |

* Binary: `MOBAIDE_COPILOT_BIN`, then PATH, then the npm fallback folders, then (on Windows) the WinGet link. `MOBAIDE_ACP_COPILOT_COMMAND` overrides the chat command.
* Terminal sessions run `copilot [--allow-all]`. They raise attention from OSC 7501 reports (`app=copilot`; Copilot CLI 1.0.106 or later), with no hooks (see Delta 2026-11-10).
* Chat sessions run the ACP server built into the CLI, `copilot --acp`. The process starts in the home folder and takes the session folder from `session/new`. `allow_all` sets the ACP config option `allow_all` to `on`; the chat shows it and the user can turn it off.
* Mode values are Copilot's Agent, Plan and Autopilot. The model and reasoning-effort options appear only when the account's plan offers more than the Auto model.
* Not live: no attach to a session open in Copilot's terminal UI. Continue uses `session/list` (kept to the requested folder) and `session/load`.
* Folder trust: `copilot --acp` loads a folder's hooks and MCP servers without asking, so the PC agent asks in the chat. The record is `trustedFolders` in `$COPILOT_HOME/config.json` (default `~/.copilot/config.json`). A listed folder also covers the folders inside it. The home folder, a folder above it and a filesystem root are trusted for the session only.
* Sign-in: the method `github` (see Delta 2026-11-11, sign-in from the chat). GitHub Enterprise `--host` sign-in is not offered.
* Command output: a finished command becomes a `console` part with the exit code. Live command output is sent at most once per 0.3 s. The "Info: <path>" note Copilot sends after it creates a file is not shown.
* A continued session whose replay has no title takes it from the agent's session list, for every agent with the list capability.
* Voice transcript cleanup: `capabilities.voice.cleanup_kinds` lists `copilot`, and `POST /api/voice/cleanup` accepts `kind: "copilot"`. The run is `copilot --output-format json --stream off --available-tools=mobaide-no-tools --deny-tool=shell --deny-tool=write --deny-tool=read --deny-tool=url --deny-tool=memory --no-custom-instructions --disable-builtin-mcps --sync-session=off`, on Copilot's default model, with the rules and the transcript on stdin (docs/voice-input.md, *Transcript cleanup*).
* The chat's session list never shows sessions of the `voice-cleanup` work folder (the cleanup runs of Antigravity, OpenCode and Copilot), for every agent.
* Not supported for Copilot: a model or effort picker in the wizard.
* Test mode runs the fake agent with `--flavor copilot`.

### Delta 2026-11-13: Cursor sessions

Additive delta within proto-v7 (2026-11-13): **one session kind, one options block, three capability entries. No version bump; `central_server` is untouched.** An older agent does not list `cursor`, and the clients then disable the card with "Update Mobaide on your PC to use Cursor."

Cursor CLI is a managed agent of kind `cursor`, for terminal and chat sessions.

| Where | Change |
|-------|--------|
| `POST /api/sessions` and relay `create_session` | `cli: "cursor"` with `cursor_options` (`force`, `worktree`, `branch_name`, `base_branch`). A missing binary answers `503` with `cursor_cli_missing`. |
| `capabilities.cli` | A `cursor` entry. |
| `capabilities.chat` | A `cursor` entry. |
| `capabilities.voice.cleanup_kinds` | The id `cursor`. |
| Session mode order | The id `cursor`. Default position: after `copilot`. |

* Binary: `cursor-agent`. `MOBAIDE_CURSOR_BIN`, then PATH, then `~/.local/bin`, then (on Windows) `%LOCALAPPDATA%\cursor-agent\cursor-agent.{exe,cmd}`. `MOBAIDE_ACP_CURSOR_COMMAND` overrides the chat command.
* Terminal sessions run the Cursor TUI, `cursor-agent [--force]`. They raise no attention alert and set no `activity`: the Cursor TUI reports no state (no OSC 7501).
* Chat sessions run the ACP server built into the CLI, `cursor-agent acp` (`cursor-agent --force acp` when `force` is set), with no adapter. The process starts in the session folder, because Cursor reads the folder's rules, hooks and MCP servers from its working directory. The client declares `parameterizedModelPicker` in `clientCapabilities._meta`.
* Mode values are Cursor's Agent, Plan and Ask. The model and the picked model's settings (effort, context, fast) are config options.
* Cursor's own requests: `cursor/ask_question` becomes a form request. `cursor/create_plan` becomes the plan text as an agent message plus a permission request, **Accept plan** or **Reject plan**, which is never auto-approved. `cursor/update_todos` becomes the timeline `plan`. A shell command outside Cursor's allowlist is a normal permission request (allow once, allow always, reject); file edits send none.
* Not live: Continue uses `session/list` and `session/load`, and lists only Cursor chat sessions. A session started in Cursor's terminal UI is not listed.
* Folder trust: `cursor-agent acp` reads the folder's rules, hooks and MCP servers, so the PC agent asks in the chat. The record is `<CURSOR_DATA_DIR or ~/.cursor>/projects/<folder slug>/.workspace-trusted`. A parent folder's record also covers the folders inside it, except the home folder, a folder above it, and a folder fewer than three levels deep.
* Sign-in: the method `cursor` (see Delta 2026-11-11, sign-in from the chat).
* Command output: a finished command becomes a `console` part with the exit code.
* Voice transcript cleanup: `capabilities.voice.cleanup_kinds` lists `cursor`, and `POST /api/voice/cleanup` accepts `kind: "cursor"`. The run is `cursor-agent --print --output-format stream-json --mode ask --trust --model auto`, on Cursor's Auto model (included in the Free plan), not on the model saved in the user's Cursor settings. The run sets `CURSOR_CONFIG_DIR=<state prefix>/voice-cleanup-cursor`, because Cursor saves a run's model as the user's default; the agent copies the user's `cli-config.json` there before each run and never writes the user's own settings. The run's cwd is the `voice-cleanup` work folder, with a `.cursor/cli.json` that denies every tool (docs/voice-input.md, *Transcript cleanup*).
* Not supported for Cursor: a model picker in the wizard, "your turn" alerts for terminal sessions, continuing a session started in Cursor's terminal UI.
* Test mode runs the fake agent with `--flavor cursor`.

### Delta 2026-11-11: sign-in from the chat

Additive delta within proto-v7 (2026-11-11): **one chat request kind, one chat command. No version bump; `central_server` is untouched.**

A chat session can now sign its coding agent in on the PC. ACP agents report a missing or expired login as `authRequired` (`-32000`), and their own sign-in methods open a browser on the PC or expect a terminal. So the PC agent runs the agent's own login command in a hidden PTY and shows what the user needs in the chat.

| Agent | Methods (`id`) | Login command |
|-------|----------------|---------------|
| Claude Code | `claude_ai`, `console` | `claude auth login --claudeai` / `--console`: prints the sign-in page, then reads the code that the page shows. With the browser on the PC it finishes alone. |
| Codex | `chatgpt`, `api_key` | `codex login --device-auth`: prints the page and a one-time code to enter there. `api_key`: the value goes to `codex login --with-api-key` on stdin. |
| Antigravity | `google` | `agy acp --login`: prints the Google sign-in page, then reads the code (or the address of the page the user lands on). |
| GitHub Copilot | `github` | `copilot login --device-code`: prints GitHub's device page and a one-time code to enter there. |
| Cursor | `cursor` | `cursor-agent login`: prints a sign-in page that works on any device. No code to type; it finishes alone. |
| Pi | `openai-codex`, `anthropic`, `openrouter-key`, `google-key`, `openai-key`, `anthropic-key` | `pi auth login <provider> --no-browser`: ChatGPT prints a device page and a one-time code to enter there; Claude prints the sign-in page, then reads the code that the page shows. A `*-key` method asks for an API key (`input.secret: true`) and runs `pi auth login <provider> --api-key` (providers `openrouter`, `google`, `openai`, `anthropic`) in a PTY; the key is typed at Pi's prompt, never put in argv. Pi does not check the key; a wrong key fails the first prompt, which offers the sign-in again. `anthropic-key` replaces a Claude account login. |

* When it is offered: `session/new`, `session/load` or a prompt fails with `authRequired`, or claude-agent-acp pushes `_auth/status_update` with `authStatus.kind: "none"`. A user message `/login` (no images) also opens it, unless a turn runs. The state `commands` list gains a `login` entry.
* The request in `requests`: `{"id": "sign-in", "kind": "sign_in", "tool_call_id": null, "title", "text", "stage", "methods": [{id, name, description}], "url", "user_code", "input", "busy", "error"}`.
  * `stage: "choose"`: the client offers one button per method (or one **Sign in** button for a single method). `error` says why the last try failed.
  * `stage: "waiting"`: `url` (always `https://`) is the sign-in page, `user_code` the code to enter there, and `input` (`{label, secret}`) asks for a value; `secret: true` means a password field. `busy` means the agent is waiting for the login to finish.
* Command `sign_in` with `action`: `start` + `method`, `submit` + `value` (≤ 4 096 characters), or `cancel`. The result answers the command; the request itself carries the progress.
* The login's exit code decides: `0` removes the request, starts the agent again (a new session when the agent stored nothing yet, else a reload) and sends the messages the agent refused. Any other code returns to `choose` with the last lines of its output in `error`. A login still running after 20 minutes is stopped.
* A Codex chat create no longer answers `409 codex_auth_required`: the chat opens and offers the sign-in. Terminal Codex sessions keep the `409`.
* Older clients show the request as an unknown kind ("Waiting for an answer").

### Delta 2026-11-11: session activity

Additive delta within proto-v7 (2026-11-11): **one nullable field on the session wire dict. No version bump; `central_server` is untouched.**

The session wire dict (`sessions.changed`, `session.updated`, the `/ws/sessions` snapshot, `GET /sessions`) gains `activity`: `working` while the agent is mid-turn, `waiting` while the main agent is parked and only passive background work runs (Claude Code and Auto Scan terminal sessions only), `idle` between turns, `null` when unknown. `activity` is a free string, so older clients render an unknown value as no chip. A change publishes `session.updated` and never `session.attention`, so it never raises a notification.

| Session | Source |
|---------|--------|
| Claude Code and Auto Scan terminal | The attention gate's hooks. `UserPromptSubmit` → `working`. A `Stop` that lists a running background agent → `working`. A `Stop` that lists only passive work (a background shell, a monitor, a scheduled wakeup) or `session_crons` → `waiting`. Otherwise `idle` when the gate's quiet window ends, even when the alert is muted or deduped. A `PreToolUse` heartbeat from the main agent → `working` and cancels a pending quiet window. A sub-agent tool call → `working` only while the last `Stop` listed in-flight work. `idle_prompt` re-reports the activity the last `Stop` implied, or `idle`. `SessionEnd` → `idle`. `null` until the first hook. |
| Chat (`ui: "chat"`), every kind | The timeline `status`: `running` → `working`, `ready` → `idle`, otherwise `null`. A turn another app on a shared host runs counts too. A Claude chat adds the hooks of Delta 2026-11-15 (`waiting`, and `working` while `ready`). |
| Junie terminal | The same hooks, posted by Junie's CLI command hooks: `UserPromptSubmit` → `working`, `Stop` or `StopFailure` → `idle` when the gate's quiet window ends, an Esc interrupt → `idle`. `null` until the first hook, and always `null` when the session started with no hooks. |
| Codex, Antigravity, OpenCode, GitHub Copilot, Pi, Terminal and custom terminal (PTY) sessions | Their OSC 7501 reports (Delta 2026-11-23): `working` → `working`; `idle`, `done`, `error` and `clear` → `idle`; `blocked` keeps the value. `null` until the first report, and always `null` when the program writes none (Cursor, a plain shell). |

* Only a `running` session reports a value. Clients show a "Working" chip and an animated status square for `working`, and a muted "Background" chip with the static square for `waiting`. A pending `awaiting_permission` or `awaiting_os_permission` outranks it, because the turn waits for the user.
* Gate rule: an `idle_prompt` no longer raises `turn_finished` while the last `Stop` listed in-flight work. It also does not alert again once the quiet window already alerted for the turn. A passive task still in flight when the user types the next prompt stops counting as in-flight work. Task notifications, sub-agent hand-backs and scheduled wakeups are not typed prompts.
* A client keystroke no longer cancels a pending quiet window. It mutes the window's alert, and the window still turns the activity `idle`.
* `stop_hook_active` no longer suppresses a `Stop`. It marks the stop after a continuation that another `Stop` hook forced, and Claude Code advises hooks to let that stop through, so for a well-behaved hook it is the real end of the turn. Before, a session with a blocking `Stop` hook lost that end-of-turn alert when the forced continuation ended within the quiet window. A longer continuation (a `/goal` loop, for example) still alerts once, early.

### Delta 2026-11-10: agent status notifications

Additive delta within proto-v7 (2026-11-10): **one attention reason, two optional payload fields. No version bump (`PROTOCOL_VERSION` is unchanged).**

* New attention reason `turn_failed`. It appears on the session dict `attention`, the agent-bus `session.attention` event and the user-event `reason`. Older clients post nothing for an unknown reason.
* Chat sessions raise `turn_failed` when the agent answers the user's own prompt with an RPC error. They do not raise it when the user pressed Stop, when the process died (that stays `ended`), or when a queued prompt starts next.
* The slim `session.attention` user-event payload gains optional `kind` and `kind_label`. No `kind` (older agent) reads as a Claude session.
* Terminal sessions of kind `codex`, `antigravity`, `opencode`, `copilot`, `pi`, `shell` and `custom_*` have no Claude Code hooks. They raise attention from OSC 7501 program-status reports (Program Status Protocol rev 0.4, `ESC]7501;state=...[:kind=...][:app=...]`).
* For these sessions the agent answers the support query `ESC]7501;?` and strips it from the replayed stream. For `agy` sessions it sets `AGY_CLI_OSC7501=on` and `AGY_CLI_OSC7501_MESSAGES=brief` unless the user set them.
* Mapping: `blocked` (permission or question) gives `awaiting_permission` at once. `blocked` (auth) is ignored. `done` gives `turn_finished` and `error` gives `turn_failed`, both after a 1.5 s quiet window.
* `working`, `idle`, `clear` and any client keystroke (except DEC 1004 focus reports) cancel a pending alert. `working`, `idle` and `clear` also drop a standing `awaiting_permission`.
* Only a change of state alerts. One session's alerts are at least 5 s apart.
* `claude` and `auto_scan` terminal sessions are unchanged: hooks only. The agent never parses OSC 7501 for them, so nothing alerts twice. They do not raise `turn_failed`.
* Shared-thread dedupe: a Codex chat and a Codex terminal session in the same real folder, on Codex's shared app-server, alert once for the same event within 6 s. Chats on a private app server and managed Codex terminal sessions are excluded.
* Windows ConPTY passthrough of OSC 7501 is unverified.

### Delta 2026-11-09: Antigravity chat sessions

Additive delta within proto-v7 (2026-11-09): **one capability entry, one row field, one create-field mapping. No version bump; `central_server` is untouched.**

Antigravity sessions open in the chat view too (`ui: "chat"` with `cli: "antigravity"`). There is no adapter: the chat session runs the ACP server built into the Antigravity CLI, `agy acp`, with the `agy` that terminal sessions use (`MOBAIDE_AGY_BIN`, then PATH). So chat and terminal sessions share the CLI version, Google sign-in, settings and stored conversations.

| Where | Change |
|-------|--------|
| `capabilities.chat` | An `antigravity` entry (`resume: true`, `live: false`) when `agy` is found and `agy help acp` succeeds (a yes or no answer is kept until the binary changes; a missing `agy` or a failed probe is checked again after 30 s). |
| `ChatStoredSessionsResponse` rows | `open_elsewhere` (bool, default `false`): another app on the PC has the conversation open, so messages sent from both apps can overwrite each other. Only Antigravity sets it. Clients show a warning on the row; they still let the user open it. |
| Antigravity options in a chat create | `skip_permissions` → `agy acp --dangerously-skip-permissions`, `sandbox` → `agy acp --sandbox`, `add_dirs` → `additionalDirectories` of `session/new` and `session/load` (made absolute against the session folder), `model` (a name from `agy models`, e.g. `Gemini 3.8 Flash (High)`) → the `model` config option plus `thought_level` when the name carries a thinking level. A name the agent no longer offers is skipped. `worktree` / `branch_name` as for terminal sessions. |

* `agy acp` works in its own working directory, not in the `cwd` of `session/new`, so each chat session is a separate `agy acp` process started in the session folder. The session list process runs in the home folder.
* `agy`'s `session/list` ignores its `cwd` filter and is not sorted. The PC agent keeps the rows of the requested folder (compared as real paths) and sorts them newest first.
* Folder trust: `agy acp` skips the "Do you trust the contents of this project?" prompt of the `agy` TUI. The PC agent asks in the chat (as for Claude Code and Codex). The record is `trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json`; like `agy`, only the exact folder (real path) counts, never a parent or the repository root. `trust` appends the real path, and records nothing for the home folder, a folder above it or a root (that trust lasts for the session).
* Conversation lock: every `agy` process that opens a conversation holds an exclusive `flock` on `~/.gemini/antigravity-cli/presence/<conversation id>.lock`, and a second `agy` warns that "sending messages from both may cause conflicts". `agy acp` takes no such lock, so the chat session takes it after `session/new` or `session/load` and keeps it until the chat closes or the agent exits; an `agy` in a terminal then shows its warning. When another app already holds it, the chat shows a `notice` item (severity `warning`) and tries again before each turn. Not on Windows.
* Reload after the lock frees: the other app may have added turns this process never saw. The next message waits in the queue (status `starting`), the session reopens the conversation in a new `agy acp` process (`session/load`, new epoch, as for Restart), shows a `notice` "Loaded the latest messages", and then sends it.
* Continuing a conversation is not live: a conversation still open in an `agy` terminal does not show that terminal's new messages, and the terminal does not show the chat's. `agy acp` has no `_session/steering`; **Send now** goes out as a second `session/prompt` (Delta 2026-11-27).
* Command output: `agy` reports a finished command as one text, `Command exited with code N.` followed by `Command output:` and the output, or by `No output from the command.`. The PC agent turns it into the same `console` part as other agents' command output (see Delta 2026-11-08, Command output).
* The env file key `MOBAIDE_ACP_ANTIGRAVITY_COMMAND` overrides the command. Test mode runs the fake agent with `--flavor antigravity`.

### Delta 2026-11-09: worktree base branch

Additive delta within proto-v7 (2026-11-09, choose the branch a new session's worktree starts from — web, Android and iOS): **one additive REST route, one additive session-create field, one additive capability key, no version bump.** Both transports are byte-identical.

#### Base-branch list route

``GET /api/projects/{name}/git/worktree-bases`` → ``WorktreeBasesResponse``:

```json
{
  "default": "origin/main",
  "last_used": "origin/develop",
  "refs": [
    {"ref": "origin/main", "kind": "remote", "subject": "…", "committed_at": "2026-11-08T10:15:00+00:00"},
    {"ref": "wip", "kind": "local", "subject": "…", "committed_at": "…"}
  ]
}
```

* ``refs`` lists every local branch (``kind: "local"``, the bare name) and every remote-tracking branch (``kind: "remote"``, ``<remote>/<name>``). ``<remote>/HEAD`` and refs under no configured remote are left out.
* Order: ``default`` first, then the others by tip commit date, newest first.
* ``default`` is the repository's default branch: ``origin/HEAD`` first, then ``origin/<MAIN_BRANCH>``, then the local ``<MAIN_BRANCH>`` (agent setting, default ``main``). ``null`` when none exists; the agent then branches from ``HEAD``.
* ``last_used`` is the ``base_branch`` of the last worktree session created in this workspace, from any client. It is stored in the agent's ``projects.json`` (never in the public workspace row) and follows a rename. ``null`` when unset or when that ref no longer exists.
* ``default`` and ``last_used`` are always ``null`` or a ``ref`` present in ``refs``.
* The route reads local refs only and never fetches. Errors are the workspace-git ones: ``404`` unknown workspace, ``400`` not a git repository, then the session git vocabulary.

Clients preselect ``last_used``, then ``default``, then nothing (the agent's own default).

#### ``base_branch`` on session create

``base_branch: str | null`` (default ``null``) joins ``branch_name`` in ``claude_options``, ``codex_options``, ``antigravity_options`` and the top-level Terminal-mode fields of ``POST /api/sessions`` and the relay ``create_session`` control message. It applies only when ``worktree`` is ``true`` on a git workspace:

* A local branch name is used first (``refs/heads/<base>``). Otherwise, when ``<base>`` starts with a configured remote name, the agent fetches that remote (best effort) and uses ``refs/remotes/<base>``. Full refnames keep a same-named tag from winning.
* An unknown base or one starting with ``-`` fails the create with the usual worktree ``400`` (``Could not create worktree on branch <b>: base branch '<base>' not found``).
* ``null`` or blank keeps the old start point (fetch ``origin``, then ``default`` above, then ``HEAD``).
* The base is ignored when the worktree or the branch already exists and is reused. It is then not resolved, so a deleted base does not block reopening the branch.
* After a successful worktree resolution the agent records the base as the workspace's ``last_used``. A ``null`` base (the "Default branch" row) clears it.
* Older agents ignore the field and branch from their default.

#### Worktree-base capability key

``GET /api/version`` ``capabilities.git`` gains ``"worktree_base": true`` (both transports). Clients show the base-branch picker only behind it.

Golden fixtures: new ``tests/protocol_fixtures/dto/agent/WorktreeBasesResponse.json``; the ``full`` cases of ``ClaudeOptionsModel``, ``CodexOptionsModel``, ``AntigravityOptionsModel`` and ``CreateSessionRequest`` gain ``base_branch``.

### Delta 2026-11-08: Codex chat sessions, live Codex sessions, Send now

Additive delta within proto-v7 (2026-11-08): **one capability key, one chat command, one chat state key. No version bump; `central_server` is untouched.**

Codex sessions open in the chat view too (`ui: "chat"` with `cli: "codex"`), through [`@agentclientprotocol/codex-acp`](https://github.com/agentclientprotocol/codex-acp). It runs the host's own `codex app-server` (`CODEX_PATH`), so chat and terminal sessions share the CLI version, login, settings and stored sessions.

| Where | Change |
|-------|--------|
| `capabilities.chat` | A `codex` entry when the adapter can run. Every entry gains `live`: `true` when sessions stay in sync with the agent's own apps on the PC (Codex on macOS and Linux). Missing `live` reads as `false`. |
| `'c'` command `steer` | `queue_id`. Sends a queued message into the running turn now (`_session/steering` with `idleBehavior: "promptRequired"`). `data: {steered: true}` when the turn took it (the message leaves the queue and shows as a `user` item); `{steered: false}` when no turn could take it (it stays first in the queue and runs next). A `dequeue` of a message being steered fails. |
| State key `steering` | `true` when the agent can take a message during a turn (`initialize._meta.steering.supported`). Clients offer **Send now** on queued messages only then. |

* Codex options map onto the session: `full_auto`, or sandbox `danger-full-access` with `approval: never` → mode `agent-full-access`; sandbox `read-only` → `read-only`; anything else → `workspace-write`, where a person (not Codex's reviewer model) approves each step outside the sandbox (the chat's mode chip offers Auto review). The mode is required: when the agent cannot apply it, the session does not start (error with Restart) instead of running in the agent's default mode. `model` → `model`, `reasoning_effort` → `reasoning_effort`, `initial_prompt` → the first prompt. A Codex sign-in that is missing answered `409 codex_auth_required`, as for terminal sessions, until the 2026-11-11 delta "sign-in from the chat".
* Codex's trust record is `[projects."<folder>"] trust_level` in `$CODEX_HOME/config.toml` (default `~/.codex`). Like Codex, the agent looks up the folder (real path, then as given), then its git repository root, then the main checkout of a linked worktree; the first key found decides, and no other parent folder counts. `trust` appends a `trust_level = "trusted"` table for the main checkout (or the repository root, or the folder), and records nothing for the home folder, a folder above it, a root, or a folder the file already lists (that trust lasts for the session).

#### Live Codex sessions

`codex` in a terminal keeps its sessions in Codex's shared app-server (the daemon behind `codex agents`, control socket `$CODEX_HOME/app-server-control/app-server-control.sock`, JSON-RPC over a WebSocket). When that socket answers at session start, the chat session joins it through `mobaide/acp/codex_bridge.py`, a stdio-to-WebSocket relay that the adapter starts as its app-server. Mobaide never starts the daemon itself, and uses a private app-server instead on Windows, for a workspace with a conda env (the daemon runs commands with its own environment), or when the socket does not answer.

* "Continue a session" then opens a session that is live in a terminal: both sides see the conversation and can send messages.
* Codex reports its thread status in `session_info_update._meta.codex.threadStatus`. A turn that another app started shows as `status: "running"` after a `notice` item ("Message sent from another app on your PC"), also when it was already running as the chat opened; the adapter does not forward that message's text. Prompts sent meanwhile wait in the queue, and `cancel` stops that turn (after 15 s without an end report the chat returns to `ready`).
* Closing the chat cancels only the turn it started and does not send `session/close`, which would stop a turn of the other app.

#### Command output

Tool calls can carry command output in `_meta` instead of content: `terminal_output` (replaces) and `terminal_output_delta` (appends) `{terminal_id, data}`, and `terminal_exit` `{exit_code}` (Codex always; Claude Code for background commands). The agent turns it into one `text` part, a fenced `console` block without escape sequences (a carriage return overwrites its line), plus `Exit code N` when it is not 0. While the command runs, the part holds the last 8 KB and the item is re-sent at most every 0.3 s; when it ends, the usual 32 KB head and tail of the last 128 KB. `terminal` content parts are dropped.

#### Adapter install

The PC agent no longer runs the adapters through `npx`: `npm exec` exports its own settings (`npm_config_ignore_scripts` …) into the adapter, and from there into every command the coding agent runs. The adapters are pinned (`claude-agent-acp@0.110.1`, `codex-acp@2.7.0`), installed once with `npm install --ignore-scripts --omit=optional --before=<release date>` into `<state prefix>/acp-adapters/<name>-<version>/`, and started with `node`. `--before` resolves the adapters' dependency ranges to versions published before the pinned release. Optional dependencies are the adapters' bundled copies of the CLIs, which chat sessions never use. The first chat session after an install or an update shows a "Setting up …" notice while npm runs. An adapter command on PATH, or `MOBAIDE_ACP_CLAUDE_COMMAND` / `MOBAIDE_ACP_CODEX_COMMAND`, still wins. Test mode runs the fake agent with `--flavor codex` for Codex.

### Delta 2026-11-07: chat sessions (ACP)

Additive delta within proto-v7 (2026-11-07): **one session wire field, two create fields, one capability key, one REST route and two frame types on the existing per-session socket. No version bump, and `central_server` is untouched** (the REST proxy and the session socket are opaque to it).

A session can now run in the structured **chat UI**. The agent then starts an [Agent Client Protocol](https://agentclientprotocol.com) agent (for Claude Code: `@agentclientprotocol/claude-agent-acp`, which drives the host's own `claude` binary) as a stdio JSON-RPC subprocess, and turns its updates into an agent-agnostic timeline. The code is `mobaide_agent/mobaide/acp/`.

#### Chat capability, create fields and wire field

| Where | Change |
|-------|--------|
| `GET /api/version` | `capabilities.chat` maps each agent kind that can start in the chat UI on this host to `{"resume": bool}`, e.g. `{"claude": {"resume": true}}`. A missing kind means "not on this host" (for Claude: no `claude-agent-acp` and no `npx`). A missing `chat` key means an older agent. |
| `POST /api/sessions`, `create_session` | `ui`: `"chat"` \| `"terminal"` \| null (null = terminal). `chat_resume_id`: chat only, an id from the route below — continue that stored session instead of starting one. A resume of a session that is already open answers with the open session. |
| Session wire dict | `ui`: `"chat"` or `"terminal"`. Clients open the chat screen for `"chat"` and the terminal for anything else. |

* `ui: "chat"` for a kind without a chat agent → `400`. No runnable adapter on the host → `503` with `chat_unavailable: true`.
* Claude options map onto the ACP session: `skip_permissions` / `plan_mode` → the `mode` config option (plan mode is required: when the agent cannot apply it, the session does not start, as for the Codex mode), `model` / `effort` / `fast_mode` / `ultracode` → the matching config options, `append_system_prompt` → `systemPrompt` (append), `initial_prompt` → the first prompt (new sessions only). The config options also apply to a resumed session, and Restart re-applies the values in use before it. `debug` and `focus_mode` are terminal-only.
* The env file key `MOBAIDE_ACP_CLAUDE_COMMAND` overrides the adapter command. `MOBAIDE_ENV=test` runs the scripted fake agent `mobaide/acp/fake_agent.py` (set `MOBAIDE_ACP_REAL=1` to keep the real one).

#### Stored sessions route

`GET /api/chat/{kind}/sessions?cwd=<abs>&cursor=<opaque>` → `ChatStoredSessionsResponse` `{"sessions": [{"session_id", "cwd", "title", "updated_at", "open_session_id"}], "next_cursor"}`, newest first.

* It lists every session the agent stored on the host, also those started outside Mobaide (for example `claude` in a terminal), except the sessions of the `voice-cleanup` work folder, which are dropped before the page is cut. This is how a client continues them; there is no live attach to a running TUI.
* `open_session_id` names the open Mobaide chat session that already shows the row.
* Errors carry `{"detail"}`: `400` bad `cwd` or cursor, `404` no chat agent of that kind, `409` the agent cannot list, `502` the agent failed, `503` it cannot run here, or its adapter is still installing for first use (the install goes on; retry in a minute).
* On the relay it runs aside from the tunnel reader, like the voice routes: its first call starts the agent process.
* `can_delete` and `DELETE /api/chat/{kind}/sessions/{session_id}` (→ `204`): see *Delta 2026-11-21: delete a stored chat session*.

#### Chat frames on the session socket

Chat sessions use the same `/ws/session/{id}` and `CH_SESSION_IO` channel. Two frame types join the PTY set; each payload is one UTF-8 JSON object:

| Byte | Direction | Payload |
|------|-----------|---------|
| `0x63` `'c'` | client → agent | a command: `{"id", "op", …}` |
| `0x61` `'a'` | agent → client | a frame: `snapshot`, `events` or `result` |

* On attach the agent sends the state frame and, inside the usual `'b'` … `'e'` markers, one `'o'` frame telling a client without chat support to update. Chat clients ignore those three and send `sync`.
* The relay broadcasts agent frames to every attached client, so frames are idempotent and command results carry the command `id`.

| Command `op` | Fields | Effect |
|--------------|--------|--------|
| `sync` | `epoch?`, `after_seq?` | The missed `events` (or an empty `events` ack), else a `snapshot`. No `result`. |
| `prompt` | `text`, `images?` (`[{mime, data}]`, ≤ 4, base64 ≤ 1.5 MB each and ≤ 3.5 MB together, so the frame stays under the 4 MiB tunnel limit) | Sends the message, or queues it while a turn runs (`data: {queued, queue_id}`). |
| `cancel` | — | Cancels the turn (`session/cancel`); pending requests are answered as cancelled. |
| `set_config` | `config_id`, `value` | `session/set_config_option` (or `session/set_mode` for agents without config options). |
| `respond` | `request_id`, `option_id` or `action` + `values` | Answers a permission or a form question; the first answer wins. |
| `dequeue` | `queue_id` | Removes a queued message. |
| `restart` | — | After a failure: respawns the agent and reloads the conversation (new epoch). |
| `new_conversation` | — | Starts an empty conversation with the same settings in a new agent process (new epoch) (Delta 2026-11-18). |
| `cancel_subagent` | `subagent_id` | Stops one subagent; the turn goes on (Delta 2026-11-25). |
| `sign_in` | `action` (`start` + `method`, `submit` + `value`, `cancel`) | Drives the `sign_in` request (Delta 2026-11-11 "sign-in from the chat"). |

| Frame `t` | Fields | Client action |
|-----------|--------|---------------|
| `snapshot` | `epoch`, `seq`, `truncated`, `state`, `items` | Replace everything. |
| `events` | `epoch`, `seq`, `events[]` with own `seq` | Apply in order: `item` (upsert by `id`), `append` (add `text` to an item), `remove` (drop the item with that `id`; an unknown `id` is a no-op), `state` (shallow-merge `patch`). Skip an unknown event type but count its `seq`. On a gap or another epoch, send `sync`. |
| `result` | `id`, `ok`, `data?`, `error?` | Resolve the command with that `id`; ignore others. |

* State keys: `status`, `error`, `agent`, `title`, `cwd`, `acp_session_id`, `config`, `commands`, `usage`, `plan`, `requests`, `queue`, `prompt_caps`, `turn_started_at`, `cancelling`, `steering` (Delta 2026-11-08), `background` (Delta 2026-11-16), `new_conversation` (Delta 2026-11-18), `limit_wait` (Delta 2026-11-21), `prompt_suggestions` (Delta 2026-11-27).
* Item types: `user`, `agent`, `thought`, `tool`, `notice`, `compaction`, `turn_end`, `error`, `subagent` (Delta 2026-11-25). Tool content parts: `text`, `diff` (a unified diff the agent computes), `image`, `link`, `resource`, `terminal`.
* Bounds: text parts 32 KB, message texts 256 KB, diffs 48 KB / 1 200 lines, a snapshot ~2 MB (`truncated` marks dropped older items).
* Attention works as for terminal sessions: a pending request raises `awaiting_permission`, a finished turn `turn_finished`, an agent exit `ended` (`exit_reason: process_exited`). A Claude chat raises `turn_finished` from Claude Code hooks (Delta 2026-11-15). An RPC error on the user's own prompt raises `turn_failed` (not after Stop, not on process death, not when a queued prompt starts next, not when a usage-limit wait is armed (Delta 2026-11-21)).

#### ACP use

| ACP surface | Used for |
|-------------|----------|
| `initialize` | Client capabilities `session.notices`, `session.compaction`, `session.configOptions.boolean`, `elicitation.form`; `subagents` for Claude, Codex and Cursor (Delta 2026-11-25). No `fs` or `terminal`: the agent already works on the host. |
| `session/new`, `session/load`, `session/list`, `session/close` | Create, continue (with history replay), list, close. |
| `session/prompt`, `session/cancel`, `session/update` | Turns and the timeline. |
| `session/set_config_option`, `session/set_mode` | Mode, model, effort and other options. |
| `session/request_permission`, `elicitation/create` (form) | Approval cards and question forms (Claude's AskUserQuestion). |
| `$/cancel_request` (from the agent) | Cancels the agent's open request with that `requestId`: its card leaves `requests`, `awaiting_permission` clears when no card is left, and the reply is error `-32800`. An answer the user already gave wins. An unknown or finished id is ignored. |

An agent run over ACP skips its own "trust this folder?" prompt. So before `session/new` or `session/load`, the PC agent checks the agent's trust record (for Claude Code, `projects[<folder or a parent>].hasTrustDialogAccepted` in `~/.claude.json`, real paths only, and parents only up to the enclosing git repository root — stricter than the CLI, which walks every parent). For a folder that is not trusted, it publishes a `permission` request (`tool_call_id: null`, options `trust` / `decline`) in the normal `requests` state. `trust` records the trust the way the CLI does and continues; for the home folder, a folder above it or a root it records nothing, and the trust lasts for this session only (Restart does not ask again). `decline` ends the start with an error that offers Restart. The adapter process itself runs in the home folder (how it is installed: Delta 2026-11-08).

#### Chat golden fixtures

`tests/protocol_fixtures/frames/chat_frames.json` (every command and frame), and `ChatStoredSessionsResponse.json` plus new cases in `SessionResponse.json`, `CreateSessionRequest.json` and `VersionInfoResponse.json` under `dto/agent/`.

### Delta 2026-11-04: reorderable new-session lists

Additive delta within proto-v7 (2026-11-04, drag-to-reorder in the new-session wizard): **one additive ``central_server`` REST resource, one additive user-event kind, no version bump.** The agent is untouched.

**Workspaces** reuse the agent's existing ``PATCH /api/projects/order`` (full name list; the ``200`` body is the re-sorted list, and ``projects.changed`` follows). The order lives in the agent's ``projects.json``, so every client shows it.

**Mode-picker cards** (the "agents" step) are ordered per account on the central server:

| Route | Body | Result and notes |
|-------|------|------------------|
| ``GET /api/session-mode-order`` | none | ``{"order": [str], "version": int, "updated_at": str \| null}``. ``order: []``, ``version: 0`` and ``updated_at: null`` until the first save. |
| ``PUT /api/session-mode-order`` | ``{"order": [str]}`` | Full replace; same body as ``GET``. ``[]`` restores the default order. |

* Dashboard/user JWT, non-admin.
* Each id matches the shortcuts ``KIND_PATTERN`` (``^[a-z0-9_]{1,32}$``): ``claude``, ``codex``, ``antigravity``, ``opencode``, ``copilot``, ``cursor``, ``junie``, ``pi``, ``auto_scan``, ``shell``, ``custom_<12hex>``. At most 64 ids, no duplicates → else ``422``. The server stores the ids opaquely and does not check them against the live custom types.
* ``version`` grows by one on every change. A ``PUT`` of the stored order changes nothing and pushes nothing.
* A change pushes the user event ``session_mode_order.updated`` with the same body to every user-events socket of the account. Clients keep the higher ``version`` of the cached and incoming snapshot, and reconcile with ``GET`` on every user-events connect.
* Clients apply the list the same way: a card the list names is ranked by its place there; a card it does not name (a custom type created after the last save, a mode a newer client added) keeps its default place among the other unnamed cards, after the named ones; an id that names no card is skipped.
* Stored in ``user_session_mode_orders`` (``PRAGMA user_version`` 23, Alembic ``0021``) and purged with the account.

Older clients ignore the event kind and never call the route, so they keep the default order.

### Delta 2026-11-03: voice transcript cleanup

Additive delta within proto-v7 (2026-11-03, voice transcript cleanup — web, Android and iOS): **one additive REST route, one additive capability key, no version bump.** An opt-in, per-agent client setting, offered in Claude Code sessions at first (Codex and Antigravity below), has the session's AI agent tidy a dictated transcript before the review: fillers, stutters and false starts go, self-corrections keep the final version, and punctuation and technical terms are fixed.

* ``capabilities.voice`` gains ``"cleanup": true`` (the route exists;
  whether Claude Code is installed is only known when it runs). Clients
  offer the setting only when the key is present.
* ``POST /api/voice/cleanup`` ``{"text"}`` (1–6000 characters) →
  ``VoiceCleanupResponse {"text"}``: the cleaned text, or the input when
  there was nothing to clean. It is independent of the speech model's
  install state. The agent runs one hidden ``claude -p --model haiku
  --effort low --safe-mode --no-session-persistence --tools=
  --system-prompt-file cleanup-prompt.md --output-format json`` per call, with
  the transcript on stdin (never argv) in an agent-owned directory that
  holds only the prompt file, under the user's own Claude Code login. No
  session is created, persisted or listed. It answers within 20 s (under
  the relay's 30 s proxy timeout), runs at most two at once, and never
  logs the text. A running cleanup, like an open dictation, makes the
  auto-updater defer its restart. Both transports answer
  byte-identically, and the relay runs the call off the tunnel reader
  like the other voice routes.
* Refusals (``{"detail": {"code", "message"}}``):
  * ``503 cleanup_unavailable``: no Claude CLI, or an agent built without a
    cleaner.
  * ``503 cleanup_outdated``: the CLI predates ``--safe-mode`` (2.1.169).
  * ``409 cleanup_busy``.
  * ``503 cleanup_timeout``.
  * ``503 cleanup_failed``: ``message`` carries the CLI's own error text,
    such as "Not logged in". It also covers an answer longer than twice
    the input plus 200 characters, which means the model replied to the
    text instead of cleaning it.
  * ``422`` validation.
* Clients fall back to the raw transcript on any failure and say why. The
  web review keeps the original one tap away. Clients wait at most 35 s
  for the answer.
* Golden fixtures: ``VoiceCleanupRequest.json`` and
  ``VoiceCleanupResponse.json``. The ``voice_capability`` case in
  ``VersionInfoResponse.json`` carries the new key.
* The setting is stored on the agent, so every client of it shares one
  choice (2026-11-07):
  * ``VoiceStatusResponse`` gains ``cleanup_enabled`` (bool, always
    emitted; clients read a missing key as ``false``). It does not depend
    on the install state and survives ``DELETE /api/voice/model``.
  * ``PUT /api/voice/cleanup/settings`` ``{"enabled": bool}`` (strict
    bool) → the full ``VoiceStatusResponse``. The agent stores it in
    ``<voice root>/cleanup.json`` and publishes ``voice.changed``, so the
    other clients update live. ``500 storage_error`` when the file cannot
    be written; ``422`` validation. Both transports answer identically.
  * The first-opt-in privacy notice stays per device.
  * Golden fixtures: ``VoiceCleanupSettingsRequest.json``; every
    ``VoiceStatusResponse.json`` case carries ``cleanup_enabled``.
* Codex and Antigravity sessions clean up too, each with its own CLI's
  cheapest model (2026-11-08):
  * ``capabilities.voice`` gains ``"cleanup_kinds": ["claude", "codex",
    "antigravity"]``, the session kinds the agent can clean up for.
    Clients offer cleanup in a session whose ``kind`` is in the list. An
    agent with ``"cleanup": true`` and no ``cleanup_kinds`` (or a
    malformed one) means ``["claude"]``.
  * ``VoiceCleanupRequest`` gains ``kind`` (one of those strings; absent
    means ``"claude"``, anything else is a ``422``). Clients always send
    the session's kind. The agent runs that kind's CLI, resolved like its
    sessions (``MOBAIDE_CODEX_BIN`` / ``MOBAIDE_AGY_BIN``, PATH,
    fallbacks), in the same directory and with the same limits:
    * ``codex``: ``codex exec --model <first Luna in the cached catalog,
      else gpt-6-luna> -c model_reasoning_effort=low -c
      model_instructions_file=cleanup-prompt.md -c features.shell_tool=false
      -c features.unified_exec=false -c features.hooks=false -c
      web_search=disabled --sandbox read-only --skip-git-repo-check
      --ephemeral --ignore-user-config --ignore-rules --json -``, transcript
      on stdin. ``codex login status`` runs first, inside the same 20 s
      budget: signed out is ``cleanup_failed`` at once. Only a run that
      exits 0 after ``turn.completed`` counts as a cleanup.
    * ``antigravity``: ``agy --model <first Gemini Flash on low thinking
      in the cached list, else gemini-3.8-flash-low> --input-format
      stream-json --output-format stream-json --disable-slash-commands
      --settings antigravity-settings.json``. The settings file sets
      ``toolPermission: strict``, so headless ``agy`` denies every tool.
      The rules and the transcript go in one stream-json user message on
      stdin (``agy`` has no system-prompt option). Antigravity keeps the
      run in its own local conversation history.
  * Errors keep their codes; ``message`` names the CLI.
    ``cleanup_outdated`` also covers a Codex that rejects a flag or
    ignores ``model_instructions_file``, and an ``agy`` that rejects a
    flag; the hint is ``codex update`` / ``agy update``. An ``agy`` turn
    that tried a tool is ``cleanup_failed``.
  * Golden fixtures: every ``VoiceCleanupRequest.json`` case carries
    ``kind``; the ``voice_capability`` case carries ``cleanup_kinds``.

### Delta 2026-10-19: Claude focus view toggle

Additive delta within proto-v7 (2026-10-19, Claude focus view toggle): **additive optional field, no version bump.** ``claude_options`` gains ``focus_mode`` — bool or null, default ``null``.

The agent merges it into the session's single ``--settings`` source as Claude Code's ``viewMode`` setting:

| ``focus_mode`` | ``viewMode`` result |
|----------------|---------------------|
| ``true`` | ``"focus"`` |
| ``false`` | ``"default"`` — explicit, so a sticky host-side ``/focus`` cannot keep a session in focus view after the user turned the toggle off; it also overrides a host ``verbose: true`` |
| ``null`` / absent | no ``viewMode`` key |

* An unknown top-level settings key raises no startup dialog (verified with ``claude --settings <f> doctor`` on 2.1.303), so the merged hooks file stays safe on CLIs predating ``viewMode``.
* All three clients surface it as a **Focus view** toggle (default on, remembered per Claude / Auto Scan bucket) and always send a concrete bool.
* Old client + new agent therefore keeps the pre-change behaviour; new client + old agent is a no-op (the old agent ignores the unknown key).
* The relay forwards ``claude_options`` opaquely, so ``central_server`` is untouched.
* Fixtures ``tests/protocol_fixtures/dto/agent/{ClaudeOptionsModel,CreateSessionRequest,StartScanRequest}.json`` carry it.

### Delta 2026-10-17: host path suggestions

Additive delta within proto-v7 (2026-10-17, host path suggestions): **one additive REST route, one additive capability key, no version bump.** Backs folder suggestions under the workspace form's Path field and the new-session wizard's one-off custom path, on both transports byte-identically (the relay forwards it through the ``/proxy`` catch-all; no ``central_server`` change).

* ``GET /api/fs/dirs?path=<dir>[&q=<substring>]`` answers ``FsDirsResponse {"path", "dirs", "truncated"}``: the names (not paths) of ``path``'s subdirectories — symlinks to directories included, dot directories included, sorted case-insensitively. ``path`` is expanded with ``expanduser`` and must then be absolute; the response echoes the expanded directory so a client completing ``~/co`` writes back an absolute path.

  ``q`` keeps only names containing it case-insensitively.

* One ``scandir`` per call, no recursion, stopped at 5 000 entries scanned, 1 000 names returned or 250 ms — ``truncated: true`` then means the list is partial: a client must not filter it locally as complete and re-asks with ``q`` set to what the user typed.

  Scans run on a private two-thread pool, and the agent answers ``503`` if a scan has not finished within 1 s — a hung network mount can pin at most those two threads, and stalls the relay tunnel's reader (which awaits each proxied request) for at most 1 s per call.

**Errors** carry ``{"detail": <str>}``:

| Status | Cause |
|--------|-------|
| ``400`` | Empty, relative or malformed ``path``. |
| ``404`` | Missing or not a directory. |
| ``403`` | Permission denied (including macOS privacy-protected folders). |
| ``503`` | Timed out. |

Clients treat every error as "no suggestions".

* ``GET /api/version`` ``capabilities`` gains ``"fs": {"dirs": true}``; clients offer suggestions only when it is ``true``.

* ``capabilities.fs.windows_paths`` is ``true`` on a Windows host, where the echoed ``path`` also has every ``/`` rewritten to ``\``: clients word the browse hint for drive letters and show picked folders with a ``\`` separator. Absent (an older agent) reads as ``false``.

* Clients fetch each directory once, filter locally while the user types within it, and cache listings per agent for 30 s.

### Delta 2026-10-14: on-PC voice input

Additive delta within proto-v7 (2026-10-14, on-PC voice input — the agent half, #736; the Android/iOS/web half is #737): **eight additive REST routes, one additive event, one additive capability key, no version bump.**

Speech-to-text runs on the agent's PC (NVIDIA Parakeet TDT 0.6B v3 int8 through sherpa-onnx, CPU only) in a helper process that starts on the first dictation and exits after 3 idle minutes.

It is opt-in: nothing is downloaded or started until a client (or ``mobaide-agent voice install``) enables it, and agent updates never fetch the model.

Both transports answer byte-identically. The relay runs ``/api/voice/*`` requests off the tunnel reader, so a waiting ``finish`` never stalls other sessions.

#### Voice capability key

``GET /api/version`` ``capabilities.voice`` = ``{"dictation": true, "sample_rate": 16000, "max_seconds": 120, "max_chunk_bytes": 65536}`` whenever the agent build supports voice, whatever the install state. Clients offer voice only when the key is present.

#### Voice status response

``GET /api/voice/status`` → ``VoiceStatusResponse {"state", "enabled", "repairing", "model": {"id", "name", "languages", "download_bytes", "disk_bytes", "ram_bytes", "notice"}, "progress": {"done_bytes", "total_bytes"} | null, "host": {"ram_total_bytes", "disk_free_bytes", "cpu_count", "recommendation", "reasons": [{"code", "message"}]}, "speed_factor", "error": {"code", "message"} | null}``, every key always present.

| Field | Notes |
|-------|-------|
| ``state`` | An open vocabulary (clients treat an unknown value as unavailable). Values are listed below. |
| ``repairing`` | True while voice is enabled with its verified model in place and only the runtime is missing or has not yet passed the self-test with it. The agent re-fetches the ~15 MB runtime by itself after a Python upgrade. Clients label that progress as an update, not a first install. |
| ``model.languages`` | ISO 639-1 codes (25 European; no ja/zh/ko/ar/hi/tr). |
| ``model.notice`` | The model's attribution and license text (CC BY 4.0 for Parakeet), shown by clients next to the model. |
| ``speed_factor`` | Seconds of speech per second of transcription, from the self-test. Set only while ``ready``. |
| ``host.recommendation`` | ``ok``, ``low_resources`` or ``unsupported`` (see below). |
| ``host.reasons[].code`` | Open vocabulary. ``message`` is display text. |

**``state`` values:**

* ``unsupported`` — no runtime for this OS, CPU or Python.
* ``not_installed`` — the default: voice is off.
* ``queued`` — another Mobaide process (e.g. ``mobaide-agent voice install``) holds the install lock.
* ``downloading``
* ``verifying`` — final checks plus a self-test transcription.
* ``ready`` — needs the files and a passed self-test. A repair whose self-test fails reads ``failed`` with ``selftest_failed``, and a retry runs the self-test again.
* ``failed`` — ``error`` is set until a retry. An install whose process died before it finished reports ``failed`` with ``interrupted``.

While ``queued``, the agent re-publishes ``voice.changed`` from disk every 2 s while the other process runs and once it ends. Agents older than that publish nothing for it, so clients also poll ``GET /api/voice/status`` every few seconds while ``queued``.

**``host.recommendation`` values:**

| Value | Meaning |
|-------|---------|
| ``ok`` | No warning. |
| ``low_resources`` | Warn first; the user may still enable. ``reasons`` carry ``low_ram`` under 11.5 GiB, ``low_cpu`` under 4 logical CPUs, ``low_disk`` under 2 GiB free. |
| ``unsupported`` | ``reasons`` carry ``python_unsupported``, ``platform_unsupported``, ``libc_unsupported``, ``disk_insufficient``. |

#### Voice install, cancel and remove

* ``POST /api/voice/install`` ``{"model_id"?}`` enables voice, or retries a ``failed`` install; it is a no-op once ``ready``.
* ``POST /api/voice/install/cancel`` cancels an install.
* Both answer the status at once; progress arrives as events.
* While an install, cancel or remove runs, new dictations are refused with ``409 voice_not_ready``.
* ``DELETE /api/voice/model`` turns voice off and deletes the model and runtime.
* Cancel deletes what was fetched and turns voice off (a ``ready`` install is left alone).
* While ``repairing``, cancel only stops the runtime download. The model stays, voice stays enabled, and the status turns ``failed`` with ``interrupted`` so a retry fetches just the runtime. ``DELETE /api/voice/model`` is the only route that deletes the model.
* A ``failed`` install keeps its partial files and a retry resumes them; an agent restart mid-download resumes by itself.

Install refusals:

| Refusal | Cause |
|---------|-------|
| ``409 voice_unsupported`` | — |
| ``409 install_in_progress`` | Another process holds the lock, or another model is installing. |
| ``409 files_in_use`` | — |
| ``422 unknown_model`` | — |
| ``500 storage_error`` | — |

``error.code`` is open: ``network``, ``http_error``, ``hash_mismatch``, ``size_mismatch``, ``disk_full``, ``io_error``, ``selftest_failed``, ``interrupted`` (a CLI install stopped with Ctrl-C), ``storage_error``, ``internal_error``.

#### Voice status event

Event ``voice.changed`` on the agent events channel (both transports, and in the connect-time snapshot of each): the full ``VoiceStatusResponse``, throttled to 2/s while downloading; state changes go out at once.

#### Voice dictation

1. **Open.** ``POST /api/voice/dictations`` ``{"session_id"?}`` → ``VoiceDictationResponse {"dictation_id", "sample_rate", "max_seconds", "max_chunk_bytes"}``. ``session_id`` is reserved for repository hotwords and unused today.
2. **Upload audio.** ``POST /api/voice/dictations/{id}/audio`` ``{"seq", "pcm16_b64"}`` uploads 16 kHz mono little-endian PCM16, at most ``max_chunk_bytes`` raw per chunk (~87 KB of base64, far below the tunnel's 4 MiB frame cap) → ``{"seq", "duration_ms"}``.
    * ``pcm16_b64`` is standard padded base64 with no line breaks (Android: ``Base64.NO_WRAP``).
    * ``seq`` starts at 0 and **one audio request is in flight per dictation**.
    * A resent ``seq`` is acknowledged without being appended twice.
3. **Finish.** ``POST /api/voice/dictations/{id}/finish`` ``{"last_seq"}`` → ``VoiceDictationResultResponse {"dictation_id", "state", "text", "duration_ms"}``.
    * ``last_seq`` is the highest ``seq`` the agent **accepted** (answered 200; a chunk refused with 4xx, e.g. the one that hit ``too_long``, does not count), ``-1`` when none was.
    * The result is ``done`` with the transcript, or ``transcribing`` after a 20 s wait (under the relay's 30 s proxy timeout). In that case call ``finish`` again; it is idempotent and the result is kept for 2 minutes.
4. **Abort.** ``DELETE /api/voice/dictations/{id}`` aborts (204, idempotent).

Pause-delimited segments are transcribed while audio is still arriving, so ``finish`` normally waits only for the last one.

Refusals have the shape ``{"detail": {"code", "message", …}}``:

| Refusal | Cause |
|---------|-------|
| ``409 voice_not_ready`` | — |
| ``409 voice_busy`` | Two dictations already open or still transcribing. The relay also answers ``503 voice_busy`` past 32 voice requests in flight. |
| ``404 dictation_not_found`` | Unknown, aborted, idle for 60 s, or open for over 5 minutes. |
| ``409 seq_gap {"expected_seq"}`` | Also from ``finish`` while an earlier chunk is missing. |
| ``409 dictation_finished`` | — |
| ``409 too_long {"max_seconds"}`` | — |
| ``413 chunk_too_large {"max_chunk_bytes"}`` | — |
| ``422 bad_audio`` | — |
| ``503 transcription_failed`` | Latched per dictation: once the model fails to load or a segment fails for good, every later ``audio`` and ``finish`` call answers it. A client learns of it while still recording; a retry needs a new dictation. |

Clients put the transcript in the composer for review and never send it automatically. Audio stays in memory and is never logged; the auto-updater defers its restart while a dictation is open.

#### Voice golden fixtures

* Golden fixtures under ``tests/protocol_fixtures/dto/agent/``: ``VoiceStatusResponse``, ``VoiceInstallRequest``, ``VoiceDictationCreateRequest``, ``VoiceDictationResponse``, ``VoiceDictationAudioRequest``, ``VoiceDictationAudioResponse``, ``VoiceDictationFinishRequest``, ``VoiceDictationResultResponse``, plus a ``voice_capability`` case in ``VersionInfoResponse.json``.
* ``voice/pcm16_chunks.json`` pins the client side of the audio format — chunk size, little-endian PCM16, padded base64 without line breaks, and the Float32→PCM16 rule — for all three clients and the agent's decoder.

### Delta 2026-09-21: in-session conflict resolver

Additive delta within proto-v7 (2026-09-21, in-session conflict resolver — read a conflicted file, write its resolution, take one side, rebuild the conflict): **four additive REST routes, one additive status field, one additive capability key, no version bump.** Session-scoped next to the rebase routes, both transports byte-identical.

Every route names exactly one repo-relative ``path`` and acts on it only while the index holds it unmerged under that exact name — a literal pathspec still matches by directory prefix, so the stages are compared byte for byte; that check is what bounds the write route to a file git itself reports as conflicted.

``ours`` is index stage 2 and ``theirs`` stage 3: during a rebase, the branch being rebased onto (plus the commits already replayed) and the commit being replayed.

#### Conflict read route

``GET /api/sessions/{session_id}/git/conflict?path=`` answers ``GitConflictResponse {"path", "code", "state", "kind", "content", "token", "markers", "has_base", "ours": {"label", "present"}, "theirs": {"label", "present"}, "byte_limit"}``.

* ``code`` is the porcelain pair (``UU`` ``AA`` ``DU`` ``UD`` ``DD`` ``AU`` ``UA``).
* ``present`` is false for a side that deleted the file or never had it.
* ``kind`` is an open vocabulary — clients treat an unknown value as "not editable here" (values below).
* Only ``text`` carries ``content`` — LF line endings, BOM stripped — and ``token``, the SHA-256 of the file's bytes as read.
* ``markers`` counts lines starting with ``<<<<<<<``.
* ``has_base`` tells that the file holds ``|||||||`` sections (diff3 style).
* ``label`` names a side for the user and is nullable. While ``rebasing``, ``ours`` is the ``onto`` branch (else its short oid) and ``theirs`` is ``<short_oid> <subject>`` of the stopped commit; while ``merging``, the current branch and ``MERGE_HEAD``.
* Clients never print "ours"/"theirs" and never read the label text inside the markers, which ``restore`` rewrites.

| ``kind`` | Meaning |
|----------|---------|
| ``text`` | Editable; the only kind that carries ``content`` and ``token``. |
| ``too_large`` | Over ``byte_limit``, 256 KiB. The file travels whole inside one tunnel frame, ``json.dumps`` escapes non-ASCII up to 6x, and a frame over the 4 MiB cap closes the tunnel for every session. |
| ``binary`` | A NUL byte. |
| ``encoding`` | Not UTF-8 |
| ``missing`` | — |
| ``symlink`` | — |
| ``submodule`` | — |
| ``unsupported`` | A directory in the file's place. |

Refusals: ``409 not_conflicted`` (message = the path) when the path is not unmerged; ``400 bad_path`` also for any ``.git`` component.

#### Conflict resolve route

``POST …/git/conflict/resolve`` with ``GitConflictResolveRequest {"path", "token", "content", "mark_resolved": true}`` replaces the file and stages it.

Under the per-toplevel lock, the checks run in this order:

1. ``not_conflicted``.
2. ``409 unsupported`` for a submodule or symlink entry.
3. ``409 file_changed`` unless ``token`` matches the file on disk — Claude or the user may be editing it in the terminal — or the file already holds exactly the bytes this request would write (a retry after a lost answer or a failed ``git add``, which skips the write and still runs ``git add``).
4. An atomic replace (exclusive temporary beside the file, its permission bits kept, no symlink followed at any level; ``409 file_busy`` when the platform refuses the rename) and ``git add``.

* The file's own line-ending and BOM convention is re-applied from the bytes on disk (a file mixing endings gets its dominant one), since ``core.safecrlf`` would refuse an LF rewrite of a CRLF file.
* With ``mark_resolved`` a ``content`` still holding a ``<<<<<<<`` / ``>>>>>>>`` line is ``409 conflict_markers`` before anything is written.
* ``mark_resolved: false`` is a draft — it writes the file, markers included, and leaves the path unmerged.
* ``content`` is validated by the service, not the schema (a ``422`` echoes its input): ``400 too_large`` over ``byte_limit`` — measured on the request's LF text and again on the bytes that would land on disk, since CRLF and a BOM grow it — and ``400 bad_content`` for a NUL or an unencodable string.

#### Conflict take route

``POST …/git/conflict/take`` with ``GitConflictTakeRequest {"path", "side": "ours" | "theirs"}`` makes the path look like one side and marks it resolved — the only resolution for a ``kind`` that is not ``text``.

A side that is ``present`` runs ``git checkout --<side>`` and ``git add``; an absent side resolves by removal, which is how a modify/delete conflict is settled as "delete": the agent unlinks the file itself without following a symlink and then drops the index entry with ``git rm --cached`` — plain ``git rm`` unlinks by path and would follow a symlinked parent directory out of the repository.

The choice is read from the index stages, never from git's wording. ``409 unsupported`` for a submodule, a directory in the file's place, a symlink (or non-directory) where a parent directory should be, or a file the no-follow walk cannot reach; ``409 file_busy`` when the platform refuses the unlink.

#### Conflict restore route

``POST …/git/conflict/restore`` with ``GitConflictRestoreRequest {"path", "style": "merge" | "diff3", "token"}`` runs ``git checkout --merge --conflict=<style>``: it rebuilds the markers of a file still in conflict (discarding edits made to it — ``diff3`` adds the original lines), or of a path in ``resolved_paths``. ``--conflict`` is always explicit, so ``merge.conflictStyle`` never decides.

Git labels the rebuilt markers ``ours`` / ``theirs``, which reads backwards during a rebase, so the agent then swaps those two labels for the sides' names (best effort; ``||||||| base`` stays).

``409 not_restorable`` unless the path is unmerged with both sides present or listed in ``resolved_paths`` — on any other path ``checkout --merge`` silently overwrites the working file from the index, so this guard is a safety check. An optional ``token`` adds the ``file_changed`` check.

#### Status field resolved_paths

``GitStatusResponse`` gains top-level ``resolved_paths`` (default ``[]``, at most 200): paths marked resolved during the operation in progress whose conflict git can still rebuild, read from the index's resolve-undo records.

Listing rules:

* Listed only while ``repo.state`` is ``merging``, ``cherry_picking`` or ``reverting``, or ``rebasing`` with ``repo.rebase.stopped_on`` set — the stops whose own index rewrite cleared every older record.
* The records outlive a finished rebase or merge and a pick already committed, so a ``bisecting`` (or ``clean``) repository, a ``git am`` session and a rebase stopped on ``exec`` or ``break`` would offer a conflict nobody is in.
* Only with both sides recorded (``checkout --merge`` cannot rebuild a modify/delete conflict).
* Never for a submodule (git cannot merge a gitlink back into markers).
* Never while unmerged again, and only while "ours" is still ``HEAD``'s blob for the path.

It is a top-level list rather than an entry group because a resolution equal to ``HEAD`` has no status entry at all and one that differs already has a ``staged`` row. The etag covers it.

#### Conflict mutations: shared behaviour

All three mutations refuse ``job_running`` first, share one 25 s budget across their git calls and publish ``git.changed`` whenever the file or the index may have moved — also when ``git add`` fails after the write. ``git checkout`` runs the repository's ``post-checkout`` hook and any configured filters under the agent's account, the trust boundary the rebase and stage routes already have.

#### Conflict capability key

``GET /api/version`` ``capabilities.git`` gains ``"conflicts": true`` (both transports). Clients open the resolver from a conflict row, show the *Resolved* section and word their "resolve the conflicts" hints for the app only with it; without it a conflict row keeps opening the ``HEAD`` diff and the hints keep pointing to the terminal.

#### Windows path validation

Also: ``validate_repo_path`` now rejects ``\`` and ``:`` on Windows agents (``400 bad_path`` on every session git route) — ``os.path.join`` honours the first as a separator and the second opens an alternate data stream; git never reports either.

#### Conflict golden fixtures

Golden fixtures: new ``tests/protocol_fixtures/dto/agent/{GitConflictResponse,GitConflictResolveRequest,GitConflictTakeRequest,GitConflictRestoreRequest}.json``; ``GitStatusResponse.json`` gains ``resolved_paths`` on every case and a ``rebasing_with_resolved_paths`` case.

### Delta 2026-09-21: workspace Git remote delete

Additive delta within proto-v7 (2026-09-21, workspace Git management — delete remote branches, remove a worktree and keep its branch): **one widened request enum, six additive per-item error codes, one additive capability key, no version bump.** Same workspace-scoped ``POST /api/projects/{name}/git/items:delete``, both transports byte-identical (the relay already validates the body with ``GitDeleteRequest``).

#### Remote-branch delete items

``GitDeleteItem.kind`` accepts ``remote_branch`` again, with the row's ``branch`` (``<remote>/<name>``) verbatim; ``force`` does not apply.

The agent answers per item:

* ``invalid`` for a missing or NUL-bearing ``branch``.
* ``not_found`` unless it is one of the rows ``GET /items?remote=true`` lists (a configured remote, not the ``<remote>/HEAD`` symref).
* ``error`` unless the remote's fetch refspecs map the tracking ref back to exactly one ``refs/heads/`` branch (a name-based guess would delete the wrong branch under a custom refspec).
* ``error`` for every item of a remote whose configured push destinations — every ``remote.<r>.pushurl``, else every ``remote.<r>.url`` — are not exactly its one ``remote.<r>.url`` (the row describes the fetch side; a push to another repository would delete there and still report success).

The raw config strings are compared: a ``pushurl`` naming the same repository in another URL form is refused too, while ``url.<base>.pushInsteadOf`` rewrites are not checked, so an https-fetch / ssh-push setup written that way keeps working.

The survivors go out as one ``git push --porcelain -- <remote> :refs/heads/<name>…`` per remote, the remotes in parallel with the batch's local deletes, under the fetch/pull/push jobs' never-prompt environment and a 20 s budget (inside the relay's 30 s). Hooks run.

Result mapping:

* A ``-`` ref line is ``ok`` (git drops the tracking ref; a branch already gone on the remote reports the same and clears the stale ref).
* A ``!`` line is ``rejected``, or ``hook_failed`` for a declining remote hook, with git's summary as ``error_detail``.
* A ref git never reported on takes the jobs' classifier, narrowed to ``auth_required`` / ``network`` / ``remote_not_found`` / ``rejected`` / ``hook_failed`` (else ``error``), and a timeout is ``git_timeout``.

These codes never fail the batch: ``results`` stays one entry per request item, in request order. No busy guard (no worktree is touched). Each success is audited (``audit.git.delete``, ``kind=remote_branch``) and any success publishes ``git.changed``.

#### Remove worktree and keep branch

An ``orphan_worktree`` item removes only the worktree at ``worktree_path`` whatever row it came from, so clients send it for a ``branch_with_worktree`` row's *Remove worktree* action and the branch survives as a ``branch_only`` row. *Delete* on that row keeps removing both.

#### Delete-remote capability key

``GET /api/version`` ``capabilities.git`` gains ``"delete_remote": true`` (both transports). Clients offer delete on ``remote_branch`` rows only behind it — an older agent answers ``422`` for the whole batch — and confirm a batch holding remote rows before it enters the undo window. *Remove worktree* needs no gate.

#### Git screen folder tree

Clients render the Git screen's rows as a folder tree: a branch name splits on ``/`` (a remote row under a top folder named by its ``remote``), folders first then rows, each A→Z by lowercased UTF-16 code units with the raw name as tiebreak; ``orphan_worktree`` rows follow the local tree by path. The New-branch start-point and rebase ``onto`` pickers use the same order.

Presentation only — the wire order of ``GET /items`` is unchanged.

### Delta 2026-09-17: in-session git rebase

Additive delta within proto-v7 (2026-09-17, in-session git rebase — start, continue, skip, abort, and Mark resolved): **four additive REST routes, one additive status field, one additive stage refusal, two additive capability keys, no version bump.** A rebase is a working-tree operation, so it lives on the session-scoped prefix next to the mutations, both transports byte-identical.

#### Rebase start route

``POST /api/sessions/{session_id}/git/rebase`` with ``GitRebaseRequest`` ``{onto}`` runs ``git rebase --no-autostash --end-of-options <onto>``.

Guards, in order: ``409 unknown_ref`` (message = ``onto``) for a ``-``-leading or NUL-carrying ref, then ``job_running``, then under the per-toplevel lock ``operation_in_progress`` (message = the state) unless the state is ``clean``, ``no_head`` on an unborn branch, and ``unknown_ref`` when ``rev-parse -q --verify --end-of-options <onto>^{commit}`` fails.

Whether the tree is too dirty to start is git's own call (``dirty_worktree``: staged or unstaged changes to tracked files, or an untracked file the checkout would overwrite); ``--no-autostash`` keeps ``rebase.autoStash`` from stashing around it. An empty ``onto`` is a ``422``.

The clients' branch pickers send fully qualified refs (``refs/heads/<name>``, ``refs/remotes/<remote>/<name>``) so a tag of the same name cannot shadow the branch; a typed ref goes as entered.

#### Rebase continue, skip and abort routes

| Route | Runs | Notes |
|-------|------|-------|
| ``POST …/git/rebase/continue`` (no body) | ``git rebase --continue`` | Refused with ``409 unmerged`` while ``ls-files -u`` lists anything. |
| ``POST …/git/rebase/skip`` | ``--skip`` | Drops the commit git stopped at (details below). |
| ``POST …/git/rebase/abort`` | ``--abort`` | Back to the branch as it was. |

All three refuse ``409 no_rebase`` (message = the state) unless the state is ``rebasing``, and ``job_running`` first.

Skip details:

* For a rescheduled ``pick``/``reword``/``edit``, which ``--skip`` alone would retry, the agent first removes that line from ``git-rebase-todo`` under git's ``.lock`` and puts it back if the skip then fails without git touching the todo.
* A held ``git-rebase-todo.lock`` is ``409 index_locked``.
* A rescheduled ``squash``/``fixup`` is left for git to retry, because dropping it would corrupt the combined commit's message.

#### Rebase response and outcomes

All four answer ``GitRebaseResponse {"outcome", "rebase", "message"}``. ``outcome`` is an open vocabulary: ``rebased`` (finished), ``up_to_date`` (a start that left HEAD where it was), ``stopped`` and ``aborted``.

``stopped`` is decided from the repository, never from git's wording (which quotes commit subjects and paths): the rebase is still in progress when git exits and the call moved it — a start always does; a continue, skip or abort did when it changed HEAD or ``REBASE_HEAD`` (the commit git stopped at), or left unmerged paths where there were none (a rescheduled pick — one an untracked file blocked — retried into a conflict).

``message`` is ``null`` while unmerged paths explain the stop, else git's own reason (an untracked file in the way of a later pick, say). ``rebase`` is the ``GitRebaseInfo`` below, set only when ``stopped``.

A call that fails without moving the rebase is a ``409`` with the fetch/pull/push jobs' classifier codes and git's explanation as ``message``: ``dirty_worktree`` (``--continue`` with unstaged changes is reported this way too, although git words it like unmerged paths), ``hook_failed`` (the ``pre-rebase`` hook refused), ``operation_in_progress``, ``index_locked``, ``git_error``.

#### Rebase editors, locking and hooks

Editors are disabled with ``GIT_EDITOR=:`` and ``GIT_SEQUENCE_EDITOR=:``, so ``--continue`` keeps the original commit message. Each call runs under the per-toplevel lock with a 25 s budget (the relay gives a proxied request 30 s); a rebase the timeout cuts off is left in progress (``504 git_timeout``) for Abort to undo.

``git.changed {project_name, toplevel, common_dir}`` is published whenever git ran, whatever it answered. Repository hooks (``pre-rebase``, ``post-checkout``, ``post-rewrite``) run under the agent's account, the same trust boundary as commit.

#### Status rebase field

``GET …/git/status`` ``repo`` gains ``rebase``: ``null`` unless the state is ``rebasing``, else ``GitRebaseInfo {"branch", "onto_oid", "onto_short_oid", "onto_ref", "stopped_on": {"oid", "short_oid", "subject"} | null, "step", "total"}`` read from git's state directory (``head-name``, ``onto``, ``msgnum``/``end`` for the merge backend, ``next``/``last`` for the apply backend) and ``REBASE_HEAD``.

``branch`` is null for a detached HEAD, ``onto_ref`` is a branch pointing at the base (a local branch first, then a remote-tracking one) or null, and every key is nullable because a rebase git was killed in need not have written every file. It stays ``null`` during a ``git am`` session, which shares ``rebase-apply``.

The etag covers it.

#### Stage refusal for conflict markers

``POST …/git/stage`` now refuses ``409 conflict_markers`` (message = the path) for an unmerged path whose working-tree file still holds a line starting with ``<<<<<<<`` or ``>>>>>>>``; staging an unmerged path is how a conflict is marked resolved. Binary files, symlinks, submodules and a deleted file (resolving by removal) are not scanned.

#### Rebase capability keys

``GET /api/version`` ``capabilities.git`` gains ``"rebase": true`` and ``"resolve": true`` (both transports).

Clients gate the terminal menu's *Rebase…* entry on ``rebase``, the Changes screen's Continue / Skip / Abort on ``rebase`` with a non-null ``repo.rebase`` — a ``rebasing`` state without it (a ``git am`` session, or an older agent) gets a banner pointing to the terminal instead — and a conflict row's *Mark resolved* action on ``resolve`` (with ``stage``); an agent without ``resolve`` would stage the markers.

#### Rebase golden fixtures

Golden fixtures: new ``tests/protocol_fixtures/dto/agent/{GitRebaseRequest,GitRebaseResponse}.json``; ``GitStatusResponse.json`` gains ``rebase`` on every repo and a ``rebasing_stopped_on_conflict`` case.

### Delta 2026-09-16: workspace Git branches

Additive delta within proto-v7 (2026-09-16, workspace Git management — remote branches, create and check out): **one additive query flag, two additive REST routes, three additive row fields, one additive row kind, one additive capability key, no version bump.** Branches are objects of the repository, not of a session, so ref management lives on the workspace-scoped prefix ``/api/projects/{name}/git`` the delete-only Git screen already used, both transports byte-identical.

#### Branch items route

``GET /api/projects/{name}/git/items?remote=true``:

* Every row now carries ``subject`` and ``committed_at`` (the tip commit's subject line and committer date as ``iso-strict``, both ``null`` when the ref has no commit).
* With ``remote=true``, the remote-tracking branches follow the local rows as ``{"kind": "remote_branch", "branch": "<remote>/<name>", "remote": "<remote>", "is_current": false, …}``. ``<remote>/HEAD`` symrefs are skipped, as are refs under a name that matches no configured remote; ``remote`` is resolved by the longest configured remote name because both halves may contain ``/``.
* The flag exists so clients that predate the kind never receive a row they would offer for delete; the new clients always send it and an older agent ignores it.
* ``remote`` is parsed as a pydantic bool on both transports (``true/t/yes/y/on/1`` and their negatives; anything else is ``422``).

#### Branch create route

``POST /api/projects/{name}/git/branches`` ``{"name", "start_point"?, "checkout"?: false}`` → ``GitBranchResponse {"branch", "checked_out", "created": true}``. ``git branch -- <name> <start_point|HEAD>``; ``checkout: true`` runs ``git switch -c <name> -- <start_point>`` under the checkout guards below.

The agent rejects an empty or > 255-byte name, a leading ``-``, ``HEAD``, ``@`` and any ``@{`` itself (``check-ref-format --branch`` expands revision syntax rather than refusing it), then asks ``git check-ref-format --branch``: ``400 bad_branch_name``.

``409 exists`` when ``refs/heads/<name>`` resolves, ``409 unknown_start_point`` (message = the start point) when ``rev-parse --verify --end-of-options <start>^{commit}`` fails.

A start point naming a local or remote-tracking branch is resolved as that branch (``refs/heads/…`` first, then ``refs/remotes/…``), so a tag of the same name cannot make it ambiguous; the item rows list local branches by their exact name (``%(refname:lstrip=2)``). Tracking follows git's own ``branch.autoSetupMerge``.

#### Checkout route

``POST /api/projects/{name}/git/checkout`` ``{"ref", "force"?}`` → ``GitBranchResponse {"branch", "checked_out": true, "created"}``.

``ref`` is a row's ``branch`` verbatim: a local branch runs ``git switch --no-guess -- <ref>`` in the workspace root; a remote-tracking ``<remote>/<short>`` switches to the local ``<short>`` when it exists, else creates it tracking the remote (``git switch --no-guess --track -c <short> -- <remote>/<short>``, ``created: true``); anything else is ``404 {"detail": {"code": "ref_not_found"}}`` (detached checkouts are out of scope; ``400 bad_ref`` for an empty or ``-``-leading ref).

#### Checkout guards

Checkout guards, all ``409``:

* ``job_running`` (message = job id) while a fetch/pull/push job owns the repository — asked first, before queuing on the per-toplevel lock, so a running job's whole duration is never awaited only to be refused.
* Then, once queued, the workspace-git ``{"detail": {"busy_by": [...]}}`` shape when a session or auto-scan item works in the workspace root. A holder counts when its realpath-normalised cwd is the root or lies below it and not inside a linked worktree, so a session in a sub-folder blocks and a session in a linked worktree does not.
  ``force: true`` (on ``POST /checkout`` and on ``POST /branches`` with ``checkout``; default ``false``) skips only this guard. Clients send it after the user confirms the soft "Workspace folder in use" prompt. An agent older than the field ignores it and answers ``busy_by`` again; clients then ask the user to update the agent. Additive, no version bump.
* ``operation_in_progress`` (message = ``merging`` / ``rebasing`` / ``cherry_picking`` / ``reverting`` / ``bisecting``).
* ``dirty`` and ``checked_out_elsewhere`` (message = the worktree path) are git's own refusals classified from stderr. ``git switch`` carries unrelated local changes along and only refuses an overwrite, so a modified tree does not by itself block a checkout.

The mutation floor (``git_too_old`` below 2.26), ``not_a_repo``, ``bare_repository``, ``unsafe_directory``, ``cwd_missing`` (400), ``git_unavailable`` (503), ``git_timeout`` (504, 15 s) and ``git_error`` (500) are the session vocabulary. Clients read ``detail.busy_by`` first, then ``detail.code``.

Both routes serialise under the worktree manager's per-workspace lock *and* the session mutations' per-toplevel lock, audit (``audit.git.branch.created`` / ``audit.git.checked_out``) and publish ``git.changed {project_name, toplevel, common_dir}``.

#### Branches capability key

``GET /api/version`` ``capabilities.git`` gains ``"branches": true`` (both transports). Clients gate the Git screen's New branch and Check out actions and the terminal menu's *Branches…* entry (a link to the workspace Git screen, only for a session with a ``project_name``) on it; the remote rows need no gate because the flag is sent regardless.

#### GitDeleteItem kind narrowing

``GitDeleteItem.kind`` is narrowed to the three deletable kinds, so a ``remote_branch`` row in a delete batch is a ``422``.

#### Branch golden fixtures

Golden fixtures: ``GitItem.json`` gains ``subject`` / ``committed_at`` and a ``remote_branch`` case; new ``tests/protocol_fixtures/dto/agent/{GitBranchCreateRequest,GitCheckoutRequest,GitBranchResponse}.json``.

### Delta 2026-09-15: in-session git fetch/pull/push jobs

Additive delta within proto-v7 (2026-09-15, in-session git fetch/pull/push jobs): **five additive REST routes + two additive event types + three additive capability keys, no version bump.** Network git operations can outlast the relay's 30 s proxied-request budget and need progress, so they run as agent-side **jobs**: the route answers at once with a snapshot and the work continues in a background task that reports over the agent events channel.

Same session-scoped prefix, both transports byte-identical.

#### Fetch route

``POST /api/sessions/{session_id}/git/fetch`` (no body) runs ``git fetch --progress --prune <remote>`` — the branch's upstream remote, else ``origin``, else the only (first listed) remote.

#### Pull route

``POST …/git/pull`` with ``GitPullRequest`` ``{mode: "ff_only" | "rebase" | "merge"}`` (optional, default ``ff_only``; an empty body is accepted) runs ``git pull --progress --no-stat`` plus ``--ff-only`` / ``--rebase`` / ``--no-rebase --no-edit``. Clients start with ``ff_only`` and offer the two history-rewriting modes only as a confirmed follow-up to ``not_fast_forward``.

#### Push route

``POST …/git/push`` with ``GitPushRequest`` ``{force: false}`` (optional) runs ``git push --progress``, with ``--force-with-lease`` only when ``force`` is true (clients confirm first, and offer it only as a follow-up to a push that failed with ``not_fast_forward`` or ``rejected``) and ``--set-upstream <remote> <branch>`` when the branch has no upstream configured (remote = ``branch.<name>.pushRemote`` / ``remote.pushDefault`` / ``branch.<name>.remote``, else ``origin``, else the only remote).

A branch whose upstream is configured but whose remote-tracking ref is gone (the remote branch was deleted, then pruned; the status reports ``upstream`` without ``ahead``/``behind``) is pushed as ``<remote> refs/heads/<branch>:<branch.<name>.merge>``, recreating that branch — never as a new one. Plain ``git push`` is never used to publish a branch.

#### Job start response and error codes

Every start route answers ``200`` ``GitJobResponse`` ``{job_id, session_id, op: fetch | pull | push, status: running | done, phase, percent, line, ok, error_code, message}`` — every key always present.

* ``phase`` is git's own progress label with any ``remote: `` prefix stripped (``Counting objects``, ``Writing objects``, ``Receiving objects``, ``Resolving deltas``, ``Rebasing``…).
* ``percent`` is the per-phase 0–100 (it resets at each phase — clients label the bar with ``phase`` and never animate the width).
* ``line`` is the latest stderr line (≤ 200 chars).
* ``ok`` / ``error_code`` / ``message`` stay ``null`` until ``status`` is ``done``.
* ``message`` (≤ 500 chars) is the last meaningful output line on success (``443f30d..9419146 main -> main``, ``Everything up-to-date``, ``Fast-forward``, ``Already up to date.``) and git's own error line(s) on failure.

``error_code`` is an **open** vocabulary (clients render an unknown code from ``message``):

| Code | Meaning |
|------|---------|
| ``cancelled`` | — |
| ``git_timeout`` | — |
| ``auth_required`` | Git could not obtain credentials. ``GIT_TERMINAL_PROMPT=0``, ``GIT_ASKPASS=""``, ``SSH_ASKPASS_REQUIRE=never`` and ``GCM_INTERACTIVE=never`` guarantee nothing ever waits for a person at the host; a credential helper or ``ssh-agent`` there is the only fix. |
| ``network`` | — |
| ``remote_not_found`` | — |
| ``not_fast_forward`` | The branch is behind the remote: an ``ff_only`` pull on diverged branches, or a push the remote refused as ``non-fast-forward`` / ``fetch first``. Clients offer *Pull with rebase* / *Pull with merge*, and after a push also *Force push (with lease)*. |
| ``rejected`` | Any other push refusal — a stale lease, ``[remote rejected]``. Clients offer only the lease push. |
| ``conflicts`` | The merge or rebase stopped; the repository is now ``merging`` / ``rebasing`` and the Changes list shows the conflicts group. |
| ``dirty_worktree`` | — |
| ``unmerged`` | — |
| ``operation_in_progress`` | — |
| ``no_upstream`` | — |
| ``no_remote`` | — |
| ``detached_head`` | — |
| ``no_head`` | No commits yet. |
| ``hook_failed`` | — |
| ``index_locked`` | — |
| ``git_error`` | — |

#### Job status and cancel routes

``GET …/git/jobs/{job_id}`` → the current ``GitJobResponse`` (finished jobs stay readable for 10 minutes, at most 100 of them); ``POST …/git/jobs/{job_id}/cancel`` kills the whole git process tree and answers the snapshot once the job settled: ``error_code: cancelled`` unless git had already finished successfully before the cancel arrived, in which case the real outcome (``ok: true`` and git's summary) is reported because the remote or worktree may already have moved; a no-op on a finished job.

Both answer ``404 {"detail": {"code": "job_not_found"}}`` for an unknown id — a coded detail, unlike the string detail of a missing session — and the id is looked up agent-wide (any session may read a job it learned of through ``job_running``).

#### Job events

Events on the agent events channel (both transports; the relay stays opaque): ``git.job.progress`` carries the full ``GitJobResponse`` with ``status: running`` — once immediately when the job is accepted (even before the HTTP answer reaches the caller, so every viewer of the session can adopt a job another client started), then at most **four per second per job**, newest counters winning; because every frame is the whole state a client that misses one loses nothing.

``git.job.done`` carries the final snapshot exactly once, and is followed by ``git.changed`` ``{project_name, toplevel, common_dir}`` after **every** job, successful or not (refs and worktree may have moved either way).

Clients treat the events as the fast path and ``GET …/jobs/{job_id}`` as the source of truth: while a job is active and the screen is visible they poll it every 3 s, and a ``running`` snapshot never overwrites a ``done`` one.

#### One job per repository

One job per repository toplevel: a second start answers ``409 {"detail": {"code": "job_running", "message": "<active job id>"}}``, and so do ``stage`` / ``unstage`` / ``discard`` / ``commit`` while a job owns that toplevel (the job holds the per-toplevel lock for its whole run, so a commit that already held it finishes first, and a write that arrives later is refused rather than queued behind the job's 120 s budget).

The other start-time refusals are the mutation ones — ``404`` session / ``503 git_unavailable`` / ``400 cwd_missing`` / ``422`` / ``409 not_a_repo | bare_repository | unsafe_directory | git_too_old`` — everything after acceptance is reported through the job's ``error_code``.

#### Fetch/pull/push capability keys

``GET /api/version`` ``capabilities.git`` becomes ``{"changes": true, "stage": true, "discard": true, "commit": true, "fetch": true, "pull": true, "push": true}`` (both transports). Clients show the terminal menu's *Fetch / Pull / Push…* entry and the Changes screen's Sync button when any of the three keys is true and gate each action on its own key.

#### Job bounds

Bounds: the network command has 120 s and a 1 MiB stdout cap (progress is on stderr and is streamed, not retained — the runner keeps the first 64 KiB of stderr and the classifier reads the job's own tail of the last 50 ordinary lines).

The pre-steps (``symbolic-ref``, ``rev-parse``, ``remote``, ``for-each-ref``) have 5 s each. The agent's shutdown cancels running jobs.

#### Job golden fixtures

Golden fixtures ``tests/protocol_fixtures/dto/agent/{GitPullRequest,GitPushRequest,GitJobResponse}.json`` pin the three shapes.

### Delta 2026-09-14: in-session git mutations

Additive delta within proto-v7 (2026-09-14, in-session git mutations): **four additive REST routes + one additive read route + three additive capability keys, no version bump.** The agent mutates the staged set of the directory a session was spawned in (same session-scoped prefix, both transports byte-identical).

#### Stage route

``POST /api/sessions/{session_id}/git/stage`` with ``GitPathsRequest`` ``{paths: [..]}`` (1–5 000 repo-relative paths exactly as ``status`` reported them; a renamed entry lists both ``path`` and ``orig_path``) runs ``git add -A`` limited to those paths — modifications, deletions, type changes and untracked files alike — and answers ``GitMutationResponse`` ``{ok: true}``.

#### Unstage route

``POST …/git/unstage`` (same body) runs ``git reset -q`` on the paths (not ``restore --staged``, which cannot resolve ``HEAD`` on a branch with no commits yet); a staged rename is undone only when both of its paths are sent.

#### Discard route

``POST …/git/discard`` with ``GitDiscardRequest`` ``{paths, scope: "unstaged" | "untracked"}`` throws working-tree changes away: ``unstaged`` → ``git restore --worktree`` (a staged part of the same file survives; a modify-delete is recreated from the index), ``untracked`` → ``git clean -f`` (which can only ever delete untracked, non-ignored files). Clients must confirm before calling it; it is never one tap.

#### Commit context route

``GET …/git/commit/context`` → ``GitCommitContextResponse`` ``{author {name, email} | null, head {oid, short_oid, subject, body} | null}``: ``author`` is what ``git var GIT_AUTHOR_IDENT`` resolves under the same strictness ``git commit`` applies (``null`` means the commit would be refused with ``identity_required``); ``head`` is ``null`` on an unborn branch, so the commit sheet can show who is committing, prefill an Amend, and hide Amend when there is nothing to amend.

#### Commit route

``POST …/git/commit`` with ``GitCommitRequest`` ``{message, amend: false, no_verify: false}`` (``message`` non-blank, ≤ 64 KiB, first line is the subject; ``amend`` and ``no_verify`` are optional and default to ``false``, so a client may omit them) commits **the staged set only** (never ``-a``): the message travels on stdin (``-F -``) and is cleaned with ``--cleanup=whitespace`` so a leading ``#`` survives; ``--amend`` / ``--no-verify`` are passed only when asked.

Answers ``GitCommitResponse`` ``{oid, short_oid, subject}`` of the new ``HEAD``. Repository hooks (``pre-commit``, ``commit-msg``, …) run on the agent machine under the agent's account — the same trust boundary as the PTY — with the runner's scrubbed environment.

#### Mutation locking and pathspecs

Every mutation resolves the repository first, then runs under a per-toplevel ``asyncio.Lock`` (two clients of one session cannot interleave index writes) and, on success, publishes ``git.changed`` ``{"project_name": <session's workspace or "">, "toplevel", "common_dir"}`` so every viewer of that repository — including the caller — refreshes through the etag poll; the reads still take no lock.

Pathspecs travel NUL-separated on stdin (``--pathspec-from-file=- --pathspec-file-nul``): no argv limit on Windows, and for ``stage`` / ``discard`` (``add``, ``restore``) one unmatched path fails the whole call before anything is touched; ``unstage`` (``reset``) and an untracked ``discard`` (``clean``) silently skip a path that no longer matches.

An empty ``paths`` list is refused by the schema (``422``) and again by the service itself (``400 bad_path``, for any caller that bypasses the schema) — an empty pathspec file would mean "everything" to git. Only ``git clean`` lacks the stdin option and takes argv in chunks of 100.

#### Mutation errors

Errors are identical on both transports:

| Status | Cause |
|--------|-------|
| ``404`` session / ``503 git_unavailable`` / ``504 git_timeout`` / ``500 git_error`` | As for the reads. |
| ``400 {"detail": {"code": "bad_path" \| "bad_scope" \| "cwd_missing" \| "empty_message"}}`` | — |
| ``422`` | A malformed body. |
| **new** ``409 {"detail": {"code": <refusal>, "message": <≤ 500 chars of git output>}}`` | Whenever git declines because of the repository's state. Codes below. |

The ``409`` refusal codes:

| Code | Meaning |
|------|---------|
| ``not_a_repo`` | — |
| ``bare_repository`` | — |
| ``unsafe_directory`` | — |
| ``git_too_old`` | Mutations need git ≥ 2.26 for ``--pathspec-from-file`` on ``reset``; the reads keep 2.15. |
| ``pathspec_mismatch`` | ``add`` / ``restore`` found no file for a path — git touched nothing, refresh. |
| ``identity_required`` | — |
| ``unmerged`` | A commit is refused while conflicts are unresolved. |
| ``nothing_to_commit`` | — |
| ``no_head`` | Amend on an unborn branch. |
| ``hook_failed`` | Exit 1 after the pre-checks — ``message`` is the hook's output. |
| ``index_locked`` | ``index.lock`` exists — another git process is running, retry. |

Old agents answer ``404`` for the new routes; clients never show a mutating control without the matching capability key.

#### Mutation capability keys

``GET /api/version`` ``capabilities.git`` becomes ``{"changes": true, "stage": true, "discard": true, "commit": true}`` (both transports). Clients gate row/section/selection Stage–Unstage on ``stage``, Discard on ``discard``, and the Commit sheet plus the terminal menu's *Commit…* entry on ``commit``.

#### Mutation timeouts

Timeouts: stage / unstage / discard 15 s, commit 25 s (hooks run inside it and the relay gives a proxied request 30 s), context 5 s.

#### Mutation golden fixtures

Golden fixtures ``tests/protocol_fixtures/dto/agent/{GitPathsRequest,GitDiscardRequest,GitMutationResponse,GitCommitRequest,GitCommitResponse,GitCommitContextResponse}.json`` pin the six shapes.

### Delta 2026-08-27: in-session git Changes API

Additive delta within proto-v7 (2026-08-27, in-session git Changes API): **two additive read-only REST routes + one additive capability key + two additive event keys, no version bump.** The agent now answers, for the directory a session was spawned in (workspace root or linked worktree — clients never send a path to run in).

#### Status route

``GET /api/sessions/{session_id}/git/status?etag=<opaque>`` → ``GitStatusResponse``. **Every key is always present.**

| Field | Notes |
|-------|-------|
| ``session_id`` | — |
| ``cwd`` | Nullable. |
| ``etag`` | Covers the porcelain output, the resolved repo and the state, so a commit or a finished merge moves it; an untracked file's *content* change does not (porcelain carries no hash for it). |
| ``unchanged`` | ``true`` (the supplied ``etag`` still matches) carries ``repo: null``, ``entries: []``, ``counts: null`` and means "keep your cached body". |
| ``unavailable_reason`` | ``null`` / ``not_a_repo`` / ``bare_repository`` / ``cwd_missing`` / ``unsafe_directory`` / ``git_too_old``. |
| ``repo`` | ``toplevel`` / ``git_dir`` / ``common_dir`` as absolute paths with forward-slash separators (backslash bytes in a POSIX filename are kept verbatim), plus the fields below. |
| ``repo.is_linked_worktree`` | — |
| ``repo.head`` | ``{oid, short_oid, branch, detached, initial}`` with nullable oid/branch. |
| ``repo.upstream`` | ``{ref, ahead, behind} \| null``; ``ahead`` / ``behind`` are ``null`` when the upstream ref is gone. |
| ``repo.state`` | ``clean`` / ``merging`` / ``rebasing`` / ``cherry_picking`` / ``reverting`` / ``bisecting``. |
| ``entries[]`` | Fields: ``group``, ``path``, ``orig_path``, ``code``, ``index_status``, ``worktree_status``, ``rename_score``, ``submodule``. A file that is both staged and modified yields one entry per group, like VS Code. |
| ``entries[].group`` | ``conflicts`` / ``staged`` / ``unstaged`` / ``untracked``. |
| ``entries[].code`` | One letter, or two for conflicts such as ``UU``. |
| ``counts`` | ``{conflicts, staged, unstaged, untracked, total}``; they come from the full parse. |
| ``truncated`` / ``entry_limit`` | ``entry_limit`` is 5 000. |

Repo *conditions* are ``200`` with ``unavailable_reason`` and ``repo: null``, never HTTP errors.

#### Diff route

``GET /api/sessions/{session_id}/git/diff?path=&scope=staged|unstaged|untracked&context=3&orig_path=`` → ``GitDiffResponse``.

| Field | Notes |
|-------|-------|
| ``path``, ``orig_path`` | Repo-relative as returned by ``status``. |
| ``scope`` | — |
| ``kind`` | ``text`` / ``binary`` / ``submodule`` / ``too_large`` / ``missing``. |
| ``change`` | ``modified`` / ``added`` / ``deleted`` / ``renamed`` / ``copied`` / ``type_changed``; ``null`` only for ``missing``. |
| ``old_mode`` / ``new_mode`` | Nullable. |
| ``additions``, ``deletions`` | — |
| ``hunks[]`` | ``{old_start, old_lines, new_start, new_lines, heading, lines[] {t: ctx\|add\|del, old, new, text, no_newline}}``. |
| ``truncated`` | — |
| ``total_lines`` | Body lines seen — a lower bound when the byte cap killed git. |
| ``line_limit`` | 5 000. |
| ``byte_limit`` | 1 MiB. |

* Past the line cap the agent keeps counting but returns no further hunks, so a sparse diff never ships header-only hunks after the preview (clients still skip a header-only hunk of a truncated diff).
* The agent parses the unified diff; ``\r`` is preserved verbatim in ``text``.
* Pass the entry's ``orig_path`` for a rename, because a pathspec-limited ``git diff -M`` cannot see the rename's source otherwise.
* Conflicted files (group ``conflicts``) are requested with ``scope=unstaged`` and are diffed against ``HEAD``. Conflict markers appear as ``+`` lines; a modify/delete conflict whose working-tree file equals ``HEAD`` answers ``kind: text`` with no hunks. ``scope=staged`` on an unmerged path is ``kind: missing``.
* Untracked diffs are synthesised by the agent (no ``/dev/null`` — broken on Git for Windows) and only for paths git itself lists as untracked and not ignored (``ls-files --others --exclude-standard``; ``.git/`` and gitignored files answer ``kind: missing``). A symlink diffs as its target text with mode ``120000``.
* ``context`` is clamped to 0–5 000 (the line cap). At the cap an untruncated diff is the whole file as one hunk, which is how clients request their *Full file* view.

#### Changes API errors

Errors are identical on the direct and relay transports:

| Status | Body and cause |
|--------|----------------|
| ``404`` | ``{"detail": "Session '<id>' not found"}``. |
| ``400`` | ``{"detail": {"code": "bad_scope" \| "bad_path" \| "<unavailable_reason>"}}``. ``path`` must normalise to a repo-relative POSIX path — no absolute, drive-letter, ``..`` or NUL. |
| ``503`` | ``{"detail": {"code": "git_unavailable"}}`` when no ``git`` binary is on the agent's PATH. |
| ``504`` | ``{"detail": {"code": "git_timeout"}}``. |
| ``500`` | ``{"detail": {"code": "git_error", "message": …}}`` for an unexpected git failure. A repository path containing a literal newline is unsupported and always answers this. |

Old agents answer ``404`` for both routes → clients render an "update your agent" state; the capability key lets them gate proactively.

#### Changes capability key

``GET /api/version`` gains ``capabilities.git = {"changes": true}`` (emitted by both transports, next to ``capabilities.cli``).

#### git.changed payload

``git.changed`` (published after a successful branch/worktree delete) gains ``toplevel`` and ``common_dir`` — the workspace's resolved repository root and common git dir, ``null`` when the workspace is no longer a repo — so a session whose ``repo.common_dir`` (linked worktree) or ``repo.toplevel`` matches can invalidate its Changes cache even when it has no ``project_name``.

Payload: ``{"project_name", "toplevel", "common_dir"}``. All three clients read the payload by key and ignore unknown ones.

#### Changes bounds and safety

Every git call goes through a dedicated runner:

* ``timeout`` + ``max_bytes`` enforced by killing the whole process tree (``taskkill /T`` on Windows, where PATH usually resolves to the ``cmd\git.exe`` wrapper).
* A scrubbed environment: every inherited ``GIT_*`` variable removed — repository redirection, config injection, external programs and trace files alike — and ``LC_ALL=C``, ``GIT_TERMINAL_PROMPT=0``, ``GIT_OPTIONAL_LOCKS=0`` set.
* ``--no-optional-locks --literal-pathspecs -c core.quotePath=false``.
* ``--no-ext-diff --no-textconv`` on every diff call (numstat and patch alike) so a repository's ``.gitattributes`` diff drivers can never execute. The patch call alone also sets ``--submodule=short`` so a user's ``diff.submodule`` setting cannot change the parsed format.

Git ≥ 2.15 is required (older → ``git_too_old``). The relay's 4 MB tunnel frame is why the caps are server-side (5 000 entries; 1 MiB / 5 000 lines per diff).

User-side accelerators for very large trees are ``git update-index --untracked-cache`` and ``core.fsmonitor``; the agent never watches files.

#### Changes golden fixtures

Golden fixtures ``tests/protocol_fixtures/dto/agent/{GitStatusResponse,GitDiffResponse}.json`` pin both shapes; each ``decoded_canonical`` equals its ``wire`` with null-valued keys removed (the Swift ``Codable`` nil-dropping rule).

### Delta 2026-08-23: Codex model catalog

Additive delta within proto-v7 (2026-08-23, Codex model catalog): **additive optional key + one relaxed field, no version bump.**

Each ``capabilities.cli.<cli>`` entry on ``GET /api/version`` (see the proto-v5 CLI-availability bullet for the base ``{"available": <bool>}`` shape) may also carry:

| Key | Meaning |
|-----|---------|
| ``models`` | The agent's startup enumeration of that CLI's launchable models, in CLI-reported order. |
| ``model_details`` | An array of ``{"id": <str>, "efforts": [<str>, …], "default_effort"?: <str>}`` for CLIs whose catalog exposes per-model reasoning levels (today: Codex, via ``codex debug models``). |

* Both keys are omitted — never emitted empty or null — when enumeration is unsupported or produced nothing; clients then fall back to their hardcoded model/effort lists, so old and new agents degrade identically.
* An entry may also carry ``"models_pending": true`` (omitted, never false): the agent's startup enumeration was still running when this response was built and *this CLI's own* enumeration had not yet produced a result.
* The absence of ``models``/``model_details`` in a marked entry is provisional, not authoritative: clients keep persisted catalog selections instead of reconciling them away, and re-probe until an unmarked answer arrives.
* The presence of ``models_pending`` also proves a catalog-era agent (pre-catalog agents never emit it).
* Emitted by the shared ``build_cli_capabilities`` builder on both the direct and relay transports.

#### Free-form reasoning effort

``codex_options.reasoning_effort`` is relaxed from the proto-v5 enum (``minimal`` | ``low`` | ``medium`` | ``high``) to a free-form string on the agent: the effort vocabulary is now enumerated per-model at runtime, and a closed set would reject values the agent itself just advertised (e.g. ``xhigh``/``max``/``ultra`` on the gpt-5.6 catalog).

The value is embedded after ``=`` inside the single exec'd argv element ``-c model_reasoning_effort=<v>``, so no flag-smuggling path opens up.

### Delta 2026-08-11: Claude debug toggle

Additive delta within proto-v7 (2026-08-11, Claude debug toggle): **additive optional field, no version bump.** ``claude_options`` gains ``debug`` — bool, default ``false`` — emitted by the agent as ``--debug``.

It is a per-session choice, surfaced as a **Debug logging** toggle in all three clients' create-session wizards, default off.

The relay forwards ``claude_options`` opaquely, so ``central_server`` is untouched.

#### Debug default and compatibility

The wire-absence default (``false``) **matches** the clients' UI default, which is what makes this additive rather than a ``skip_permissions``-style hazard: an omitted field means exactly what a client leaving the toggle off means. Old client + new agent therefore lands on debug **off** — the intended outcome.

New client + old agent is a harmless no-op: the old agent ignores the unknown key and keeps hardcoding ``--debug`` until it updates.

### Delta 2026-08-05: stale-attach fix

Additive delta within proto-v7 (2026-08-05, stale-attach fix): **agent-side only — no wire change, no version bump.** ``RelayClient._handle_session_input`` no longer auto-creates a session for a ``CH_SESSION_IO`` frame naming an unknown ``session_id``; it drops the frame and answers with the same ``stopped`` state frame ``_handle_mobile_attach`` already sends for a stale attach (both now share ``_send_stopped_frame``).

Auto-create used to fire off the bare resize frame that all three clients send on every socket connect, so merely attaching to a stale id silently spawned a real login shell.

The empty-payload guard now runs first. An unpatched client paired with a fixed agent needs no changes: it receives exactly the ``stopped`` frame the attach path has always sent for a stale id and renders a stopped session instead of a phantom shell — old client + new agent is the supported combination.

Deciding that a session is *gone* (rather than merely stopped) stays a client-side judgement from the ``sessions.changed`` snapshot, not a wire signal.

## proto-v6 (superseded by proto-v7)

* Introduced: 2026-06-26
* App support: ``APP_PROTOCOL_VERSION = 6`` (Android), ``PROTOCOL_VERSION = 6`` (Python — both ``mobaide_agent`` and ``central_server``).

### Claude options redesign (breaking)

Change vs proto-v5: **breaking** to the ``claude_options`` shape (pre-production, no compatibility window). Replaces Claude's boolean ``max_effort`` with a rich option set mirroring the Codex precedent (CLA-84). The version is bumped so the three artifacts stay in lockstep and an old client is cleanly rejected (``4002`` + HTTPS-bootstrap auto-update) rather than silently launching with the wrong reasoning level.

### Claude options wire shape

Wire shape (delta from proto-v5), all on ``CH_CONTROL`` (forwarded verbatim by ``central_server`` — no server logic change).

The ``create_session`` control message (the ``POST /api/sessions`` body proxied over ``CH_CONTROL``, and the Auto Scan ``claude_options``) drops ``max_effort`` (bool) and gains five fields:

| Field | Type | Notes |
|-------|------|-------|
| ``model`` | str\|null | A curated Claude alias (``opus`` \| ``sonnet`` \| ``haiku`` \| ``fable``) or null for the account default. Free-form on the server (the client curates the list); emitted as ``--model <alias>``. |
| ``effort`` | ``low`` \| ``medium`` \| ``high`` \| ``xhigh`` \| ``max`` \| null | Every non-null level is emitted as ``--effort <level>`` (``high`` included); null emits no flag. Also emitted when ``ultracode`` is on: since Claude Code 2.1.284 Ultracode leaves the effort unchanged. |
| ``fast_mode`` | bool | Opus-only fast mode. See the ``--settings`` note below. |
| ``ultracode`` | bool | See the ``--settings`` note below. |
| ``append_system_prompt`` | str\|null | Emitted as ``--append-system-prompt <text>``. |

* ``high`` included: with no flag the CLI falls back to the operator's persisted ``effortLevel`` / per-model default, which is not reliably ``high``.
* ``fast_mode`` and ``ultracode`` are both emitted through a single merged ``--settings`` dict (``{"fastMode": true, "ultracode": true}``), merged into the per-session attention-hooks settings file when one is written — never two separate ``--settings`` flags.

``codex_options`` and all other CH_CONTROL / CH_EVENTS / CH_SESSION_IO shapes are unchanged.

### Managed Gemini CLI (CLA-87)

Managed **Gemini CLI** as a third coding agent (CLA-87 — **additive, no version bump**): Gemini is wired through the same extension points the Codex foundation established, so no JSON shape breaks and the version stays at ``6``.

An old proto-v6 client is unaffected (it never sends ``cli:"gemini"``); a new client feature-detects Gemini via the ``capabilities.cli`` advertisement rather than a version gate — the same forward-compat contract that made the Codex auth/login and CLI-availability additions no-bump changes (see proto-v5).

#### Gemini create_session fields

``create_session`` (``POST /api/sessions`` proxied over ``CH_CONTROL``): ``cli`` gains the value ``"gemini"`` and a new optional ``gemini_options`` object, plus the shared ``worktree`` / ``branch_name`` intent. Exactly one of ``claude_options`` / ``codex_options`` / ``gemini_options`` is honoured per session.

| ``gemini_options`` field | Notes |
|--------------------------|-------|
| ``model`` | Free-form string: a preset alias ``auto`` / ``pro`` / ``flash`` / ``flash-lite``, any concrete model id, or null. |
| ``approval_mode`` | ``default`` \| ``auto_edit`` \| ``yolo`` \| ``plan``; ``--approval-mode`` replaces Gemini's deprecated ``--yolo``. |
| ``include_directories`` | Array of str. |
| ``sandbox`` | bool. |

#### Gemini session kind

``session_to_wire_dict`` ``kind`` gains the value ``"gemini"`` — the Android client already coerces an unknown ``kind`` forward-compatibly (a typed ``Gemini`` enum member lands with the create-session UI, CLA-88).

#### Gemini missing binary and auth

Missing binary → ``gemini_cli_missing: true`` + ``searched_paths`` (``503`` / ``session_create_failed``); an unauthenticated host → ``gemini_auth_required: true`` (``409``, non-``401`` for the same app-JWT-interceptor reason as Codex).

Auth is **detection-only**: Gemini has no ``login`` / ``--with-api-key`` subcommand and its OAuth needs a browser, so there is **no** ``POST /api/gemini/login`` route — the operator authenticates ``gemini`` on the host (``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` / Vertex-AI credentials / cached OAuth). In-app API-key entry is deferred (CLA-91).

#### Gemini CLI capability

``capabilities.cli`` on ``GET /api/version`` gains a ``gemini`` entry from the same registry-driven builder, so the mode-select screen enables Gemini only when its binary resolves on the host.

### APNs push pipeline (CLA-106)

APNs push pipeline for session attention (CLA-106 — **additive, no version bump**; neither ``envelope.py`` copy changes, so the diff guard is untouched). Fills the iOS-09 gap: a suspended iOS app has no live socket, so the server must be able to *see* attention (today it rides the server-opaque ``CH_EVENTS``) and push it.

#### User-scoped events channel

**User-scoped events channel** (``/relay/ws/user-events``, documented here for the first time): a one-way server→client text channel authenticated with the dashboard/user JWT. Each frame is a JSON envelope ``{"type": <kind>, "payload": {...}}``.

Existing kinds (all server-originated): ``agent.status_changed`` ``{"agent_id", "online"}`` and ``template.created`` / ``template.updated`` (full template row) / ``template.deleted`` ``{"template_id", "version", "updated_at"}``. Clients must ignore unknown kinds (both apps already do — Android ``else -> Unit``, iOS ``default: .none``).

#### Session attention user event

New user-event kind ``session.attention`` — payload ``{"session_id", "reason", "title"?, "project_name"?, "exit_reason"?, "kind"?, "kind_label"?, "agent_id"}`` (slim, not the full session wire dict; ``reason`` is the attention enum; ``kind`` and ``kind_label`` are optional, see Delta 2026-11-10). ``agent_id`` is **stamped by the server** from the authenticated agent tunnel — an agent-supplied value is never trusted.

#### Agent to server user_event message

New agent→server ``CH_CONTROL`` message ``{"type": "user_event", "kind": "session.attention", "payload": {…}}``, sent by the agent alongside (not instead of) the existing opaque ``CH_EVENTS`` ``session.attention`` publication, on attention **set** only.

The server validates the kind against an allowlist (only ``session.attention``), broadcasts it via the user-events channel, and hands it to the push dispatcher along with the devices that channel already reaches. Back-compat: an old server WARN-counts the unknown control type and drops it; an old agent simply never sends it (no pushes, previous behaviour).

#### Per-device presence gating

**Presence gating is per device, not per account.** A client may pass its registered ``device_id`` as a query param on ``/ws/user-events``; the server accepts it only when it matches one of that user's own non-revoked device rows, and treats it purely as a delivery hint (an absent or unrecognised value is logged and the socket serves events as before — it simply cannot suppress anything).

The dispatcher then pushes to every registered device **except** those holding such a socket, so WebSocket delivery still wins for a connected device while a second, sleeping device is not muted by it.

#### Push dispatch

**Push dispatch** (server→APNs, not a wire-protocol surface but recorded for the delivery contract): alert push (never silent), one per targeted device of the user, ``apns-collapse-id`` = ``thread-id`` = ``session_id`` (matches the clients' notification identifier so replace/suppress/reconcile semantics hold), ``aps.alert`` title/body from the shared attention content map, custom data ``session_id`` + ``agent_id`` for the tap deep link.

Requires ``APNS_KEY_PATH`` / ``APNS_KEY_ID`` / ``APNS_TEAM_ID`` / ``APNS_TOPIC`` on the server; unconfigured → dispatch disabled, WebSocket-only delivery (no errors).

#### Push-token REST

**REST**: ``POST /api/devices/{device_id}/push-token`` ``{"platform": "ios", "token": <hex>, "environment": "sandbox"|"production"}`` → ``204`` (``404`` when the device isn't the caller's or is revoked); ``DELETE`` on the same path clears the stored token (sign-out).

``mobile_devices`` gains nullable ``platform`` / ``push_token`` / ``push_environment`` columns; ``MobileTokenRequest`` and the pairing-claim body gain an optional ``platform`` field (iOS sends ``"ios"``; Android sends nothing — server-side optional). A cross-tenant device reassign, a device revoke, and an APNs ``410 Unregistered``/``BadDeviceToken`` result all clear the stored token.

### Server-synced saved keyboard shortcuts (CLA-137)

Server-synced saved keyboard shortcuts (CLA-137 — **additive, no version bump**; ``central_server`` + clients only, the agent protocol is untouched). Persistent named chord buttons per session kind, shared across the user's devices — the server stores ``kind`` (SessionKind wire name) and ``chord_token`` (the client's ``KeyChord.token()`` string) opaquely and never interprets either.

#### Shortcut user events

New user-event kinds (server-originated, same envelope as ``template.*``): ``shortcut.created`` / ``shortcut.updated`` (full shortcut row: ``{"shortcut_id", "kind", "chord_token", "name"?, "position", "created_at", "updated_at", "version"}``) and ``shortcut.deleted`` ``{"shortcut_id", "version", "updated_at"}``. Clients ignoring unknown kinds (both apps) make this a no-op for older builds.

#### Shortcuts REST

Dashboard/user JWT, non-admin.

| Route | Body | Result and notes |
|-------|------|------------------|
| ``GET /api/shortcuts`` | none | List. |
| ``POST /api/shortcuts`` | ``{"kind", "chord_token", "name"?}`` | ``201`` — an **upsert**: re-saving an existing live (kind, chord_token) updates its name (when provided) instead of duplicating, still ``201``. |
| ``PATCH /api/shortcuts/{id}`` | ``{"name"}`` | Renames. The field is required (an empty body ``{}`` → ``422``, it must not silently clear). |
| ``DELETE`` | none | ``204`` soft-delete. |

* ``""`` (or explicit null) clears the name back to null (the Android JSON encoder omits null defaults, so the empty string is the wire's "clear" sentinel).
* Ownership violations → ``403`` — and since the ownership probe filters soft-deleted rows, a **repeat** ``DELETE`` of an already-deleted shortcut also answers ``403``, not ``204``.
* DELETE is therefore **not idempotent**: a client whose UI lets the same row be removed twice must treat ``403``/``404`` on an id from its own list as "already gone" rather than reporting a failure the user would rightly disbelieve.

## proto-v5 (superseded by proto-v6)

* Introduced: 2026-06-21
* App support: ``APP_PROTOCOL_VERSION = 5`` (Android), ``PROTOCOL_VERSION = 5`` (Python — both ``mobaide_agent`` and ``central_server``).

### Codex CLI foundation (CLA-68)

Change vs proto-v4: **additive** (no frame-layout or existing-shape break). Foundation for managing the **Codex CLI** as a second coding agent alongside Claude (CLA-68).

### Codex wire shape

Wire shape (delta from proto-v4), all on ``CH_CONTROL`` (forwarded verbatim by ``central_server`` — no server logic change).

#### Codex create_session fields

The ``create_session`` control message (and the ``POST /api/sessions`` body proxied over ``CH_CONTROL``) gains three optional fields:

| Field | Notes |
|-------|-------|
| ``cli`` | ``"claude"`` \| ``"codex"``: selects the managed CLI. When omitted, the agent infers it from whichever options block is present, so a legacy ``claude_options``-only payload still launches Claude (forward/backward compatible). |
| ``codex_options`` | Object mirroring ``claude_options`` for Codex (fields below). Exactly one of ``claude_options`` / ``codex_options`` is honoured per session. |

``codex_options`` fields:

| Field | Values |
|-------|--------|
| ``model`` | str\|null |
| ``sandbox`` | ``read-only`` \| ``workspace-write`` \| ``danger-full-access`` |
| ``approval`` | ``on-request`` \| ``never``. Codex 0.149 retired ``untrusted``; the agent still accepts it (also in saved Auto Scan options) and reads it as ``on-request``. |
| ``reasoning_effort`` | ``minimal`` \| ``low`` \| ``medium`` \| ``high`` \| null |
| ``full_auto`` | bool — Codex ``--dangerously-bypass-approvals-and-sandbox`` |
| ``auto_review`` | bool — Codex ``--approve-for-me`` (Delta 2026-11-25); not with ``full_auto`` |
| ``initial_prompt`` | str\|null |

#### Codex session kind

The single-session wire dict (``session_to_wire_dict``) ``kind`` field gains a new value ``"codex"`` (alongside ``claude`` / ``auto_scan`` / ``shell``). The Android app models ``kind`` as a typed ``SessionKind`` enum and now carries a ``Codex`` member (CLA-69), so it decodes a v5 session directly rather than relying on the proto-5 version gate to reject it.

For forward-compatibility it also coerces an unknown future ``kind`` to the enum's default (``coerceInputValues`` on the event-bus JSON decoder) instead of dropping the whole event.

#### Codex missing-binary marker

``session_create_failed`` / proxy ``503`` responses for a missing Codex binary carry ``codex_cli_missing: true`` + ``searched_paths`` — the exact parallel of the existing ``claude_cli_missing`` marker.

#### Codex auth and login (CLA-71)

Codex auth/login (CLA-71 — **additive, no version bump**): when Codex is installed but not signed in, the agent guards the spawn path and returns a ``codex_auth_required: true`` marker on session create — HTTP ``409`` (direct REST: marker inside a dict ``detail``; relay proxy: top-level in the body), and top-level on the ``session_create_failed`` control message.

``409`` (not ``401``) so the Android app-JWT interceptor's refresh-retry-on-401 can't swallow the marker before the client reads it. Parallel to ``codex_cli_missing``.

A new authenticated endpoint ``POST /api/codex/login`` (body ``{"api_key": "<secret>"}``) runs ``codex login --with-api-key`` (key fed on stdin, never argv / never persisted by the agent) and returns ``{"authenticated": true}`` on success, or ``400`` with a sanitized detail on a rejected key (also non-``401`` for the same interceptor reason). The app shows a login prompt on the marker, then retries the create.

#### CLI-availability advertisement

CLI-availability advertisement (**additive, no version bump**): the ``capabilities`` field on ``GET /api/version`` — previously always ``{}`` — now carries ``{"cli": {"<cli>": {"available": <bool>}, …}}`` with one entry per registered managed CLI (e.g. ``claude``, ``codex``).

``available`` reflects whether that CLI's binary resolves on the agent host via the same override → env → PATH → fallbacks resolution a real spawn uses. Emitted identically by the direct FastAPI handler and the relay proxy path (one shared builder).

The Android client parses it tolerantly — an absent / old-agent / malformed value is treated as available (optimistic) — and the create-session ``503`` ``<cli>_cli_missing`` / ``409`` ``<cli>_auth_required`` markers remain the authoritative backstop. Drives the mode-select screen's per-CLI button enablement.

(Later extended — see the proto-v7 additive delta of 2026-08-23 for the ``models`` / ``model_details`` keys an entry may also carry.)

### proto-v5 out of scope

Out of scope (downstream issues): Android create-session UI for Codex (CLA-69, done), terminal control-key / approval affordances (CLA-70, done). The agent defaults Codex sessions to a non-interactive sandboxed preset (``--sandbox workspace-write --ask-for-approval never``) and cannot yet mediate in-session Codex approval prompts.

Further Codex-auth work is tracked downstream: device-code login (CLA-79), mid-session re-auth (CLA-80), credential-validity probing (CLA-81).

## proto-v4 (superseded by proto-v5)

* Introduced: 2026-06-09
* App support: ``APP_PROTOCOL_VERSION = 4`` (Android), ``PROTOCOL_VERSION = 4`` (Python — both ``mobaide_agent`` and ``central_server``).

### Session-attention notifications (CLA-64)

Change vs proto-v3: **additive** (no frame-layout or existing-shape break). Adds hook-driven session-attention notifications (CLA-64).

### Attention wire shape

Wire shape (delta from proto-v3), all on ``CH_EVENTS`` (forwarded verbatim by ``central_server``).

#### Session attention event

New event type ``session.attention`` — payload ``{"session": <session dict>}``. Emitted when a Claude Code hook reports the session needs the user. Chat sessions and hookless terminal sessions raise it too (Delta 2026-11-10). The ``attention`` field is one of:

| Value | Meaning |
|-------|---------|
| ``awaiting_permission`` | Permission prompt. |
| ``awaiting_os_permission`` | The agent's macOS startup-stall watchdog: a spawned child producing no output is most likely blocked on an invisible host-desktop TCC file-access modal — additive within proto-v4, no version bump. |
| ``turn_finished`` | Your move — see the attention-gate delta below. |
| ``ended`` | SessionEnd. |
| ``turn_failed`` | The turn ended in an error (added 2026-11-10). |

It is a one-shot notification trigger; clients that miss it (the in-process ``EventBus`` drops on a full queue) still recover from the snapshot field below. Older clients ignore the unknown type (forward-compatible).

#### Session wire dict attention fields

The single-session wire dict (``session_to_wire_dict`` — used by ``sessions.changed``, ``session.updated``, the ``/ws/sessions`` snapshot and the ``/sessions`` REST list) gains two nullable fields: ``attention`` (``awaiting_permission`` | ``awaiting_os_permission`` | ``turn_finished`` | ``turn_failed`` | ``ended`` | ``null``) and ``exit_reason`` (human-readable end cause from the Claude Code ``SessionEnd`` ``reason``, or ``process_exited`` on an unexpected death).

These let a reconnecting/foregrounding client resync the "awaiting you" state after a missed one-shot event.

#### Hook detection source

Detection source: the agent spawns interactive Claude sessions with a per-session ``--settings`` file wiring ``http`` hooks to a local ``POST /internal/hook`` endpoint (loopback + per-agent token guarded); the old terminal-buffer ``PromptDetector`` heuristic is retired.

### Delta 2026-08-03: attention gate

Additive delta within proto-v4 (2026-08-03, attention gate): **agent-side only — no wire change, no version bump.** The reason enum, the ``session.attention`` event and both snapshot fields are untouched; what changes is *when* the agent raises ``turn_finished``.

#### Stop debounce and dedupe

Claude Code's ``Stop`` fires once per model turn, including every re-invocation a completing background task or subagent causes, so mapping it straight to ``turn_finished`` alerted N+1 times for a session that fanned out N background agents.

It is now debounced and deduped by ``mobaide/core/attention_gate.py`` before it reaches ``TerminalSession.set_attention``: a ``Stop`` whose payload reports ``stop_hook_active``, in-flight ``background_tasks``, or pending ``session_crons`` is dropped, and ``prompt_id`` dedupes repeat stops within one turn.

A suppressed hook never writes the public ``attention`` field, so a client resyncing from a snapshot cannot resurrect a withheld alert.

#### Hook settings registration

The per-session settings file now registers ``Notification`` (``permission_prompt`` **and** ``idle_prompt``), ``Stop``, ``UserPromptSubmit``, ``SessionEnd`` and ``PreToolUse`` (matcher ``*``, ``hook=tool``, 2 s timeout, a per-tool activity heartbeat), and bakes a ``hook=<kind>`` query param into each URL alongside ``session_id``/``token`` so the endpoint classifies without parsing the body. ``idle_prompt`` is the backstop that still alerts when the payload-driven suppression was wrong.

``UserPromptSubmit`` is the turn boundary: it resets the gate and clears the public ``attention`` field, so a turn answered on the host cannot leave a stale badge that mutes later turns.

#### set_attention equality guard

``set_attention`` gained an equality guard: re-raising the reason already pending refreshes state without re-emitting the one-shot event. A changed ``exit_reason`` still emits, so the second ``ended`` that a ``/clear`` produces (``SessionEnd`` reason ``clear``, PTY still running) still reaches clients.

#### Hook event registration constraint

**Registration constraint** (``hook_settings.REGISTERED_HOOK_EVENTS``): Claude Code strips an unrecognised ``hooks.<Event>`` key with a validation *warning* and raises a blocking invalid-settings dialog for any validation record when interactive, so registering an event newer than the oldest supported CLI would hang every session at a modal. Matcher values and optional payload fields are safe; new event keys are not.

### Delta 2026-06-12: Send-button submit fix

Additive delta within proto-v4 (2026-06-12, Send-button submit fix): the PTY frame set on ``/ws/session/<id>`` and the ``CH_SESSION_IO`` channel gains one C->S type and one semantic change. No version bump: the frame *layout* (1 type byte + payload) is unchanged, the new type is purely additive, ``central_server`` forwards ``CH_SESSION_IO`` payloads opaquely, and the app + agent ship together pre-production.

#### Submit frame 0x6d

New C->S frame ``0x6d`` / ``'m'`` (**submit**): the agent writes the payload to the PTY, pauses ``SUBMIT_CR_DELAY_SECONDS`` (0.2 s), then writes CR as a **separate** PTY write. Ink TUIs (Claude CLI) treat a CR embedded in the same stdin chunk as pasted content; only a CR arriving as its own later read registers as the Enter key. Frames with an empty payload are dropped — a bare Enter could submit stale composer text.

#### Execute frame 0x78 semantics

Changed ``0x78`` / ``'x'`` (**execute**) semantics: the agent now ends the sequence with the same delayed CR (Ctrl+C → 0.1 s pause → payload → 0.2 s pause → CR). Clients no longer append a trailing ``\r`` to submit or execute payloads; an old client that still sends ``text\r`` gets a harmless extra Enter on an empty composer.

## proto-v3 (superseded by proto-v4)

* Introduced: 2026-05-23
* App support: ``APP_PROTOCOL_VERSION = 3`` (Android), ``PROTOCOL_VERSION = 3`` (Python — both ``mobaide_agent`` and ``central_server``).

### Chunked replay handshake

Breaking change vs proto-v2: the reconnect replay handshake is now **chunked** and explicitly terminated. Before v3 the handshake was ``state → 'b' → 'o'(entire-ring)`` and the client treated the first ``'o'`` after the ``'b'`` marker as both the only replay frame and the trigger to flip back to *Live*.

That made it impossible to replay more than one frame worth of history without the client dropping the *ReplayingBuffer* overlay prematurely, which kept the server ring buffer tiny (``256 KB``). proto-v3 introduces a new frame type ``FRAME_REPLAY_END`` (byte ``0x65`` / ``'e'``) and changes the handshake to ``state → 'b' → 'o'(chunk_0) → 'o'(chunk_1) → … → 'e'``.

The server ring buffer default is also bumped to ``4 MB`` (configurable per session via ``SessionConfig.ring_buffer_bytes``); chunk size is ``64 KB``.

### Replay and incompatible wire shape

Wire shape (delta from proto-v2).

#### Reconnect handshake frames

Reconnect handshake on ``/ws/session/<id>`` and on the ``CH_SESSION_IO`` envelope channel through ``central_server``: ``state-frame → FRAME_REPLAY → N × FRAME_OUTPUT(chunk) → FRAME_REPLAY_END → (live frames…)``. ``N >= 1`` always; an empty ring still emits one empty ``FRAME_OUTPUT`` between the markers so clients can rely on at least one output frame inside the replay span.

#### ReplayingBuffer state

Mobile clients now hold *ReplayingBuffer* state until ``'e'`` is received; chunked ``'o'`` frames in between are appended without state transitions.

#### Incompatible frame manifest_url

``incompatible`` rejection frame (server → agent, text, immediately before the ``4002`` close) now carries an additional ``manifest_url`` field: ``{type:"incompatible", reason, min_client_version, server_protocol_versions, manifest_url}``. The value is a relative path (``/api/v1/agent/manifest.json``) the agent resolves against the relay's HTTPS origin.

The agent's HTTPS-bootstrap path uses it to fetch the signed manifest out-of-band, breaking the chicken-and-egg in which the rejected protocol version is also the channel that would deliver the manifest pointer the agent needs to update past it. The signature remains the trust anchor — a misleading ``manifest_url`` can at worst force a fail-closed ``signature_failed`` outcome.

### HTTPS-bootstrap auto-update path (proto-v3+)

In addition to the in-band manifest delivery (``auth_ok`` and ``agent_manifest_update`` control frames), the agent reaches the same signed manifest over plain HTTPS at ``GET /api/v1/agent/manifest.json`` on the central server. The bootstrap fetch is wired on three triggers:

* **Startup pre-flight** — the lifespan calls ``run_https_update_check`` before the first relay connect, so an outdated agent self-heals during cold boot.

* **Protocol-incompatible recovery** — on close-code ``4002`` the relay's connect loop fires a callback that runs ``run_https_update_check(force=True)`` against the ``manifest_url`` hint from the rejection payload (falling back to the URL derived from ``RELAY_SERVER_URL``).

* **Periodic poll** — a 60-minute background task reconciles any manifest the relay channel missed (queue saturation, missed rebroadcast, etc.).

The HTTPS bootstrap is the floor below the relay-driven path. As long as a deployed agent has shipped with the bootstrap and a pinned trust key (``<prefix>/trust/central_server.pub``), any future ``PROTOCOL_VERSION`` bump is recoverable without operator intervention.

## proto-v2 (superseded by proto-v3)

* Introduced: 2026-05-13; retired: 2026-05-23.
* App support: ``APP_PROTOCOL_VERSION = 2`` (Android), ``PROTOCOL_VERSION = 2`` (Python — both ``mobaide_agent`` and ``central_server``).

### Inline manifest delivery

Breaking change vs proto-v1: the signed manifest **body** is now delivered inline over the relay WebSocket (in ``auth_ok`` and ``agent_manifest_update`` control frames).

The previous design carried only a relative URL pointer (``/api/v1/agent/manifest.json``) and required the agent to fetch the manifest over HTTP — but the agent treated any ``/``-prefixed value as a local filesystem path, so the user-facing "Install now" path always failed with ``signature_failed``. proto-v2 hands the body directly to ``verify_signature``.

### proto-v2 wire shape

Wire shape.

#### Auth handshake messages

Auth handshake (agent ↔ central_server, text frame).

* Agent sends ``{type:"auth", agent_id, token, protocol_version, agent_version, capabilities}``.

* Server replies ``{type:"auth_ok", server_protocol_versions, min_client_protocol_version, server_version, capabilities, latest_agent_version, manifest_body, manifest_signature}`` OR closes with code ``4002`` and emits ``{type:"incompatible", reason, min_client_version, server_protocol_versions}``.

  ``manifest_body`` is the full signed JSON body (``version``, ``tarball_url``, ``tarball_sha256``, ``wheel_url``, ``wheel_sha256``, ``released_at``, …), exactly the bytes the release pipeline Ed25519-signed.

#### Manifest push message

Push (server → agent, CH_CONTROL): same shape as ``auth_ok`` for the manifest fields — ``{type:"agent_manifest_update", version, manifest_body, manifest_signature}``.

#### Mobile to agent server_info

Mobile ↔ agent (text frame, after auth + before binary handshake): ``{type:"server_info", agent_version, protocol_version, supported_protocol_versions, capabilities}``.

#### Version REST route

REST: ``GET /api/version`` returns ``{name, server_version, protocol_versions, min_client_version, capabilities, latest_agent_version}``. ``latest_agent_version`` is the published version only when it is higher than ``server_version``, else ``null``: the updater installs only a higher version, so a lower one is not offered. The manifest body / signature is intentionally NOT exposed here — agents receive it on the relay tunnel; install scripts fetch ``GET /api/v1/agent/manifest.json`` directly during bootstrap.

## Application-level WebSocket close codes

The following codes are used in the application-private range (4000–4999). They are version-independent (not tied to any ``protocol_version`` integer) and apply to every current proto-v revision.

| Code | Constant | Direction | Semantics |
|------|----------|-----------|-----------|
| 4001 | ``AUTH_FAILED_CLOSE_CODE`` | server → client | JWT missing, expired, or invalid, **or** a device-bound JWT (a mobile token, or a user JWT carrying a ``device_id`` claim) issued at or before its device's revoke. Details below. |
| 4002 | ``PROTOCOL_INCOMPATIBLE_CLOSE_CODE`` | server → agent | Agent's ``protocol_version`` is not in the server's ``SUPPORTED_PROTOCOL_VERSIONS``. Details below. |
| 4010 | ``WS_CLOSE_ADMIN_REVOKED`` | server → client/agent | Admin permanently deleted the user or unregistered the agent, **or** the user self-scheduled deletion via ``POST /api/auth/me/delete`` (CLA-61) and the server is tearing the live session down before committing the schedule. Details below. |
| 4011 | ``WS_CLOSE_ADMIN_FORCE_DISCONNECT`` | server → client/agent | Admin issued a non-destructive force-disconnect. Credentials remain valid; the client/agent **may** reconnect. Deliberately distinct from 4001 so Android does not treat this as an auth failure. |
| 4012 | ``WS_CLOSE_AGENT_TRANSIENT_OFFLINE`` | server → mobile client | The mobile client tried to attach to a per-agent relay endpoint while the agent's tunnel was not currently connected (transient: agent restart, crash, network blip, deploy). Details below. |

### Close code 4001 details

* ``DELETE /api/devices/{device_id}`` also closes that device's live sockets (per-agent mobile sockets and ``/ws/user-events`` sockets bound by claim or verified hint) with this code and the reason frame ``device_revoked``.
* Deliberately not 4010, which clients render as "account removed".
* Android treats this as a session-expiry trigger (force logout).

### Close code 4002 details

* The relay never authenticates — but the agent's HTTPS-bootstrap path (see "HTTPS-bootstrap auto-update path" in the proto-v3 section) fetches the signed manifest from ``/api/v1/agent/manifest.json``, stages the new release, and the supervisor respawns the agent on the upgraded binary.
* The relay's reconnect-backoff jumps to ``reconnect_max_delay`` on this code so the per-60s log churn stops while the binary swap is in flight.

### Close code 4010 details

* **Also** emitted by the auth dependencies (`ws_authenticate_user`, `ws_authenticate_mobile`) when a still-cryptographically-valid JWT references a user/agent that has been cascade-deleted — so the offline-then-reconnect logout path renders the same "account removed" message as the synchronous teardown.
* The CLA-61 sweeper also fires this code at final-purge time so any reconnected client on a stale token is forced out.
* All associated sessions are terminated and credentials are invalid.
* Android displays a user-visible "account removed" snackbar and permanently suppresses auto-reconnect.

### Close code 4012 details

* Also emitted to paired mobile clients when an agent's tunnel disconnects mid-session.
* Credentials remain valid; the client **must** continue its exponential-backoff reconnect loop.
* Deliberately distinct from 4010 so Android does not treat a transient agent restart as a destructive admin-revoke.

## proto-v1 (superseded by proto-v2)

* Introduced: 2026-04-26; retired: 2026-05-13.

* Carried ``manifest_url`` + ``manifest_signature`` in ``auth_ok`` and ``agent_manifest_update``. The agent treated the relative URL as a local file path, producing a fail-closed ``signature_failed`` on "Install now". See proto-v2 above for the fix and the new shape.

## How to bump

1. Increment ``PROTOCOL_VERSION`` and update ``SUPPORTED_PROTOCOL_VERSIONS`` in **both** ``mobaide_agent/mobaide/relay/envelope.py`` and ``central_server/central_server/relay/envelope.py``. The CI ``protocol-integrity`` workflow rejects any commit that drifts the two.

2. Update the ``BuildConfig.APP_PROTOCOL_VERSION`` field in ``android/app/build.gradle.kts`` to match, the ``APP_PROTOCOL_VERSION`` / ``MIN_SERVER_PROTOCOL_VERSION`` values in ``ios/Config/Protocol.xcconfig``, and ``APP_PROTOCOL_VERSION`` in ``web/src/api/bootstrap.ts``.

   The ``protocol-integrity`` workflow asserts all three ``APP_PROTOCOL_VERSION`` values are equal (and iOS's ``MIN_SERVER_PROTOCOL_VERSION`` equals Android's — the web gate is set membership against the server's advertised list and carries no floor of its own), so a bump that misses a client fails CI.

3. Update ``tests/protocol_golden.json`` to match the new shape; any change must update the golden file or CI fails.

4. Add a new section to this file documenting the change.

5. Tag the protocol commit ``proto-v<N>``.

### HTTPS-bootstrap pre-requisite

Before bumping ``PROTOCOL_VERSION``, confirm that **every deployed agent release in the support window already speaks the HTTPS-bootstrap path** (this means every release at or after ``agent-v0.2.5``).

The bootstrap is the only channel that can deliver a manifest pointer to an agent whose ``protocol_version`` is below ``MIN_CLIENT_PROTOCOL_VERSION``; an agent that pre-dates the bootstrap is stranded by any incompatible bump and must be reinstalled by hand.

The release pipeline does not enforce this gate yet — it is a release-checklist item until the ``test_e2e_autoupdate`` suite grows coverage for the bootstrap path itself.
