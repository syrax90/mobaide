# GitHub Pages source for the Mobaide install endpoint (`get.mobaide.com`)

This directory is the GitHub Pages source for the install-script CDN. The
release-agent workflow copies the canonical install scripts from
`deploy/install.sh` and `deploy/install.ps1` into this directory at release
time and Pages serves them as plain text.

Public URLs:

* `https://get.mobaide.com/install.sh`
* `https://get.mobaide.com/install.ps1`

## Setting up DNS / Pages

1. Configure GitHub Pages on this repo with source = `gh-pages` branch, OR
   set source = `main` and folder = `/docs/get`.
2. Add a `CNAME` record at the apex: `get.mobaide.com → <user>.github.io`.
3. Push the install scripts here at release time (the workflow does this
   automatically once the agent-v* tag is created).

## Public-key trust

Production install commands embed the central-server Ed25519 public key
via the `MOBAIDE_AGENT_TRUST_KEY_B64` env var passed inline:

```bash
MOBAIDE_AGENT_TRUST_KEY_B64=<base64> curl -fsSL https://get.mobaide.com/install.sh | bash
```

The script writes the key to `<prefix>/trust/central_server.pub`; subsequent
auto-update cycles use that to verify each new manifest.
