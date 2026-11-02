# Versioning & Auto-Update

This document is the single reference for how Mobaide versions its four
components, ships releases, and keeps deployed installations up to date.

The system has **two orthogonal version concepts** and you should not conflate
them:

| Concept | Type | Purpose |
|--------|------|---------|
| Component semver (`android-vX.Y.Z`, `agent-vX.Y.Z`, `server-vX.Y.Z`, `web-vX.Y.Z`) | Independent semver per component | Human-facing release identity. Ships on its own cadence. **Does not** gate compatibility. |
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

Releases are driven by git tags pushed to the repo. Each prefix triggers a
dedicated workflow under `.github/workflows/`:

| Tag prefix | Component | Workflow |
|------------|-----------|----------|
| `android-vX.Y.Z` | Android app | `release-android.yml` |
| `agent-vX.Y.Z` | `mobaide_agent` | `release-agent.yml` |
| `server-vX.Y.Z` | `central_server` | `release-server.yml` |
| `web-vX.Y.Z` | Web client (`web/`) | _(none yet)_ |
| `proto-vN` | Wire contract | _(none)_ |

### What each tag produces

**`android-vX.Y.Z`**

- Signed `.aab`, uploaded to Play Console internal track.
- `inAppUpdatePriority` is derived from the `android/CHANGELOG.md` section
  release-please just generated for this tag (rules under
  [Cutting an Android minor or major](#cutting-an-android-minor-or-major)),
  **not** from the tagged commit subject: the tag sits on release-please's
  `chore: release main` commit, which is never a `feat`/`fix`.

**`agent-vX.Y.Z`**

Builds a `uv build` wheel + source tarball and an **Ed25519-signed manifest**,
then:

1. Publishes a GitHub Release **into the public sibling repo `$RELEASES_REPO`**
   (the source repo is private, so anonymous installs cannot reach release URLs
   there).
2. Runs an anonymous reachability gate over every manifest URL (details under
   [Agent rollout manifest](#agent-rollout-manifest)).
3. POSTs the manifest to `central_server` (with curl `--retry 5`).
4. Verifies that the served manifest matches.

**`server-vX.Y.Z`**

- Docker image pushed to `ghcr.io/<owner>/<repo>-server:vX.Y.Z` (and `:latest`).
  No GitHub Release object is created on this repo — the GHCR image is the
  only artifact.
- A second `deploy` job gates the rollout via `environment: production`
  (manual approval required).
- Host-side rollout is operator-driven: run `deploy/prod/deploy_central.py` or
  `docker compose pull && up -d` against the prod compose file.

**`web-vX.Y.Z`** — nothing yet

- The tag only versions the `web/` workspace (`release-type: node`); no workflow
  fires on tag push.
- The SPA ships with the central-server deploy (same-origin Caddy serving,
  tracked in WEB-02).

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
| Anything else (CI, docs, refactor) | `chore: …`, `docs: …`, `refactor: …` — no release fires |

**How far the version moves is not the same for every component.** Every
automatic bump in this repo is currently a *patch*, but for two different
reasons, and only one of them is permanent:

| Component | `fix` | `feat` | `feat!` | Why |
|-----------|-------|--------|---------|-----|
| Android | patch | patch | patch | `"versioning": "always-bump-patch"` on the `android` package. **Permanent.** |
| agent, server, web | patch | patch | minor | Root-level `bump-minor-pre-major` + `bump-patch-for-minor-pre-major`. Pre-`1.0.0` only. |

- **Android.** release-please's `AlwaysBumpPatch` ignores the commit type
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
     annotated with `// x-release-please-version`), or `web/package.json` for
     web,
   - the component's `CHANGELOG.md` (prepends a new `## [vX.Y.Z]`
     section).
3. Merging that release-PR pushes the corresponding `agent-vX.Y.Z` /
   `server-vX.Y.Z` / `android-vX.Y.Z` / `web-vX.Y.Z` tag, which fires
   the matching `release-*.yml` workflow (no such workflow exists for
   `web-v*` yet — see the tag table above).

**Release-object side effect of step 3:** release-please-action v4 also
creates an empty GitHub Release on this repo with the tag (see
release-please-action issue #1034). A follow-up step in `release-please.yml`
deletes it and keeps the tag, so the Releases tab here stays empty for
`agent-v*` / `server-v*` / `android-v*` / `web-v*`. Artifacts live elsewhere:
agent in the public `mobaide-agent-releases` sibling repo; server in GHCR;
android in Play Console. A Release that appears briefly and vanishes is that
cleanup step, not a bug.

### Manual release path (operator escape hatch)

Use this when the conventional-commits flow is the wrong tool:

- release-please opened no release-PR (the merged PR's title was a
  non-release type such as `chore:` / `docs:` / `refactor:`, so no
  version-bearing commit reached `main`);
- you want to ship a hotfix release-please can't represent; or
- you've already hand-bumped the version files and want to ship those exact
  bytes.

Currently wired for `mobaide_agent` only via
`.github/workflows/release-agent-manual.yml`. The same shape clones
to `central_server` / `android` when needed.

1. **On a branch**, edit the version files in lockstep:
   - `mobaide_agent/pyproject.toml` — bump `[project] version`.
   - `.release-please-manifest.json` — bump the `mobaide_agent`
     entry to the same version.
   - `mobaide_agent/CHANGELOG.md` — replace `## [Unreleased]` with
     `## [X.Y.Z] - YYYY-MM-DD` and fill in the entries.
2. **Open and merge a PR** to `main`. Commit grammar does not matter for *this*
   path: the manual workflow trigger replaces release-please.
3. **Run the manual workflow:** Actions → **Release agent (manual)**
   → Run workflow → enter the version (e.g. `0.1.2`). The workflow
   validates that `pyproject.toml`, the manifest, and the tag state
   all agree, then creates and pushes `agent-vX.Y.Z`. The downstream
   `release-agent.yml` fires on the tag push exactly as on the release-please
   path.

The validation hard-fails if `pyproject.toml` and the manifest don't already
agree at the requested version, or if the tag already exists, so a half-and-half
state (manifest at one version, tag at another) is not reachable from this path.

**Local fallback.** If you'd rather not grant the workflow PAT scope or CI is
down, run `git tag agent-vX.Y.Z origin/main && git push origin agent-vX.Y.Z`
from a developer machine. A locally-pushed tag does fire `release-agent.yml`;
the `GITHUB_TOKEN` caveat below only applies to tags GHA itself pushes.

> [!WARNING]
> **Pick one path and stay on it.** Either let release-please own the
> bump (the conventional-commits flow above), **or** use the manual
> release path end-to-end (hand-edit the version files, then trigger
> the manual workflow / push the tag yourself).

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
   it tags `android-v3.7.0` and `release-android.yml` ships the bundle as usual.

The footer is scoped by path like every other commit: it only reaches the
`android` package if the commit touches `android/**`. If you have nothing to
ship alongside the bump, land a `chore(android): …` PR touching an Android file
and put the footer on that. Editing the open release-PR by hand before merging
is the other supported path.

**A breaking change still ships as a patch, and that is fine.**
`release-android.yml` derives `inAppUpdatePriority` from the changelog section,
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

#### Tag-trigger chaining and the release token

**Caveat — tag-trigger chaining.** GitHub's default `GITHUB_TOKEN` cannot
trigger downstream workflows from a tag it pushes (a hard GitHub Actions limit,
not a release-please bug). The release-please workflow's `token` input falls
back to `GITHUB_TOKEN` if `RELEASE_PLEASE_TOKEN` is unset. In that mode the
auto-tag is created but **no `release-*` workflow fires**, leaving the wheel/aab
unbuilt. Set `RELEASE_PLEASE_TOKEN` to make tag-trigger chaining work.

**Recommended PAT shape.** Use a **fine-grained PAT** scoped to **this
repository only** with these permissions:

| Permission | Access |
|-----------|--------|
| Contents | Read and write |
| Pull requests | Read and write |
| Workflows | Read and write |

Do **not** use a classic PAT — those grant write access to every repo
the user owns and are unnecessarily broad. `Workflows: write` is what lets
release-please's tag push trigger the downstream `release-agent.yml` /
`release-server.yml` / `release-android.yml` workflows; without it, the tag is
created but no workflow fires.

#### Required CI secrets and variables

Required CI secrets:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | `release-android.yml` | Signs the `.aab` with the upload key. |
| `PLAY_SERVICE_ACCOUNT_JSON` | `release-android.yml` | Service account used to upload the signed bundle to the Play Console internal track. |
| `AGENT_MANIFEST_PRIVATE_KEY` | `release-agent.yml` | Base64 Ed25519 private key. Signs the manifest body. |
| `CENTRAL_SERVER_RELEASE_TOKEN` | `release-agent.yml` | Bearer token for `POST /api/internal/agent_manifest`. |
| `CENTRAL_SERVER_URL` | `release-agent.yml` | Base URL of the production `central_server` that hosts the manifest. |
| `RELEASES_REPO_TOKEN` | `release-agent.yml` | Fine-grained PAT scoped to the **public releases repo** (e.g. `syrax90/mobaide-agent-releases`) with `Contents: Read and write`. Used by `softprops/action-gh-release@v2` to publish the release there. |
| `RELEASE_PLEASE_TOKEN` _(optional)_ | `release-please.yml`, `release-agent-manual.yml` | PAT with `contents:write` + `workflow` scopes. Required to chain CI-side tag pushes into the downstream `release-*` workflows; without it the operator must re-push the tag from a developer machine. |

Repo **variables** (not secrets — values appear in workflow logs and are not sensitive):

| Variable | Used by | Purpose |
|----------|---------|---------|
| `RELEASES_REPO` | `release-agent.yml` | `<owner>/<repo>` of the public releases repo (e.g. `syrax90/mobaide-agent-releases`). |

The workflow embeds `RELEASES_REPO` in the manifest's `tarball_url`/`wheel_url`
and pushes the `softprops/action-gh-release` step into this repo.

**Failure modes by which prereq is missing:**

* `AGENT_MANIFEST_PRIVATE_KEY` missing: `Build signed manifest`,
  `Create GitHub Release in public releases repo`, and the reachability gate all
  skip via `if: ${{ env.AGENT_MANIFEST_PRIVATE_KEY != '' }}`. No state changes
  anywhere.
* `RELEASES_REPO_TOKEN` or the `RELEASES_REPO` variable missing:
  `Create GitHub Release in public releases repo` fails loudly before any
  URL-bearing manifest can be posted to `central_server`.
* `CENTRAL_SERVER_*` missing: the public-repo release IS created, but
  `Publish manifest
  to central_server` and `Verify manifest is published` skip, and the prior
  manifest on `central_server` keeps serving.

#### Server-side counterparts

Two of the GitHub secrets above must be
mirrored as env vars on the production `central_server` host (typically
`/opt/mobaide/.env`). The two halves are matched byte-for-byte at request
time, so the names in CI and the names on the server intentionally
differ — keep them in sync as a pair:

| GitHub Actions secret | Server `.env` variable | Relationship |
|--|--|--|
| `CENTRAL_SERVER_RELEASE_TOKEN` | `RELEASE_AUTH_TOKEN` | Identical string. The workflow sends it in the `X-Release-Token` header; the server compares with `hmac.compare_digest`. |
| `AGENT_MANIFEST_PRIVATE_KEY` | `MANIFEST_SIGNING_PUBLIC_KEY` | Public/private halves of the same Ed25519 key. CI signs; the server verifies. |

- If `RELEASE_AUTH_TOKEN` is unset on the server,
  `POST /api/internal/agent_manifest` returns **HTTP 503** ("Manifest
  publishing is disabled") and the release workflow's curl step fails
  after `--retry 5` exhaustion.
- If `MANIFEST_SIGNING_PUBLIC_KEY` is unset, the server stores manifests
  without verification. Agents that have pinned a trust key will then reject
  the manifest at update time, so set both vars together.

### Server-side deploy gate

`release-server.yml` builds and pushes the Docker image to GHCR, then a
second `deploy` job runs in the `production` environment.

- Configure required reviewers under repo Settings → Environments → production
  so the job blocks on a manual click before recording the rollout.
- The job itself does not SSH to the host.
- Host-side rollout is operator-driven, in one of two ways:
  - `deploy/prod/deploy_central.py` (which preserves the previous image for
    one-step rollback);
  - for the polling pattern, a watchtower-style container on the prod host
    that pulls `ghcr.io/<repo>-server:latest` on a timer.

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
    "tarball_url": "https://github.com/syrax90/mobaide-agent-releases/releases/download/agent-v0.2.0/mobaide-agent-0.2.0.tar.gz",
    "tarball_sha256": "a1b2...",
    "wheel_url": "https://github.com/syrax90/mobaide-agent-releases/releases/download/agent-v0.2.0/mobaide_agent-0.2.0-py3-none-any.whl",
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
  * The release workflow signs with `AGENT_MANIFEST_PRIVATE_KEY`.
  * The public half is configured on `central_server` as
    `MANIFEST_SIGNING_PUBLIC_KEY` and pinned on each agent host at
    `<prefix>/trust/central_server.pub`.
* **Publishing** — `release-agent.yml`:
  1. creates a GitHub Release in the public sibling repo (`$RELEASES_REPO`)
     with the tarball, wheel, and `manifest.json` attached;
  2. runs the **anonymous reachability gate** — `curl -fsSL --retry 5 -r 0-0`
     (a 1-byte ranged GET) against every URL the signed manifest references —
     and refuses to proceed if any URL is not anonymously reachable (privacy
     mismatch, asset-name typo, missing upload);
  3. only then POSTs the record to `POST /api/internal/agent_manifest` on
     `central_server` with header
     `X-Release-Token: $CENTRAL_SERVER_RELEASE_TOKEN`.

  The route validates the signature **before** persisting; an invalid
  signature returns 400 and the manifest is not stored. The gate uses a ranged
  GET rather than HEAD because CDN redirect targets can reject HEAD with
  403/405; retries absorb CDN propagation lag.
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

### Releases repo bootstrap

The source repo (`syrax90/claude_mobile`) is private; anonymous installs
cannot reach a private repo's GitHub Release assets. Release artifacts
therefore live in a dedicated **public** sibling repo. One-time bootstrap:

1. **Create the public repo** (e.g. `syrax90/mobaide-agent-releases`),
   Visibility = Public. A one-line README is enough; only release artifacts
   live here.
2. **Mint a fine-grained PAT scoped to that repo only**, with
   `Contents: Read and write`. Save it on `syrax90/claude_mobile` as a
   secret named `RELEASES_REPO_TOKEN`.
3. **Add a repo variable** on `syrax90/claude_mobile` named
   `RELEASES_REPO` with value `<owner>/<public-releases-repo>` (a Variable,
   not a secret).
4. **Cut a release** as usual (push an `agent-v*` tag). The workflow signs the
   manifest with URLs that point at the public repo, pushes the GitHub Release
   there, runs the reachability gate against the public URLs, then publishes
   the manifest to `central_server`.

If a previous release left a stale manifest record on `central_server`
that points at unreachable URLs (e.g. the pre-bootstrap state), clear it by
deleting the file at the configured `MANIFEST_STORE_PATH` on the central_server
host (default `data/agent_manifest.json`). The endpoint then returns 404 ("no
manifest published yet") until the next successful release pipeline run.

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
  while voice dictation is open or still being transcribed. Deferral retries
  after 30 minutes; a single pending update deferred for >24h logs a `WARN`.
* **Supervisor restart** — the updater calls `sys.exit(75)`.
  `RestartForceExitStatus=75` (systemd), the Windows `mobaide_launcher.py`
  supervisor's exit-code-75 respawn loop, and Task Scheduler's outer
  `-RestartCount` / `-RestartInterval` all restart it into the freshly-pinned
  release.
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

   This is the **only** CI-enforced fixture arm. iOS runs its suite on push to
   `main` only, and no workflow runs Gradle unit tests, so Android's arm of
   `keys/key_chords.json` (WEB-06) is a local check. Anything shared that only
   Kotlin or Swift asserts can drift through a PR unnoticed; put the
   load-bearing assertion in the TypeScript arm.

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
