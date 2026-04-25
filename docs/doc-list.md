# Documentation Registry

- README.md — Project overview: problem statement, client-agent-relay architecture, feature list, agent stack, and Google Sign-In configuration.
- docs/configuration-profiles.md — Configuration Profiles system: one PowerShell command swaps env-specific files (agent .env, android/local.properties, TLS trust config) between named profiles.
- docs/deployment.md — Central-server deployment to a VPS (deploy-central.ps1, LetsEncrypt or CloudflareOrigin TLS) and per-machine claude_agent deployment on Windows (conda) and Linux (systemd).
- docs/email-verification-workflow.md — Email OTP verification flow for email+password registration: happy/unhappy paths, SMTP setup (Mailpit dev, provider prod), rate-limiting knobs, security model.
- docs/terminal-view-fork-changes.md — Changelog of edits applied to the vendored Termux `terminal-view` Java sources (`TerminalView.java`, `TerminalViewClient.java`); single source of truth for fork drift on upstream rebase.
- deploy/prod/bootstrap-mobaide.md — One-time VPS bootstrap before deploy-central.ps1 runs: server prerequisites, /opt/claude-mobile layout, .env seeding, ufw rules, TLS mode choice, optional SQLite backup cron.
