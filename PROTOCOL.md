# Wire-protocol versions

The Mobaide system uses a single **integer** ``protocol_version`` as the
authoritative compatibility signal between the Android app, ``claude_agent``,
and ``central_server``. The three human-facing semvers (``android-vX.Y.Z``,
``agent-vX.Y.Z``, ``server-vX.Y.Z``) are independent and ship on their own
cadence; they do **not** gate compatibility.

## proto-v1 (current)

* Introduced: 2026-04-26
* App support: ``APP_PROTOCOL_VERSION = 1`` (Android), ``PROTOCOL_VERSION = 1``
  (Python — both ``claude_agent`` and ``central_server``).
* Wire shape:
  * Auth handshake (agent ↔ central_server, text frame):
    * Agent sends ``{type:"auth", agent_id, token, protocol_version,
      agent_version, capabilities}``.
    * Server replies ``{type:"auth_ok", server_protocol_versions,
      min_client_protocol_version, server_version, capabilities,
      latest_agent_version, manifest_url, manifest_signature}`` OR closes
      with code ``4002`` and emits ``{type:"incompatible", reason,
      min_client_version, server_protocol_versions}``.
  * Mobile ↔ agent (text frame, after auth + before binary handshake):
    ``{type:"server_info", agent_version, protocol_version,
    supported_protocol_versions, capabilities}``.
  * REST: ``GET /api/version`` returns ``{name, server_version,
    protocol_versions, min_client_version, capabilities,
    latest_agent_version, manifest_url, manifest_signature}``.

## How to bump

1. Increment ``PROTOCOL_VERSION`` and update ``SUPPORTED_PROTOCOL_VERSIONS``
   in **both** ``claude_agent/claude_mobile/relay/envelope.py`` and
   ``central_server/central_server/relay/envelope.py``. The CI
   ``protocol-integrity`` workflow rejects any commit that drifts the two.
2. Update the ``BuildConfig.APP_PROTOCOL_VERSION`` field in
   ``android/app/build.gradle.kts`` to match.
3. Update ``tests/protocol_golden.json`` to match the new shape; any change
   must update the golden file or CI fails.
4. Add a new section to this file documenting the change.
5. Tag the protocol commit ``proto-v<N>``.
