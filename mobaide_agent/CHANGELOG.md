# mobaide_agent Changelog

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
