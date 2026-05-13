# central_server Changelog

## [0.1.3](https://github.com/syrax90/claude_mobile/compare/server-v0.1.2...server-v0.1.3) (2026-05-13)


### Bug Fixes

* **database:** clear claimed_agent_id FK before deleting agent ([ac416fd](https://github.com/syrax90/claude_mobile/commit/ac416fd8d93b06bc581f65a773db706f3d61f04b))
* **database:** clear claimed_agent_id FK before deleting agent ([9f909ee](https://github.com/syrax90/claude_mobile/commit/9f909ee835bf87782de9c364fdc0f6e6671cd9b9))

## [0.1.2](https://github.com/syrax90/claude_mobile/compare/server-v0.1.1...server-v0.1.2) (2026-05-08)


### Bug Fixes

* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([cae3ae2](https://github.com/syrax90/claude_mobile/commit/cae3ae2ad559cb8cbe09b7f2da0305f6fd28e2ff))
* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([3b88eb2](https://github.com/syrax90/claude_mobile/commit/3b88eb2464ace3a780c1f195d9b19601ba8fcf6a))

## [0.1.1](https://github.com/syrax90/claude_mobile/compare/server-v0.1.0...server-v0.1.1) (2026-05-08)


### Features

* add non-expiring token support by omitting exp claim when TTL is 0 ([ada6181](https://github.com/syrax90/claude_mobile/commit/ada6181c3f3e8faea842312320e0834fa2ed953d))
* add non-expiring token support by omitting exp claim when TTL is 0 ([649fc89](https://github.com/syrax90/claude_mobile/commit/649fc8903a88bc0069483604936906cb35cd5b07))
* add real-time agent online/offline status via user-scoped WebSocket channel ([3fcd576](https://github.com/syrax90/claude_mobile/commit/3fcd57643b7082d243583161b11157e9bf3c7524))
* add real-time agent online/offline status via user-scoped WebSocket channel ([27b3431](https://github.com/syrax90/claude_mobile/commit/27b34319d403bce8cccee5059f092e5d11035bc4))
* **cla-30:** central server WebSocket relay with agent registration and web dashboard ([6d2762b](https://github.com/syrax90/claude_mobile/commit/6d2762b1d548ac918adac458abff387c43dcd4e4))
* **cla-32:** Android QR pairing and dual-mode connection relay ([066cd00](https://github.com/syrax90/claude_mobile/commit/066cd00b6cae954529e98fc562c1b6ff3b660e78))
* **cla-32:** Android QR pairing and dual-mode connection relay ([a93d4d7](https://github.com/syrax90/claude_mobile/commit/a93d4d7d2456030e811a735730f9f6e420615b75))
* **cla-33:** implement user authentication system with registration, login, and Google OAuth ([d5cbed1](https://github.com/syrax90/claude_mobile/commit/d5cbed193a7f4770cebc876f16d2a2a3ab2ec1d0))
* **cla-33:** implement user authentication system with registration, login, and Google OAuth ([1865ba5](https://github.com/syrax90/claude_mobile/commit/1865ba5d5c375f17a8d0addc5060fbf537635634))
* **cla-34:** implement user-owned agent management with credential isolation ([77135f5](https://github.com/syrax90/claude_mobile/commit/77135f59c652f95a1dcc6c8691db6cc3a4b47a25))
* **cla-34:** implement user-owned agent management with credential isolation ([e26746e](https://github.com/syrax90/claude_mobile/commit/e26746e60cd4ec751fe89ab0d379204020c9d70a))
* **CLA-38:** implement email verification for email/password registr… ([cb4e71b](https://github.com/syrax90/claude_mobile/commit/cb4e71bb0f4eddfdbb6ebc253fe7d148e26f9839))
* **CLA-38:** implement email verification for email/password registration via server OTP ([fa8c2d1](https://github.com/syrax90/claude_mobile/commit/fa8c2d100d02fd174fe5e790e7ce8905c225b538))
* **cla-42:** wire-contract versioning, auto-update, Play publishing, and CI/CD ([204350d](https://github.com/syrax90/claude_mobile/commit/204350dfb2a017a8fbe6b3169a5f225e5f408c76))
* **cla-44:** wire StagedBinaryUpdater, force-update endpoint, launcher rollback, and Android update UX ([4d27647](https://github.com/syrax90/claude_mobile/commit/4d27647f9d93c36ad77c0110094434c450bdc3bf))
* **cla-44:** wire StagedBinaryUpdater, force-update endpoint, launcher rollback, and Android update UX ([e5d4da1](https://github.com/syrax90/claude_mobile/commit/e5d4da1851e9e45f26472515dc166541f505a9ba))
* **CLA-50:** agent-initiated QR pairing across server, agent, and Android ([8d1f9b9](https://github.com/syrax90/claude_mobile/commit/8d1f9b956791d2944c4732af165327e7cd2f78ca))
* enforce admin-users-never-own-agents invariant and fix viewer security gaps ([6280127](https://github.com/syrax90/claude_mobile/commit/62801275d22805a871fa8e349206d27b9372cd71))
* enforce admin-users-never-own-agents invariant and fix viewer security gaps ([4367e5d](https://github.com/syrax90/claude_mobile/commit/4367e5dc2d1705bf9b5eae2887060b49023c945c))
* **mobaide_agent:** agent-initiated QR pairing across server, agent, and Android ([5fa3179](https://github.com/syrax90/claude_mobile/commit/5fa3179286bfc51948c1727cbc3412f4ef042a89))


### Bug Fixes

* **cla-30:** resolve validation warnings — secure cookie, unused var, re-registration race ([37a7045](https://github.com/syrax90/claude_mobile/commit/37a704520cec95026f9e397cc841e5e65725df44))
* fail-closed trust key pinning and fetch-trust-key CLI verb ([2dd5613](https://github.com/syrax90/claude_mobile/commit/2dd561377071f0c6c66f55eb473db58cb716e677))
* fail-closed trust key pinning and fetch-trust-key CLI verb ([4a8f281](https://github.com/syrax90/claude_mobile/commit/4a8f281b397807112dde6ab19b43fa6686d299ed))
* issue dashboard login cookie as session cookie when TTL=0 ([fc619a5](https://github.com/syrax90/claude_mobile/commit/fc619a5fea74c4c2d85ed1d15def6e332e340e26))
* issue dashboard login cookie as session cookie when TTL=0 ([203bcd7](https://github.com/syrax90/claude_mobile/commit/203bcd7f70738456d584ddabadd83ce96a29f7e5))
* recover from server-side JWT invalidation on mobile client ([32b4773](https://github.com/syrax90/claude_mobile/commit/32b4773425ea8ad230094c85f3683f52e71f4918))
* recover from server-side JWT invalidation on mobile client ([389c950](https://github.com/syrax90/claude_mobile/commit/389c95033d59378ff99cec36193a47803480fd3a))
* **relay:** fix session-not-showing and blank-terminal-on-reopen bugs ([e8894ab](https://github.com/syrax90/claude_mobile/commit/e8894ab364cdcb87fe74411bce0e9e4b91c30862))
* **relay:** unstick keyboard on session reopen via explicit mobile_session_attach ([b3c829e](https://github.com/syrax90/claude_mobile/commit/b3c829ee3deb538a7e476e9d101aeee406685b03))
* **relay:** unstick keyboard on session reopen via explicit mobile_session_attach ([45e5731](https://github.com/syrax90/claude_mobile/commit/45e5731766124129afc004075c8c1af140d4eb1f))


### Documentation

* new docs/email-verification-workflow.md; updated deploy/prod/bootstrap-mobaide.md with SMTP guidance. ([fa8c2d1](https://github.com/syrax90/claude_mobile/commit/fa8c2d100d02fd174fe5e790e7ce8905c225b538))

## [Unreleased]

### Breaking
- **`POST /api/pairing/initiate` no longer returns a `ws_url` field.** The
  pairing WS URL is now derived agent-side from `RELAY_SERVER_URL` + the
  response `nonce` (single source of truth). Removes a class of
  deployment-skew bugs where a stale server image returned an `https://`
  scheme that the Python `websockets` client hard-rejected with
  `"isn't a valid URI: scheme isn't ws or wss"`. The
  `derive_ws_url` helper in `central_server.pairing.qr_signer` is removed.

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
  protocol_versions, min_client_version, capabilities, and
  latest_agent_version. The manifest body/signature is NOT returned here;
  agents receive it inline over the relay tunnel (proto-v2).
- Manifest store and HTTP routes:
  * ``GET /api/v1/agent/manifest.json`` — public read (404 when no manifest
    has been published yet — install scripts treat 404 as a first-install
    bootstrap).
  * ``POST /api/internal/agent_manifest`` — token-auth via ``X-Release-Token``;
    validates an Ed25519 signature against the configured public key before
    persisting.
- Agent-tunnel handshake validates ``protocol_version`` against
  ``SUPPORTED_PROTOCOL_VERSIONS`` and closes with code ``4002`` when the
  agent is outside the supported range. ``auth_ok`` now carries the signed
  manifest body inline (``manifest_body`` + ``manifest_signature``) — proto-v2
  replaces the earlier URL-pointer design.
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
