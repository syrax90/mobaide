# central_server Changelog

## [Unreleased]

### Added
- Prompt-templates CRUD: ``GET/POST /api/templates``,
  ``GET/PUT/DELETE /api/templates/{id}``, and
  ``POST /api/templates/{id}/touch`` (updates ``last_used_at`` and bumps the
  template to the top of the recents list).
- ``prompt_templates`` SQLite table (schema v6 migration — pure additive; runs
  automatically on startup alongside the existing v1-v5 migrations). Indexed
  on ``owner_user_id`` for per-user scoped queries.
- Three new push events broadcast over the existing ``/ws/user-events``
  channel: ``template.created``, ``template.updated``, and
  ``template.deleted`` — so all connected clients for a user stay in sync
  without polling.
- ``__version__`` exposed via ``importlib.metadata.version("central-server")``
  and used as the canonical source of truth for ``FastAPI(version=...)`` and
  the relay handshake ``server_version`` field.
- ``[project.scripts]`` entry ``claude-central``.
- ``GET /api/version`` endpoint returning name, server_version,
  protocol_versions, min_client_version, capabilities, and the latest
  manifest pointer when one has been published.
- Manifest store and HTTP routes:
  * ``GET /api/v1/agent/manifest.json`` — public read (404 when no manifest
    has been published yet — install scripts treat 404 as a first-install
    bootstrap).
  * ``POST /api/internal/agent_manifest`` — token-auth via ``X-Release-Token``;
    validates an Ed25519 signature against the configured public key before
    persisting.
- Agent-tunnel handshake validates ``protocol_version`` against
  ``SUPPORTED_PROTOCOL_VERSIONS`` and closes with code ``4002`` when the
  agent is outside the supported range. ``auth_ok`` now carries the manifest
  rollout pointer.
- Forward-compat ``unknown_control_type`` counter + WARN logging on
  ``_handle_control_json`` fall-through.
- ``cryptography>=43.0`` runtime dependency for Ed25519 verification.

### Configuration

New environment variables:

* ``RELEASE_AUTH_TOKEN`` — bearer token gating ``POST /api/internal/agent_manifest``.
* ``MANIFEST_SIGNING_PUBLIC_KEY`` — base64-encoded Ed25519 public key used to
  validate manifests posted by the release pipeline.
* ``MANIFEST_STORE_PATH`` — file system path for the persisted manifest
  (default ``data/agent_manifest.json``).
