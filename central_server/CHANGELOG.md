# central_server Changelog

## [Unreleased]

### Added
- **QR pairing endpoints (CLA-50):**
  * ``POST /api/pairing/initiate`` — anonymous; agent posts to receive a
    signed nonce + QR payload (``MOBA1|<nonce>|<ws_url>|<fp>``). Returns
    503 when ``MANIFEST_SIGNING_PRIVATE_KEY`` is unset.
  * ``POST /api/pairing/claim`` — authenticated (mobile user); claims a
    scanned nonce, creates the agent row, and pushes signed credentials
    (``agent_id``/``agent_token``) directly to the waiting agent over
    ``WS /ws/pairing/{nonce}``.
  * ``WS /ws/pairing/{nonce}`` — anonymous, single-flight WebSocket the
    agent holds open while the QR is displayed; closed (with the push
    payload) the instant mobile claims the nonce, or with code 4410
    (gone) if the nonce expires before a claim.
- Schema migration 0005 (``pairing_qr``): adds ``pairing_sessions`` table
  (``nonce``, ``agent_id``, ``owner_user_id``, ``expires_at``,
  ``claimed_at``) used by the QR flow. Pure additive; runs automatically
  on startup.
- ``GET /api/v1/agent/trust_key`` — public, anonymous endpoint that
  exposes ``MANIFEST_SIGNING_PUBLIC_KEY`` so the agent install scripts
  and ``mobaide-agent fetch-trust-key`` can bootstrap
  ``<prefix>/trust/central_server.pub`` without operator-side env-var
  plumbing on each install host. Returns 503 when the env var is
  unset (matches the existing manifest-publish 503 pattern).
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
* ``MANIFEST_SIGNING_PRIVATE_KEY`` — base64-encoded Ed25519 private key
  paired with ``MANIFEST_SIGNING_PUBLIC_KEY``. Required for the QR pairing
  flow: the server signs the push payload delivered to the agent over
  ``WS /ws/pairing/{nonce}``. ``POST /api/pairing/initiate`` returns 503
  when unset.
* ``PUBLIC_BASE_URL`` — public HTTPS base URL of this server (e.g.
  ``https://mobaide.com``). Embedded in the QR payload so the agent can
  open the right WebSocket. Falls back to the ``Host`` header of the
  initiate request when unset.
* ``PAIRING_QR_TOKEN_TTL_SECONDS`` — lifetime of a pairing nonce in
  seconds (default ``120``).
