# mobaide_agent Changelog

## [0.2.6](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.5...agent-v0.2.6) (2026-05-23)


### Features

* **agent:** self-heal protocol-version mismatch via HTTPS-bootstrap update ([174cd93](https://github.com/syrax90/claude_mobile/commit/174cd936307c53228d8b457e66d910426e9893d7))
* **agent:** self-heal protocol-version mismatch via HTTPS-bootstrap update ([2243d31](https://github.com/syrax90/claude_mobile/commit/2243d31040091f57908092556fb03e36883c5a8d))

## [0.2.5](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.4...agent-v0.2.5) (2026-05-23)


### Bug Fixes

* **android:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([b1740e9](https://github.com/syrax90/claude_mobile/commit/b1740e92bbe3fcc77edcf620490ae9000985217b))
* **replay:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([bd8bb63](https://github.com/syrax90/claude_mobile/commit/bd8bb633f83e7a6552b0db15f00291b71ec53504))

## [0.2.4](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.3...agent-v0.2.4) (2026-05-21)


### Features

* **agent:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([6ec5287](https://github.com/syrax90/claude_mobile/commit/6ec52878013b2ba1790cec88dc4386c24e6a3631))
* **sessions:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([72cb848](https://github.com/syrax90/claude_mobile/commit/72cb848a9552f667d3cf696b58269b2d61bcb0e0))

## [0.2.3](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.2...agent-v0.2.3) (2026-05-20)


### Features

* **agent:** add Terminal session mode as third create-session tile (CLA-59) ([874171a](https://github.com/syrax90/claude_mobile/commit/874171a4078ecb9e67455af0ace9f46c81847197))
* **sessions:** add Terminal session mode as third create-session tile (CLA-59) ([5d0b61e](https://github.com/syrax90/claude_mobile/commit/5d0b61e49d195874de8bf6d28b02d0757757fedc))

## [0.2.2](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.1...agent-v0.2.2) (2026-05-19)


### Features

* **sessions:** show Claude Code OSC terminal title on Android session list ([95f7ea7](https://github.com/syrax90/claude_mobile/commit/95f7ea738aa62e0151d8385c9d5490da7b61c863))


### Bug Fixes

* **agent:** show Claude Code OSC terminal title on Android session list ([969a878](https://github.com/syrax90/claude_mobile/commit/969a878ac2fba99176d3048ab17b61837ae4dcec))

## [0.2.1](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.0...agent-v0.2.1) (2026-05-15)


### Bug Fixes

* **agent:** hide py.exe console window for per-user Scheduled Task ([7494c80](https://github.com/syrax90/claude_mobile/commit/7494c80343cdb47f5cc9d63ee35705dde2a556f2))
* **windows:** hide py.exe console window for per-user Scheduled Task ([9c49515](https://github.com/syrax90/claude_mobile/commit/9c4951506cdf20edd53e71172122f2286a9cd5b4))

## [0.2.0](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.16...agent-v0.2.0) (2026-05-15)


### ⚠ BREAKING CHANGES

* **windows:** the Windows SCM service (MobaideAgent) is removed. On upgrade, install.ps1 detects and unregisters the old service before registering the new Scheduled Task, which runs as the current interactive user instead of SYSTEM.

### Features

* **agent:** replace LocalSystem SCM service with per-user Scheduled Task (CLA-53) ([21d1007](https://github.com/syrax90/claude_mobile/commit/21d1007de612a56dc4b820aac5209d44eaee3e39))
* **windows:** replace LocalSystem SCM service with per-user Scheduled Task (CLA-53) ([761ff72](https://github.com/syrax90/claude_mobile/commit/761ff72069102851e8322e84356b882423851f3d))

## [0.1.16](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.15...agent-v0.1.16) (2026-05-15)


### Bug Fixes

* **agent:** prevent long-paste truncation across Android UI, ViewModel, and PTY backend ([836fd89](https://github.com/syrax90/claude_mobile/commit/836fd898293d930a708e016adc82d03e42a4ab03))

## [0.1.15](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.14...agent-v0.1.15) (2026-05-15)


### Bug Fixes

* **agent:** deliver manifest body inline over relay (proto-v2) ([a7ebd08](https://github.com/syrax90/claude_mobile/commit/a7ebd08837774992fb60f28fbe89891ae1408d26))

## [0.1.14](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.13...agent-v0.1.14) (2026-05-14)


### Bug Fixes

* **agent:** auto-detect & persist claude.cmd for Windows service ([2d3abd8](https://github.com/syrax90/claude_mobile/commit/2d3abd83b3bc1d174d429a5bb3fe51c48504f97e))
* **claude-cli:** auto-detect & persist claude.cmd for Windows service ([6f6471d](https://github.com/syrax90/claude_mobile/commit/6f6471db2a7ac9399bf3c5e45d45ea2a5ffb2df2))

## [0.1.13](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.12...agent-v0.1.13) (2026-05-14)


### Bug Fixes

* **agent:** tolerate non-UTF-8 stdout when rendering pairing QR ([cb0cb1b](https://github.com/syrax90/claude_mobile/commit/cb0cb1bd7ea490909e54c5a8cdcbd7342ff86898))
* **pairing:** tolerate non-UTF-8 stdout when rendering pairing QR ([679944d](https://github.com/syrax90/claude_mobile/commit/679944d29771b2d1b9c5178c86b72462451429a8))

## [0.1.12](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.11...agent-v0.1.12) (2026-05-14)


### Bug Fixes

* **agent:** gate mobile "Paired" on agent.online=true; harden silent failure paths ([a863117](https://github.com/syrax90/claude_mobile/commit/a86311778e090c15da3bdbfe3c17bcb8f5c6a634))
* **pairing:** gate mobile "Paired" on agent.online=true; harden silent failure paths ([b45d31a](https://github.com/syrax90/claude_mobile/commit/b45d31aff702acca8fd3d06701af08911930604b))

## [0.1.11](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.10...agent-v0.1.11) (2026-05-14)


### Bug Fixes

* **agent:** restore agent log observability and close QR-before-WS race ([2d94149](https://github.com/syrax90/claude_mobile/commit/2d9414997294e747d1e30c232830d97d09513108))
* **pairing:** harden agent relay_url comparator (scheme family, default-port normalisation, userinfo/query/fragment rejection) ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))
* **pairing:** persist relay_url server-side so dispatch and recovery agree ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))
* **pairing:** restore agent log observability and close QR-before-WS race ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))

## [0.1.10](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.9...agent-v0.1.10) (2026-05-13)


### Bug Fixes

* **agent:** force SelectorEventLoop on Windows to prevent Proactor WSARecv data-loss ([65da3c8](https://github.com/syrax90/claude_mobile/commit/65da3c87e71d58d0db93cece881a3bc09510fd7a))
* **pairing:** force SelectorEventLoop on Windows to prevent Proactor WSARecv data-loss ([c5b1dd1](https://github.com/syrax90/claude_mobile/commit/c5b1dd109f69dc4cebbb7d88401fd9aaa8166304))

## [0.1.9](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.8...agent-v0.1.9) (2026-05-13)


### Bug Fixes

* **agent:** retain pending nonce on WS failure + retry write inline ([19f42ed](https://github.com/syrax90/claude_mobile/commit/19f42edef8bf46e6514522eec1deb927bcdd6167))
* **pairing:** retain pending nonce on WS failure + retry write inline ([f876146](https://github.com/syrax90/claude_mobile/commit/f876146bf640b21bbd8ca5aeae8245bb2b99df75))

## [0.1.8](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.7...agent-v0.1.8) (2026-05-13)


### Bug Fixes

* **agent:** replace post-pair sys.exit(75) with in-process relay startup ([7abe076](https://github.com/syrax90/claude_mobile/commit/7abe076833cb012be2c0d85af5ef3e4d83f7a813))
* **pairing:** replace post-pair sys.exit(75) with in-process relay startup ([4adbcca](https://github.com/syrax90/claude_mobile/commit/4adbcca68d3dbefac2553c8b37f343751463e7a2))

## [0.1.7](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.6...agent-v0.1.7) (2026-05-13)


### Bug Fixes

* **agent:** write relay credentials to .env after pairing and auto-restart service ([ba7594f](https://github.com/syrax90/claude_mobile/commit/ba7594f65da49162138b3df1a807f5dec349623a))
* **windows:** write relay credentials to .env after pairing and auto-restart service ([7b9c581](https://github.com/syrax90/claude_mobile/commit/7b9c581bf42ae6c99bf06baacee2d6097146bb78))

## [0.1.6](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.5...agent-v0.1.6) (2026-05-13)


### Bug Fixes

* **agent:** preserve launcher PID on Windows by spawning agent as subprocess ([e9fd851](https://github.com/syrax90/claude_mobile/commit/e9fd85151cedfef4e2ac2169b95de9a41b8371cc))
* **launcher:** preserve launcher PID on Windows by spawning agent as subprocess ([46a0a33](https://github.com/syrax90/claude_mobile/commit/46a0a33e32d75bfcf891eabf60ffacada7ac0c2b))

## [0.1.5](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.4...agent-v0.1.5) (2026-05-13)


### Features

* **agent:** render pairing QR only after agent service is confirmed healthy ([cb8bb5b](https://github.com/syrax90/claude_mobile/commit/cb8bb5b7d28b464e4445d02e149eaf19fdfb8d59))
* **install:** render pairing QR only after agent service is confirmed healthy ([8407e59](https://github.com/syrax90/claude_mobile/commit/8407e5908452cf08035d7c829e535fc88e2a6a49))
* **linear:** make Auto Scan Mode filter configurable per project (CLA-52) ([4882d3a](https://github.com/syrax90/claude_mobile/commit/4882d3a09f97c37c3bc9485b86a09cb7e454ff1b))
* **linear:** make Auto Scan Mode filter configurable per project (CLA-52) ([fa18866](https://github.com/syrax90/claude_mobile/commit/fa18866e414a4b6da9abc189f10090675f63e5e2))

## [0.1.4](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.3...agent-v0.1.4) (2026-05-08)


### Bug Fixes

* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([cae3ae2](https://github.com/syrax90/claude_mobile/commit/cae3ae2ad559cb8cbe09b7f2da0305f6fd28e2ff))
* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([3b88eb2](https://github.com/syrax90/claude_mobile/commit/3b88eb2464ace3a780c1f195d9b19601ba8fcf6a))

## [Unreleased]

### Fixed
- **Offline-after-pairing: pairing WebSocket failed with `"isn't a valid
  URI: scheme isn't ws or wss"`.** The agent now derives the pairing WS
  URL locally from `RELAY_SERVER_URL` + the response `nonce` rather than
  trusting the server's `ws_url` field. Eliminates a deployment-skew
  failure mode where a stale `central_server` returned `https://...` and
  the Python `websockets` client hard-rejected the scheme. New helper:
  `mobaide.agent.pairing_monitor._pairing_ws_url`. Cleartext (`ws://` /
  `http://`) is refused unless `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1`,
  mirroring the trust-key fetch's loopback escape hatch.
- **Offline-after-pairing: agent stayed in `pairing_pending` even after
  successful pairing.** The post-pairing `_on_paired` callback used
  `sudo -n systemctl restart mobaide-agent`, which silently no-op'd when
  the install user lacked NOPASSWD (the typical case after
  `curl|sudo bash`); the running process kept its frozen
  empty-credentials config in memory and never started the relay client.
  Replaced with `sys.exit(EXIT_RESTART_REQUESTED)` (exit code 75),
  recognized by the systemd unit's `RestartForceExitStatus=75`, macOS
  launchd's `KeepAlive`, and Windows SC's failure-restart actions —
  same pattern the auto-updater already uses.
- **Auto-update "Install now" always returned `signature_failed`.**
  `central_server` sent ``manifest_url = "/api/v1/agent/manifest.json"``
  as a relative path; `StagedBinaryUpdater._download_manifest` treated
  any ``/``-prefixed value as a local filesystem path
  (``Path(...).read_text()``), which raised `FileNotFoundError`, was
  caught by the broad `except Exception` handler, and returned
  ``"signature_failed"`` — causing every user-triggered update to fail
  silently. Fixed by bumping wire protocol 1→2 (``PROTOCOL_VERSION = 2``):
  the server now delivers the full signed manifest body inline in
  ``auth_ok`` and ``agent_manifest_update`` frames (field ``manifest_body``
  replaces ``manifest_url``). `_download_manifest`, `_http_download`, and
  the `downloader` constructor knob are removed;
  `consider_manifest`/`force_update_now` accept a body dict directly.

## [0.1.3](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.2...agent-v0.1.3) (2026-05-08)


### Bug Fixes

* **agent:** inject User-Agent header to bypass Cloudflare Bot Fight Mode (HTTP 403 / error 1010) ([8094125](https://github.com/syrax90/claude_mobile/commit/80941253c9b648e06ef7dc1c2e73278723e26ded))
* **agent:** inject User-Agent header to bypass Cloudflare Bot Fight Mode (HTTP 403 / error 1010) ([987988a](https://github.com/syrax90/claude_mobile/commit/987988a2db442be91e58b51b6d0d0182eefba0a6))

## [0.1.2] - 2026-05-08

### Added

- **QR pairing flow (CLA-50).** Replaces the legacy
  `mobaide-agent pair --id ... --token ...` invocation with a scan-to-pair
  experience: the agent renders a QR code in its terminal, the operator
  scans it from the Mobaide mobile app, and the agent receives its
  credentials over a server-mediated WebSocket. New CLI verbs:
  - `mobaide-agent pair-qr [--print | --wait] [--prefix] [--no-restart]` —
    initiates a pairing nonce, renders the QR; with `--wait`, blocks on
    `WS /ws/pairing/{nonce}` until the mobile claim arrives, then
    atomically writes credentials and restarts the supervisor.
  - `mobaide-agent show-qr [--prefix]` — re-renders the most-recent
    pending QR; falls back to a fresh initiate if expired.
  - `mobaide-agent re-pair [--prefix] [--no-restart]` — blanks
    `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` in `.env` and restarts so the
    boot autorun renders a fresh QR.
  Boot-time autorun: when an agent in `pairing_pending` state starts, the
  app's lifespan schedules a pairing monitor that initiates + renders +
  awaits the claim, then atomic-writes `.env` and restarts the
  supervisor. Replaces the previous "log a warning and idle" behavior.
- New dependency: `segno >= 1.6` for terminal QR rendering.
- `mobaide-agent fetch-trust-key [--from-url URL] [--no-restart] [--prefix PATH]`
  CLI verb. Repair path for installs that were set up before the install
  scripts learned to fetch the manifest-signing public key automatically.
  Default URL is derived from `RELAY_SERVER_URL` in the agent `.env`
  (`wss://host[/path]` → `https://host/api/v1/agent/trust_key`). User-info
  in the URL is rejected (would otherwise silently DNS-resolve to an
  attacker host); cleartext `ws://`/`http://` schemes are refused by
  default (set `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1` for
  self-host loopback). The fetched value is validated as a 32-byte
  base64 Ed25519 key before being written to
  `<prefix>/trust/central_server.pub`. Best-effort restarts the
  supervisor so the running agent picks up the new updater wiring.
- Install scripts (`deploy/install.sh`, `deploy/install.ps1`)
  now write a prepared `<prefix>/mobaide_agent/.env` after staging the
  release and render the pairing QR before service start so SSH-attached
  operators can scan it immediately. `JWT_SECRET_KEY` is auto-generated
  and `RELAY_SERVER_URL` is prefilled with `wss://mobaide.com`.
- `python -m mobaide.agent.config init-env [--prefix] [--force]` and
  `print-template` CLI verbs. Idempotent: re-runs without `--force` are
  no-ops; with `--force`, regenerates the file while preserving
  `RELAY_AGENT_ID`, `RELAY_AGENT_TOKEN`, and the existing
  `JWT_SECRET_KEY` (rotating the secret would invalidate live mobile
  JWTs).
- Pairing-pending boot mode: when `RELAY_SERVER_URL` is set without a
  matching id/token pair, the agent boots without crashing, schedules
  the QR pairing monitor, and exposes `pairing_pending: true` on
  `/api/health`. Mirrors Tailscale's idle-until-paired daemon model.

### Removed

- `mobaide-agent pair --id ... --token ...` verb. Replaced by the QR
  pairing flow described above. The mobile app no longer surfaces
  raw `agent_id` / `agent_token` for manual entry.
- `mobaide/agent/env.template` packaged inside the wheel as canonical
  source of truth for the agent `.env` shape. Repo-root
  `mobaide_agent/.env.example` is now a generated mirror; regenerate
  with `python -m mobaide.agent.config print-template > mobaide_agent/.env.example`.
- ``__version__`` exposed via ``importlib.metadata.version("mobaide-agent")``
  and used as the canonical source of truth for ``FastAPI(version=...)`` and
  the wire-handshake ``agent_version`` field.
- ``[project.scripts]`` entries: ``mobaide-agent`` and
  ``mobaide-agent-launcher``. ``mobaide-agent --version``, ``mobaide-agent run``
  (default), and ``mobaide-agent update`` are the new CLI surface.
- ``GET /api/version`` endpoint returning the documented shape with
  ``name="mobaide_agent"`` and ``latest_agent_version=null`` (the central
  server hop is the only one that populates that field).
- ``server_info`` text frame on ``/ws/session/<id>`` carries
  ``agent_version`` + ``protocol_version`` so the phone can render an
  "agent outdated" banner without a separate round trip.
- Auth handshake against ``central_server`` now sends ``protocol_version``,
  ``agent_version``, and a reserved ``capabilities: {}`` placeholder. The
  ``auth_ok`` reply persists ``latest_agent_version`` / ``manifest_url`` /
  ``manifest_signature`` so the staged-binary updater has a rollout pointer.
- New control message ``agent_manifest_update`` lets the central server push
  manifest pointers mid-connection.
- Forward-compat ``unknown_control_type`` counter + WARN logging on the
  fall-through branch of ``RelayClient._handle_control``.
- Staged-binary auto-updater (``mobaide.agent.updater``) — Ed25519
  signature verification, sha256 verification, idle-deferral via
  ``SessionManager``, atomic ``current_version`` swap, exit code 75 to ask
  the supervisor to restart into the new release.
- Launcher shim (``mobaide.launcher.__main__``) that reads
  ``current_version`` and ``os.execv``-es into the matching release venv.
- ``cryptography>=43.0`` runtime dependency for Ed25519 manifest signature
  verification.

### Changed

- **BREAKING:** install scripts (`deploy/install.sh`, `deploy/install.ps1`)
  no longer silently skip manifest signature verification when
  `MOBAIDE_AGENT_TRUST_KEY_B64` is unset. They now derive
  `https://<host>/api/v1/agent/trust_key` from
  `MOBAIDE_AGENT_MANIFEST_URL` and bootstrap the public key from there
  automatically. If neither the env var nor the fetch yields a key, the
  install fails-closed with an actionable message. The previous
  silent-skip branch shipped agents whose auto-updater could never
  apply rollouts (manifests received but never verified) — closing
  that gap. Operators who rely on env-var pinning don't need to change
  anything; the env var still takes priority over the fetch.
- Configuration loading migrated from `python-dotenv` + `os.getenv` to
  `pydantic-settings`. `AgentConfig`, `RelayConfig`, and `AutoScanConfig`
  are now `BaseSettings` subclasses; the agent loads its `.env` from an
  explicit absolute path (`<prefix>/mobaide_agent/.env`) resolved via
  `install_paths.default_prefix()`, fixing the Windows CWD-dependent
  silent miss of the previous `load_dotenv()` call.
- ``deploy/mobaide-agent.service`` ExecStart now points at the launcher
  shim; ``RestartForceExitStatus=75`` is added so a clean staged-binary swap
  triggers a supervisor restart into the new release.

### Removed

- `python-dotenv` runtime dependency (replaced by `pydantic-settings>=2.5`).
- `config/profiles/_template/mobaide_agent.env` (was a hand-mirrored
  duplicate of `.env.example`; the agent `.env` is now generated by
  `init-env` rather than bundled in profiles).
