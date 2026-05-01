"""Relay configuration for connecting the agent to a Central Server."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RelayConfig:
    """Configuration for the outbound relay connection to a Central Server."""

    server_url: str
    agent_id: str
    agent_token: str
    heartbeat_interval: int = 15  # seconds
    reconnect_base_delay: float = 1.0  # seconds
    reconnect_max_delay: float = 60.0  # seconds

    @classmethod
    def from_env(cls) -> RelayConfig | None:
        """Build from environment variables; return None if RELAY_SERVER_URL is unset.

        RELAY_AGENT_ID and RELAY_AGENT_TOKEN are required whenever RELAY_SERVER_URL
        is present — raises RuntimeError if either is missing.
        """
        url = os.getenv("RELAY_SERVER_URL")
        if not url:
            return None
        agent_id = os.getenv("RELAY_AGENT_ID")
        agent_token = os.getenv("RELAY_AGENT_TOKEN")
        if not agent_id or not agent_token:
            raise RuntimeError(
                "RELAY_AGENT_ID and RELAY_AGENT_TOKEN are required "
                "when RELAY_SERVER_URL is set"
            )
        return cls(
            server_url=url,
            agent_id=agent_id,
            agent_token=agent_token,
            heartbeat_interval=int(
                os.getenv("RELAY_HEARTBEAT_INTERVAL", "15"),
            ),
            reconnect_base_delay=float(
                os.getenv("RELAY_RECONNECT_BASE", "1.0"),
            ),
            reconnect_max_delay=float(
                os.getenv("RELAY_RECONNECT_MAX", "60.0"),
            ),
        )
