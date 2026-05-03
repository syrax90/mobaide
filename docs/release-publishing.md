# Install-Script Publishing Pipeline

This doc covers how the public install one-liners

```bash
curl -fsSL https://get.mobaide.com/install.sh | bash
irm https://get.mobaide.com/install.ps1 | iex
```

reach end users, and the manual step an operator must run after a change
lands on `main` so that `get.mobaide.com` actually serves the new script.

For the rest of the release machinery (component versioning, signed
manifests, the staged-binary auto-updater on already-installed agents)
see [`versioning-and-updates.md`](versioning-and-updates.md). This doc
covers only the *bootstrap* path — how a brand-new host first downloads
the agent.

## Pipeline overview

```
deploy/install.{sh,ps1}     canonical source. PRs edit these.
        │
        │ release-agent.yml on agent-v* tag
        │   (steps 141–172)
        ▼
docs/get/install.{sh,ps1}   committed back to main by the workflow.
        │                   protocol-integrity.yml asserts byte-identity
        │                   with deploy/ on every PR.
        │
        │ MANUAL step — operator copies onto gh-pages branch root.
        │ (Direct edits to docs/get/* during ordinary PRs follow the
        │  same manual sync.)
        ▼
gh-pages branch root        also holds CNAME and .nojekyll. This is the
        │                   actual GitHub Pages source.
        │
        │ GitHub Pages serves files at the apex of the gh-pages branch
        ▼
https://get.mobaide.com/    public CDN. The CNAME at the gh-pages root
                            provisions the custom domain.
```

Three places hold copies of the install scripts: `deploy/`, `docs/get/`
on `main`, and the root of the `gh-pages` branch. The first two are kept
identical by `release-agent.yml` (auto-mirror) and policed by
`protocol-integrity.yml` (CI diff guard). The third — `gh-pages` — has
no automation: a human must forward the change after merge.

## Why the manual step exists

`release-agent.yml` re-syncs `deploy/install.*` → `docs/get/install.*`
on the `main` branch only. It has no leg that pushes to `gh-pages`. Some
day a workflow extension (and a third leg in the diff guard) can close
this loop — see "Future automation" below — but until then the public
CDN goes stale every time `docs/get/install.*` moves.

## Who must run the sync

Anyone landing a PR that changes `docs/get/install.sh` or
`docs/get/install.ps1`. The two common triggers:

1. **An `agent-v*` release** ran `release-agent.yml`, which committed an
   updated `docs/get/install.*` directly to `main`. Whoever cuts the
   release should also do the gh-pages sync as part of release closeout.
2. **A direct PR** edited the install scripts (e.g. this very change —
   the auto-elevation fix). Whoever merges the PR runs the sync.

If you forget the sync, `get.mobaide.com` keeps serving the old script
indefinitely. There is no alarm — the CI diff guard only compares the
two `main`-side copies.

## Sync runbook

The `gh-pages` branch has a flat layout (`install.sh` at the root,
**not** under `docs/get/`). The cleanest sync uses `git show` to pull
file content from `main` and write it straight to the gh-pages
working tree, without touching the index of either branch.

```bash
# 1. Make sure main is in sync locally.
git fetch origin

# 2. Switch to gh-pages and fast-forward.
git checkout gh-pages
git pull --ff-only origin gh-pages

# 3. Overwrite the served scripts with the latest main content.
git show origin/main:docs/get/install.sh  > install.sh
git show origin/main:docs/get/install.ps1 > install.ps1

# 4. Sanity-check the diff (you should only see install.{sh,ps1} change;
#    CNAME and .nojekyll must stay untouched).
git status
git diff -- install.sh install.ps1

# 5. Commit and push.
git add install.sh install.ps1
git commit -m "publish: sync install scripts from main"
git push origin gh-pages
```

Worktree variant (does not require switching the current branch):

```bash
git fetch origin
git worktree add ../mobaide-gh-pages gh-pages
git -C ../mobaide-gh-pages pull --ff-only origin gh-pages
git show origin/main:docs/get/install.sh  > ../mobaide-gh-pages/install.sh
git show origin/main:docs/get/install.ps1 > ../mobaide-gh-pages/install.ps1
git -C ../mobaide-gh-pages add install.sh install.ps1
git -C ../mobaide-gh-pages commit -m "publish: sync install scripts from main"
git -C ../mobaide-gh-pages push origin gh-pages
git worktree remove ../mobaide-gh-pages
```

## Verifying the publish

GitHub Pages typically picks up a `gh-pages` push within ~30 seconds and
serves with a short `max-age`, so the new script should be live almost
immediately. Verify:

```bash
# Compare what's served against what's on the branch.
curl -fsSL https://get.mobaide.com/install.sh | sha256sum
git show origin/gh-pages:install.sh         | sha256sum
# Both digests must match.
```

A spot-check on the change you just published:

```bash
# Example for the auto-elevation fix:
curl -fsSL https://get.mobaide.com/install.sh \
  | grep -A1 'Re-executing under sudo'
```

If the digests disagree for more than a couple of minutes, check the
**Pages** section of the repo's Settings page for a deployment failure
or a stale build.

## What lives on `gh-pages`

The branch root holds exactly four files. Treat the second pair as
infrastructure — they should not move:

| File | Purpose | Touched by sync? |
|---|---|---|
| `install.sh` | Linux/macOS bootstrap. Mirrors `docs/get/install.sh` from `main`. | Yes — every sync. |
| `install.ps1` | Windows bootstrap. Mirrors `docs/get/install.ps1`. | Yes — every sync. |
| `CNAME` | Single line `get.mobaide.com`. Provisions the custom domain. | No. |
| `.nojekyll` | Empty file. Tells Pages **not** to run the Jekyll renderer, so the scripts are served as plain text rather than processed. | No. |

If `CNAME` or `.nojekyll` ever ends up touched in a sync commit,
something went wrong with the runbook above — back the change out and
restart from a fresh checkout.

## Future automation

The cleanest follow-up is to extend `release-agent.yml` with a third
sync step that force-updates `gh-pages`:

```yaml
- name: Sync install scripts to gh-pages
  run: |
    git fetch origin gh-pages
    git checkout -B gh-pages-tmp origin/gh-pages
    cp docs/get/install.sh  install.sh
    cp docs/get/install.ps1 install.ps1
    if git diff --quiet -- install.sh install.ps1; then exit 0; fi
    git add install.sh install.ps1
    git commit -m "publish: sync install scripts for ${GITHUB_REF_NAME}"
    git push origin HEAD:gh-pages
```

…paired with a third leg in the
`protocol-integrity.yml` diff guard that asserts the `gh-pages` copy
matches `docs/get/`. Both are out of scope for the change that
introduces this doc; the manual runbook above is the supported path
until that automation lands.

## See also

* [`versioning-and-updates.md`](versioning-and-updates.md) — the rest of
  the release pipeline: tag conventions, signed manifests, on-host
  staged-binary auto-updater. This doc covers only the bootstrap.
* [`get/README.md`](get/README.md) — short note next to the scripts
  themselves, with the trust-key inline-pinning command.
* [`PROTOCOL.md`](../PROTOCOL.md) — wire-contract version history
  (independent of the install pipeline).
