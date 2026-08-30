# Wire-protocol versions

The Mobaide system uses a single **integer** ``protocol_version`` as the
authoritative compatibility signal between the Android app, ``mobaide_agent``,
and ``central_server``. The three human-facing semvers (``android-vX.Y.Z``,
``agent-vX.Y.Z``, ``server-vX.Y.Z``) are independent and ship on their own
cadence; they do **not** gate compatibility.

## proto-v7 (current)

* Introduced: 2026-07-27 (CLA-153).
* Python support: ``PROTOCOL_VERSION = 7``, ``SUPPORTED_PROTOCOL_VERSIONS =
  (6, 7)``, ``MIN_CLIENT_PROTOCOL_VERSION = 6`` (both ``mobaide_agent`` and
  ``central_server``). Android advanced to ``7`` with its client half
  (CLA-154) and iOS with its half (CLA-155) — ``APP_PROTOCOL_VERSION = 7``
  on both; old mobile builds remain accepted meanwhile.
* **Deployment order matters**: a proto-v7 agent handshaking against a
  proto-v6-only relay is closed with ``4002``, so the central server must
  deploy before any agent rollout. Old (v6) agents keep connecting — the
  relay supports both versions — but cannot launch custom agent types
  (see the ``409`` gate below).
* Change vs proto-v6: account-scoped **custom AI agent types** — a name +
  launch command registry on the central server, launched as plain
  terminal sessions with an **open session kind**. The version is bumped
  (rather than riding the additive no-bump precedent) because the kind
  vocabulary stops being a closed enum: clients must **preserve unknown
  ``kind`` strings verbatim** instead of coercing them to ``claude``, and
  a detectable version boundary is needed so the relay can refuse custom
  launches against old agents that would silently ignore the new fields
  and open a bare shell.
* **Open session kind**: ``kind`` (in ``session_to_wire_dict`` and every
  session list/event envelope) may now be the raw string
  ``custom_<12hex>`` (pattern ``^custom_[0-9a-f]{12}$``; the 12-hex id is
  the registry row id). Built-in kinds are unchanged. The session wire
  dict also gains nullable ``kind_label`` — a snapshot of the type's
  display name taken at session creation, so lists render correctly after
  the type is renamed or deleted (null for built-in kinds).
* **Session create** (``POST /api/sessions`` proxied over ``CH_CONTROL``,
  and the equivalent ``create_session`` control message): the **existing
  ``cli`` discriminator** carries the custom kind — ``cli:
  "custom_<12hex>"``. Clients never send the command; the client-supplied
  ``command``/``env`` hints stay discarded exactly as on the Terminal
  path.
  * The relay **strips** ``resolved_command`` / ``resolved_name`` from
    every client body on this route, then — for a valid custom ``cli`` —
    looks the id up in the caller's registry and **injects** the stored
    command + display name into the forwarded body. These two fields are
    therefore server-only: on the agent side a non-null value is always
    relay-authored (mobile clients have no path that reaches the agent's
    control channel with arbitrary JSON).
  * Relay-side failures answer without forwarding: unknown/deleted id →
    ``404``; malformed ``custom_*`` id → ``400``; **live tunnel's**
    negotiated ``protocol_version`` ``< 7`` (or unset) → ``409`` with body
    ``{"agent_update_required": true, "detail": ...}``. Agent **offline** →
    the standard proxy ``503`` (the version gate applies only to a live
    tunnel — the relay never answers ``agent_update_required`` from a
    persisted handshake row, which overlapping tunnels can leave
    describing a different connection).
  * Agent-side: a custom ``cli`` without ``resolved_command`` (agent-direct
    REST, or a pre-v7 relay) fails cleanly — ``400`` on the REST/proxy
    paths, ``session_create_failed`` on the control path — never a silent
    bare-shell downgrade. The agent-direct REST API always rejects custom
    kinds (``400``): it has no registry access.
  * Spawn contract: POSIX ``[login_shell, "-l", "-c", command]`` with the
    stored command as **one argv element** (no interpolation, no quoting
    layer); Windows ``["cmd.exe", "/c", command]``. The login shell
    resolves ``$SHELL`` → ``pw_shell`` → ``/bin/sh``, falling back to
    ``/bin/sh`` when the basename isn't a known POSIX shell (bash / zsh /
    sh / dash / ksh). Plain Terminal sessions now use the same resolver
    (previously hardcoded ``/bin/bash``). Custom sessions are
    plain-terminal semantics end to end: the ``worktree`` /
    ``branch_name`` toggle works unchanged, and there is no managed-CLI
    machinery (no options block, no attention hooks, no autoscan).
* **REST registry** (dashboard/user JWT, non-admin, on the central
  server): ``GET /api/custom-agent-types`` → list;
  ``POST /api/custom-agent-types`` ``{"name", "command"}`` → ``201``;
  ``PATCH /api/custom-agent-types/{id}`` ``{"name"? , "command"?}`` (at
  least one field; provided-but-blank → ``422``; there is **no**
  empty-string-clears form — both attributes are mandatory);
  ``DELETE /api/custom-agent-types/{id}`` → ``204`` (soft-delete).
  Validation: ``name`` 1–60 chars trimmed, unique per account
  case-insensitively (ASCII case folding only — SQLite ``NOCASE``;
  non-ASCII characters compare byte-wise, so ``Ågent`` and ``ågent`` are
  distinct names); ``command`` 1–1000 chars trimmed; an embedded NUL
  byte in either field → ``422``. The per-account
  cap is **20** live types, enforced atomically → ``409``; a name
  conflict → ``409`` (distinct ``detail`` strings); ownership violations
  and repeat deletes → ``403`` (same non-idempotent-DELETE caveat as
  shortcuts). The row shape is ``{"id", "name", "command", "created_at",
  "updated_at"}`` — ``command`` is returned to its owner (the editor
  needs it); only session-create requests never carry it.
* **New user-event kinds** (server-originated, same envelope as
  ``template.*`` / ``shortcut.*``): ``custom_type.created`` /
  ``custom_type.updated`` (full row) and ``custom_type.deleted``
  ``{"id"}``. Deleting a type also soft-deletes the owner's saved
  shortcuts whose ``kind`` equals ``custom_<id>`` **in the same
  transaction, with only the one ``custom_type.deleted`` event** — no
  companion ``shortcut.deleted`` events; Android and iOS purge their
  local shortcut caches for that kind on receipt (the web client removed
  its shortcuts UI and ignores the cascade). Connect-time reconcile is the
  usual REST refresh (``GET /api/custom-agent-types``). Per-type shortcut
  scoping needs no server change: ``custom_<12hex>`` already satisfies
  the shortcuts ``KIND_PATTERN`` and the server stores kinds opaquely.
* Protocol-fixture updates under ``tests/protocol_fixtures/`` (dropping
  the ``unknown_coerces_to_claude`` cases, adding ``custom_*`` round-trip
  cases and the ``CustomAgentTypeDto`` fixtures) rode with the iOS client
  half (CLA-155), which was the fixtures' only consumer at the time; its
  registry gate would have broken the iOS build had they changed before
  the ``.custom`` kind existed there. **Since CLA-191 (WEB-04) the web
  client is a second consumer**: ``web/src/protocol/`` reads the same JSON
  off disk under Vitest, and ``protocol-integrity`` runs that suite on
  every PR. A fixture edit must therefore satisfy both arms — and note
  that Swift's ``Codable`` drops unknown keys on re-encode while the web
  client's ``z.looseObject`` preserves them, so an unknown-extra-key case
  added to a shared fixture would fail iOS. Web coverage is deliberately
  partial and declared in ``web/src/protocol/__fixtures__/coverage.ts``,
  which fails if a new fixture file is neither asserted nor assigned an
  owner.
* Additive delta within proto-v7 (2026-08-23, Codex model catalog): **additive
  optional key + one relaxed field, no version bump.** Each
  ``capabilities.cli.<cli>`` entry on ``GET /api/version`` (see the proto-v5
  CLI-availability bullet for the base ``{"available": <bool>}`` shape) may also
  carry ``models`` — the agent's startup enumeration of that CLI's launchable
  models, in CLI-reported order — and ``model_details`` — an array of
  ``{"id": <str>, "efforts": [<str>, …], "default_effort"?: <str>}`` for CLIs
  whose catalog exposes per-model reasoning levels (today: Codex, via
  ``codex debug models``). Both keys are omitted — never emitted empty or null —
  when enumeration is unsupported or produced nothing; clients then fall back to
  their hardcoded model/effort lists, so old and new agents degrade identically.
  An entry may also carry ``"models_pending": true`` (omitted, never false):
  the agent's startup enumeration was still running when this response was
  built (the handler waits a short bound, but a single probe attempt may
  legitimately take longer) and *this CLI's own* enumeration had not yet
  produced a result — a CLI that already memoized its list is never marked
  merely because a sibling's probe still runs. The absence of
  ``models``/``model_details`` in a marked entry is provisional, not
  authoritative: clients keep persisted catalog selections instead of
  reconciling them away, and re-probe until an unmarked answer arrives. Its
  presence also proves a catalog-era agent (pre-catalog agents never emit it).
  Emitted by the shared ``build_cli_capabilities`` builder on both the direct
  and relay transports. (``models`` itself had shipped earlier without a
  PROTOCOL.md entry; this bullet is its documentation of record.)
  * ``codex_options.reasoning_effort`` is relaxed from the proto-v5 enum
    (``minimal`` | ``low`` | ``medium`` | ``high``) to a free-form string on the
    agent, mirroring the ``model`` field's "free-form on the server; the client
    curates the list" posture: the effort vocabulary is now enumerated per-model
    at runtime, and a closed set would reject values the agent itself just
    advertised (e.g. ``xhigh``/``max``/``ultra`` on the gpt-5.6 catalog). The
    value is embedded after ``=`` inside the single exec'd argv element
    ``-c model_reasoning_effort=<v>``, so no flag-smuggling path opens up. The
    proto-v5 section below remains historically accurate for agents of that era.
* Additive delta within proto-v7 (2026-08-11, Claude debug toggle): **additive
  optional field, no version bump.** ``claude_options`` gains ``debug`` — bool,
  default ``false`` — emitted by the agent as ``--debug``. Previously
  ``ClaudeLauncher._build_argv`` passed ``--debug`` unconditionally on every
  Claude session (interactive, Auto Scan and worktree alike), so every user saw
  Claude Code's verbose diagnostics; it is now a per-session choice surfaced as
  a **Debug logging** toggle in all three clients' create-session wizards,
  default off. No other managed CLI ever carried the flag, and the relay
  forwards ``claude_options`` opaquely, so ``central_server`` is untouched.
  * The wire-absence default (``false``) **matches** the clients' UI default,
    which is what makes this additive rather than a ``skip_permissions``-style
    hazard: an omitted field means exactly what a client leaving the toggle off
    means. Old client + new agent therefore lands on debug **off** — the
    intended outcome. New client + old agent is a harmless no-op: the old
    agent ignores the unknown key and keeps hardcoding ``--debug`` until it
    updates.
  * Shared-fixture consumers (iOS ``ClaudeOptionsModel``, the web
    ``claudeOptionsSchema``) both carry the field, so all three fixtures that
    embed a ``claude_options`` object were updated:
    ``tests/protocol_fixtures/dto/agent/{ClaudeOptionsModel,CreateSessionRequest,StartScanRequest}.json``.
    Only ``ClaudeOptionsModel.json`` exercises both values — ``debug: true`` in
    its ``full`` case and ``debug: false`` materialized in
    ``empty_defaults_materialize_skip_permissions_true``; the
    ``CreateSessionRequest`` / ``StartScanRequest`` cases carry it explicitly as
    ``false`` (their own defaults cases have no ``claude_options`` block at
    all). Both consumers emit every boolean on re-encode, so an omitted key
    there fails the round-trip.
* Additive delta within proto-v7 (2026-08-05, stale-attach fix): **agent-side
  only — no wire change, no version bump.** ``RelayClient._handle_session_input``
  no longer auto-creates a session for a ``CH_SESSION_IO`` frame naming an
  unknown ``session_id``; it drops the frame and answers with the same
  ``stopped`` state frame ``_handle_mobile_attach`` already sends for a stale
  attach (both now share ``_send_stopped_frame``). Auto-create used to fire off
  a bare resize frame — which Android, iOS and the web client all send
  unconditionally on every socket connect to re-assert their cached PTY size —
  so merely attaching to a stale id (agent restarted, session swept, a resumed
  id from preferences, a stale notification, a restored navigation stack)
  silently spawned a real login shell. The empty-payload guard, which
  previously sat *after* the auto-create, now runs first. An unpatched client
  paired with a fixed agent needs no changes: it receives exactly the
  ``stopped`` frame the attach path has always sent for a stale id and renders
  a stopped session instead of a phantom shell — old client + new agent is the
  supported combination. Deciding that a session is *gone* (rather than merely
  stopped) stays a client-side judgement from the ``sessions.changed``
  snapshot, not a wire signal.

## proto-v6 (superseded by proto-v7)

* Introduced: 2026-06-26
* App support: ``APP_PROTOCOL_VERSION = 6`` (Android), ``PROTOCOL_VERSION = 6``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Change vs proto-v5: **breaking** to the ``claude_options`` shape
  (pre-production, no compatibility window). Replaces Claude's boolean
  ``max_effort`` with a rich option set mirroring the Codex precedent (CLA-84).
  The version is bumped so the three artifacts stay in lockstep and an old client
  is cleanly rejected (``4002`` + HTTPS-bootstrap auto-update) rather than
  silently launching with the wrong reasoning level.
* Wire shape (delta from proto-v5), all on ``CH_CONTROL`` (forwarded verbatim by
  ``central_server`` — no server logic change):
  * The ``create_session`` control message (the ``POST /api/sessions`` body
    proxied over ``CH_CONTROL``, and the Auto Scan ``claude_options``) drops
    ``max_effort`` (bool) and gains five fields:
    * ``model`` — str|null: a curated Claude alias (``opus`` | ``sonnet`` |
      ``haiku`` | ``fable``) or null for the account default. Free-form on the
      server (the client curates the list); emitted as ``--model <alias>``.
    * ``effort`` — ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max`` | null.
      null / ``high`` emit no ``--effort`` flag (``high`` is the CLI default);
      suppressed entirely when ``ultracode`` is on.
    * ``fast_mode`` — bool (Opus-only fast mode) and ``ultracode`` — bool: both
      emitted through a single merged ``--settings`` dict
      (``{"fastMode": true, "ultracode": true}``), merged into the per-session
      attention-hooks settings file when one is written — never two separate
      ``--settings`` flags.
    * ``append_system_prompt`` — str|null: emitted as
      ``--append-system-prompt <text>``.
  * ``codex_options`` and all other CH_CONTROL / CH_EVENTS / CH_SESSION_IO shapes
    are unchanged.
* Managed **Gemini CLI** as a third coding agent (CLA-87 — **additive, no
  version bump**): Gemini is wired through the same extension points the Codex
  foundation established, so no JSON shape breaks and the version stays at ``6``.
  An old proto-v6 client is unaffected (it never sends ``cli:"gemini"``); a new
  client feature-detects Gemini via the ``capabilities.cli`` advertisement rather
  than a version gate — the same forward-compat contract that made the Codex
  auth/login and CLI-availability additions no-bump changes (see proto-v5).
  * ``create_session`` (``POST /api/sessions`` proxied over ``CH_CONTROL``):
    ``cli`` gains the value ``"gemini"`` and a new optional ``gemini_options``
    object — ``model`` (free-form string: a preset alias ``auto`` / ``pro`` /
    ``flash`` / ``flash-lite``, any concrete model id, or null),
    ``approval_mode`` (``default`` | ``auto_edit`` | ``yolo`` | ``plan``;
    ``--approval-mode`` replaces Gemini's deprecated ``--yolo``),
    ``include_directories`` (array of str), ``sandbox`` (bool), plus the shared
    ``worktree`` / ``branch_name`` intent. Exactly one of ``claude_options`` /
    ``codex_options`` / ``gemini_options`` is honoured per session.
  * ``session_to_wire_dict`` ``kind`` gains the value ``"gemini"`` — the Android
    client already coerces an unknown ``kind`` forward-compatibly (a typed
    ``Gemini`` enum member lands with the create-session UI, CLA-88).
  * Missing binary → ``gemini_cli_missing: true`` + ``searched_paths`` (``503`` /
    ``session_create_failed``); an unauthenticated host → ``gemini_auth_required:
    true`` (``409``, non-``401`` for the same app-JWT-interceptor reason as
    Codex). Auth is **detection-only**: Gemini has no ``login`` /
    ``--with-api-key`` subcommand and its OAuth needs a browser, so there is
    **no** ``POST /api/gemini/login`` route — the operator authenticates
    ``gemini`` on the host (``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` / Vertex-AI
    credentials / cached OAuth). In-app API-key entry is deferred (CLA-91).
  * ``capabilities.cli`` on ``GET /api/version`` gains a ``gemini`` entry from the
    same registry-driven builder, so the mode-select screen enables Gemini only
    when its binary resolves on the host.
* APNs push pipeline for session attention (CLA-106 — **additive, no version
  bump**; neither ``envelope.py`` copy changes, so the diff guard is untouched).
  Fills the iOS-09 gap: a suspended iOS app has no live socket, so the server
  must be able to *see* attention (today it rides the server-opaque
  ``CH_EVENTS``) and push it.
  * **User-scoped events channel** (``/relay/ws/user-events``, documented here
    for the first time): a one-way server→client text channel authenticated
    with the dashboard/user JWT. Each frame is a JSON envelope ``{"type":
    <kind>, "payload": {...}}``. Existing kinds (all server-originated):
    ``agent.status_changed`` ``{"agent_id", "online"}`` and
    ``template.created`` / ``template.updated`` (full template row) /
    ``template.deleted`` ``{"template_id", "version", "updated_at"}``. Clients
    must ignore unknown kinds (both apps already do — Android
    ``else -> Unit``, iOS ``default: .none``).
  * New user-event kind ``session.attention`` — payload ``{"session_id",
    "reason", "title"?, "project_name"?, "exit_reason"?, "agent_id"}`` (slim,
    not the full session wire dict; ``reason`` is the proto-v4 attention
    enum). ``agent_id`` is **stamped by the server** from the authenticated
    agent tunnel — an agent-supplied value is never trusted.
  * New agent→server ``CH_CONTROL`` message ``{"type": "user_event", "kind":
    "session.attention", "payload": {…}}``, sent by the agent alongside (not
    instead of) the existing opaque ``CH_EVENTS`` ``session.attention``
    publication, on attention **set** only. The server validates the kind
    against an allowlist (only ``session.attention``), broadcasts it via the
    user-events channel, and hands it to the push dispatcher along with the
    devices that channel already reaches. Back-compat: an old server
    WARN-counts the unknown control type and drops it; an old agent simply
    never sends it (no pushes, previous behaviour).
  * **Presence gating is per device, not per account.** A client may pass its
    registered ``device_id`` as a query param on ``/ws/user-events``; the
    server accepts it only when it matches one of that user's own non-revoked
    device rows, and treats it purely as a delivery hint (an absent or
    unrecognised value is logged and the socket serves events as before —
    it simply cannot suppress anything). The dispatcher then pushes to every
    registered device **except** those holding such a socket, so WebSocket
    delivery still wins for a connected device while a second, sleeping device
    is not muted by it. Gating on the account instead — the original CLA-106
    rule — meant one always-on client (Android's foreground service) silenced
    pushes to every other device the user owned.
  * **Push dispatch** (server→APNs, not a wire-protocol surface but recorded
    for the delivery contract): alert push (never silent), one per targeted
    device of the user, ``apns-collapse-id`` = ``thread-id`` = ``session_id``
    (matches the clients' notification identifier so replace/suppress/
    reconcile semantics hold), ``aps.alert`` title/body from the shared
    attention content map, custom data ``session_id`` + ``agent_id`` for the
    tap deep link. Requires ``APNS_KEY_PATH`` / ``APNS_KEY_ID`` /
    ``APNS_TEAM_ID`` / ``APNS_TOPIC`` on the server; unconfigured → dispatch
    disabled, WebSocket-only delivery (no errors).
  * **REST**: ``POST /api/devices/{device_id}/push-token`` ``{"platform":
    "ios", "token": <hex>, "environment": "sandbox"|"production"}`` → ``204``
    (``404`` when the device isn't the caller's or is revoked); ``DELETE`` on
    the same path clears the stored token (sign-out). ``mobile_devices``
    gains nullable ``platform`` / ``push_token`` / ``push_environment``
    columns; ``MobileTokenRequest`` and the pairing-claim body gain an
    optional ``platform`` field (iOS sends ``"ios"``; Android sends nothing —
    server-side optional). A cross-tenant device reassign, a device revoke,
    and an APNs ``410 Unregistered``/``BadDeviceToken`` result all clear the
    stored token.
* Server-synced saved keyboard shortcuts (CLA-137 — **additive, no version
  bump**; ``central_server`` + clients only, the agent protocol is untouched).
  Persistent named chord buttons per session kind, shared across the user's
  devices — the server stores ``kind`` (SessionKind wire name) and
  ``chord_token`` (the client's ``KeyChord.token()`` string) opaquely and
  never interprets either.
  * New user-event kinds (server-originated, same envelope as ``template.*``):
    ``shortcut.created`` / ``shortcut.updated`` (full shortcut row:
    ``{"shortcut_id", "kind", "chord_token", "name"?, "position",
    "created_at", "updated_at", "version"}``) and ``shortcut.deleted``
    ``{"shortcut_id", "version", "updated_at"}``. Clients ignoring unknown
    kinds (both apps) make this a no-op for older builds.
  * **REST** (dashboard/user JWT, non-admin): ``GET /api/shortcuts`` →
    list; ``POST /api/shortcuts`` ``{"kind", "chord_token", "name"?}`` →
    ``201`` — an **upsert**: re-saving an existing live (kind, chord_token)
    updates its name (when provided) instead of duplicating, still ``201``;
    ``PATCH /api/shortcuts/{id}`` ``{"name"}`` renames — the field is
    required (an empty body ``{}`` → ``422``, it must not silently clear);
    ``""`` (or explicit null) clears the name back to null (the Android JSON
    encoder omits null defaults, so the empty string is the wire's "clear"
    sentinel); ``DELETE`` → ``204`` soft-delete. Ownership violations →
    ``403`` — and since the ownership probe filters soft-deleted rows, a
    **repeat** ``DELETE`` of an already-deleted shortcut also answers
    ``403``, not ``204``. DELETE is therefore **not idempotent**: a client
    whose UI lets the same row be removed twice must treat ``403``/``404``
    on an id from its own list as "already gone" rather than reporting a
    failure the user would rightly disbelieve.

## proto-v5 (superseded by proto-v6)

* Introduced: 2026-06-21
* App support: ``APP_PROTOCOL_VERSION = 5`` (Android), ``PROTOCOL_VERSION = 5``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Change vs proto-v4: **additive** (no frame-layout or existing-shape break).
  Foundation for managing the **Codex CLI** as a second coding agent alongside
  Claude (CLA-68). The version is bumped so the three artifacts stay in lockstep
  and ``protocol-integrity`` has a single authoritative integer to assert against.
* Wire shape (delta from proto-v4), all on ``CH_CONTROL`` (forwarded verbatim by
  ``central_server`` — no server logic change):
  * The ``create_session`` control message (and the ``POST /api/sessions`` body
    proxied over ``CH_CONTROL``) gains three optional fields:
    * ``cli`` — ``"claude"`` | ``"codex"``: selects the managed CLI. When
      omitted, the agent infers it from whichever options block is present, so a
      legacy ``claude_options``-only payload still launches Claude
      (forward/backward compatible).
    * ``codex_options`` — object mirroring ``claude_options`` for Codex:
      ``model`` (str|null), ``sandbox`` (``read-only`` | ``workspace-write`` |
      ``danger-full-access``), ``approval`` (``untrusted`` | ``on-request`` |
      ``never``), ``reasoning_effort`` (``minimal`` | ``low`` | ``medium`` |
      ``high`` | null), ``full_auto`` (bool — Codex
      ``--dangerously-bypass-approvals-and-sandbox``), ``initial_prompt``
      (str|null). Exactly one of ``claude_options`` / ``codex_options`` is
      honoured per session.
  * The single-session wire dict (``session_to_wire_dict``) ``kind`` field gains
    a new value ``"codex"`` (alongside ``claude`` / ``auto_scan`` / ``shell``).
    The Android app models ``kind`` as a typed ``SessionKind`` enum and now
    carries a ``Codex`` member (CLA-69), so it decodes a v5 session directly
    rather than relying on the proto-5 version gate to reject it. For
    forward-compatibility it also coerces an unknown future ``kind`` to the
    enum's default (``coerceInputValues`` on the event-bus JSON decoder) instead
    of dropping the whole event.
  * ``session_create_failed`` / proxy ``503`` responses for a missing Codex
    binary carry ``codex_cli_missing: true`` + ``searched_paths`` — the exact
    parallel of the existing ``claude_cli_missing`` marker.
  * Codex auth/login (CLA-71 — **additive, no version bump**): when Codex is
    installed but not signed in, the agent guards the spawn path and returns a
    ``codex_auth_required: true`` marker on session create — HTTP ``409`` (direct
    REST: marker inside a dict ``detail``; relay proxy: top-level in the body),
    and top-level on the ``session_create_failed`` control message. ``409`` (not
    ``401``) so the Android app-JWT interceptor's refresh-retry-on-401 can't
    swallow the marker before the client reads it. Parallel to
    ``codex_cli_missing``. A new authenticated endpoint ``POST /api/codex/login``
    (body ``{"api_key": "<secret>"}``) runs ``codex login --with-api-key`` (key
    fed on stdin, never argv / never persisted by the agent) and returns
    ``{"authenticated": true}`` on success, or ``400`` with a sanitized detail on
    a rejected key (also non-``401`` for the same interceptor reason). The app
    shows a login prompt on the marker, then retries the create.
  * CLI-availability advertisement (**additive, no version bump**): the
    ``capabilities`` field on ``GET /api/version`` — previously always ``{}`` —
    now carries ``{"cli": {"<cli>": {"available": <bool>}, …}}`` with one entry
    per registered managed CLI (e.g. ``claude``, ``codex``). ``available``
    reflects whether that CLI's binary resolves on the agent host via the same
    override → env → PATH → fallbacks resolution a real spawn uses. Emitted
    identically by the direct FastAPI handler and the relay proxy path (one
    shared builder). The Android client parses it tolerantly — an absent /
    old-agent / malformed value is treated as available (optimistic) — and the
    create-session ``503`` ``<cli>_cli_missing`` / ``409`` ``<cli>_auth_required``
    markers remain the authoritative backstop. Drives the mode-select screen's
    per-CLI button enablement. (Later extended — see the proto-v7 additive delta
    of 2026-08-23 for the ``models`` / ``model_details`` keys an entry may also
    carry.)
* Out of scope (downstream issues): Android create-session UI for Codex
  (CLA-69, done), terminal control-key / approval affordances (CLA-70, done).
  The agent defaults Codex sessions to a non-interactive sandboxed preset
  (``--sandbox workspace-write --ask-for-approval never``) and cannot yet
  mediate in-session Codex approval prompts. Further Codex-auth work is tracked
  downstream: device-code login (CLA-79), mid-session re-auth (CLA-80),
  credential-validity probing (CLA-81).

## proto-v4 (superseded by proto-v5)

* Introduced: 2026-06-09
* App support: ``APP_PROTOCOL_VERSION = 4`` (Android), ``PROTOCOL_VERSION = 4``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Change vs proto-v3: **additive** (no frame-layout or existing-shape break).
  Adds hook-driven session-attention notifications (CLA-64). The version is
  bumped so the three artifacts stay in lockstep and ``protocol-integrity``
  has a single authoritative integer to assert against.
* Wire shape (delta from proto-v3), all on ``CH_EVENTS`` (forwarded verbatim
  by ``central_server``):
  * New event type ``session.attention`` — payload ``{"session": <session dict>}``.
    Emitted when a Claude Code hook reports the session needs the user:
    ``attention`` field is one of ``awaiting_permission`` (permission prompt),
    ``awaiting_os_permission`` (the agent's macOS startup-stall watchdog: a
    spawned child producing no output is most likely blocked on an invisible
    host-desktop TCC file-access modal — additive within proto-v4, no version
    bump), ``turn_finished`` (your move — see the attention-gate delta below),
    or ``ended`` (SessionEnd). It is a one-shot notification trigger; clients
    that miss it (the in-process ``EventBus`` drops on a full queue) still
    recover from the snapshot field below. Older clients ignore the unknown
    type (forward-compatible).
  * The single-session wire dict (``session_to_wire_dict`` — used by
    ``sessions.changed``, ``session.updated``, the ``/ws/sessions`` snapshot and
    the ``/sessions`` REST list) gains two nullable fields:
    ``attention`` (``awaiting_permission`` | ``awaiting_os_permission`` |
    ``turn_finished`` | ``ended`` | ``null``) and ``exit_reason`` (human-readable end cause from the Claude Code
    ``SessionEnd`` ``reason``, or ``process_exited`` on an unexpected death).
    These let a reconnecting/foregrounding client resync the "awaiting you"
    state after a missed one-shot event.
  * Detection source: the agent spawns interactive Claude sessions with a
    per-session ``--settings`` file wiring ``http`` hooks to a local
    ``POST /internal/hook`` endpoint (loopback + per-agent token guarded); the
    old terminal-buffer ``PromptDetector`` heuristic is retired.
* Additive delta within proto-v4 (2026-08-03, attention gate): **agent-side
  only — no wire change, no version bump.** The reason enum, the
  ``session.attention`` event and both snapshot fields are untouched; what
  changes is *when* the agent raises ``turn_finished``.
  * Claude Code's ``Stop`` fires once per model turn, including every
    re-invocation a completing background task or subagent causes, so mapping it
    straight to ``turn_finished`` alerted N+1 times for a session that fanned out
    N background agents. It is now debounced and deduped by
    ``mobaide/core/attention_gate.py`` before it reaches
    ``TerminalSession.set_attention``: a ``Stop`` whose payload reports
    ``stop_hook_active``, in-flight ``background_tasks``, or pending
    ``session_crons`` is dropped, and ``prompt_id`` dedupes repeat stops within
    one turn. A suppressed hook never writes the public ``attention`` field,
    so a client resyncing from a snapshot cannot resurrect a withheld alert.
  * The per-session settings file now registers ``Notification``
    (``permission_prompt`` **and** ``idle_prompt``), ``Stop``,
    ``UserPromptSubmit`` and ``SessionEnd``, and bakes a ``hook=<kind>`` query
    param into each URL alongside ``session_id``/``token`` so the endpoint
    classifies without parsing the body. ``idle_prompt`` is the backstop that
    still alerts when the payload-driven suppression was wrong.
    ``UserPromptSubmit`` is the turn boundary: it resets the gate and clears
    the public ``attention`` field, so a turn answered on the host cannot
    leave a stale badge that mutes later turns.
  * ``set_attention`` gained an equality guard: re-raising the reason already
    pending refreshes state without re-emitting the one-shot event. A changed
    ``exit_reason`` still emits, so the second ``ended`` that a ``/clear``
    produces (``SessionEnd`` reason ``clear``, PTY still running) still
    reaches clients.
  * **Registration constraint** (``hook_settings.REGISTERED_HOOK_EVENTS``):
    Claude Code strips an unrecognised ``hooks.<Event>`` key with a validation
    *warning* and raises a blocking invalid-settings dialog for any validation
    record when interactive, so registering an event newer than the oldest
    supported CLI would hang every session at a modal. Matcher values and
    optional payload fields are safe; new event keys are not.
* Additive delta within proto-v4 (2026-06-12, Send-button submit fix): the
  PTY frame set on ``/ws/session/<id>`` and the ``CH_SESSION_IO`` channel
  gains one C->S type and one semantic change. No version bump: the frame
  *layout* (1 type byte + payload) is unchanged, the new type is purely
  additive, ``central_server`` forwards ``CH_SESSION_IO`` payloads opaquely,
  and the app + agent ship together pre-production.
  * New C->S frame ``0x6d`` / ``'m'`` (**submit**): the agent writes the
    payload to the PTY, pauses ``SUBMIT_CR_DELAY_SECONDS`` (0.2 s), then
    writes CR as a **separate** PTY write. Ink TUIs (Claude CLI) treat a CR
    embedded in the same stdin chunk as pasted content; only a CR arriving
    as its own later read registers as the Enter key. Frames with an empty
    payload are dropped — a bare Enter could submit stale composer text.
  * Changed ``0x78`` / ``'x'`` (**execute**) semantics: the agent now ends
    the sequence with the same delayed CR (Ctrl+C → 0.1 s pause → payload →
    0.2 s pause → CR). Clients no longer append a trailing ``\r`` to submit
    or execute payloads; an old client that still sends ``text\r`` gets a
    harmless extra Enter on an empty composer.

## proto-v3 (superseded by proto-v4)

* Introduced: 2026-05-23
* App support: ``APP_PROTOCOL_VERSION = 3`` (Android), ``PROTOCOL_VERSION = 3``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Breaking change vs proto-v2: the reconnect replay handshake is now
  **chunked** and explicitly terminated. Before v3 the handshake was
  ``state → 'b' → 'o'(entire-ring)`` and the client treated the first
  ``'o'`` after the ``'b'`` marker as both the only replay frame and the
  trigger to flip back to *Live*. That made it impossible to replay more
  than one frame worth of history without the client dropping the
  *ReplayingBuffer* overlay prematurely, which in turn forced the server
  ring buffer to stay tiny (``256 KB``) and caused visible truncation of
  long sessions on reconnect. proto-v3 introduces a new frame type
  ``FRAME_REPLAY_END`` (byte ``0x65`` / ``'e'``) and changes the handshake
  to ``state → 'b' → 'o'(chunk_0) → 'o'(chunk_1) → … → 'e'``. The server
  ring buffer default is also bumped to ``4 MB`` (configurable per session
  via ``SessionConfig.ring_buffer_bytes``); chunk size is ``64 KB``.
* Wire shape (delta from proto-v2):
  * Reconnect handshake on ``/ws/session/<id>`` and on the
    ``CH_SESSION_IO`` envelope channel through ``central_server``:
    ``state-frame → FRAME_REPLAY → N × FRAME_OUTPUT(chunk) → FRAME_REPLAY_END
    → (live frames…)``. ``N >= 1`` always; an empty ring still emits one
    empty ``FRAME_OUTPUT`` between the markers so clients can rely on at
    least one output frame inside the replay span.
  * **The replay span is width-homogeneous.** Ring bytes carry no width of
    their own and the client renders them at whatever grid it has now, so a
    span mixing column counts desynchronizes a full-screen TUI's cursor
    arithmetic for the rest of it — and nothing repairs that, because a
    same-size ``TIOCSWINSZ`` raises no ``SIGWINCH``. The agent therefore
    replays only from the last column change made while the child was on the
    alternate screen (``TerminalSession._ring_width_start``); history older
    than that cut is not sent. Off the alternate screen no cut is made, since
    a line-oriented replay re-wraps correctly at any width and dropping it
    would delete usable scrollback. Clients need no change for this — the
    frame sequence is unaffected — but a replay can legitimately be shorter
    than the ring, and a session whose width has just changed can replay
    almost nothing.
  * Mobile clients now hold *ReplayingBuffer* state until ``'e'`` is
    received; chunked ``'o'`` frames in between are appended without state
    transitions.
  * ``incompatible`` rejection frame (server → agent, text, immediately
    before the ``4002`` close) now carries an additional ``manifest_url``
    field: ``{type:"incompatible", reason, min_client_version,
    server_protocol_versions, manifest_url}``. The value is a relative
    path (``/api/v1/agent/manifest.json``) the agent resolves against the
    relay's HTTPS origin. The agent's HTTPS-bootstrap path uses it to
    fetch the signed manifest out-of-band, breaking the chicken-and-egg
    in which the rejected protocol version is also the channel that
    would deliver the manifest pointer the agent needs to update past
    it. The signature remains the trust anchor — a misleading
    ``manifest_url`` can at worst force a fail-closed
    ``signature_failed`` outcome.

### HTTPS-bootstrap auto-update path (proto-v3+)

In addition to the in-band manifest delivery (``auth_ok`` and
``agent_manifest_update`` control frames), the agent reaches the same
signed manifest over plain HTTPS at
``GET /api/v1/agent/manifest.json`` on the central server. The bootstrap
fetch is wired on three triggers:

* **Startup pre-flight** — the lifespan calls
  ``run_https_update_check`` before the first relay connect, so an
  outdated agent self-heals during cold boot.
* **Protocol-incompatible recovery** — on close-code ``4002`` the
  relay's connect loop fires a callback that runs
  ``run_https_update_check(force=True)`` against the
  ``manifest_url`` hint from the rejection payload (falling back to
  the URL derived from ``RELAY_SERVER_URL``).
* **Periodic poll** — a 60-minute background task reconciles any
  manifest the relay channel missed (queue saturation, missed
  rebroadcast, etc.).

The HTTPS bootstrap is the floor below the relay-driven path. As long
as a deployed agent has shipped with the bootstrap and a pinned trust
key (``<prefix>/trust/central_server.pub``), any future
``PROTOCOL_VERSION`` bump is recoverable without operator
intervention.

## proto-v2 (superseded by proto-v3)

* Introduced: 2026-05-13; retired: 2026-05-23.
* App support: ``APP_PROTOCOL_VERSION = 2`` (Android), ``PROTOCOL_VERSION = 2``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Breaking change vs proto-v1: the signed manifest **body** is now delivered
  inline over the relay WebSocket (in ``auth_ok`` and ``agent_manifest_update``
  control frames). The previous design carried only a relative URL pointer
  (``/api/v1/agent/manifest.json``) and required the agent to fetch the
  manifest over HTTP — but the agent treated any ``/``-prefixed value as a
  local filesystem path, so the user-facing "Install now" path always failed
  with ``signature_failed`` (FileNotFoundError → fail-closed → wrong error
  message). proto-v2 hands the body directly to ``verify_signature``.
* Wire shape:
  * Auth handshake (agent ↔ central_server, text frame):
    * Agent sends ``{type:"auth", agent_id, token, protocol_version,
      agent_version, capabilities}``.
    * Server replies ``{type:"auth_ok", server_protocol_versions,
      min_client_protocol_version, server_version, capabilities,
      latest_agent_version, manifest_body, manifest_signature}`` OR closes
      with code ``4002`` and emits ``{type:"incompatible", reason,
      min_client_version, server_protocol_versions}``.
      ``manifest_body`` is the full signed JSON body
      (``version``, ``tarball_url``, ``tarball_sha256``, ``wheel_url``,
      ``wheel_sha256``, ``released_at``, …), exactly the bytes the release
      pipeline Ed25519-signed.
  * Push (server → agent, CH_CONTROL): same shape as ``auth_ok`` for the
    manifest fields — ``{type:"agent_manifest_update", version,
    manifest_body, manifest_signature}``.
  * Mobile ↔ agent (text frame, after auth + before binary handshake):
    ``{type:"server_info", agent_version, protocol_version,
    supported_protocol_versions, capabilities}``.
  * REST: ``GET /api/version`` returns ``{name, server_version,
    protocol_versions, min_client_version, capabilities,
    latest_agent_version}``. The manifest body / signature is intentionally
    NOT exposed here — agents receive it on the relay tunnel; install scripts
    fetch ``GET /api/v1/agent/manifest.json`` directly during bootstrap.

## Application-level WebSocket close codes

The following codes are used in the application-private range (4000–4999).
They are version-independent (not tied to any ``protocol_version`` integer)
and apply to every current proto-v revision.

| Code | Constant | Direction | Semantics |
|------|----------|-----------|-----------|
| 4001 | ``AUTH_FAILED_CLOSE_CODE`` | server → client | JWT missing, expired, or invalid. Android treats this as a session-expiry trigger (force logout). |
| 4002 | ``PROTOCOL_INCOMPATIBLE_CLOSE_CODE`` | server → agent | Agent's ``protocol_version`` is not in the server's ``SUPPORTED_PROTOCOL_VERSIONS``. The relay never authenticates — but the agent's HTTPS-bootstrap path (see "HTTPS-bootstrap auto-update path" in the proto-v3 section) fetches the signed manifest from ``/api/v1/agent/manifest.json``, stages the new release, and the supervisor respawns the agent on the upgraded binary. The relay's reconnect-backoff jumps to ``reconnect_max_delay`` on this code so the per-60s log churn stops while the binary swap is in flight. |
| 4010 | ``WS_CLOSE_ADMIN_REVOKED`` | server → client/agent | Admin permanently deleted the user or unregistered the agent, **or** the user self-scheduled deletion via ``POST /api/auth/me/delete`` (CLA-61) and the server is tearing the live session down before committing the schedule. **Also** emitted by the auth dependencies (`ws_authenticate_user`, `ws_authenticate_mobile`) when a still-cryptographically-valid JWT references a user/agent that has been cascade-deleted — so the offline-then-reconnect logout path renders the same "account removed" message as the synchronous teardown. The CLA-61 sweeper also fires this code at final-purge time so any reconnected client on a stale token is forced out. All associated sessions are terminated and credentials are invalid. Android displays a user-visible "account removed" snackbar and permanently suppresses auto-reconnect. |
| 4011 | ``WS_CLOSE_ADMIN_FORCE_DISCONNECT`` | server → client/agent | Admin issued a non-destructive force-disconnect. Credentials remain valid; the client/agent **may** reconnect. Deliberately distinct from 4001 so Android does not treat this as an auth failure. |
| 4012 | ``WS_CLOSE_AGENT_TRANSIENT_OFFLINE`` | server → mobile client | The mobile client tried to attach to a per-agent relay endpoint while the agent's tunnel was not currently connected (transient: agent restart, crash, network blip, deploy). Also emitted to paired mobile clients when an agent's tunnel disconnects mid-session. Credentials remain valid; the client **must** continue its exponential-backoff reconnect loop. Deliberately distinct from 4010 so Android does not treat a transient agent restart as a destructive admin-revoke. |

## proto-v1 (superseded by proto-v2)

* Introduced: 2026-04-26; retired: 2026-05-13.
* Carried ``manifest_url`` + ``manifest_signature`` in ``auth_ok`` and
  ``agent_manifest_update``. The agent treated the relative URL as a local
  file path on the apply-now path, producing a fail-closed
  ``signature_failed`` whenever a user tapped "Install now" on a real
  deployment. See proto-v2 above for the fix and the new shape.

## How to bump

1. Increment ``PROTOCOL_VERSION`` and update ``SUPPORTED_PROTOCOL_VERSIONS``
   in **both** ``mobaide_agent/mobaide/relay/envelope.py`` and
   ``central_server/central_server/relay/envelope.py``. The CI
   ``protocol-integrity`` workflow rejects any commit that drifts the two.
2. Update the ``BuildConfig.APP_PROTOCOL_VERSION`` field in
   ``android/app/build.gradle.kts`` to match, the
   ``APP_PROTOCOL_VERSION`` / ``MIN_SERVER_PROTOCOL_VERSION`` values in
   ``ios/Config/Protocol.xcconfig``, and ``APP_PROTOCOL_VERSION`` in
   ``web/src/api/bootstrap.ts``. The ``protocol-integrity`` workflow
   asserts all three ``APP_PROTOCOL_VERSION`` values are equal (and iOS's
   ``MIN_SERVER_PROTOCOL_VERSION`` equals Android's — the web gate is set
   membership against the server's advertised list and carries no floor of
   its own), so a bump that misses a client fails CI.
3. Update ``tests/protocol_golden.json`` to match the new shape; any change
   must update the golden file or CI fails.
4. Add a new section to this file documenting the change.
5. Tag the protocol commit ``proto-v<N>``.

### HTTPS-bootstrap pre-requisite

Before bumping ``PROTOCOL_VERSION``, confirm that **every deployed agent
release in the support window already speaks the HTTPS-bootstrap path**
(this means every release at or after ``agent-v0.2.5``). The bootstrap is
the only channel that can deliver a manifest pointer to an agent whose
``protocol_version`` is below ``MIN_CLIENT_PROTOCOL_VERSION``; an agent
that pre-dates the bootstrap is stranded by any incompatible bump and
must be reinstalled by hand. The release pipeline does not enforce this
gate yet — it is a release-checklist item until the
``test_e2e_autoupdate`` suite grows coverage for the bootstrap path
itself.
