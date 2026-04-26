# Versioning & Auto-Update

This document is the single reference for how Claude Mobile versions its three
components, ships releases, and keeps deployed installations up to date.

The system has **two orthogonal version concepts** and you should not conflate
them:

| Concept | Type | Purpose |
|--------|------|---------|
| Component semver (`android-vX.Y.Z`, `agent-vX.Y.Z`, `server-vX.Y.Z`) | Independent semver per component | Human-facing release identity. Ships on its own cadence. **Does not** gate compatibility. |
| `protocol_version` | Single shared integer | The only compatibility signal between Android, `claude_agent`, and `central_server`. Bumped when the wire contract changes. |

Wire-contract details and the `protocol_version` history live in
[PROTOCOL.md](../PROTOCOL.md). This doc covers the release machinery and the
update flows that sit on top of it.

## Tag conventions

Releases are driven by git tags pushed to the repo. Each prefix triggers a
dedicated workflow under `.github/workflows/`:

| Tag prefix | Component | Workflow | What it produces |
|------------|-----------|----------|------------------|
| `android-vX.Y.Z` | Android app | `release-android.yml` | Signed `.aab`, uploaded to Play Console internal track. |
| `agent-vX.Y.Z` | `claude_agent` | `release-agent.yml` | Wheel + source tarball, **Ed25519-signed manifest**, GitHub Release, manifest POSTed to `central_server`. |
| `server-vX.Y.Z` | `central_server` | `release-server.yml` | Wheel + Docker image pushed to `ghcr.io/<owner>/<repo>-server:vX.Y.Z` (and `:latest`). |
| `proto-vN` | Wire contract | informational only | Marks the commit that bumps `PROTOCOL_VERSION`. Must change **both** `claude_agent/.../envelope.py` AND `central_server/.../envelope.py` in lockstep — `protocol-integrity.yml` rejects drift. |

### Cutting a release

1. Bump the version field for the component:
   * Python components — `pyproject.toml` `[project] version`. The runtime
     reads it via `importlib.metadata.version(...)`.
   * Android — `versionCode` + `versionName` in `android/app/build.gradle.kts`.
2. Update the matching `CHANGELOG.md` (`android/`, `claude_agent/`, or
   `central_server/`).
3. Push the tag, e.g. `git tag agent-v0.2.0 && git push --tags`.

The relevant workflow takes it from there. Required CI secrets:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | `release-android.yml` | Signs the `.aab` with the upload key. |
| `PLAY_SERVICE_ACCOUNT_JSON` | `release-android.yml` | Service account used to upload the signed bundle to the Play Console internal track. |
| `AGENT_MANIFEST_PRIVATE_KEY` | `release-agent.yml` | Base64 Ed25519 private key. Signs the manifest body. |
| `CENTRAL_SERVER_RELEASE_TOKEN` | `release-agent.yml` | Bearer token for `POST /api/internal/agent_manifest`. |
| `CENTRAL_SERVER_URL` | `release-agent.yml` | Base URL of the production `central_server` that hosts the manifest. |

If `AGENT_MANIFEST_PRIVATE_KEY` or `CENTRAL_SERVER_*` is missing, the agent
release still publishes a GitHub Release but skips signing / publishing the
manifest. Deployed agents will not see the new version until the manifest is
delivered.

## Compatibility gating: `protocol_version`

`protocol_version` is a single integer carried in every cross-component
handshake. Each side declares the range it supports, and any peer outside that
range is rejected up front.

**Android probe — `GET /api/version`** (before any WebSocket connect):

```json
{
  "name": "central_server",
  "server_version": "0.1.0",
  "protocol_versions": [1],
  "min_client_version": 1,
  "capabilities": {},
  "latest_agent_version": "0.1.0",
  "manifest_url": "/api/v1/agent/manifest.json",
  "manifest_signature": "<base64>"
}
```

The Android client (`VersionGateRepository`) compares
`BuildConfig.APP_PROTOCOL_VERSION` against the server's
`[min_client_version, max(protocol_versions)]`. Outcome:

* **Compatible** → connect normally; persist `latest_agent_version` for the
  agent-outdated banner.
* **Incompatible** → switch to `IncompatibleServerScreen`. The user sees a
  Play Store deep link (`market://details?id=com.claudemobile.app`) and a
  "What changed" link to `PROTOCOL.md`. No further WebSocket attempts.

**Agent ↔ central server tunnel** (`/ws/agent-tunnel`): the agent sends
`{type:"auth", protocol_version, agent_version, capabilities}`. If
`protocol_version` is not in the server's `SUPPORTED_PROTOCOL_VERSIONS`, the
server emits `{type:"incompatible", reason, min_client_version,
server_protocol_versions}` and closes with WebSocket code **4002**
(`PROTOCOL_INCOMPATIBLE_CLOSE_CODE`). The agent treats 4002 as a terminal
condition and does not retry on the same binary.

## Agent rollout manifest

The signed manifest is the unit of truth for what version every agent should
be running. It is a small JSON document:

```json
{
  "body": {
    "version": "0.2.0",
    "tarball_url": "https://github.com/.../claude-agent-0.2.0.tar.gz",
    "tarball_sha256": "a1b2...",
    "wheel_url": "https://github.com/.../claude_mobile_agent-0.2.0-py3-none-any.whl",
    "wheel_sha256": "c3d4...",
    "released_at": "2026-04-26T10:30:00+00:00",
    "min_central_server_version": "0.1.0"
  },
  "signature_b64": "<Ed25519 signature over canonical JSON of body>"
}
```

* **Signing** — Ed25519. Canonical JSON encoding is
  `json.dumps(body, sort_keys=True, separators=(",", ":"))`. The release
  workflow signs with `AGENT_MANIFEST_PRIVATE_KEY`; the public half is
  configured on `central_server` as `MANIFEST_SIGNING_PUBLIC_KEY` and pinned
  on each agent host at `<prefix>/trust/central_server.pub`.
* **Publishing** — `release-agent.yml` POSTs the record to
  `POST /api/internal/agent_manifest` with header
  `X-Release-Token: $CENTRAL_SERVER_RELEASE_TOKEN`. The route validates the
  signature **before** persisting; an invalid signature returns 400 and the
  manifest is not stored.
* **Distribution** — agents read the manifest from
  `GET /api/v1/agent/manifest.json` and via the `auth_ok` reply on the agent
  tunnel. After a successful `POST /api/internal/agent_manifest`, the
  router fans out an `agent_manifest_update` CH_CONTROL frame to every
  connected agent in the routing table (`broadcast_manifest_update`),
  so a freshly-published manifest reaches running agents within one
  heartbeat without a reconnect.
* **Storage** — `ManifestStore` writes atomically via `os.replace`; reads are
  served from an in-memory last-good cache.

## Auto-update on the agent (staged-binary swap)

The agent updates itself in place using a **staged-binary** pattern: every
release lives in its own directory and a single pointer file selects the
"current" one. There is no in-process patching.

### Install layout

```
<prefix>/                            # /opt/claude-mobile, /usr/local/claude-mobile, or %ProgramFiles%\ClaudeAgent
├── current_version                  # text file: "0.2.0"
├── trust/
│   └── central_server.pub           # base64 Ed25519 public key, pinned at install time
├── releases/
│   ├── 0.1.0/
│   │   └── venv/                    # python venv with the wheel installed
│   └── 0.2.0/
│       └── venv/
├── agent.env                        # JWT_SECRET_KEY, RELAY_*, etc.
└── bin/                             # macOS/Windows launcher shims
```

The platform-specific service unit (`systemd`, `launchd`, Windows SCM) does
**not** invoke a release directly. It calls
`python -m claude_mobile.launcher`, which:

1. Reads `<prefix>/current_version`.
2. Appends the boot attempt to `<prefix>/.boot_journal` (timestamp + version).
3. If the current version has been booted **3 times within 60s**, picks the
   highest-versioned sibling release from `<prefix>/releases/` (skipping any
   that lacks a usable `venv`), atomically rewrites `current_version` to that
   target, and logs `ERROR — version <X> crashed N+ times…; rolling back to
   <Y>`. With no eligible sibling, it logs the rollback miss and continues
   into the failing release; the supervisor's hard cap finally stops the loop.
4. `os.execv`s into `<prefix>/releases/<version>/venv/bin/python -m claude_mobile run`.

This means a swap of `current_version` followed by a process restart is the
only thing required to roll forward (or back). The launcher's rollback
heuristic gives a deployed install one automatic recovery step before
operator intervention is needed; matching supervisor settings —
systemd `StartLimitBurst=10`/`StartLimitIntervalSec=600`, launchd
`ThrottleInterval=10`, Windows SCM `sc failure … reset= 600` — give the
rolled-back release time to prove stable.

### State machine

```
+---------------+   manifest    +-----------------+
|  Idle (Run)   |  ----------> |  Verify          |
+---------------+              +-----------------+
                                       |
                           valid sig + sha + new version
                                       v
                               +-----------------+
                               |  Defer if busy  |  -- retry every 30 min
                               +-----------------+
                                       |
                                    idle
                                       v
                               +-----------------+
                               |  Stage release  |  releases/<v>/
                               +-----------------+
                                       |
                                       v
                               +-----------------+
                               |  Swap pointer   |  current_version (atomic)
                               +-----------------+
                                       |
                                       v
                               +-----------------+
                               |  Exit code 75   |  supervisor reboot
                               +-----------------+
```

Properties guaranteed by `StagedBinaryUpdater` (`claude_agent/.../updater.py`):

* **Fail-closed signature check** — a malformed or invalid Ed25519 signature
  rejects the manifest; the agent stays on the current version.
* **sha256 verification** — both the tarball and wheel are checksummed against
  the manifest before they are extracted/installed.
* **Tar-extraction safety** — `tarfile.extractall(filter="data")` mitigates
  CVE-2007-4559 path traversal. Required because the tarball arrives over
  the network from a manifest the agent only verified for signature.
* **Activity awareness** — defers the swap when any session is running. The
  updater queries `SessionManager.list_sessions()` and only proceeds when all
  sessions are stopped/crashed. Deferral retries after 30 minutes; a single
  pending update that has been deferred for >24h logs a `WARN` for
  observability.
* **Atomic pointer swap** — `os.replace` of `current_version.tmp` →
  `current_version`.
* **Supervisor restart** — the updater calls `sys.exit(75)`. Both
  `RestartForceExitStatus=75` (systemd) and Windows SCM
  `SC_ACTION_RESTART` interpret this as "please bring me back up", and the
  launcher boots into the freshly-pinned release.
* **Rollback target** — the previous release directory is retained for one
  cycle; a future successful boot of the new release garbage-collects it.

### Forced update (operator escape hatch)

```bash
CLAUDE_AGENT_MANIFEST_URL=https://api.claudemobile.io/v1/agent/manifest.json \
  /opt/claude-mobile/releases/$(cat /opt/claude-mobile/current_version)/venv/bin/python \
  -m claude_mobile update
```

`claude-agent update` skips activity-awareness and applies the manifest if a
new version is available. Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Manifest applied, or already on this version. |
| `2` | Signature verification failed (fail-closed). |
| `3` | Cannot reach the manifest URL or no public key configured. |
| `4` | Active sessions; deferred. (Use `--force` is implied via this CLI; this code surfaces only when `force=False` callers invoke the underlying API.) |

## Installing the agent (end users)

Linux / macOS:

```bash
curl -fsSL https://get.claudemobile.io/install.sh | bash
```

Windows (elevated PowerShell):

```powershell
irm https://get.claudemobile.io/install.ps1 | iex
```

Both scripts are mirrored from `deploy/install.sh` and `deploy/install.ps1`
into `docs/get/` (the GitHub Pages source for `get.claudemobile.io`). They:

1. Detect the OS and locate / create `<prefix>` (`/opt/claude-mobile`,
   `/usr/local/claude-mobile`, or `%ProgramFiles%\ClaudeAgent`).
2. Pull the manifest from
   `https://api.claudemobile.io/v1/agent/manifest.json` (override via
   `CLAUDE_AGENT_MANIFEST_URL`).
3. Verify the Ed25519 signature against `CLAUDE_AGENT_TRUST_KEY_B64` and
   persist the key at `<prefix>/trust/central_server.pub`.
4. Download tarball + wheel, verify sha256s, extract under
   `<prefix>/releases/<version>/`, install the wheel into a fresh venv.
5. Atomically pin `<prefix>/current_version`.
6. Register the service:
   * **Linux** — write `/etc/systemd/system/claude-mobile-agent.service`,
     `daemon-reload`, `enable`, `restart`. The unit's `ExecStart` is the
     launcher shim and `RestartForceExitStatus=75` is set.
   * **macOS** — write `~/Library/LaunchAgents/io.claudemobile.agent.plist`,
     `launchctl load`.
   * **Windows** — register the `ClaudeAgent` SCM service via `sc.exe create`,
     with restart-on-failure tuned to recover from updater exit code 75.

Re-running an install script is idempotent: a higher-versioned manifest stages
into a sibling `releases/<version>/` and re-pins the pointer, so the same
one-liner is also the operator-driven upgrade path.

### Trust-key bootstrap

The first install needs the public key that signs every subsequent manifest.
Two strategies:

1. **Pinned at install time (recommended):**
   ```bash
   CLAUDE_AGENT_TRUST_KEY_B64=<base64> \
     curl -fsSL https://get.claudemobile.io/install.sh | bash
   ```
   The script writes the key to `<prefix>/trust/central_server.pub` and uses
   it both for the install-time signature check and for every subsequent
   auto-update cycle.
2. **Bootstrap install** (no `CLAUDE_AGENT_TRUST_KEY_B64`): the install script
   warns and skips signature verification on the very first download. This is
   acceptable only if you trust the install channel itself (e.g. a private
   ZTNA URL) and you immediately publish a key into `<prefix>/trust/` before
   the next update cycle. **Production installs should always pin the key
   inline**.

## Android update flows

The Android client has three distinct update paths that work together:

* **Pre-flight version gate** — `MainActivity.onResume` triggers
  `VersionGateRepository.probe()`. If the result is `Incompatible`, the
  navigation graph is replaced with `IncompatibleServerScreen` until the user
  installs a new app build. Tunnel close code `4002` reaches the same screen
  via the `onIncompatibleServer` callback in case the gate races a server
  upgrade.
* **Flexible Play in-app update** — `InAppUpdateManager.checkForUpdate()` runs
  every `Activity.onResume`. When the Play Core API reports an update is
  available and the build allows the flexible flow, the user gets the
  non-blocking download/install prompt. Failures are silent: the in-app
  update is best-effort, and Play's own update banner remains the fallback.
* **Immediate Play in-app update** — `InAppUpdateManager.requestImmediateUpdate()`
  is reserved for the recovery path from `IncompatibleServerScreen` and is
  triggered automatically on screen entry via a `LaunchedEffect` wired
  from `MainActivity` through `AppNavigation`'s `onImmediateUpdate`
  callback. Sideloaded builds (Play has no update record) silently no-op
  and the user falls back to the screen's Play Store deep-link button.
  The server-side gate is still the authoritative signal: if Play reports
  nothing, the user stays on the upgrade screen so a stale-cache Play
  response cannot smuggle them back into a broken connection.
* **Agent-outdated banner** — `AgentVersionMonitor` fetches
  `GET /api/version` from the authenticated agent (proxied through
  `central_server`) and compares `server_version` (running agent) with
  `latest_agent_version` (manifest pointer the agent received over its
  relay). Mismatch → `SessionListScreen` renders `AgentOutdatedBanner`.
  The banner is non-blocking: dismissing it suppresses the visible
  prompt while the staged-binary updater on the agent host still
  installs silently the moment the agent goes idle. The
  banner's "Install now" CTA hits `POST /api/agent/update/force_now`
  on the agent, which routes through `StagedBinaryUpdater.force_update_now`
  to skip the idle deferral.

## Protocol-integrity CI gate

`protocol-integrity.yml` runs on every push and PR to `main`:

1. Imports `claude_agent/.../envelope.py` and
   `central_server/.../envelope.py` and asserts that `PROTOCOL_VERSION`,
   `SUPPORTED_PROTOCOL_VERSIONS`, and `MIN_CLIENT_PROTOCOL_VERSION` match
   between the two — refuses any commit that drifts them.
2. Runs `tests/test_protocol_integrity.py`, which compares the live wire
   shape against `tests/protocol_golden.json`. Any change to the handshake
   shape **must** update the golden file in the same commit.

This is the safety net that lets the two component packages duplicate
`envelope.py` (a deliberate constraint — the two packages are independent
deployables and cannot share runtime code) without going out of sync.

## See also

* [PROTOCOL.md](../PROTOCOL.md) — wire-contract version history and the
  step-by-step procedure for bumping `protocol_version`.
* [docs/deployment.md](deployment.md) — full deployment guide for both the
  central server and per-machine agents (legacy from-source layout, new
  versioned `releases/<v>/` layout, troubleshooting checklist).
* [docs/get/README.md](get/README.md) — GitHub Pages source for
  `get.claudemobile.io`, including the trust-key inline-pinning command.
* `android/CHANGELOG.md`, `claude_agent/CHANGELOG.md`,
  `central_server/CHANGELOG.md` — per-tag release notes.
