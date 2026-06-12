# Wire-protocol versions

The Mobaide system uses a single **integer** ``protocol_version`` as the
authoritative compatibility signal between the Android app, ``mobaide_agent``,
and ``central_server``. The three human-facing semvers (``android-vX.Y.Z``,
``agent-vX.Y.Z``, ``server-vX.Y.Z``) are independent and ship on their own
cadence; they do **not** gate compatibility.

## proto-v4 (current)

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
    ``turn_finished`` (Stop — your move), or ``ended`` (SessionEnd). It is a
    one-shot notification trigger; clients that miss it (the in-process
    ``EventBus`` drops on a full queue) still recover from the snapshot field
    below. Older clients ignore the unknown type (forward-compatible).
  * The single-session wire dict (``session_to_wire_dict`` — used by
    ``sessions.changed``, ``session.updated``, the ``/ws/sessions`` snapshot and
    the ``/sessions`` REST list) gains two nullable fields:
    ``attention`` (``awaiting_permission`` | ``turn_finished`` | ``ended`` |
    ``null``) and ``exit_reason`` (human-readable end cause from the Claude Code
    ``SessionEnd`` ``reason``, or ``process_exited`` on an unexpected death).
    These let a reconnecting/foregrounding client resync the "awaiting you"
    state after a missed one-shot event.
  * Detection source: the agent spawns interactive Claude sessions with a
    per-session ``--settings`` file wiring ``http`` hooks to a local
    ``POST /internal/hook`` endpoint (loopback + per-agent token guarded); the
    old terminal-buffer ``PromptDetector`` heuristic is retired.
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
   ``android/app/build.gradle.kts`` to match.
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
