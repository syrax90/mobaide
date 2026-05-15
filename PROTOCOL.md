# Wire-protocol versions

The Mobaide system uses a single **integer** ``protocol_version`` as the
authoritative compatibility signal between the Android app, ``mobaide_agent``,
and ``central_server``. The three human-facing semvers (``android-vX.Y.Z``,
``agent-vX.Y.Z``, ``server-vX.Y.Z``) are independent and ship on their own
cadence; they do **not** gate compatibility.

## proto-v2 (current)

* Introduced: 2026-05-13
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
