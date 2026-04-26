"""Signed agent-rollout manifest store and HTTP endpoints.

The manifest is the single source of truth for which ``claude_agent`` version
is currently rolled out. Agents read it via ``GET /api/v1/agent/manifest.json``
and the auto-updater verifies the embedded Ed25519 signature before applying
the staged-binary swap.
"""
