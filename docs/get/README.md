# Install scripts mirrored for `get.mobaide.com`

This directory holds the install scripts that end up published at
`get.mobaide.com`. The canonical source lives at `deploy/install.sh` and
`deploy/install.ps1`; the `release-agent.yml` workflow mirrors them here
on every `agent-v*` tag, and `protocol-integrity.yml` enforces
byte-identity between the two copies on every PR.

Public URLs (after the gh-pages sync — see below):

* `https://get.mobaide.com/install.sh`
* `https://get.mobaide.com/install.ps1`

## Pages source is the `gh-pages` branch

GitHub Pages is configured to serve from the `gh-pages` branch (with the
`CNAME` and `.nojekyll` files at its root), **not** from `main`/docs/get.
That means changes committed here on `main` do not automatically reach
`get.mobaide.com` — an operator must forward them onto the `gh-pages`
branch root after merge.

The runbook for that manual sync, plus a verify step and a description
of what each file on `gh-pages` is for, lives in
[`../release-publishing.md`](../release-publishing.md). Read that before
landing changes to `install.sh` / `install.ps1`.

## Public-key trust

Production install commands embed the central-server Ed25519 public key
via the `MOBAIDE_AGENT_TRUST_KEY_B64` env var passed inline:

```bash
MOBAIDE_AGENT_TRUST_KEY_B64=<base64> curl -fsSL https://get.mobaide.com/install.sh | bash
```

The script writes the key to `<prefix>/trust/central_server.pub`; subsequent
auto-update cycles use that to verify each new manifest.
