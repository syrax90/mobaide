# Versioning & Auto-Update

This document is the single reference for how Mobaide versions its three
components, ships releases, and keeps deployed installations up to date.

The system has **two orthogonal version concepts** and you should not conflate
them:

| Concept | Type | Purpose |
|--------|------|---------|
| Component semver (`android-vX.Y.Z`, `agent-vX.Y.Z`, `server-vX.Y.Z`) | Independent semver per component | Human-facing release identity. Ships on its own cadence. **Does not** gate compatibility. |
| `protocol_version` | Single shared integer | The only compatibility signal between Android, `mobaide_agent`, and `central_server`. Bumped when the wire contract changes. |

Wire-contract details and the `protocol_version` history live in
[PROTOCOL.md](../PROTOCOL.md). This doc covers the release machinery and the
update flows that sit on top of it.

## Tag conventions

Releases are driven by git tags pushed to the repo. Each prefix triggers a
dedicated workflow under `.github/workflows/`:

| Tag prefix | Component | Workflow | What it produces |
|------------|-----------|----------|------------------|
| `android-vX.Y.Z` | Android app | `release-android.yml` | Signed `.aab`, uploaded to Play Console internal track. `inAppUpdatePriority` is computed from the tag commit's conventional-commit prefix (`feat!:` / `BREAKING CHANGE:` → 5, `fix:` → 3, else → 1). |
| `agent-vX.Y.Z` | `mobaide_agent` | `release-agent.yml` | `uv build` wheel + source tarball, **Ed25519-signed manifest**, GitHub Release, manifest POSTed to `central_server` (with curl `--retry 5`), then a verify-after step asserts the served manifest matches. The workflow also re-syncs `docs/get/install.sh` and `docs/get/install.ps1` from `deploy/` so GitHub Pages stays current. |
| `server-vX.Y.Z` | `central_server` | `release-server.yml` | Wheel + Docker image pushed to `ghcr.io/<owner>/<repo>-server:vX.Y.Z` (and `:latest`). A second `deploy` job gates the rollout via `environment: production` (manual approval required); host-side rollout is operator-driven (run `deploy/prod/deploy-central.ps1` or `docker compose pull && up -d` against the prod compose file). |
| `proto-vN` | Wire contract | `release-protocol.yml` | Re-runs the lockstep envelope-integrity check defensively, then publishes a GitHub Release named `proto-vN` with the matching PROTOCOL.md section as the body and a diff vs the previous proto tag appended. Must change **both** `mobaide_agent/.../envelope.py` AND `central_server/.../envelope.py` in lockstep — `protocol-integrity.yml` rejects drift on every PR. |

### Cutting a release

There are two equivalent paths:

**Automated (preferred): release-please.** Land work on `main` using
[Conventional Commits](https://www.conventionalcommits.org/) prefixed by
component path (`feat(mobaide_agent): …`, `fix(central_server): …`,
`feat(android): …`). The `release-please.yml` workflow opens or updates a
release-PR per component that:

* bumps `[project] version` in the component's `pyproject.toml` (or
  `versionName`/`versionCode` for android — the simple release type tracks
  the version in `.release-please-manifest.json` for the Android entry, so
  `app/build.gradle.kts` must be bumped manually before tagging),
* prepends the new `## [vX.Y.Z]` section to that component's `CHANGELOG.md`
  derived from the commits since the last release tag,
* on PR merge, pushes the corresponding `agent-vX.Y.Z` /
  `server-vX.Y.Z` / `android-vX.Y.Z` tag, which fires the matching
  release-* workflow.

  **Caveat — tag-trigger chaining.** GitHub's default `GITHUB_TOKEN`
  cannot trigger downstream workflows from a tag it pushes. The
  release-please workflow's `token` input falls back to `GITHUB_TOKEN` if
  `RELEASE_PLEASE_TOKEN` is unset, but in that mode the operator must
  push the tag manually after merging the release-PR (e.g.
  `git fetch && git tag agent-v0.2.0 && git push origin agent-v0.2.0`).

  **Recommended PAT shape.** Use a **fine-grained PAT** scoped to **this
  repository only** with these permissions:

  | Permission | Access |
  |-----------|--------|
  | Contents | Read and write |
  | Pull requests | Read and write |
  | Workflows | Read and write |

  Do **not** use a classic PAT — those grant write access to every repo
  the user owns and are unnecessarily broad. The fine-grained PAT also
  needs `Workflows: write` so that release-please's tag-push triggers the
  downstream `release-agent.yml` / `release-server.yml` /
  `release-android.yml` workflows; without it, the tag is created but no
  workflow fires.

**Manual.** Bump the version field for the component, update the matching
`CHANGELOG.md` by hand, push the tag (`git tag agent-v0.2.0 && git push --tags`).
The relevant workflow takes it from there.

Required CI secrets:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | `release-android.yml` | Signs the `.aab` with the upload key. |
| `PLAY_SERVICE_ACCOUNT_JSON` | `release-android.yml` | Service account used to upload the signed bundle to the Play Console internal track. |
| `AGENT_MANIFEST_PRIVATE_KEY` | `release-agent.yml` | Base64 Ed25519 private key. Signs the manifest body. |
| `CENTRAL_SERVER_RELEASE_TOKEN` | `release-agent.yml` | Bearer token for `POST /api/internal/agent_manifest`. |
| `CENTRAL_SERVER_URL` | `release-agent.yml` | Base URL of the production `central_server` that hosts the manifest. |
| `RELEASE_PLEASE_TOKEN` _(optional)_ | `release-please.yml`, `release-agent.yml` | PAT with `contents:write` + `workflow` scopes. Required only to chain release-please tag pushes into the downstream `release-*` workflows and for the agent release to push a Pages re-sync commit. |

If `AGENT_MANIFEST_PRIVATE_KEY` or `CENTRAL_SERVER_*` is missing, the agent
release still publishes a GitHub Release but skips signing / publishing the
manifest. Deployed agents will not see the new version until the manifest is
delivered.

**Server-side counterparts.** Two of the GitHub secrets above must be
mirrored as env vars on the production `central_server` host (typically
`/opt/mobaide/.env`). The two halves are matched byte-for-byte at request
time, so the names in CI and the names on the server intentionally
differ — keep them in sync as a pair:

| GitHub Actions secret | Server `.env` variable | Relationship |
|--|--|--|
| `CENTRAL_SERVER_RELEASE_TOKEN` | `RELEASE_AUTH_TOKEN` | Identical string. The workflow sends it in the `X-Release-Token` header; the server compares with `hmac.compare_digest`. |
| `AGENT_MANIFEST_PRIVATE_KEY` | `MANIFEST_SIGNING_PUBLIC_KEY` | Public/private halves of the same Ed25519 key. CI signs; the server verifies. |

If `RELEASE_AUTH_TOKEN` is unset on the server,
`POST /api/internal/agent_manifest` returns **HTTP 503** ("Manifest
publishing is disabled") and the release workflow's curl step fails
after `--retry 5` exhaustion. If `MANIFEST_SIGNING_PUBLIC_KEY` is unset,
the server stores manifests without verification — agents that have
pinned a trust key will then reject the manifest at update time, so set
both vars together.

### Server-side deploy gate

`release-server.yml` builds and pushes the Docker image to GHCR, then a
second `deploy` job runs in the `production` environment. Configure
required reviewers under repo Settings → Environments → production so the
job blocks on a manual click before recording the rollout. The job itself
does not SSH to the host — host-side rollout is operator-driven via
`deploy/prod/deploy-central.ps1` (which preserves the previous image for
one-step rollback) or, for the polling pattern, a watchtower-style
container on the prod host that pulls `ghcr.io/<repo>-server:latest` on a
timer.

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
  Play Store deep link (`market://details?id=com.mobaide.app`) and a
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
    "tarball_url": "https://github.com/.../mobaide-agent-0.2.0.tar.gz",
    "tarball_sha256": "a1b2...",
    "wheel_url": "https://github.com/.../mobaide_agent-0.2.0-py3-none-any.whl",
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
<prefix>/                            # /opt/mobaide, /usr/local/mobaide, or %ProgramFiles%\MobaideAgent
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
`python -m mobaide.launcher`, which:

1. Reads `<prefix>/current_version`.
2. Appends the boot attempt to `<prefix>/.boot_journal` (timestamp + version).
3. If the current version has been booted **3 times within 60s**, picks the
   highest-versioned sibling release from `<prefix>/releases/` (skipping any
   that lacks a usable `venv`), atomically rewrites `current_version` to that
   target, and logs `ERROR — version <X> crashed N+ times…; rolling back to
   <Y>`. With no eligible sibling, it logs the rollback miss and continues
   into the failing release; the supervisor's hard cap finally stops the loop.
4. `os.execv`s into `<prefix>/releases/<version>/venv/bin/python -m mobaide run`.

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

Properties guaranteed by `StagedBinaryUpdater` (`mobaide_agent/.../updater.py`):

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
MOBAIDE_AGENT_MANIFEST_URL=https://api.mobaide.com/v1/agent/manifest.json \
  /opt/mobaide/releases/$(cat /opt/mobaide/current_version)/venv/bin/python \
  -m mobaide update
```

`mobaide-agent update` skips activity-awareness and applies the manifest if a
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
curl -fsSL https://get.mobaide.com/install.sh | bash
```

Windows (elevated PowerShell):

```powershell
irm https://get.mobaide.com/install.ps1 | iex
```

Both scripts are mirrored from `deploy/install.sh` and `deploy/install.ps1`
into `docs/get/` (the GitHub Pages source for `get.mobaide.com`). They:

1. Detect the OS and locate / create `<prefix>` (`/opt/mobaide`,
   `/usr/local/mobaide`, or `%ProgramFiles%\MobaideAgent`).
2. Pull the manifest from
   `https://api.mobaide.com/v1/agent/manifest.json` (override via
   `MOBAIDE_AGENT_MANIFEST_URL`).
3. Verify the Ed25519 signature against `MOBAIDE_AGENT_TRUST_KEY_B64` and
   persist the key at `<prefix>/trust/central_server.pub`.
4. Download tarball + wheel, verify sha256s, extract under
   `<prefix>/releases/<version>/`, install the wheel into a fresh venv.
5. Atomically pin `<prefix>/current_version`.
6. Register the service:
   * **Linux** — write `/etc/systemd/system/mobaide-agent.service`,
     `daemon-reload`, `enable`, `restart`. The unit's `ExecStart` is the
     launcher shim and `RestartForceExitStatus=75` is set.
   * **macOS** — write `~/Library/LaunchAgents/com.mobaide.agent.plist`,
     `launchctl load`.
   * **Windows** — register the `MobaideAgent` SCM service via `sc.exe create`,
     with restart-on-failure tuned to recover from updater exit code 75.

Re-running an install script is idempotent: a higher-versioned manifest stages
into a sibling `releases/<version>/` and re-pins the pointer, so the same
one-liner is also the operator-driven upgrade path.

### Trust-key bootstrap

The first install needs the public key that signs every subsequent manifest.
Two strategies:

1. **Pinned at install time (recommended):**
   ```bash
   MOBAIDE_AGENT_TRUST_KEY_B64=<base64> \
     curl -fsSL https://get.mobaide.com/install.sh | bash
   ```
   The script writes the key to `<prefix>/trust/central_server.pub` and uses
   it both for the install-time signature check and for every subsequent
   auto-update cycle.
2. **Bootstrap install** (no `MOBAIDE_AGENT_TRUST_KEY_B64`): the install script
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

1. Imports `mobaide_agent/.../envelope.py` and
   `central_server/.../envelope.py` and asserts that `PROTOCOL_VERSION`,
   `SUPPORTED_PROTOCOL_VERSIONS`, and `MIN_CLIENT_PROTOCOL_VERSION` match
   between the two — refuses any commit that drifts them.
2. Runs `tests/test_protocol_integrity.py`, which compares the live wire
   shape against `tests/protocol_golden.json`. Any change to the handshake
   shape **must** update the golden file in the same commit.
3. **Diff guard (PR-only):** if a PR modifies one of the two `envelope.py`
   files, it must modify the other. Comparison uses
   `${{ github.event.pull_request.base.sha }}..${{ github.event.pull_request.head.sha }}`
   so the check is base-branch-agnostic. The same diff guard also asserts
   that `deploy/install.{sh,ps1}` and `docs/get/install.{sh,ps1}` are
   byte-identical — Pages serves the latter, and any drift between the
   canonical source and the published mirror is flagged before the next
   release re-syncs them.

This is the safety net that lets the two component packages duplicate
`envelope.py` (a deliberate constraint — the two packages are independent
deployables and cannot share runtime code) without going out of sync.

## End-to-end auto-update verification

`tests/test_e2e_autoupdate.py` exercises the full release pipeline in a
single in-process pytest run:

1. Generates an Ed25519 signing keypair.
2. Boots a fresh `central_server` `FastAPI` app (via `create_app()`) with
   the public key wired into config and a tmp-path manifest store.
3. Builds a synthetic agent v0.2.0 release artifact (tarball with a
   `__version__ = "0.2.0"` payload + a placeholder wheel), signs the
   manifest, and POSTs it to `/api/internal/agent_manifest`.
4. Asserts `GET /api/v1/agent/manifest.json` returns the published record.
5. Runs `StagedBinaryUpdater.consider_manifest()` against the served
   manifest URL with a stubbed installer; asserts the staged release dir
   was created, the staged `__init__.py` carries the expected version
   string, and `current_version` was atomically swapped.
6. **Negative cases:** corrupting the tarball sha256 in the manifest body
   makes the updater return `signature_failed` and leaves
   `current_version` untouched (fail-closed). An invalid `X-Release-Token`
   makes the publish endpoint return 401 without persisting.

The test is run by `.github/workflows/e2e-update.yml` on every PR and on
push to `main`, with a 5-minute timeout. The implementation purposely
does NOT spawn a real systemd / launchd / Windows SCM supervisor — that
boundary is platform-coupled and is exercised separately by
`mobaide_agent/tests/test_launcher_rollback.py` and the install scripts'
own integration tests.

## See also

* [PROTOCOL.md](../PROTOCOL.md) — wire-contract version history and the
  step-by-step procedure for bumping `protocol_version`.
* [docs/deployment.md](deployment.md) — full deployment guide for both the
  central server and per-machine agents (legacy from-source layout, new
  versioned `releases/<v>/` layout, troubleshooting checklist).
* [docs/get/README.md](get/README.md) — GitHub Pages source for
  `get.mobaide.com`, including the trust-key inline-pinning command.
* `android/CHANGELOG.md`, `mobaide_agent/CHANGELOG.md`,
  `central_server/CHANGELOG.md` — per-tag release notes.
