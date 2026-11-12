# Versioning & Auto-Update

This document is the single reference for how Mobaide versions its four
components, ships releases, and keeps deployed installations up to date.

The system has **two orthogonal version concepts** and you should not conflate
them:

| Concept | Type | Purpose |
|--------|------|---------|
| Component semver (`android-vX.Y.Z`, `ios-vX.Y.Z`, `agent-vX.Y.Z`, `server-vX.Y.Z`, `web-vX.Y.Z`) | Independent semver per component | Human-facing release identity. Ships on its own cadence. **Does not** gate compatibility. |
| `protocol_version` | Single shared integer | The only compatibility signal between Android, `mobaide_agent`, and `central_server`. Bumped when the wire contract changes. |

Wire-contract details and the `protocol_version` history live in
[PROTOCOL.md](../PROTOCOL.md). This doc covers the release machinery and the
update flows that sit on top of it.

**On this page**

- [Tag conventions](#tag-conventions)
- [Compatibility gating: `protocol_version`](#compatibility-gating-protocol_version)
- [Agent rollout manifest](#agent-rollout-manifest)
- [Auto-update on the agent (staged-binary swap)](#auto-update-on-the-agent-staged-binary-swap)
- [Installing the agent (end users)](#installing-the-agent-end-users)
- [Android update flows](#android-update-flows)
- [Protocol-integrity CI gate](#protocol-integrity-ci-gate)
- [End-to-end auto-update verification](#end-to-end-auto-update-verification)
- [See also](#see-also)

## Tag conventions

Each component version gets a git tag. release-please pushes the tag when you
merge its release-PR. **A tag ships nothing by itself.** You ship each release
from your own machine with a local script. No release key is stored on GitHub.

| Tag prefix | Component | How it ships |
|------------|-----------|--------------|
| `android-vX.Y.Z` | Android app | `python deploy/prod/release_android.py` |
| `agent-vX.Y.Z` | `mobaide_agent` | `python deploy/prod/release_agent.py` |
| `server-vX.Y.Z` | `central_server` | `python deploy/prod/deploy_central.py` (the tag only marks the version) |
| `web-vX.Y.Z` | Web client (`web/`) | `python deploy/prod/deploy_web.py` (the tag only marks the version) |
| `ios-vX.Y.Z` | iOS app | Archive and upload from Xcode (the tag only marks the version) |
| `proto-vN` | Wire contract | _(nothing ships)_ |

> [!NOTE]
> No GitHub workflow builds or ships a release, and the repo holds no release
> secrets. Every key lives in the local release folder that the scripts read.

### The local release scripts

Both scripts work the same way:

- Run them from the main checkout. Without a version argument, they release the
  newest `agent-v*` / `android-v*` tag.
- They fetch tags and check that the local tag points at the same commit as on
  `origin`.
- They refuse a dirty working tree unless you pass `--force`.
- They build in a temporary detached `git worktree` at the tag, then remove it.
  The worktree always has LF line endings, also on Windows, so every OS builds
  the same files.
- They read keys from the [local key folder](#local-key-folder).
- They run on macOS, Linux and Windows (Python 3.12+).

```bash
python deploy/prod/release_agent.py [VERSION] [--skip-publish] [--force] [--release-dir DIR]
python deploy/prod/release_android.py [VERSION] [--skip-upload] [--force] [--release-dir DIR]
```

On Windows, use `py deploy\prod\release_agent.py` and `py deploy\prod\release_android.py`.

Shared helpers live in `deploy/prod/release_common.py`.

### What each tag produces

**`android-vX.Y.Z`** — `release_android.py`

1. Finds a JDK 17 or newer and the Android SDK:

   | | JDK | Android SDK |
   |---|---|---|
   | All | `JAVA_HOME` | `ANDROID_HOME` or `ANDROID_SDK_ROOT` |
   | macOS default | Android Studio JBR in `/Applications` | `~/Library/Android/sdk` |
   | Windows default | Android Studio JBR in `%ProgramFiles%\Android\Android Studio` or `%LOCALAPPDATA%\Programs\Android Studio` | `%LOCALAPPDATA%\Android\Sdk` |
   | Linux default | `java` on `PATH` | `~/Android/Sdk` |

2. Checks that `versionName` in `android/app/build.gradle.kts` equals the tag.
3. Computes `inAppUpdatePriority` from the `android/CHANGELOG.md` section
   release-please generated for this tag (rules under
   [Cutting an Android minor or major](#cutting-an-android-minor-or-major)).
   It does **not** read the tagged commit subject: the tag sits on
   release-please's `chore: release main` commit, which is never a
   `feat`/`fix`.
4. Copies `google-services.json` into the worktree and runs the Gradle wrapper
   (`gradle-wrapper.jar` directly, so the same call works on Windows)
   with `bundleRelease` and the upload-key signing values. It removes
   `CENTRAL_SERVER_URL`, `PRIVACY_POLICY_URL`, `SUPPORT_GITHUB_URL` and
   `SUPPORT_EMAIL` from the environment, so the production defaults apply.
5. Checks that the bundle certificate SHA-256 equals the keystore certificate.
   This catches the silent fallback to debug signing.
6. Copies the bundle to `android/app/release/mobaide-X.Y.Z.aab` (git-ignored)
   and prints its SHA-256.
7. Uploads it to the Play Console internal track through the Play Developer API
   as the service account: `edits.insert` → `bundles.upload` →
   `tracks.update` (status `completed`, with `inAppUpdatePriority`) →
   `edits.commit`.

`--skip-upload` stops after step 6.

**`agent-vX.Y.Z`** — `release_agent.py`

1. Checks that the public half of the local signing key equals
   `GET https://api.mobaide.com/api/v1/agent/trust_key`.
2. Checks that `mobaide_agent/pyproject.toml` has the tag version.
3. Builds the wheel and sdist with `uv build --wheel --sdist`. Builds the source
   tarball with Python `tarfile` (no `__pycache__`, no `*.pyc`, owner
   normalised). Computes SHA-256 values.
4. Signs the **Ed25519 manifest** (same canonical body as before).
5. PUTs the tarball and the wheel to `central_server`, which stores them by
   SHA-256 (details under [Release file hosting](#release-file-hosting)).
6. Downloads every manifest URL anonymously and checks its SHA-256.
7. POSTs the manifest to `central_server` with retries.
8. Reads `/api/v1/agent/manifest.json` back and compares the version.

You can run the script again for the same version at any time. The rebuild
makes new bytes, so the files get new URLs and nothing is overwritten.

`--skip-publish` stops after step 4. The script needs `uv`, `gh`, and the
`cryptography` package (`pip install cryptography`).

**`server-vX.Y.Z`**

- The tag only marks the version. No image is built and no workflow runs.
- `deploy/prod/deploy_central.py` is the only server deploy path.

**`web-vX.Y.Z`**

- The tag only versions the `web/` workspace (`release-type: node`); no workflow
  fires on tag push.
- The SPA ships with `deploy/prod/deploy_web.py`.

**`proto-vN`**

- Hand-pushed by a developer alongside the lockstep `PROTOCOL.md` +
  `envelope.py` bump. It is an anchor tag only: no workflow fires and no
  Release object is created.
- `protocol-integrity.yml` enforces that **both**
  `mobaide_agent/.../envelope.py` AND `central_server/.../envelope.py` move
  together, on every PR and every push to `main`.

### Cutting a release

The rule is one line: **the PR title is the release trigger.**

| You want to release… | PR title prefix |
|----------------------|-----------------|
| `mobaide_agent` | `feat(agent): …` or `fix(agent): …` |
| `central_server` | `feat(server): …` or `fix(server): …` |
| Android app | `feat(android): …` or `fix(android): …` |
| Web client | `feat(web): …` or `fix(web): …` |
| iOS app | `feat(ios): …` or `fix(ios): …` |
| Anything else (CI, docs, refactor) | `chore: …`, `docs: …`, `refactor: …` — no release fires |

**How far the version moves is not the same for every component.** Every
automatic bump in this repo is currently a *patch*, but for two different
reasons, and only one of them is permanent:

| Component | `fix` | `feat` | `feat!` | Why |
|-----------|-------|--------|---------|-----|
| Android, iOS | patch | patch | patch | `"versioning": "always-bump-patch"` on the `android` and `ios` packages. **Permanent.** |
| agent, server, web | patch | patch | minor | Root-level `bump-minor-pre-major` + `bump-patch-for-minor-pre-major`. Pre-`1.0.0` only. |

- **Android, iOS.** release-please's `AlwaysBumpPatch` ignores the commit type
  entirely, breaking changes included. **Permanent** — minor and major are
  hand-cut, see [Cutting an Android minor or major](#cutting-an-android-minor-or-major).
- **agent, server, web.** release-please gates both flags on
  `version.isPreMajor`, so this holds **only while the version is below
  `1.0.0`**. The day one of these ships `1.0.0` those flags go inert and it
  silently reverts to plain semver — `feat` → minor, `!` → major — with no
  config change.

#### Squash-only merging and the PR title

The repo enforces **squash-only merging** (merge commits and rebase merges
are disabled at the GitHub repo level). The squash commit **title is the PR
title** and its **body is blank**. So the PR title is the *single* commit on
`main` that release-please reads. The feature branch's own commit messages are
**discarded at merge time** and have no effect on the version bump or the
changelog.

Breaking changes are signalled with `!` in the PR title (e.g.
`feat(agent)!: …`). That always earns the commit a `### ⚠ BREAKING CHANGES`
section in the changelog, but it does **not** force a major here — see the bump
table above.

**This is enforced two ways:**

- The repo merge settings. The source of truth is a
  `gh api -X PATCH repos/<owner>/<repo> -f squash_merge_commit_title=PR_TITLE
  -f squash_merge_commit_message=BLANK -F allow_merge_commit=false
  -F allow_rebase_merge=false`, mirrored as policy-as-code in
  `.github/settings.yml`.
- `pr-title-lint.yml` runs on every pull request. It fails the check if the
  title doesn't match the grammar above, or uses a stale scope like
  `mobaide_agent` / `central_server` instead of the short `agent` / `server`.

What happens after merge:

1. release-please sees the new conventional commit on `main`.
2. It opens (or updates) a release-PR for that component — bumps the
   per-component version in lockstep across:
   - `.release-please-manifest.json` (the manifest of record),
   - the version file of the component: `pyproject.toml` for `mobaide_agent` /
     `central_server`, `android/app/build.gradle.kts` for android (the line
     annotated with `// x-release-please-version`), `ios/Config/Version.xcconfig`
     for ios, or `web/package.json` for web,
   - the component's `CHANGELOG.md` (prepends a new `## [vX.Y.Z]`
     section).
3. Merging that release-PR pushes the corresponding `agent-vX.Y.Z` /
   `server-vX.Y.Z` / `android-vX.Y.Z` / `ios-vX.Y.Z` / `web-vX.Y.Z` tag. Nothing ships yet.
4. You run the matching local script from the tag table above. This is a
   deliberate pause: a bad merge does not ship until you start the release.

release-please runs with `GITHUB_TOKEN` only. A tag that `GITHUB_TOKEN` pushes
does not start other workflows (a GitHub Actions rule), so no release workflow
fires after a merge.

**Release-object side effect of step 3:** release-please-action v4 also
creates an empty GitHub Release on this repo with the tag (see
release-please-action issue #1034). A follow-up step in `release-please.yml`
deletes it and keeps the tag, so the Releases tab here stays empty for
`agent-v*` / `server-v*` / `android-v*` / `ios-v*` / `web-v*`. Artifacts live elsewhere:
agent files on `central_server` itself; android in Play
Console; server and web on the production host. A Release that appears briefly
and vanishes is that cleanup step, not a bug.

### Manual release path (operator escape hatch)

Use this when the conventional-commits flow is the wrong tool:

- release-please opened no release-PR (the merged PR's title was a
  non-release type such as `chore:` / `docs:` / `refactor:`, so no
  version-bearing commit reached `main`);
- you want to ship a hotfix release-please can't represent; or
- you've already hand-bumped the version files and want to ship those exact
  bytes.

The steps below are for `mobaide_agent`. The same shape works for `android`
(`android/app/build.gradle.kts`, `android/CHANGELOG.md`, `release_android.py`).

1. **On a branch**, edit the version files in lockstep:
   - `mobaide_agent/pyproject.toml` — bump `[project] version`.
   - `.release-please-manifest.json` — bump the `mobaide_agent`
     entry to the same version.
   - `mobaide_agent/CHANGELOG.md` — replace `## [Unreleased]` with
     `## [X.Y.Z] - YYYY-MM-DD` and fill in the entries.
2. **Open and merge a PR** to `main`. Commit grammar does not matter for *this*
   path: your own tag replaces release-please.
3. **Tag the merge commit** from your machine:
   `git fetch origin && git tag -a agent-vX.Y.Z origin/main -m "Release agent-vX.Y.Z" && git push origin agent-vX.Y.Z`.
   Check first that the tag does not exist and that `pyproject.toml` and the
   manifest agree.
4. **Run** `python deploy/prod/release_agent.py X.Y.Z`. The script refuses to
   build if `pyproject.toml` does not match the tag.

> [!WARNING]
> **Pick one path and stay on it.** Either let release-please own the
> bump (the conventional-commits flow above), **or** use the manual
> release path end-to-end (hand-edit the version files, then push the tag
> yourself).

The half-and-half mode (hand-edit `pyproject.toml` and the manifest, then
expect release-please to tag) shipped `agent-v0.1.1` with a stale `0.1.0` wheel
and silently dropped the `agent-v0.1.2` tag: release-please saw no conventional
commits, so it didn't tag, and nothing else watches the manifest. To ship without an upstream change, use the manual release path or
edit the open release-PR before merging.

**Android note.** The `android` component is `release-type: simple`. The
`extra-files` entry in `release-please-config.json` points the generic updater
at `android/app/build.gradle.kts` (the `// x-release-please-version` annotation
marks the `versionName` line), so that file and `.release-please-manifest.json`
are bumped automatically. `versionCode` is computed in `build.gradle.kts` from
`versionName` (`major*10000 + minor*100 + patch`), so Play's
monotonic-versionCode requirement needs no manual edit.

**iOS note.** The `ios` component is also `release-type: simple` with
`always-bump-patch`. The version lives in `ios/Config/Version.xcconfig`, which
`ios/Config/Protocol.xcconfig` includes. The `Mobaide` target does not set it
in `project.pbxproj`, so do not edit the version in Xcode's target settings.

- `MARKETING_VERSION` carries the `// x-release-please-version` annotation.
  The generic updater bumps it by one patch.
- `CURRENT_PROJECT_VERSION` (the build number) is a plain counter that
  release-please cannot bump. A step in `release-please.yml` sets it to main's
  value + 1 on the release-PR branch, with a
  `chore(ios): bump build number to N` commit. The step is idempotent and adds
  the commit again when release-please regenerates the branch.
- The top-level `bootstrap-sha` in `release-please-config.json` limits the
  first iOS changelog to commits after it. release-please uses it only for a
  package with no release yet, so it is inert after the first `ios-v*` tag.
- A minor or major works as for Android: a `Release-As:` footer on a commit
  that touches `ios/**`.

#### Cutting an Android minor or major

The `android` package carries `"versioning": "always-bump-patch"`, so **no
PR title can move its minor or major**. Both are deliberate human acts.

The supported way is a `Release-As:` footer. release-please applies it before
it consults the versioning strategy, so it overrides `always-bump-patch`:

1. Merge the Android PR as usual, but in GitHub's squash dialog **type a body**
   under the title (the repo defaults that body to blank, so it is empty until
   you fill it in):

   ```
   feat(android): whatever the PR title already says

   Release-As: 3.7.0
   ```

2. release-please opens the android release-PR at exactly `3.7.0`, with the
   normal changelog section and `build.gradle.kts` / manifest rewrite. Merging
   it tags `android-v3.7.0`, and `release_android.py` ships the bundle as usual.

The footer is scoped by path like every other commit: it only reaches the
`android` package if the commit touches `android/**`. If you have nothing to
ship alongside the bump, land a `chore(android): …` PR touching an Android file
and put the footer on that. Editing the open release-PR by hand before merging
is the other supported path.

**A breaking change still ships as a patch, and that is fine.**
`release_android.py` derives `inAppUpdatePriority` from the changelog section,
not from the version delta:

- any `### ⚠ BREAKING CHANGES` heading yields priority 5 (immediate in-app
  update)
- a `### Bug Fixes` heading yields 3
- otherwise 1

A breaking `3.6.x` release is therefore still pushed to users at
full urgency.

> [!WARNING]
> That grep reads release-please's *default* section headings, so
> never introduce a `changelog-sections` config for this package — it would
> silently drop the urgency to 1.

#### release-please and `GITHUB_TOKEN`

`release-please.yml` uses only the short-lived `GITHUB_TOKEN`. No PAT is
stored. Two repo settings must allow it:

| Setting | Required value |
|---------|----------------|
| Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests" | On. Without it, release-please cannot open its release-PR. |
| Ruleset "Owner-only protected branches" | GitHub Actions may update `release-please--branches--main` and `gh-pages` (`publish-gh-pages.yml` pushes `gh-pages` with `GITHUB_TOKEN`). |

A release-PR that `GITHUB_TOKEN` opens starts no CI checks. It changes only
versions and changelogs, and no check is required, so this is acceptable.

#### Local key folder

The scripts read every key from one folder outside the repo:

1. `--release-dir DIR`, else
2. the `MOBAIDE_RELEASE_DIR` environment variable, else
3. the default folder:

| OS | Default folder | Access rule the scripts check |
|----|----------------|-------------------------------|
| macOS, Linux | `~/.config/mobaide-release/` | Folder `chmod 700`, each file `chmod 600`. Group and others must have no access. |
| Windows | `%LOCALAPPDATA%\mobaide-release\` | No access for Everyone, Users, Authenticated Users, Guests or Anonymous. The default ACL of `%LOCALAPPDATA%` already meets this. |

The scripts refuse a folder or file that breaks the rule and print the command
that fixes it (`chmod` or `icacls`). On Windows the folder is in `LOCALAPPDATA`,
not `APPDATA`, so a roaming profile does not copy the keys to other machines.
Keep a backup of the folder in a password manager.

| File | Used by | Content |
|------|---------|---------|
| `agent/manifest-signing-key` | `release_agent.py` | Ed25519 private key that signs the manifest: base64 of the 32 raw bytes (the old `AGENT_MANIFEST_PRIVATE_KEY` format), or a PEM file. |
| `agent/release-token` | `release_agent.py` | Token for `PUT /api/internal/agent_release/...` and `POST /api/internal/agent_manifest`. |
| `android/upload.keystore` | `release_android.py` | Play upload key. |
| `android/signing.properties` | `release_android.py` | `storePassword`, `keyPassword`, `keyAlias`. |
| `android/google-web-client-id` | `release_android.py` | Google OAuth web client ID, one line. |
| `android/google-services.json` | `release_android.py` | Firebase config of the release app. |
| `android/play-service-account.json` | `release_android.py` | Service account that uploads to the Play Console internal track. |

**Failure modes:**

* Signing key missing, or its public half differs from
  `/api/v1/agent/trust_key`: the script stops before it creates a release.
  Nothing changes anywhere.
* `agent/release-token` missing or wrong: the upload fails with HTTP 401 before
  anything is stored. The previous manifest keeps serving. Fix the token and
  run the script again.
* The relay has no upload endpoint (HTTP 404 or 405): `central_server` is older
  than the script. Deploy `central_server` first.
* A file does not download anonymously, or its SHA-256 differs: the script
  stops before it posts the manifest, and the previous manifest keeps serving.
* The bundle is not signed with the upload key: `release_android.py` stops
  before the upload.

#### Server-side counterparts

Two local keys have a matching env var on the production `central_server`
host (typically `/opt/mobaide/.env`). Keep each pair in sync:

| Local key folder | Server `.env` variable | Relationship |
|--|--|--|
| `agent/release-token` | `RELEASE_AUTH_TOKEN` | Identical string. The script sends it in the `X-Release-Token` header; the server compares with `hmac.compare_digest`. |
| `agent/manifest-signing-key` | `MANIFEST_SIGNING_PUBLIC_KEY` | Private and public halves of the same Ed25519 key. The script signs; the server verifies. |

- If `RELEASE_AUTH_TOKEN` is unset on the server,
  `POST /api/internal/agent_manifest` returns **HTTP 503** ("Manifest
  publishing is disabled") and the script's publish step fails.
- If `MANIFEST_SIGNING_PUBLIC_KEY` is unset, the server stores manifests
  without verification. Agents that have pinned a trust key will then reject
  the manifest at update time, so set both vars together.

## Compatibility gating: `protocol_version`

`protocol_version` is a single integer carried in every cross-component
handshake. Each side declares the range it supports, and any peer outside that
range is rejected up front.

**Android probe — `GET /api/version`** (before any WebSocket connect):

```json
{
  "name": "central_server",
  "server_version": "0.1.0",
  "protocol_versions": [5],
  "min_client_version": 5,
  "capabilities": {
    "cli": {
      "claude": { "available": true },
      "codex": {
        "available": true,
        "models": ["gpt-5.6-sol", "gpt-5.5"],
        "model_details": [
          { "id": "gpt-5.6-sol", "efforts": ["low", "high", "ultra"], "default_effort": "low" }
        ]
      }
    },
    "git": {
      "changes": true, "stage": true, "discard": true, "commit": true,
      "fetch": true, "pull": true, "push": true, "branches": true,
      "rebase": true, "resolve": true, "conflicts": true
    }
  },
  "latest_agent_version": "0.1.0"
}
```

**Capability keys.** `PROTOCOL.md` (proto-v5 bullet plus the 2026-08-23 proto-v7
additive delta) owns the full shape.

- The agent advertises one `capabilities.git.*` key per route group:
  `capabilities.git.changes` (session git status/diff),
  `capabilities.git.stage` / `discard` / `commit`,
  `capabilities.git.fetch` / `pull` / `push` (background jobs and their
  `git.job.*` events), `capabilities.git.branches` (branch create/checkout),
  `capabilities.git.rebase` (rebase routes and `repo.rebase` on status),
  `capabilities.git.resolve` and `capabilities.git.conflicts` (conflict
  resolution). Additive, no bump: `changes`, `stage` / `discard` / `commit`,
  `fetch` / `pull` / `push`, `branches`, `rebase`, `conflicts`.
- A client shows a control only when its key is `true`, so an older agent keeps
  a read-only viewer. An agent without `changes` answers the session git routes
  with 404, and clients render an "update your agent" state.
- `capabilities.git.resolve` certifies that `stage` refuses an unmerged path
  still holding conflict markers. That makes a conflict row's *Mark resolved*
  action (gated on `resolve` and `stage` together) safe to offer.
- `capabilities.cli` carries one `{ "available": <bool> }` entry per managed CLI
  the agent registers — additive, no `protocol_version` bump. The Android
  mode-select screen disables a per-CLI button when its binary is absent, and an
  absent / old-agent / malformed value counts as available.
- Entries whose startup model enumeration succeeded also carry `models` and,
  when per-model reasoning levels exist, `model_details`; the New-session
  pickers prefer them over hardcoded fallbacks. Both keys are omitted, never
  emitted empty, on failure. While enumeration is still running,
  `"models_pending": true` marks each entry whose list is not yet produced (its
  absence is provisional).

The signed manifest is not echoed through this REST surface (agents get it
inline over the relay WebSocket since proto-v2); the Android client only needs
`latest_agent_version` for the "agent outdated" banner.

The Android client (`VersionGateRepository`) compares
`BuildConfig.APP_PROTOCOL_VERSION` against the server's
`[min_client_version, max(protocol_versions)]`. Outcome:

* **Compatible** → connect normally; persist `latest_agent_version` for the
  agent-outdated banner.
* **Incompatible** → switch to `IncompatibleServerScreen`. The user sees a
  Play Store deep link (`market://details?id=com.mobaide.app`) and a
  "What changed" link to `PROTOCOL.md`. No further WebSocket attempts.

**Agent ↔ central server tunnel** (`/ws/agent-tunnel`):

1. The agent sends `{type:"auth", protocol_version, agent_version, capabilities}`.
2. If `protocol_version` is not in the server's `SUPPORTED_PROTOCOL_VERSIONS`, the
   server emits `{type:"incompatible", reason, min_client_version,
   server_protocol_versions, manifest_url}` and closes with WebSocket code **4002**
   (`PROTOCOL_INCOMPATIBLE_CLOSE_CODE`).
3. On 4002 the relay's reconnect-backoff jumps to `reconnect_max_delay`.
4. The agent's HTTPS-bootstrap path (see below) fetches the signed manifest
   from the `manifest_url` hint (or the env-derived URL when the hint is
   absent), stages the new release, and the supervisor respawns into the
   upgraded binary.

The agent self-heals without operator intervention; the only terminal case is an
agent that pre-dates the HTTPS-bootstrap release.

On a successful auth the server persists `agent_version` and
`agent_protocol_version` (the `protocol_version` the agent declared) into the
`agents` DB row. Both are exposed via `GET /api/agents` (`AgentSummary`) and
shown on the admin dashboard's users page in a **Version** column ("Up to date"
or "latest: X.Y.Z" when behind the manifest).

## Agent rollout manifest

The signed manifest is the unit of truth for what version every agent should
be running. It is a small JSON document:

```json
{
  "body": {
    "version": "0.2.0",
    "tarball_url": "https://api.mobaide.com/api/v1/agent/releases/a1b2.../mobaide-agent-0.2.0.tar.gz",
    "tarball_sha256": "a1b2...",
    "wheel_url": "https://api.mobaide.com/api/v1/agent/releases/c3d4.../mobaide_agent-0.2.0-py3-none-any.whl",
    "wheel_sha256": "c3d4...",
    "released_at": "2026-04-26T10:30:00+00:00",
    "min_central_server_version": "0.1.0"
  },
  "signature_b64": "<Ed25519 signature over canonical JSON of body>"
}
```

* **Signing** — Ed25519.
  * Canonical JSON encoding is
    `json.dumps(body, sort_keys=True, separators=(",", ":"))`.
  * `release_agent.py` signs with the local `agent/manifest-signing-key`.
  * The public half is configured on `central_server` as
    `MANIFEST_SIGNING_PUBLIC_KEY` and pinned on each agent host at
    `<prefix>/trust/central_server.pub`.
* **Publishing** — `release_agent.py`:
  1. uploads the tarball and the wheel to `central_server` (see
     [Release file hosting](#release-file-hosting));
  2. runs the **anonymous download gate**: a full GET, with retries, of every
     URL the signed manifest names, and a SHA-256 check of each body. It stops
     if any URL fails or serves other bytes;
  3. only then POSTs the record to `POST /api/internal/agent_manifest` on
     `central_server` with header `X-Release-Token: <agent/release-token>`.

  The route validates the signature **before** persisting; an invalid
  signature returns 400 and the manifest is not stored.
* **Distribution**
  * Agents receive the signed manifest inline in the `auth_ok` reply on the
    agent tunnel and in `agent_manifest_update` CH_CONTROL push frames
    (proto-v2), as `manifest_body` (the full signed dict) and
    `manifest_signature` (the base64 Ed25519 signature). No HTTP fetch is
    needed to apply an update.
  * After a successful `POST /api/internal/agent_manifest`, the router fans out
    an `agent_manifest_update` frame to every connected agent
    (`broadcast_manifest_update`), so running agents get it within one
    heartbeat without a reconnect.

### Release file hosting

`central_server` hosts the agent release files itself. Until 0.3.22 they lived
in GitHub Releases of `syrax90/mobaide-agent-releases`. Anonymous downloads
from there failed with HTTP 504 for long periods, for every repository, so the
update path no longer depends on GitHub.

| Endpoint | Access | Purpose |
|---|---|---|
| `PUT /api/internal/agent_release/{sha256}/{filename}` | `X-Release-Token` | Upload one file. The body is the raw bytes. |
| `GET` or `HEAD /api/v1/agent/releases/{sha256}/{filename}` | Anonymous | Download one file. Range requests work. |

* **Content-addressed.** The URL holds the SHA-256 of the file. The server
  rejects an upload whose body has another digest (HTTP 400). A second upload
  of the same file returns `"created": false` and changes nothing.
* **Cache-safe.** A file response carries
  `Cache-Control: public, max-age=31536000, immutable`, so the Cloudflare edge
  can keep it. A 404 carries `no-store`, so a miss is never cached.
* **Storage.** Files live in `AGENT_RELEASE_DIR` (default
  `data/agent_releases`, inside the `central-server-data` volume in
  production). A release adds about 0.8 MB (tarball + wheel). Each upload
  enforces these limits:
  * at most 20 files and 128 MiB in total. The newest files stay, and so do the
    files of the live manifest. This leaves room to roll back about 10
    releases;
  * at most 16 MiB for one file (HTTP 413);
  * at least 1 GiB of free disk after the write, or the upload fails with
    HTTP 507 and writes nothing;
  * temp files from a killed upload are deleted after 1 hour.

  Only the release token can write. Anonymous downloads never write to disk.
* **Clients.** Agents and the install scripts download whatever URL the signed
  manifest names, and check the SHA-256. They need no change, and older agents
  work too.

If a previous release left a stale manifest record on `central_server`
that points at unreachable URLs, clear it by deleting the file at the
configured `MANIFEST_STORE_PATH` on the central_server host (default
`data/agent_manifest.json`). The endpoint then returns 404 ("no manifest
published yet") until the next successful `release_agent.py` run.

## Auto-update on the agent (staged-binary swap)

The agent updates itself in place using a **staged-binary** pattern: every
release lives in its own directory and a single pointer file selects the
"current" one. There is no in-process patching.

**HTTP wrapper.** All outbound HTTP calls (manifest polling, tarball/wheel
downloads, trust-key fetch) go through `mobaide._http`. It sends
`User-Agent: mobaide-agent/<version>` (so Cloudflare does not classify it as
generic `Python-urllib` bot traffic, error 1010) and pins TLS trust to the
bundled `certifi` store, so HTTPS fetches verify regardless of the host's
OpenSSL CA store (an operator-set `SSL_CERT_FILE` still takes precedence on the
launcher's `wss://` path).

### Install layout

```
<prefix>/                            # /opt/mobaide, /usr/local/mobaide, or %ProgramFiles%\MobaideAgent
├── current_version                  # text file: "0.2.0"
├── trust/
│   └── central_server.pub           # base64 Ed25519 public key, pinned at install time
├── mobaide_agent/
│   └── .env                         # JWT_SECRET_KEY, RELAY_*, etc. (generated by init-env)
├── releases/
│   ├── 0.1.0/
│   │   ├── .installed              # completion sentinel — release is runnable
│   │   └── venv/                   # python venv with the wheel installed
│   └── 0.2.0/
│       ├── .installed
│       └── venv/
└── bin/                             # launcher wrapper; Windows mobaide-agent.cmd
```

The `mobaide-agent` command the installers put on PATH
(`/usr/local/bin/mobaide-agent` on Linux/macOS, `bin\mobaide-agent.cmd` on
Windows) reads `current_version` on every call, so an update or rollback never
strands it on a pruned release.

**Complete releases.** A release is **complete/runnable** only once a
`.installed` marker file exists in `releases/<v>/`, written as the last install
step (after pip + the wheel). Releases that predate the sentinel count as
complete if their `mobaide-agent` console script exists.
`mobaide.agent.install_paths.is_release_complete` is canonical; the updater,
launcher rollback/prune, and the install.sh / Windows supervisor self-heal share
it, so a half-built release is never selected or booted.

**Service unit and self-heal.** The service unit (`systemd`, `launchd`, Windows
Scheduled Task) does **not** invoke a release directly. On Linux/macOS its entry
point is the generated `<prefix>/bin/mobaide-agent-launcher.sh` wrapper (written
by `write_launcher_script` in `install.sh`); on Windows it is the stdlib-only
`windows_launcher.py` supervisor.

The wrapper/supervisor first **self-heals** a missing, corrupt, or
**incomplete** current-release venv:

- If `<prefix>/releases/<current_version>` has no runnable interpreter or lacks
  the completion sentinel, it rolls `current_version` *forward* to the newest
  complete sibling release (skipping `.staging-*` / `.old-*` scratch and any
  non-version dir) before exec. This turns a permanent crash loop into
  automatic rollback to the last good release.
- With no complete sibling at all it exits 70 rather than looping.

It then execs
`releases/<version>/venv/bin/python -m mobaide.launcher`, which:

1. Reads `<prefix>/current_version`.
2. Appends the boot attempt to `<prefix>/.boot_journal` (timestamp + version).
3. If the current version has been booted **3 times within 60s**, picks the
   highest-versioned sibling release from `<prefix>/releases/` (skipping any
   that lacks a usable `venv`), atomically rewrites `current_version` to that
   target, and logs `ERROR — version <X> crashed N+ times…; rolling back to
   <Y>`. With no eligible sibling, it logs the rollback miss and continues
   into the failing release; the supervisor's hard cap finally stops the loop.
4. Garbage-collects superseded release directories, keeping the current version
   plus the rollback target (`MOBAIDE_AGENT_KEEP_RELEASES`; see
   **Rollback target** below). Cleanup is
   best-effort — a failed deletion is logged and retried on the next boot, and
   never blocks startup.
5. `os.execv`s into `<prefix>/releases/<version>/venv/bin/python -m mobaide run`.

A swap of `current_version` followed by a process restart is all that rolling
forward (or back) needs. The launcher's rollback gives a deployed install one
automatic recovery step before operator intervention is needed. Matching
supervisor settings give the rolled-back release time to prove stable:

- systemd: `StartLimitBurst=10`/`StartLimitIntervalSec=600`
- launchd: `ThrottleInterval=10`
- Windows Task Scheduler: `-RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)`

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
                               |  Stage release  |  build --without-pip venv in
                               |   (sidecar)     |  .staging-<v>-<pid>/, then
                               |                 |  os.replace -> releases/<v>/
                               +-----------------+
                                       |
                                       v
                               +-----------------+
                               |  Finalize +     |  pip + wheel via FINAL-path
                               |  sentinel       |  python, then write .installed
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
* **Non-destructive staging (shebang-safe)**
  * The venv is built with `python -m venv --without-pip` in a sibling scratch
    dir `releases/.staging-<v>-<pid>`, then one `os.replace` swaps it into
    `releases/<v>`, and pip + the wheel are installed with the **final-path**
    interpreter so every `pip` / `mobaide-agent` shebang points at the real
    location.
  * The `.installed` sentinel is written only after that finalize step, and
    `current_version` is flipped only after the sentinel, so a half-built
    release is never selected.
  * A failed stage never touches the running release. Leftover `.staging-*` /
    `.old-*` scratch and stale incomplete releases are garbage-collected
    best-effort on the next run.
* **Forward-only on the auto path**
  * Auto/poll updates only stage a manifest whose version is **strictly
    newer** than the running one, so a stale or mismatched manifest can never
    downgrade an install.
  * The forced paths — operator "Install now" and 4002 protocol-incompatible
    recovery — bypass this and apply any version.
* **Path-component validation** — the signed manifest `version` is checked
  against a safe-path allowlist at the manifest→filesystem boundary (in both
  `updater.py` and `install.sh`) before it is used as a `releases/<v>` path
  component.
* **Manager-release guard**
  * The in-process auto-updater is disabled when the agent runs from a
    developer/source checkout (the interpreter is not under
    `<prefix>/releases`), so a source run that shares the install prefix can
    no longer drive an update that overwrites the installed release.
  * Relay manifests are still recorded but never applied (fail-closed).
  * The explicit `mobaide-agent update` CLI verb is an operator opt-in that
    deliberately bypasses this guard.
* **Activity awareness** — defers the swap while any session is running, or
  while voice dictation is open or still being transcribed, or a transcript
  cleanup is running. Deferral retries
  after 30 minutes; a single pending update deferred for >24h logs a `WARN`.
* **Supervisor restart** — the running agent calls
  `mobaide.agent.restart.request_restart`. It stops the uvicorn server
  gracefully, and the process then exits with code `75`. A 60 s watchdog
  hard-exits with `75` if the shutdown hangs. `RestartForceExitStatus=75`
  (systemd), launchd `KeepAlive`, and the Windows `mobaide_launcher.py`
  supervisor's exit-code-75 respawn loop restart it into the freshly-pinned
  release.
  * The exit code must be exactly `75` on Windows. The supervisor respawns
    only on `75`, and Task Scheduler does not restart a task whose process
    exited by itself, whatever the code.
  * Do not call `sys.exit(75)` in the running agent. During lifespan startup
    (the HTTPS bootstrap below), uvicorn turns it into exit code `3`. In a
    FastAPI background task (the "Install now" route), uvicorn logs it and
    keeps serving. Both left a Windows agent stopped until the next logon.
  * The `mobaide-agent update` CLI verb has no server, so it still uses
    `sys.exit`.
* **Rollback target** — on every boot, `_prune_old_releases` keeps the current
  version plus up to `MOBAIDE_AGENT_KEEP_RELEASES - 1` highest-versioned
  **complete** siblings (default: one previous release). Dirs that are
  incomplete, lack a usable venv, or whose name is not a parseable version are
  never selected. Set `MOBAIDE_AGENT_KEEP_RELEASES=<n>` (floor `2`) to retain
  more rollback targets.

### HTTPS bootstrap (side-channel update path)

The in-band manifest delivery (`auth_ok` + `agent_manifest_update`) is the
fast path. It cannot reach an agent whose `protocol_version` is below the
server's `SUPPORTED_PROTOCOL_VERSIONS`: the relay closes with code 4002 before
the pointer is sent, so the binary that would lift the agent into the new
protocol cannot be delivered in-band.

The HTTPS bootstrap breaks that loop. The signed manifest is also served
unauthenticated over HTTPS at `GET /api/v1/agent/manifest.json`.

- The agent fetches it via `mobaide.agent.update_check.run_https_update_check`,
  which verifies through the **same** `StagedBinaryUpdater` the relay path
  uses — no second crypto stack, no skip-signature branch.
- The URL is derived from `RELAY_SERVER_URL` in the agent's `.env` (same
  hardening as the trust-key fetch: no user-info, no cleartext by default).

The bootstrap fires on three triggers:

1. **Pre-connect** — the agent's lifespan runs the HTTPS check **before** the
   first relay connect. An outdated agent self-heals during cold boot; the relay
   client is not started on this run, and the supervisor respawns into the
   freshly-staged release via the same exit-75 path the in-band updater uses.
2. **Protocol-incompatible recovery (close-code 4002)** — the relay client's
   connect loop fires a `protocol_incompatible` callback that re-runs the HTTPS
   check with `force=True`. It prefers the `manifest_url` hint from the
   rejection payload over the env-derived URL but treats it as informational
   only — the signature is the trust anchor either way.
3. **Periodic poll** — a 60-minute background task (`run_periodic_check_loop`)
   against the env-derived URL catches manifests the relay broadcast missed
   (queue saturation, restart races). Failures are logged and the loop
   continues.

The bootstrap is gated by the same trust key as the relay path — an
install without `<prefix>/trust/central_server.pub` runs with the
auto-updater disabled, and both channels stay silent (fail-closed).

**Policy.** When bumping `PROTOCOL_VERSION`, confirm that every deployed
agent in the support window already speaks the HTTPS bootstrap (every
release at or after `agent-v0.2.5`). An agent that pre-dates the bootstrap
is stranded by any incompatible bump — its only recovery is a manual
re-install. See [PROTOCOL.md → How to bump](../PROTOCOL.md) for the
release-checklist item.

### Forced update (operator escape hatch)

```bash
MOBAIDE_AGENT_MANIFEST_URL=https://api.mobaide.com/api/v1/agent/manifest.json \
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

Linux (requires sudo to write `/etc/systemd/system/` and resolve `$SUDO_USER`):

```bash
curl -fsSL https://get.mobaide.com/install.sh | sudo bash
```

macOS (runs as the invoking user; no sudo required):

```bash
curl -fsSL https://get.mobaide.com/install.sh | bash
```

Windows (self-elevates via UAC if launched from a normal PowerShell — no need to "Run as Administrator" first):

```powershell
irm https://get.mobaide.com/install.ps1 | iex
```

Both scripts are auto-published from `deploy/` to the root of the
`gh-pages` branch (the actual GitHub Pages source for
`get.mobaide.com`) by `publish-gh-pages.yml` — see
[release-publishing.md](release-publishing.md) for the runbook. They:

1. Detect the OS and locate / create `<prefix>` (`/opt/mobaide`,
   `/usr/local/mobaide`, or `%ProgramFiles%\MobaideAgent`).
2. Pull the manifest from
   `https://api.mobaide.com/api/v1/agent/manifest.json` (override via
   `MOBAIDE_AGENT_MANIFEST_URL`).
3. Bootstrap the manifest-signing public key and pin it at
   `<prefix>/trust/central_server.pub`: `MOBAIDE_AGENT_TRUST_KEY_B64` if set,
   otherwise fetch it from `/api/v1/agent/trust_key` on the host derived from
   `MOBAIDE_AGENT_MANIFEST_URL`. Then verify the manifest signature against the
   pinned key. The install fails-closed if neither path yields a key — never
   silently ships an install whose auto-updater can't verify rollouts.
4. **(Linux only, before venv creation)** Verify that `python3 -c 'import ensurepip'`
   succeeds. If it does not (common on stock Debian/Ubuntu), auto-install
   `python${version}-venv` via the detected package manager (`apt-get`,
   `dnf`/`yum`, `pacman`, `zypper`, or `apk`). Non-root user-prefix installs
   print a distro-aware error instead of attempting a privileged install.

**4a. (Windows only, before venv creation)** Auto-elevate via UAC if the
current PowerShell is not elevated — required for the Scheduled Task
registration and `%ProgramFiles%\MobaideAgent` writes. If no Python 3.12+ is on
PATH, auto-install it at machine scope: `winget install Python.Python.3.12`,
else the official python.org installer (Authenticode-verified against the
Python Software Foundation signer before execution); a manual-download message
only when both fail. Set `MOBAIDE_AGENT_SKIP_PYTHON_INSTALL=1` to opt out and
require a pre-installed interpreter (CI / locked-down hosts). Under `irm | iex`
the elevation re-launch re-fetches the installer from
`MOBAIDE_AGENT_INSTALL_URL`.

5. Download tarball + wheel, verify sha256s, extract under
   `<prefix>/releases/<version>/`, install the wheel into a fresh venv.
   On Windows, reinstalling the installed version first stops the agent and every
   process running from the prefix (Windows cannot move a release dir whose
   executables are running); the previous release is restarted if staging then
   fails. Service registration (step 8) stops leftover prefix processes too.
6. Atomically pin `<prefix>/current_version`.
7. Generate `<prefix>/mobaide_agent/.env` via `python -m mobaide.agent.config init-env` — writes a random `JWT_SECRET_KEY` and prefills `RELAY_SERVER_URL=wss://mobaide.com`.
   - On Windows, the path the pre-elevation `Find-ClaudeBinary` helper located
     for `claude.cmd` is forwarded via `MOBAIDE_AGENT_CLAUDE_BIN_HINT` and
     written as `MOBAIDE_CLAUDE_BIN`. The optional Codex and Antigravity CLIs
     work the same way (`Find-CodexBinary` → `MOBAIDE_AGENT_CODEX_BIN_HINT` →
     `MOBAIDE_CODEX_BIN`; `Find-AgyBinary` → `MOBAIDE_AGENT_AGY_BIN_HINT` →
     `MOBAIDE_AGY_BIN`); a miss is silent.
   - The optional GitHub Copilot CLI: `Find-CopilotBinary` →
     `MOBAIDE_AGENT_COPILOT_BIN_HINT` → `MOBAIDE_COPILOT_BIN`. The installer
     writes this key with `set-env` after `init-env`, not with an `init-env`
     argument, so it also works with an agent release that is older than
     Copilot support.
   - Re-running never overwrites an already-paired `.env` (pass `--force` to
     regenerate; paired credentials are preserved regardless).
8. Register the service:
   * **Linux** — resolve the runtime user from `$SUDO_USER` (or
     `MOBAIDE_RUN_AS_USER`), substitute `__RUN_USER__`/`__RUN_GROUP__`/`__RUN_HOME__`
     placeholders in the service template, write `/etc/systemd/system/mobaide-agent.service`,
     `daemon-reload`, `enable`, `restart`. The unit runs as the invoking
     user (not a `mobaide` system account), uses `ProtectSystem=strict` with
     `ReadWritePaths=$HOME /opt/mobaide`, and sets `RestartForceExitStatus=75`.
   * **macOS** — resolve the runtime user (`MOBAIDE_RUN_AS_USER` →
     `$SUDO_USER` → console owner), `chown` the prefix to that user, write
     `~/Library/LaunchAgents/com.mobaide.agent.plist`, and load it into the
     user's GUI domain via `launchctl bootstrap gui/<uid>` (run with
     `launchctl asuser` from root), falling back to a legacy `launchctl load`
     on headless hosts. The agent runs as the GUI user, not root.
   * **Windows** — download `windows_launcher.py` from
     `https://get.mobaide.com/windows_launcher.py` (override via
     `MOBAIDE_AGENT_WINDOWS_LAUNCHER_URL`) into
     `<prefix>\svc\mobaide_launcher.py`, then register a per-user
     Scheduled Task named `MobaideAgent` pinned to `$env:USERNAME` with
     `-LogonType Interactive` and `-AtLogOn`. The supervisor handles
     updater exit code 75 with an immediate in-process respawn;
     Task Scheduler's outer `-RestartCount` / `-RestartInterval`
     catches supervisor-level failure.

Re-running an install script is idempotent: a higher-versioned manifest stages
into a sibling `releases/<version>/` and re-pins the pointer, so the same
one-liner is also the operator-driven upgrade path.

### Trust-key bootstrap

The first install needs the public key that signs every subsequent manifest.
Three resolution paths, in priority order:

1. **Auto-fetch from `central_server` (default):** the install scripts derive
   `https://<host>/api/v1/agent/trust_key` from the manifest URL host (default
   `https://api.mobaide.com`) and pin the returned key to
   `<prefix>/trust/central_server.pub`. No operator-side env vars needed. The
   endpoint requires `MANIFEST_SIGNING_PUBLIC_KEY` to be set on the
   central_server (already required for manifest publishing).
2. **Operator-pinned via env var (airgap / explicit):**
   ```bash
   MOBAIDE_AGENT_TRUST_KEY_B64=<base64> \
     curl -fsSL https://get.mobaide.com/install.sh | sudo bash
   ```
   Skips the central_server fetch. Use this when the install host has no
   network path to your central_server, or for maximum trust isolation between
   the install channel and the trust root.
3. **Repair existing install:** if you have an agent that was set up before
   path #1 existed (or pre-`MOBAIDE_AGENT_TRUST_KEY_B64`), run
   `mobaide-agent fetch-trust-key` on the agent host. The CLI:
   - derives `https://<host>/api/v1/agent/trust_key` from `RELAY_SERVER_URL` in
     the agent's `.env`,
   - validates the result as base64-Ed25519,
   - writes `<prefix>/trust/central_server.pub`,
   - and best-effort restarts the supervisor so the running agent picks up the
     new updater.

   Options:
   - `--from-url <full-url>` overrides the env-derivation.
   - `--no-restart` skips the supervisor restart when you'd rather schedule it
     yourself.

**Fail-closed semantics:** if neither the env var nor the central_server
fetch yields a key, the install script aborts with an actionable error.
There is no "skip signature verification" branch.

**Hardening on the fetch path:**

* User-info in the manifest URL (`wss://api.mobaide.com@evil.example/...`)
  is rejected up front, because `urlparse`'s `netloc` would otherwise smuggle
  the attacker host into the trust-key URL. Both the install scripts and
  `mobaide-agent fetch-trust-key` enforce this.
* Cleartext `ws://` / `http://` is refused by default, since a passive on-path
  attacker on the install host's LAN could substitute the public key. Set
  `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1` to override (only safe on a
  trusted local network — e.g. a self-host central_server reachable only over
  loopback / a VPN). The default `https://` and `wss://` paths are unaffected.
* The fetched value is validated as a 32-byte Ed25519 public key before any
  file lands on disk, with the same validator applied to env-var-pinned values,
  so a malformed key fails immediately.

## Android update flows

The Android client has four distinct update paths that work together:

* **Pre-flight version gate** — `MainActivity.onResume` triggers
  `VersionGateRepository.probe()`. On `Incompatible`, the navigation graph is
  replaced with `IncompatibleServerScreen` until the user installs a new app
  build. Tunnel close code `4002` reaches the same screen via the
  `onIncompatibleServer` callback, in case the gate races a server upgrade.
* **Flexible Play in-app update** — `InAppUpdateManager.checkForUpdate()` runs
  every `Activity.onResume`, except within 60 s of a Play in-app review card
  launch (see [in-app-review.md](in-app-review.md)). When Play Core reports an
  update and the build allows the flexible flow, the user gets a non-blocking
  download/install prompt. Failures are silent.
* **Immediate Play in-app update** — `InAppUpdateManager.requestImmediateUpdate()`
  is reserved for recovery from `IncompatibleServerScreen` and runs
  automatically on screen entry. Sideloaded builds silently no-op and the user
  falls back to the screen's Play Store deep-link button. The server-side gate
  stays authoritative: if Play reports nothing, the user stays on the upgrade
  screen.
* **Agent-outdated banner** — `AgentVersionMonitor` fetches `GET /api/version`
  from the authenticated agent (proxied through `central_server`) and compares
  `server_version` (running agent) with `latest_agent_version` (manifest
  pointer the agent received over its relay). Mismatch → `SessionListScreen`
  renders `AgentOutdatedBanner`. Dismissing it only hides the prompt; the
  agent's staged-binary updater still installs silently once idle. The
  banner's "Install now" CTA hits `POST /api/agent/update/force_now` on the
  agent, which routes through `StagedBinaryUpdater.force_update_now` to skip
  the idle deferral.

## Protocol-integrity CI gate

`protocol-integrity.yml` runs on every push and PR to `main`:

1. Runs `tests/test_protocol_integrity.py` under pytest: (a) asserts that the
   two `envelope.py` copies agree on `PROTOCOL_VERSION`,
   `MIN_CLIENT_PROTOCOL_VERSION`, `SUPPORTED_PROTOCOL_VERSIONS`,
   `PROTOCOL_INCOMPATIBLE_CLOSE_CODE`, and the channel-type constants;
   (b) compares the live wire shape against `tests/protocol_golden.json`. Any
   handshake-shape change **must** update the golden file in the same commit.
2. **Client constants check:** asserts that `APP_PROTOCOL_VERSION` in
   `ios/Config/Protocol.xcconfig` and in `web/src/api/bootstrap.ts` equals the
   `buildConfigField` value in `android/app/build.gradle.kts`, so a proto bump
   that misses `ios/` or `web/` fails here. `MIN_SERVER_PROTOCOL_VERSION` stays
   an iOS↔Android comparison; the web version gate has no client-side floor.
3. **Diff guard (PR-only):** a PR that modifies one of the two `envelope.py`
   files must modify the other.
4. **Web protocol fixtures** (separate `web-protocol-fixtures` job, Node 22):
   `npm ci` + `npm run test:protocol` in `web/` runs the TypeScript arm of the
   `tests/protocol_fixtures/` goldens (the same files the iOS tests read) and
   fails when a new fixture file is neither asserted by a web test nor assigned
   an owning milestone.

   The other two arms are CI-gated too: `ios-build.yml` runs the Swift suite
   on PRs that touch `ios/` or `tests/protocol_fixtures/`, and the `android`
   job of `unit-tests.yml` runs the Kotlin suite (including `keys/key_chords.json`)
   on every PR. This job is still the only arm that runs on every PR with no
   paths filter, so keep the load-bearing assertion in the TypeScript arm.

This is the safety net that lets the two component packages duplicate
`envelope.py` (a deliberate constraint — the two packages are independent
deployables and cannot share runtime code) without going out of sync.

## End-to-end auto-update verification

`tests/test_e2e_autoupdate.py` runs the release pipeline in one in-process
pytest run: a signed synthetic agent v0.2.0 manifest is POSTed to
`/api/internal/agent_manifest`, served at `GET /api/v1/agent/manifest.json`, and
applied by `StagedBinaryUpdater.consider_manifest()` (stubbed installer), which
stages the release and atomically swaps `current_version`. A corrupted tarball
sha256 makes the updater return `signature_failed` and leave `current_version`
untouched (fail-closed); an invalid `X-Release-Token` returns 401 without
persisting.

`.github/workflows/e2e-update.yml` runs it on every PR and on push to `main`
(5-minute timeout). Real supervisor behaviour (systemd / launchd / Scheduled
Task) is not exercised here; see `mobaide_agent/tests/test_launcher_rollback.py`
and the install scripts' own integration tests.

## See also

* [PROTOCOL.md](../PROTOCOL.md) — wire-contract version history and the
  step-by-step procedure for bumping `protocol_version`.
* [docs/deployment.md](deployment.md) — full deployment guide for both the
  central server and per-machine agents (legacy from-source layout, new
  versioned `releases/<v>/` layout, troubleshooting checklist).
* [docs/release-publishing.md](release-publishing.md) — install-script
  publishing pipeline (`deploy/` → `gh-pages` → `get.mobaide.com`) and
  the manual gh-pages sync runbook.
* `android/CHANGELOG.md`, `mobaide_agent/CHANGELOG.md`,
  `central_server/CHANGELOG.md` — per-tag release notes.
