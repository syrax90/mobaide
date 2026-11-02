# Documentation Registry

Every documentation file in this repository, grouped by topic. Read this first, then open only the docs you need.

## Start here

| File | What it covers |
|---|---|
| [README.md](../README.md) | Public landing page: what Mobaide is, store and website links, main features, voice input, install steps, data summary, repository map, support. Its claims follow the accuracy gate in [website.md](website.md). |
| [architecture.md](architecture.md) | Why Mobaide exists and how the parts fit: components, the client → relay → agent → CLI data flow, agent stack, project structure. |
| [features.md](features.md) | Detailed behaviour of each feature: limits, per-platform differences, main settings. Wire details are in PROTOCOL.md. |

## Wire contract

| File | What it covers |
|---|---|
| [PROTOCOL.md](../PROTOCOL.md) | The wire contract: `protocol_version`, WebSocket close codes, the HTTPS-bootstrap update path, agent self-uninstall, voice input API, and the session and project git APIs (status, mutations, jobs, branches, rebase, conflict resolver). Also how to bump the version. |

## Clients

| File | What it covers |
|---|---|
| [ios-client.md](ios-client.md) | iOS app: project layout, theme layer, device support, privacy manifest, build and test commands, simulator and APNs testing, sign-in configuration, notifications, Android→Swift protocol parity, golden fixtures. |
| [web-client.md](web-client.md) | Web SPA: WEB-01…WEB-13 roadmap, typed API pipeline, CI drift gates, app shell, auth, realtime layer, terminal, sessions, pairing, projects and git, keyboard, auto-scan and notifications, Sign in with Apple, ad attribution. |
| [web/README.md](../web/README.md) | Web client developer quick start: prerequisites and `npm` commands. |
| [web-keyboard-reserved-keys.md](web-keyboard-reserved-keys.md) | Which chords the browser or window manager takes before the web terminal sees them: the two-fact model behind `reservedKeys.ts`, sources, `/dev/key-sweep` runbook, Keyboard Lock immersive mode. |
| [terminal-view-fork-changes.md](terminal-view-fork-changes.md) | Changelog of edits to the vendored Termux `terminal-view` and `terminal-emulator` Java sources. The source of truth for fork drift on upstream rebase. |
| [in-app-review.md](in-app-review.md) | In-app store review prompt on Android and iOS: shared rules, `review_prompt_*` keys, file map, how to see the card in testing. |
| [android/CHANGELOG.md](../android/CHANGELOG.md) | Android release notes, per `android-vX.Y.Z` tag. |

## Features across components

| File | What it covers |
|---|---|
| [voice-input.md](voice-input.md) | Voice input on Android, iOS and web: opt-in flow, per-platform module map, status → mic-button table, dictation rules, permissions and privacy, release checklist. |
| [qr-pairing.md](qr-pairing.md) | Agent pairing: QR flow and `MOBA1\|...` payload, printed pairing link, device-code direction, relay URL persistence, trust-key pinning, re-pair rules, operator runbook, troubleshooting. |

## Account workflows

| File | What it covers |
|---|---|
| [email-verification-workflow.md](email-verification-workflow.md) | Email OTP verification at registration: paths, SMTP setup, rate-limit knobs, security model. |
| [password-reset-workflow.md](password-reset-workflow.md) | Password reset via OTP in the app: paths, rate limits, refresh-token revoke, password-changed notification, security model. |
| [account-deletion-workflow.md](account-deletion-workflow.md) | Self-service account deletion: 14-day grace window, re-auth rules, cancellation paths, sweeper, schema, config knobs, the public `/portal/delete-account-info` URL. |
| [sign-in-setup.md](sign-in-setup.md) | Social sign-in setup: Google OAuth client IDs and the Apple Services ID (`APPLE_WEB_CLIENT_ID`). |

## Deployment and operations

| File | What it covers |
|---|---|
| [deployment.md](deployment.md) | Central-server and web-SPA deploys, TLS certificates, capacity triggers, agent deployment per OS, agent uninstall, security headers and CSP, voice input operations, Google Ads conversion upload. |
| [bootstrap-mobaide.md](../deploy/prod/bootstrap-mobaide.md) | One-time VPS bootstrap before `deploy_central.py` runs: prerequisites, layout, `.env`, ufw, TLS mode, backups. |
| [configuration-profiles.md](configuration-profiles.md) | One PowerShell command swaps env-specific files (agent `.env`, `android/local.properties`, TLS trust config) between named profiles. |
| [central_server/CHANGELOG.md](../central_server/CHANGELOG.md) | `central_server` release notes, per `server-vX.Y.Z` tag. |

## Releases

| File | What it covers |
|---|---|
| [versioning-and-updates.md](versioning-and-updates.md) | Tag conventions, cutting a release, signed manifests, the staged-binary auto-updater, install scripts and trust-key bootstrap, Android update flows, the protocol-integrity CI gate. |
| [release-publishing.md](release-publishing.md) | Publishing the install scripts: `deploy/` on `main` → `gh-pages` → `get.mobaide.com`, with the manual sync runbook. |
| [mobaide_agent/CHANGELOG.md](../mobaide_agent/CHANGELOG.md) | `mobaide_agent` release notes, per `agent-vX.Y.Z` tag. |

## Website and marketing

| File | What it covers |
|---|---|
| [website.md](website.md) | The mobaide.com site and the shared "modern pixel" design system: where files live, the copy-accuracy gate, CSP and headers, assets, consent-gated Google Ads tag, static-page styling. |
| [agent-paired-conversion.md](agent-paired-conversion.md) | Umbrella doc for the "Agent paired" Google Ads rollout: stages, history, traps, configuration inventory, monitoring, pause / change / remove runbook. Read this first for that topic. |
| [app-conversion-measurement.md](app-conversion-measurement.md) | The Firebase lane of that rollout: client rules, consent defaults, per-platform integration, DebugView, store privacy answers. |

## Testing

| File | What it covers |
|---|---|
| [e2e-harness.md](e2e-harness.md) | Python E2E harness in `tests/e2e/`: real server + agent subprocesses and a mock mobile client over the real protocol. |
| [manual-e2e-testing.md](manual-e2e-testing.md) | `tests/e2e/manual_stack.py`: a LAN-reachable stack for real devices, emulators and the web client, with connectivity modes, iOS ATS traps and troubleshooting. |
