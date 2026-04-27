# Documentation Registry

- README.md — Project overview: problem statement, client-agent-relay architecture, feature list, agent stack, and Google Sign-In configuration.
- docs/configuration-profiles.md — Configuration Profiles system: one PowerShell command swaps env-specific files (agent .env, android/local.properties, TLS trust config) between named profiles.
- docs/deployment.md — Central-server deployment to a VPS (deploy-central.ps1, LetsEncrypt or CloudflareOrigin TLS) and per-machine claude_agent deployment on Windows (conda), Linux (systemd), and macOS (launchd), including crash-loop supervisor settings.
- docs/email-verification-workflow.md — Email OTP verification flow for email+password registration: happy/unhappy paths, SMTP setup (Mailpit dev, provider prod), rate-limiting knobs, security model.
- docs/terminal-view-fork-changes.md — Changelog of edits applied to the vendored Termux `terminal-view` Java sources (`TerminalView.java`, `TerminalViewClient.java`); single source of truth for fork drift on upstream rebase.
- docs/versioning-and-updates.md — Release tag conventions (android-v / agent-v / server-v / proto-v), signed-manifest format, staged-binary auto-updater state machine, install-script + trust-key bootstrap, Android version-gate / Play in-app update / agent-outdated-banner flows, release-please monorepo workflow with PAT caveat, server-side deploy gate, protocol-integrity CI gate (constants + golden + diff guard), end-to-end auto-update verification test.
- deploy/prod/bootstrap-mobaide.md — One-time VPS bootstrap before deploy-central.ps1 runs: server prerequisites, /opt/claude-mobile layout, .env seeding, ufw rules, TLS mode choice, optional SQLite backup cron.
- PROTOCOL.md — Wire-contract version history: the single integer protocol_version that gates Android / claude_agent / central_server compatibility, plus how to bump it.
- android/CHANGELOG.md — Android app release notes (per android-vX.Y.Z tag).
- claude_agent/CHANGELOG.md — claude_agent release notes (per agent-vX.Y.Z tag).
- central_server/CHANGELOG.md — central_server release notes (per server-vX.Y.Z tag).
- docs/get/README.md — GitHub Pages source for get.claudemobile.io install scripts.
