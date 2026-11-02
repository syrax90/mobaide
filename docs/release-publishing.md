# Install-Script Publishing Pipeline

This doc covers how the public install one-liners

```bash
curl -fsSL https://get.mobaide.com/install.sh | bash
irm https://get.mobaide.com/install.ps1 | iex
```

reach end users, the GitHub Actions workflow that auto-publishes them
when `deploy/install.{sh,ps1}` or `deploy/windows_launcher.py` change
on `main`, and the manual override an operator can fall back to if the
automation does not fire.

For the rest of the release machinery (component versioning, signed
manifests, the staged-binary auto-updater on already-installed agents)
see [`versioning-and-updates.md`](versioning-and-updates.md). This doc
covers only the *bootstrap* path — how a brand-new host first downloads
the agent.

**On this page**

- [Pipeline overview](#pipeline-overview)
- [When to use the manual fallback](#when-to-use-the-manual-fallback)
- [What to watch after a release](#what-to-watch-after-a-release)
- [Manual fallback runbook](#manual-fallback-runbook)
- [Verifying the publish](#verifying-the-publish)
- [What lives on `gh-pages`](#what-lives-on-gh-pages)
- [See also](#see-also)

## Pipeline overview

```
deploy/install.{sh,ps1}     canonical source. PRs edit these.
deploy/windows_launcher.py   per-user Scheduled-Task supervisor (stdlib); co-versioned with install.ps1.
        │
        │ publish-gh-pages.yml on push: main with paths
        │ deploy/install.{sh,ps1} or deploy/windows_launcher.py.
        │ workflow_dispatch is the manual fallback when the chain breaks.
        ▼
gh-pages branch root        also holds CNAME and .nojekyll. This is the
        │                   actual GitHub Pages source.
        │
        │ GitHub Pages serves files at the apex of the gh-pages branch
        ▼
https://get.mobaide.com/    public CDN. The CNAME at the gh-pages root
                            provisions the custom domain.
```

Three places hold copies of these files: `deploy/` on `main` and
the root of the `gh-pages` branch. The second is auto-published from
`deploy/` by `publish-gh-pages.yml` on every push to `main` that
touches `deploy/install.{sh,ps1}` or `deploy/windows_launcher.py`.

## When to use the manual fallback

`publish-gh-pages.yml` listens for `push: main` events that touch
`deploy/install.{sh,ps1}` or `deploy/windows_launcher.py`. Direct PR
merges trigger it cleanly under the default `GITHUB_TOKEN`, so the
common path is fully automatic.

Use the runbook below only when:

- the workflow did not fire (transient Actions outage, paths filter mismatch
  from a partial revert),
- an emergency override is needed, or
- you need to reproduce the publish locally for debugging.

## What to watch after a release

The Actions tab is the source of truth. After any merge that touches
`deploy/install.{sh,ps1}` or `deploy/windows_launcher.py`, confirm that "Publish install scripts to
gh-pages" ran green on the resulting `main` SHA. There is no automated
alarm: if the workflow ever silently skips, the served scripts will
lag the canonical source until the next push or a `workflow_dispatch`
nudge.

## Manual fallback runbook

The `gh-pages` branch has a flat layout (`install.sh` at the root,
**not** under `deploy/`). The cleanest sync uses `git show` to pull
file content from `main` and write it straight to the gh-pages working
tree, without touching the index of either branch.

```bash
# 1. Make sure main is in sync locally.
git fetch origin

# 2. Switch to gh-pages and fast-forward.
git checkout gh-pages
git pull --ff-only origin gh-pages

# 3. Overwrite the served scripts with the latest main content.
git show origin/main:deploy/install.sh         > install.sh
git show origin/main:deploy/install.ps1        > install.ps1
git show origin/main:deploy/windows_launcher.py > windows_launcher.py

# 4. Sanity-check the diff (you should only see install.{sh,ps1} and
#    windows_launcher.py change; CNAME and .nojekyll must stay untouched).
git status
git diff -- install.sh install.ps1 windows_launcher.py

# 5. Commit and push.
git add install.sh install.ps1 windows_launcher.py
git commit -m "publish: sync install scripts from main"
git push origin gh-pages
```

Worktree variant (does not require switching the current branch):

```bash
git fetch origin
git worktree add ../mobaide-gh-pages gh-pages
git -C ../mobaide-gh-pages pull --ff-only origin gh-pages
git show origin/main:deploy/install.sh         > ../mobaide-gh-pages/install.sh
git show origin/main:deploy/install.ps1        > ../mobaide-gh-pages/install.ps1
git show origin/main:deploy/windows_launcher.py > ../mobaide-gh-pages/windows_launcher.py
git -C ../mobaide-gh-pages add install.sh install.ps1 windows_launcher.py
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

The branch root holds exactly five files. Treat the last pair as
infrastructure — they should not move:

| File | Purpose | Touched by sync? |
|---|---|---|
| `install.sh` | Linux/macOS bootstrap. Mirrors `deploy/install.sh` from `main`. | Yes — every sync. |
| `install.ps1` | Windows bootstrap. Mirrors `deploy/install.ps1`. | Yes — every sync. |
| `windows_launcher.py` | Stdlib-only per-user Scheduled-Task supervisor downloaded by `install.ps1` into `<prefix>\svc\mobaide_launcher.py`. Co-versioned with `install.ps1` so supervisor API changes ship atomically with the installer. Mirrors `deploy/windows_launcher.py`. | Yes — every sync. |
| `CNAME` | Single line `get.mobaide.com`. Provisions the custom domain. | No. |
| `.nojekyll` | Empty file. Tells Pages **not** to run the Jekyll renderer, so the scripts are served as plain text rather than processed. | No. |

If `CNAME` or `.nojekyll` ever ends up touched in a sync commit,
something went wrong with the runbook above — back the change out and
restart from a fresh checkout.

## See also

* [`versioning-and-updates.md`](versioning-and-updates.md) — the rest of
  the release pipeline: tag conventions, signed manifests, on-host
  staged-binary auto-updater. This doc covers only the bootstrap.
* [`PROTOCOL.md`](../PROTOCOL.md) — wire-contract version history
  (independent of the install pipeline).
