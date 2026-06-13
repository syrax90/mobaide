"""Relay configuration for connecting the agent to a Central Server.

Loaded via ``pydantic-settings``. Four runtime states:

* **not configured** — ``RELAY_SERVER_URL`` empty. ``configured`` is False;
  the agent boots without starting a relay client.
* **pairing pending (fresh install)** — ``RELAY_SERVER_URL`` set,
  ``RELAY_AGENT_ID`` AND ``RELAY_AGENT_TOKEN`` both empty.
  ``pairing_pending`` is True, ``re_pair_pending`` is False; the agent
  boots in QR pairing mode. Mirrors Tailscale's idle-until-paired
  daemon.
* **re-pair pending** — ``RELAY_SERVER_URL`` set, ``RELAY_AGENT_ID`` set,
  ``RELAY_AGENT_TOKEN`` empty. Both ``pairing_pending`` and
  ``re_pair_pending`` are True; the boot autorun carries the prior
  ``agent_id`` into the next claim so the server re-registers (rotates the
  token for) the existing agent for its current owner instead of
  registering a fresh one. A claim by a different account is refused —
  agent ownership is not transferable by pairing. Written by
  ``mobaide-agent re-pair``.
* **configured** — all three set. ``pairing_pending`` is False; the agent
  starts a relay client normally.

The id-empty + token-set state is still a misconfig and raises at
construction time.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelayConfig(BaseSettings):
    """Configuration for the outbound relay connection to a Central Server."""

    server_url: str = ""
    agent_id: str = ""
    agent_token: str = ""
    heartbeat_interval: int = 15
    # ``RELAY_RECONNECT_BASE`` / ``RELAY_RECONNECT_MAX`` env var names predate
    # CLA-48 and are documented in `.env.example`; explicit aliases keep the
    # contract stable while the field names follow the seconds-suffixed
    # ``_delay`` Python convention. Without these, pydantic-settings derives
    # ``RELAY_RECONNECT_BASE_DELAY`` from the field name + prefix and silently
    # ignores the documented variable.
    reconnect_base_delay: float = Field(
        default=1.0, validation_alias="RELAY_RECONNECT_BASE",
    )
    reconnect_max_delay: float = Field(
        default=60.0, validation_alias="RELAY_RECONNECT_MAX",
    )
    # Uptime a connection must survive before the reconnect backoff resets to
    # the base delay; flapping connections keep their escalated delay. New
    # field, so no legacy alias — env var is the derived
    # ``RELAY_RECONNECT_STABILITY_THRESHOLD``.
    reconnect_stability_threshold: float = 60.0

    model_config = SettingsConfigDict(
        env_prefix="RELAY_",
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    @property
    def configured(self) -> bool:
        return bool(self.server_url)

    @property
    def pairing_pending(self) -> bool:
        return self.configured and not (self.agent_id and self.agent_token)

    @property
    def re_pair_pending(self) -> bool:
        """Sub-state of `pairing_pending`: agent_id retained, token blanked.

        Set by ``mobaide-agent re-pair``: only the
        token is cleared so the boot autorun can carry ``current_agent_id``
        into the next initiate request and trigger the server's same-owner
        ``reregister_agent`` (token rotation) instead of registering a fresh
        agent. A claim by a different account is refused server-side — agent
        ownership is not transferable by pairing. The fresh-install
        pairing-pending case has both fields blank and ``re_pair_pending``
        is False.
        """
        return (
            self.configured
            and bool(self.agent_id)
            and not self.agent_token
        )

    @model_validator(mode="after")
    def _check_partial(self) -> "RelayConfig":
        if not self.configured:
            return self
        # The id-set / token-blank state is intentional during re-pair:
        # blanking the token alone signals the boot autorun to re-register
        # (rotate the token for) the existing agent under its owner. The
        # reverse — token set without id — is still nonsensical and refused.
        only_token = bool(self.agent_token) and not self.agent_id
        if only_token:
            raise RuntimeError(
                "RELAY_AGENT_ID is required when RELAY_AGENT_TOKEN is set",
            )
        return self

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "RelayConfig | None":
        """Build from environment variables and/or an ``env_file``.

        Returns ``None`` when ``RELAY_SERVER_URL`` is not set, mirroring the
        legacy contract that callers already understand. The ``configured``
        / ``pairing_pending`` properties encode the remaining states.
        """
        kwargs: dict = {}
        if env_file is not None:
            kwargs["_env_file"] = str(env_file)
        cfg = cls(**kwargs)
        if not cfg.configured:
            return None
        return cfg
