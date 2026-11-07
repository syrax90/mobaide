"""Agent Client Protocol (ACP) integration: chat sessions.

A chat session runs an ACP agent (for example the Claude Code ACP adapter)
as a stdio JSON-RPC subprocess and turns its updates into the agent-agnostic
chat timeline that the clients render. Nothing in this package is specific
to one coding agent except the launch specs in :mod:`mobaide.acp.agents`.
"""
