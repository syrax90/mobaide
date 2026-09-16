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

## Tag conventions

Releases are driven by git tags pushed to the repo. Each prefix triggers a
dedicated workflow under `.github/workflows/`:

| Tag prefix | Component | Workflow | What it produces |
|------------|-----------|----------|------------------|
| `android-vX.Y.Z` | Android app | `release-android.yml` | Signed `.aab`, uploaded to Play Console internal track. `inAppUpdatePriority` is derived from the `android/CHANGELOG.md` section release-please just generated for this tag (any breaking change → 5, else any `### Bug Fixes` → 3, else → 1). It is **not** read from the tagged commit subject — the tag sits on release-please's `chore: release main` commit, which is never a `feat`/`fix`. |
| `agent-vX.Y.Z` | `mobaide_agent` | `release-agent.yml` | `uv build` wheel + source tarball, **Ed25519-signed manifest**, GitHub Release **published into the public sibling repo `$RELEASES_REPO`** (the source repo is private, so anonymous installs cannot reach release URLs there), an anonymous reachability gate (1-byte ranged GET, with retries to absorb CDN-propagation lag) over every URL in the manifest, manifest POSTed to `central_server` (with curl `--retry 5`), then a verify-after step asserts the served manifest matches. |
| `server-vX.Y.Z` | `central_server` | `release-server.yml` | Docker image pushed to `ghcr.io/<owner>/<repo>-server:vX.Y.Z` (and `:latest`). No GitHub Release object is created on this repo — the GHCR image is the only artifact. A second `deploy` job gates the rollout via `environment: production` (manual approval required); host-side rollout is operator-driven (run `deploy/prod/deploy_central.py` or `docker compose pull && up -d` against the prod compose file). |
| `web-vX.Y.Z` | Web client (`web/`) | _(none yet)_ | Nothing yet — the tag versions the `web/` workspace (release-please bumps `web/package.json` and prepends `web/CHANGELOG.md`; `release-type: node`), but no workflow fires on tag push. The SPA ships with the central-server deploy (same-origin Caddy serving, tracked in WEB-02). |
| `proto-vN` | Wire contract | _(none)_ | Hand-pushed by a developer alongside the lockstep `PROTOCOL.md` + `envelope.py` bump. Serves as a changelog/anchor tag — no workflow fires on tag push and no Release object is created. Lockstep enforcement that **both** `mobaide_agent/.../envelope.py` AND `central_server/.../envelope.py` move together lives in `protocol-integrity.yml`, which runs on every PR and every push to `main`. |

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
| Android | patch | patch | patch | `"versioning": "always-bump-patch"` on the `android` package. release-please's `AlwaysBumpPatch` ignores the commit type entirely, breaking changes included. **Permanent** — minor and major are hand-cut, see [Cutting an Android minor or major](#cutting-an-android-minor-or-major). |
| agent, server, web | patch | patch | minor | Root-level `bump-minor-pre-major` + `bump-patch-for-minor-pre-major`. release-please gates both on `version.isPreMajor`, so this holds **only while the version is below `1.0.0`**. The day one of these ships `1.0.0` those flags go inert and it silently reverts to plain semver — `feat` → minor, `!` → major — with no config change. |

The repo enforces **squash-only merging** (merge commits and rebase merges
are disabled at the GitHub repo level): the squash commit **title is the PR
title** and the squash commit **body is blank**. So the PR title literally
becomes the *single* commit on `main` that release-please reads, and the
feature branch's own commit messages are **discarded at merge time** — they
never reach `main` and have no effect on the version bump or the changelog.
Breaking changes are signalled with `!` in the PR title (e.g.
`feat(agent)!: …`). That always earns the commit a `### ⚠ BREAKING CHANGES`
section in the changelog, but it does **not** force a major here — how far the
version actually moves is the bump table above.

This is enforced two ways: the repo merge settings (the source of truth is a
`gh api -X PATCH repos/<owner>/<repo> -f squash_merge_commit_title=PR_TITLE
-f squash_merge_commit_message=BLANK -F allow_merge_commit=false
-F allow_rebase_merge=false`, mirrored as policy-as-code in
`.github/settings.yml`), plus `pr-title-lint.yml`. There is no longer any
"but the title was right and nothing happened" failure mode tied to
merge-commit style, and no "the branch said feat but the merge said fix"
double-entry: exactly one commit per PR, one source of truth.

`pr-title-lint.yml` runs on every pull request and fails the check if
the title doesn't match the grammar above (or uses a stale scope like
`mobaide_agent` / `central_server` instead of the short `agent` /
`server`). A typo can no longer silently skip a release.

What happens after merge:

1. release-please sees the new conventional commit on `main`.
2. It opens (or updates) a release-PR for that component — bumps the
   per-component version in lockstep across:
   - `.release-please-manifest.json` (the manifest of record),
   - `pyproject.toml` for `mobaide_agent` / `central_server`,
     `android/app/build.gradle.kts` for android (the line annotated
     with `// x-release-please-version`; `versionCode` is derived from
     `versionName` at gradle build time so it never drifts), or
     `web/package.json` for web,
   - the component's `CHANGELOG.md` (prepends a new `## [vX.Y.Z]`
     section).
3. Merging that release-PR pushes the corresponding `agent-vX.Y.Z` /
   `server-vX.Y.Z` / `android-vX.Y.Z` / `web-vX.Y.Z` tag, which fires
   the matching `release-*.yml` workflow (no such workflow exists for
   `web-v*` yet — see the tag table above). release-please-action v4
   also creates an empty GitHub Release object on this repo as a
   side-effect of the tag push (its `manifest.createReleases()`
   couples tag and Release
   into one API call, and `skip-github-release: true` would silently
   skip both — see release-please-action issue #1034). A follow-up
   step in `release-please.yml` immediately deletes that Release
   while preserving the tag, so the Releases tab here stays empty for
   `agent-v*` / `server-v*` / `android-v*` / `web-v*`. The meaningful artifacts
   live elsewhere: agent in the public `mobaide-agent-releases`
   sibling repo; server in GHCR; android in Play Console. You may
   briefly see the Release appear and then vanish while
   `release-please.yml` runs — that is the cleanup step, not a bug.

### Manual release path (operator escape hatch)

Use this when the conventional-commits flow is the wrong tool:
release-please opened no release-PR (the merged PR's title was a
non-release type such as `chore:` / `docs:` / `refactor:`, so no
version-bearing commit reached `main`); you want to ship a hotfix
release-please can't represent; or you've already hand-bumped the
version files and want to ship those exact bytes. (With squash-only
merging now enforced, the old "squash was bypassed and the commit on
`main` is just `Merge pull request #N…`" failure mode can no longer
occur for normal PRs — every PR lands as a single squash commit
carrying the linted PR title.)

Currently wired for `mobaide_agent` only via
`.github/workflows/release-agent-manual.yml`. The same shape clones
to `central_server` / `android` when needed.

1. **On a branch**, edit the version files in lockstep:
   - `mobaide_agent/pyproject.toml` — bump `[project] version`.
   - `.release-please-manifest.json` — bump the `mobaide_agent`
     entry to the same version.
   - `mobaide_agent/CHANGELOG.md` — replace `## [Unreleased]` with
     `## [X.Y.Z] - YYYY-MM-DD` and fill in the entries.
2. **Open and merge a PR** to `main`. Commit grammar does not matter
   for *this* path — the manual workflow trigger replaces release-please
   here. The squash commit carries the PR title regardless; on the
   manual path release-please simply isn't the actor that tags.
3. **Run the manual workflow:** Actions → **Release agent (manual)**
   → Run workflow → enter the version (e.g. `0.1.2`). The workflow
   validates that `pyproject.toml`, the manifest, and the tag state
   all agree, then creates and pushes `agent-vX.Y.Z`. The downstream
   `release-agent.yml` fires on the tag push and ships the wheel
   exactly as it would on the release-please path.

The validation step hard-fails if `pyproject.toml` and the manifest
don't already agree at the requested version, or if the tag already
exists, so the half-and-half failure modes (manifest at one version,
tag at another) are not reachable from this path.

**Local fallback.** If you'd rather not grant the workflow PAT scope
or CI is down, the same effect is `git tag agent-vX.Y.Z origin/main &&
git push origin agent-vX.Y.Z` from a developer machine. A
locally-pushed tag does fire `release-agent.yml`; the
`GITHUB_TOKEN`-can't-trigger-downstream caveat below only applies
to tags GHA itself pushes.

> **Pick one path and stay on it.** Either let release-please own the
> bump (the conventional-commits flow above), **or** use the manual
> release path end-to-end (hand-edit the version files, then trigger
> the manual workflow / push the tag yourself). The half-and-half
> mode — hand-edit `pyproject.toml` and the manifest, then expect
> release-please to tag for you — is the failure mode that shipped
> `agent-v0.1.1` with a stale `0.1.0` wheel and silently dropped the
> `agent-v0.1.2` tag entirely (release-please saw no conventional
> commits to act on, so it didn't tag; nothing else watches the
> manifest). Mixing paths desyncs `.release-please-manifest.json`
> from either the tags or the `pyproject.toml` versions. If you need
> to ship without an upstream change, the manual release path is the
> supported way; editing the open release-PR before merging is the
> other supported way.

Android note: the `android` component is `release-type: simple`. Both
`.release-please-manifest.json` and `android/app/build.gradle.kts` are
bumped automatically — the `extra-files` entry in
`release-please-config.json` points the generic updater at
`build.gradle.kts`, where the `// x-release-please-version` annotation
identifies the `versionName` line. `versionCode` is computed inside
`build.gradle.kts` from the new `versionName`
(`major*10000 + minor*100 + patch`), so Play Store's
monotonic-versionCode requirement is satisfied without a second
manual edit.

#### Cutting an Android minor or major

The `android` package carries `"versioning": "always-bump-patch"`, so **no
PR title can move its minor or major**. Both are deliberate human acts.

The supported way is a `Release-As:` footer. release-please resolves it in
`strategies/base.ts#buildNewVersion` *before* it consults the versioning
strategy, so it overrides `always-bump-patch`:

1. Merge the Android PR as usual, but in GitHub's squash dialog **type a body**
   under the title (the repo defaults that body to blank, so it is empty until
   you fill it in):

   ```
   feat(android): whatever the PR title already says

   Release-As: 3.7.0
   ```

2. release-please opens the android release-PR at exactly `3.7.0`, with the
   normal changelog section and the usual `build.gradle.kts` / manifest
   rewrite. Merging it tags `android-v3.7.0` and `release-android.yml` ships
   the bundle as it always does.

The footer is scoped by path like every other commit — it only reaches the
`android` package if the commit touches `android/**`, so it cannot move the
agent, server, or web versions by accident. If you have nothing to ship
alongside the bump, land a `chore(android): …` PR touching an Android file and
put the footer on that.

Editing the open release-PR by hand before merging is the other supported path.
Do **not** hand-edit the manifest and then wait for release-please to tag —
that is the half-and-half mode the callout above warns about.

**A breaking change still ships as a patch, and that is fine.**
`release-android.yml` derives `inAppUpdatePriority` from the changelog section,
not from the version delta: any `### ⚠ BREAKING CHANGES` heading yields
priority 5 (immediate in-app update), a `### Bug Fixes` heading yields 3,
otherwise 1. A breaking `3.6.x` release is therefore still pushed to users at
full urgency. That grep reads release-please's *default* section headings, so
never introduce a `changelog-sections` config for this package — it would
silently drop the urgency to 1.

**Caveat — tag-trigger chaining.** GitHub's default `GITHUB_TOKEN`
cannot trigger downstream workflows from a tag it pushes. The
release-please workflow's `token` input falls back to `GITHUB_TOKEN` if
`RELEASE_PLEASE_TOKEN` is unset; in that mode the auto-tag is created but
**no `release-*` workflow fires**, leaving the wheel/aab unbuilt. This is
a hard GitHub Actions limit, not a release-please bug. Set
`RELEASE_PLEASE_TOKEN` to make tag-trigger chaining work.

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

Required CI secrets:

| Secret | Used by | Purpose |
|--------|---------|---------|
| `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` | `release-android.yml` | Signs the `.aab` with the upload key. |
| `PLAY_SERVICE_ACCOUNT_JSON` | `release-android.yml` | Service account used to upload the signed bundle to the Play Console internal track. |
| `AGENT_MANIFEST_PRIVATE_KEY` | `release-agent.yml` | Base64 Ed25519 private key. Signs the manifest body. |
| `CENTRAL_SERVER_RELEASE_TOKEN` | `release-agent.yml` | Bearer token for `POST /api/internal/agent_manifest`. |
| `CENTRAL_SERVER_URL` | `release-agent.yml` | Base URL of the production `central_server` that hosts the manifest. |
| `RELEASES_REPO_TOKEN` | `release-agent.yml` | Fine-grained PAT scoped to the **public releases repo** (e.g. `syrax90/mobaide-agent-releases`) with `Contents: Read and write`. Used by `softprops/action-gh-release@v2` to publish the release into that repo. |
| `RELEASE_PLEASE_TOKEN` _(optional)_ | `release-please.yml`, `release-agent-manual.yml` | PAT with `contents:write` + `workflow` scopes. Required to chain CI-side tag pushes into the downstream `release-*` workflows (GitHub Actions does not fire downstream workflows from tags pushed by `GITHUB_TOKEN`). Without it, those workflows still push the tag, but the operator must re-push it from a developer machine to fire the downstream build. |

Repo **variables** (not secrets — values appear in workflow logs and are not sensitive):

| Variable | Used by | Purpose |
|----------|---------|---------|
| `RELEASES_REPO` | `release-agent.yml` | `<owner>/<repo>` of the public releases repo (e.g. `syrax90/mobaide-agent-releases`). The workflow embeds this in the manifest's `tarball_url`/`wheel_url` and pushes the `softprops/action-gh-release` step into this repo. |

Failure modes by which prereq is missing:

* `AGENT_MANIFEST_PRIVATE_KEY` missing → `Build signed manifest`,
  `Create GitHub Release in public releases repo`, and the
  reachability gate all skip via `if: ${{ env.AGENT_MANIFEST_PRIVATE_KEY != '' }}`,
  with each step hoisting the secret into its own per-step `env:` so
  the gate is evaluable (`secrets.*` is not in the allowed-contexts
  list for step-level `if:`). No state changes anywhere.
* `RELEASES_REPO_TOKEN` or the `RELEASES_REPO` variable missing →
  `Create GitHub Release in public releases repo` fails loudly
  before any URL-bearing manifest can be posted to `central_server`.
* `CENTRAL_SERVER_*` missing → the upstream signing-dependent steps
  run (so the public-repo release IS created), but `Publish manifest
  to central_server` and `Verify manifest is published` skip via
  their own `if:` guard on the central_server token+URL pair, and
  the prior manifest on `central_server` keeps serving.

The reachability gate (anonymous 1-byte ranged GET against every URL
in the manifest, with `--retry 5 --retry-all-errors` to absorb
GitHub-CDN propagation lag) is the additional safeguard that
refuses to publish a manifest pointing at unreachable artifacts.

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
`deploy/prod/deploy_central.py` (which preserves the previous image for
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
      "fetch": true, "pull": true, "push": true
    }
  },
  "latest_agent_version": "0.1.0"
}
```

(`capabilities.git.changes` — additive, no bump — advertises the session-scoped
git status/diff routes behind the in-session Changes viewer; an agent
without the key answers those routes with 404 and clients render an
"update your agent" state. `capabilities.git.stage` / `discard` / `commit` —
also additive — advertise the stage/unstage, discard and commit routes; a
client shows a mutating control only when its key is `true`, so an older agent
keeps a read-only viewer. `capabilities.git.fetch` / `pull` / `push` — additive
too — advertise the background fetch/pull/push jobs and their `git.job.*`
events; the terminal menu's *Fetch / Pull / Push…* entry and the Changes
screen's Sync button exist on any of the three, each row on its own key.
`capabilities.cli` carries one `{ "available": <bool> }` entry per managed CLI
the agent registers — additive, no `protocol_version` bump — and the Android
mode-select screen disables a per-CLI button when its binary is absent. The
client parses it tolerantly: an absent / old-agent / malformed value is treated
as available. Entries for CLIs whose startup model enumeration succeeded also
carry a `models` array — and, when the CLI's catalog exposes per-model
reasoning levels, a `model_details` array — which the New-session pickers
prefer over their hardcoded model/effort fallbacks; both keys are omitted, never
emitted empty, on failure. A probe answered while the agent's startup
enumeration is still running instead carries `"models_pending": true` on each
enumeration-capable entry whose own list has not yet been produced, telling
clients that entry's absence is provisional. See
`PROTOCOL.md` (proto-v5 bullet plus the 2026-08-23 proto-v7 additive delta)
for the full shape.)

(The signed manifest body / signature is NOT echoed back through this REST
surface — it is delivered to the agent inline over the relay WebSocket as
of proto-v2. The Android client only needs `latest_agent_version` to drive
the "agent outdated" banner.)

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
server_protocol_versions, manifest_url}` and closes with WebSocket code **4002**
(`PROTOCOL_INCOMPATIBLE_CLOSE_CODE`). On 4002 the relay's reconnect-backoff
jumps to `reconnect_max_delay` and the agent's HTTPS-bootstrap path (see below)
fetches the signed manifest from the `manifest_url` hint (or the env-derived
URL when the hint is absent), stages the new release, and the supervisor
respawns into the upgraded binary. The agent self-heals without operator
intervention; the only "terminal" case is an agent that pre-dates the
HTTPS-bootstrap release and was stranded before it could pick up the new path.

On a successful auth the
server persists `agent_version` and `agent_protocol_version` (the
`protocol_version` the agent declared) into the `agents` DB row; both fields
are exposed via `GET /api/agents` (`AgentSummary`) and rendered on the admin
dashboard — the users page lists agents in an expandable per-user sub-table
that includes a **Version** column ("Up to date" or "latest: X.Y.Z" when
behind the manifest).

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

* **Signing** — Ed25519. Canonical JSON encoding is
  `json.dumps(body, sort_keys=True, separators=(",", ":"))`. The release
  workflow signs with `AGENT_MANIFEST_PRIVATE_KEY`; the public half is
  configured on `central_server` as `MANIFEST_SIGNING_PUBLIC_KEY` and pinned
  on each agent host at `<prefix>/trust/central_server.pub`.
* **Publishing** — `release-agent.yml` first creates a GitHub Release in
  the public sibling repo (`$RELEASES_REPO`) with the tarball, wheel, and
  `manifest.json` attached. It then runs an **anonymous reachability
  gate** — `curl -fsSL --retry 5 -r 0-0` (a 1-byte ranged GET) against
  every URL the signed manifest references — and refuses to proceed if
  any URL is not anonymously reachable (privacy mismatch, asset-name
  typo, missing upload). Ranged GET rather than HEAD avoids
  GitHub-CDN-signed-redirect targets that intermittently reject HEAD
  with 403/405 even when GET succeeds; retries absorb the CDN
  propagation lag that `softprops/action-gh-release@v2` is known to
  race with. Only after the gate passes does the workflow POST the
  record to `POST /api/internal/agent_manifest` on `central_server`
  with header `X-Release-Token: $CENTRAL_SERVER_RELEASE_TOKEN`. The
  route validates the signature **before** persisting; an invalid
  signature returns 400 and the manifest is not stored.
* **Distribution** — agents receive the signed manifest **body** inline in
  the `auth_ok` reply on the agent tunnel and in `agent_manifest_update`
  CH_CONTROL push frames (proto-v2). The fields carried are
  `manifest_body` (the full signed dict) and `manifest_signature` (the
  base64 Ed25519 signature); no HTTP fetch is required to apply an update.
  After a successful `POST /api/internal/agent_manifest`, the router fans
  out an `agent_manifest_update` frame to every connected agent in the
  routing table (`broadcast_manifest_update`), so a freshly-published
  manifest reaches running agents within one heartbeat without a
  reconnect. `GET /api/v1/agent/manifest.json` continues to serve the same
  record over HTTP — install scripts and the `mobaide-agent update` CLI
  consume that endpoint for bootstrap / out-of-band refresh, but the
  user-triggered "Install now" path no longer touches it.
* **Storage** — `ManifestStore` writes atomically via `os.replace`; reads are
  served from an in-memory last-good cache.

### Releases repo bootstrap

The source repo (`syrax90/claude_mobile`) is private; anonymous installs
cannot reach a private repo's GitHub Release assets. Release artifacts
therefore live in a dedicated **public** sibling repo. One-time bootstrap:

1. **Create the public repo** (e.g. `syrax90/mobaide-agent-releases`).
   Visibility = Public. A one-line README is enough; no source code lives
   here, only release artifacts attached to GitHub Releases.
2. **Mint a fine-grained PAT scoped to that repo only**, with
   `Contents: Read and write`. Save it on `syrax90/claude_mobile` as a
   secret named `RELEASES_REPO_TOKEN`.
3. **Add a repo variable** on `syrax90/claude_mobile` named
   `RELEASES_REPO` with value `<owner>/<public-releases-repo>`. This is
   a Variable, not a secret — its value shows up in workflow logs and
   is not sensitive.
4. **Cut a release** as usual (push an `agent-v*` tag). The workflow
   builds the wheel + tarball in the private repo's CI, signs the
   manifest with URLs that point at the public repo, pushes the
   GitHub Release into the public repo, runs the reachability gate
   against the public URLs, then publishes the manifest to
   `central_server`.

If a previous release left a stale manifest record on `central_server`
that points at unreachable URLs (e.g. the pre-bootstrap state), clear
it out before re-running by deleting the file at the configured
`MANIFEST_STORE_PATH` on the central_server host (default
`data/agent_manifest.json`). The endpoint then reverts to "no manifest
published yet" 404 — a clean state — until the next successful release
pipeline run.

## Auto-update on the agent (staged-binary swap)

The agent updates itself in place using a **staged-binary** pattern: every
release lives in its own directory and a single pointer file selects the
"current" one. There is no in-process patching. All outbound HTTP calls (manifest
polling, tarball/wheel downloads, trust-key fetch) are made through a centralised
wrapper (`mobaide._http`) that injects `User-Agent: mobaide-agent/<version>` so
Cloudflare-fronted relay and artifact hosts do not classify them as generic
`Python-urllib` bot traffic (Cloudflare error 1010). The wrapper also pins its
TLS trust to the bundled `certifi` store, so the manifest/tarball/wheel HTTPS
fetches verify regardless of the host interpreter's OpenSSL CA store and no
longer depend on a launcher-set `SSL_CERT_FILE` (closes the Windows-exposed TLS
gap; an operator-set `SSL_CERT_FILE` still takes precedence on the launcher's
`wss://` path).

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
└── bin/                             # macOS/Windows launcher shims
```

A release is **complete/runnable** only once a `.installed` marker file exists
in `releases/<v>/`, written as the last install step (after pip + the wheel).
For releases that predate the sentinel, a release is also treated as complete
if its `mobaide-agent` console script exists (pip writes it only after a
successful wheel install). This "complete release" definition is canonical in
`mobaide.agent.install_paths.is_release_complete`; the updater, launcher
rollback/prune, and the install.sh / Windows supervisor self-heal all share it
so a half-built release (a venv swapped into place but not yet finalized) is
never selected or booted.

The platform-specific service unit (`systemd`, `launchd`, Windows Scheduled Task) does
**not** invoke a release directly. On Linux/macOS the unit's entry point is the
generated `<prefix>/bin/mobaide-agent-launcher.sh` wrapper (shared by both, written
by `write_launcher_script` in `install.sh`); on Windows the equivalent is the
stdlib-only `windows_launcher.py` supervisor. The wrapper/supervisor first
**self-heals** a missing, corrupt, or **incomplete** current-release venv: if
`<prefix>/releases/<current_version>` has no runnable interpreter or lacks the
completion sentinel it rolls `current_version` *forward* to the newest sibling
release that is complete (skipping `.staging-*` / `.old-*` scratch and any
non-version dir) before exec, converting a permanent crash loop into automatic
rollback to the last good release. With no complete sibling at all it exits 70
rather than looping. It then execs
`releases/<version>/venv/bin/python -m mobaide.launcher`, which:

1. Reads `<prefix>/current_version`.
2. Appends the boot attempt to `<prefix>/.boot_journal` (timestamp + version).
3. If the current version has been booted **3 times within 60s**, picks the
   highest-versioned sibling release from `<prefix>/releases/` (skipping any
   that lacks a usable `venv`), atomically rewrites `current_version` to that
   target, and logs `ERROR — version <X> crashed N+ times…; rolling back to
   <Y>`. With no eligible sibling, it logs the rollback miss and continues
   into the failing release; the supervisor's hard cap finally stops the loop.
4. Garbage-collects superseded release directories under `<prefix>/releases/`,
   keeping the current version plus the highest-versioned eligible sibling (the
   rollback target). Retention is configurable via `MOBAIDE_AGENT_KEEP_RELEASES`
   (default `2`; floor `2`). Cleanup is best-effort — a failed deletion is
   logged and retried on the next boot, and never blocks startup.
5. `os.execv`s into `<prefix>/releases/<version>/venv/bin/python -m mobaide run`.

This means a swap of `current_version` followed by a process restart is the
only thing required to roll forward (or back). The launcher's rollback
heuristic gives a deployed install one automatic recovery step before
operator intervention is needed; matching supervisor settings —
systemd `StartLimitBurst=10`/`StartLimitIntervalSec=600`, launchd
`ThrottleInterval=10`, Windows Task Scheduler `-RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)` — give the
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
* **Non-destructive staging (shebang-safe)** — the venv is built with
  `python -m venv --without-pip` in a sibling scratch dir
  `releases/.staging-<v>-<pid>`, so it carries only move-safe interpreter
  symlinks and **no** shebang-bearing console scripts. A single `os.replace`
  swaps it into `releases/<v>`, then pip + the wheel are installed using the
  **final-path** interpreter so every `pip` / `mobaide-agent` shebang points at
  the real `releases/<v>` location (the fix for the "bad interpreter: No such
  file or directory" failure that the old build-then-move ordering left
  behind). The release is marked complete with the `.installed` sentinel only
  after that finalize step, and `current_version` is flipped only after the
  sentinel — so a half-built release is never selected. A failed stage never
  overwrites or destroys the running release and leaves no empty `releases/<v>`
  behind; leftover `.staging-*` / `.old-*` scratch and any stale incomplete
  release are garbage-collected best-effort on the next run (the updater also
  reclaims stale incomplete releases in its pre-stage sweep).
* **Forward-only on the auto path** — auto/poll updates only stage a manifest
  whose version is **strictly newer** than the running one, so a stale or
  mismatched manifest (e.g. a newer dev build seeing an older published
  version) can never downgrade an install. The forced paths — operator
  "Install now" and 4002 protocol-incompatible recovery — bypass this and
  apply any version.
* **Path-component validation** — the signed manifest `version` is checked
  against a safe-path allowlist at the manifest→filesystem boundary (in both
  `updater.py` and `install.sh`) before it is used as a `releases/<v>` path
  component.
* **Manager-release guard** — the in-process auto-updater is disabled when the
  agent runs from a developer/source checkout (the interpreter is not under
  `<prefix>/releases`), so a source run that shares the install prefix can no
  longer drive an update that overwrites the installed release; relay manifests
  are still recorded but never applied (fail-closed). The explicit
  `mobaide-agent update` CLI verb is an operator opt-in that deliberately
  bypasses this guard.
* **Activity awareness** — defers the swap when any session is running. The
  updater queries `SessionManager.list_sessions()` and only proceeds when all
  sessions are stopped/crashed. Deferral retries after 30 minutes; a single
  pending update that has been deferred for >24h logs a `WARN` for
  observability.
* **Atomic pointer swap** — `os.replace` of `current_version.tmp` →
  `current_version`.
* **Supervisor restart** — the updater calls `sys.exit(75)`. `RestartForceExitStatus=75` (systemd), the Windows `mobaide_launcher.py` supervisor's exit-code-75 respawn loop, and Task Scheduler's outer `-RestartCount` / `-RestartInterval` all interpret this as "please bring me back up", and the launcher boots into the freshly-pinned release.
* **Rollback target** — on every boot, `_prune_old_releases` keeps the current
  version plus up to `MOBAIDE_AGENT_KEEP_RELEASES - 1` highest-versioned
  eligible siblings (default: one previous release). Only **complete** releases
  (`.installed` sentinel, or the legacy console-script fallback) are eligible
  rollback targets; dirs that are incomplete, lack a usable venv, or whose name
  is not a parseable version are never selected as a rollback target. Set
  `MOBAIDE_AGENT_KEEP_RELEASES=<n>` (floor `2`) to retain more rollback targets.

### HTTPS bootstrap (side-channel update path)

The in-band manifest delivery (`auth_ok` + `agent_manifest_update`) is the
fast path during steady-state operation. It cannot, however, deliver a
manifest pointer to an agent whose `protocol_version` is below the server's
`SUPPORTED_PROTOCOL_VERSIONS` — the relay closes with code 4002 before the
pointer is sent, and the in-band update channel becomes circular (the
binary that would lift the agent into the new protocol is exactly what
needs to be delivered).

The HTTPS bootstrap breaks that loop. The signed manifest is also served
unauthenticated over plain HTTPS at `GET /api/v1/agent/manifest.json`
(`central_server/central_server/manifest/router.py`). The agent reaches it
via `mobaide.agent.update_check.run_https_update_check`, which fetches and
verifies the manifest through the **same** `StagedBinaryUpdater` the relay
path uses — no second crypto stack, no skip-signature branch. The
manifest URL is derived from `RELAY_SERVER_URL` in the agent's `.env`
via the shared `endpoint_url_from_relay_server_url` helper (same hardening
guards as the trust-key fetch: no user-info, no cleartext by default).

The bootstrap fires on three triggers:

1. **Pre-connect** — the agent's lifespan runs the HTTPS check **before**
   the first relay connect. An outdated agent self-heals during cold
   boot; the relay client is never started on this run, and the
   supervisor respawns into the freshly-staged release on the same
   exit-75 path the in-band updater uses.
2. **Protocol-incompatible recovery (close-code 4002)** — the relay
   client's connect loop fires a `protocol_incompatible` callback that
   re-runs the HTTPS check with `force=True`. The callback prefers the
   `manifest_url` hint from the rejection payload over the env-derived
   URL but treats it as informational only — the signature is the
   trust anchor either way.
3. **Periodic poll** — a 60-minute background task
   (`run_periodic_check_loop`) runs against the env-derived URL. Catches
   manifests the relay broadcast missed (queue saturation, restart races,
   etc.). Failures are logged and the loop continues; a single bad
   iteration cannot tear down the poll for the lifetime of the process.

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
3. Bootstrap the manifest-signing public key and persist it at
   `<prefix>/trust/central_server.pub`. Two paths, in priority order:
   `MOBAIDE_AGENT_TRUST_KEY_B64` env var if set; otherwise the host derived
   from `MOBAIDE_AGENT_MANIFEST_URL` is hit at
   `/api/v1/agent/trust_key` to fetch the key. Verify the manifest signature
   against the pinned key. The install fails-closed if neither path yields
   a key — never silently ships an install whose auto-updater can't
   verify rollouts.
4. **(Linux only, before venv creation)** Verify that `python3 -c 'import ensurepip'`
   succeeds. If it does not (common on stock Debian/Ubuntu where the base
   `python3` package omits `ensurepip`), auto-install `python${version}-venv`
   via the detected package manager (`apt-get`, `dnf`/`yum`, `pacman`, `zypper`,
   or `apk`). Non-root user-prefix installs print a distro-aware error instead
   of attempting a privileged package install.
4a. **(Windows only, before venv creation)** Auto-elevate via UAC if the
    current PowerShell is not elevated — required for the Scheduled Task
    registration and `%ProgramFiles%\MobaideAgent` writes. If no Python 3.12+
    is found on PATH, auto-install at machine scope and refresh the current
    session's PATH from the Machine + User scopes. Two paths, in priority
    order: `winget install Python.Python.3.12`; if `winget` is unavailable
    or fails, fall back to the official python.org installer (downloaded
    for the host architecture, Authenticode-verified against the Python
    Software Foundation signer before execution). Fails with a
    manual-download message only when both paths fail. Set
    `MOBAIDE_AGENT_SKIP_PYTHON_INSTALL=1` to opt out and require a
    pre-installed interpreter (CI / locked-down hosts). Override the
    re-fetch URL used by the elevation re-launch via
    `MOBAIDE_AGENT_INSTALL_URL` (parallel to the Linux side).
5. Download tarball + wheel, verify sha256s, extract under
   `<prefix>/releases/<version>/`, install the wheel into a fresh venv.
6. Atomically pin `<prefix>/current_version`.
7. Generate `<prefix>/mobaide_agent/.env` via `python -m mobaide.agent.config init-env` — writes a random `JWT_SECRET_KEY` and prefills `RELAY_SERVER_URL=wss://mobaide.com`. On Windows, if the pre-elevation `Find-ClaudeBinary` helper located `claude.cmd`, the path is forwarded via `MOBAIDE_AGENT_CLAUDE_BIN_HINT` and written as `MOBAIDE_CLAUDE_BIN` into the agent's `.env`. The optional Codex CLI is auto-detected identically: the parallel `Find-CodexBinary` helper probes the same `%APPDATA%\npm` locations for `codex.cmd`, forwards it via `MOBAIDE_AGENT_CODEX_BIN_HINT`, and writes `MOBAIDE_CODEX_BIN` (a miss is silent — Codex is the optional secondary agent). The optional Antigravity CLI is auto-detected the same way: the parallel `Find-AgyBinary` helper probes the standalone `agy` install locations (`%USERPROFILE%\.local\bin`, the `Antigravity` program folders) for `agy.exe` / `agy.cmd`, forwards it via `MOBAIDE_AGENT_AGY_BIN_HINT`, and writes `MOBAIDE_AGY_BIN` (also a silent miss — Antigravity is an optional managed agent). Re-running never overwrites an already-paired `.env` (pass `--force` to regenerate; paired credentials are preserved regardless).
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
   `<prefix>/trust/central_server.pub`. No operator-side env vars required —
   the canonical `curl|bash` line just works. The endpoint requires
   `MANIFEST_SIGNING_PUBLIC_KEY` to be set on the central_server (already
   required for manifest publishing).
2. **Operator-pinned via env var (airgap / explicit):**
   ```bash
   MOBAIDE_AGENT_TRUST_KEY_B64=<base64> \
     curl -fsSL https://get.mobaide.com/install.sh | sudo bash
   ```
   Skips the central_server fetch. Use this when the install host has no
   network path to your central_server, or when you want maximum trust
   isolation between the install channel and the trust root.
3. **Repair existing install:** if you have an agent that was set up before
   path #1 existed (or pre-`MOBAIDE_AGENT_TRUST_KEY_B64`), run
   `mobaide-agent fetch-trust-key` on the agent host. The CLI derives
   `https://<host>/api/v1/agent/trust_key` from `RELAY_SERVER_URL` in the
   agent's `.env`, validates the result as base64-Ed25519, writes
   `<prefix>/trust/central_server.pub`, and best-effort restarts the
   supervisor so the running agent picks up the new updater. Use
   `--from-url <full-url>` to override the env-derivation, and
   `--no-restart` to skip the supervisor restart when you'd rather schedule
   it yourself.

**Fail-closed semantics:** if neither the env var nor the central_server
fetch yields a key, the install script aborts with an actionable error.
There is no longer a "skip signature verification" branch — silently
shipping an unsigned install is a security regression we'd rather flag at
install time than discover when the auto-updater never works.

**Hardening on the fetch path:**

* User-info in the manifest URL (`wss://api.mobaide.com@evil.example/...`)
  is rejected up front. `urlparse`'s `netloc` would otherwise smuggle the
  attacker host into the trust-key URL and DNS-resolve to the wrong
  place. Both the install scripts and `mobaide-agent fetch-trust-key`
  enforce this.
* Cleartext `ws://` / `http://` is refused by default. A passive on-path
  attacker on the install host's LAN could otherwise substitute the
  public key. Set `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1` to
  override (only safe on a trusted local network — e.g. a self-host
  central_server reachable only over loopback / a VPN). The default
  `https://` and `wss://` paths are unaffected.
* The fetched value is round-trip validated as a 32-byte Ed25519 public
  key before any file lands on disk, with the same validator applied to
  env-var-pinned values so a malformed key surfaces immediately as
  "your key is malformed" rather than later as a confusing "manifest
  signature does not verify".

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

1. Runs `tests/test_protocol_integrity.py` under pytest, which (a) imports
   both `envelope.py` copies and asserts that `PROTOCOL_VERSION`,
   `MIN_CLIENT_PROTOCOL_VERSION`, `SUPPORTED_PROTOCOL_VERSIONS`,
   `PROTOCOL_INCOMPATIBLE_CLOSE_CODE`, and the channel-type constants match
   between the two, and (b) compares the live wire shape against
   `tests/protocol_golden.json`. Any handshake-shape change **must** update
   the golden file in the same commit.
2. **Client constants check:** extracts `APP_PROTOCOL_VERSION` from
   `ios/Config/Protocol.xcconfig` and from `web/src/api/bootstrap.ts` and
   asserts both equal the `buildConfigField` value in
   `android/app/build.gradle.kts`, so a proto bump that misses `ios/` or
   `web/` fails here. `MIN_SERVER_PROTOCOL_VERSION` stays an iOS↔Android
   comparison: the web version gate is set membership against the server's
   advertised list (`checkVersionGate`) and carries no client-side floor of
   its own. Both comparisons are version-agnostic and need no update on
   bumps.
3. **Diff guard (PR-only):** if a PR modifies one of the two `envelope.py`
   files, it must modify the other. Comparison uses
   `${{ github.event.pull_request.base.sha }}..${{ github.event.pull_request.head.sha }}`
   so the check is base-branch-agnostic.
4. **Web protocol fixtures** (separate `web-protocol-fixtures` job, Node 22
   rather than Python, so it runs in parallel): `npm ci` + `npm run
   test:protocol` in `web/`, which exercises the TypeScript arm of the
   `tests/protocol_fixtures/` goldens — the same files the iOS test target
   reads — plus a coverage manifest that fails when a new fixture file is
   neither asserted by a web test nor assigned an owning milestone.

   This is the **only** CI-enforced fixture arm. iOS runs its suite on push to
   `main` only (`ios-build.yml`'s `pull_request` trigger is commented out), and
   no workflow runs Gradle unit tests at all — so Android's arm of
   `keys/key_chords.json` (WEB-06) is a local check, not a gate. Anything
   shared that only Kotlin or Swift asserts can drift through a PR unnoticed;
   put the load-bearing assertion in the TypeScript arm.

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
5. Hands the served manifest body (and its base64 Ed25519 signature) to
   `StagedBinaryUpdater.consider_manifest()` inline — the same shape the
   agent now receives over the relay WebSocket — with a stubbed installer;
   asserts the staged release dir was created, the staged `__init__.py`
   carries the expected version string, and `current_version` was
   atomically swapped.
6. **Negative cases:** corrupting the tarball sha256 in the manifest body
   makes the updater return `signature_failed` and leaves
   `current_version` untouched (fail-closed). An invalid `X-Release-Token`
   makes the publish endpoint return 401 without persisting.

The test is run by `.github/workflows/e2e-update.yml` on every PR and on
push to `main`, with a 5-minute timeout. The implementation purposely
does NOT spawn a real systemd / launchd / Windows Scheduled Task supervisor — that
boundary is platform-coupled and is exercised separately by
`mobaide_agent/tests/test_launcher_rollback.py` and the install scripts'
own integration tests.

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
