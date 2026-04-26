# claude_agent Changelog

## [Unreleased]

### Added
- ``__version__`` exposed via ``importlib.metadata.version("claude-mobile-agent")``
  and used as the canonical source of truth for ``FastAPI(version=...)`` and
  the wire-handshake ``agent_version`` field.
- ``[project.scripts]`` entries: ``claude-agent`` and
  ``claude-agent-launcher``. ``claude-agent --version``, ``claude-agent run``
  (default), and ``claude-agent update`` are the new CLI surface.
- ``GET /api/version`` endpoint returning the documented shape with
  ``name="claude_agent"`` and ``latest_agent_version=null`` (the central
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
- Staged-binary auto-updater (``claude_mobile.agent.updater``) — Ed25519
  signature verification, sha256 verification, idle-deferral via
  ``SessionManager``, atomic ``current_version`` swap, exit code 75 to ask
  the supervisor to restart into the new release.
- Launcher shim (``claude_mobile.launcher.__main__``) that reads
  ``current_version`` and ``os.execv``-es into the matching release venv.
- ``cryptography>=43.0`` runtime dependency for Ed25519 manifest signature
  verification.

### Changed
- ``deploy/claude-mobile-agent.service`` ExecStart now points at the launcher
  shim; ``RestartForceExitStatus=75`` is added so a clean staged-binary swap
  triggers a supervisor restart into the new release.
