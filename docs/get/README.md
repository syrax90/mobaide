# Install scripts mirrored for `get.mobaide.com`

This directory holds the install scripts that end up published at
`get.mobaide.com`. The canonical source lives at `deploy/install.sh` and
`deploy/install.ps1`; the `release-agent.yml` workflow mirrors them here
on every `agent-v*` tag, and `protocol-integrity.yml` enforces
byte-identity between the two copies on every PR.

Public URLs (after the gh-pages sync — see below):

* `https://get.mobaide.com/install.sh`
* `https://get.mobaide.com/install.ps1`

On Debian/Ubuntu the script auto-installs `python3.X-venv` (the
`ensurepip` carrier) when it is missing — which is the stock state of
fresh Ubuntu, WSL, and most container base images. Other distros
(Fedora, Arch, openSUSE, Alpine) get the equivalent package via their
native package manager. Non-root user-prefix installs print an
actionable error instead of attempting privileged package install.

## Pages source is the `gh-pages` branch

GitHub Pages is configured to serve from the `gh-pages` branch (with the
`CNAME` and `.nojekyll` files at its root), **not** from `main`/docs/get.
Pushes to `main` that touch `install.sh` or `install.ps1` here are
auto-published to the `gh-pages` root by
[`.github/workflows/publish-gh-pages.yml`](../../.github/workflows/publish-gh-pages.yml),
so `get.mobaide.com` picks them up within ~30 s of merge.

The release-time chain has one caveat (the `RELEASE_PLEASE_TOKEN` PAT
requirement for `release-agent.yml`'s sync commit to fire downstream
workflows) and a `workflow_dispatch` fallback. The full story, the
manual fallback runbook, and the description of what each file on
`gh-pages` is for live in
[`../release-publishing.md`](../release-publishing.md).

## Public-key trust

Production install commands embed the central-server Ed25519 public key
via the `MOBAIDE_AGENT_TRUST_KEY_B64` env var passed inline:

```bash
MOBAIDE_AGENT_TRUST_KEY_B64=<base64> curl -fsSL https://get.mobaide.com/install.sh | bash
```

The script writes the key to `<prefix>/trust/central_server.pub`; subsequent
auto-update cycles use that to verify each new manifest.
