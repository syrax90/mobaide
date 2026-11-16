"""Wire records of the Herdr dashboard. Mobaide owns this contract.

The same records describe a Herdr TUI server and a herdr-core server. A
feature one product lacks is a ``False`` flag in :class:`HerdrFeatures`, never
a different record shape.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ``capabilities.herdr`` in ``GET /api/version``. A client shows no Herdr UI
# for an agent without ``dashboard: true`` (an older agent has no such key).
HERDR_CAPABILITIES: dict[str, bool] = {"dashboard": True}

LABEL_MAX_CHARS = 80
# No control characters: herdr draws a label on the user's desktop terminal.
LABEL_PATTERN = r"^[^\x00-\x1f\x7f-\x9f]*$"
TEXT_MAX_LINES = 1000

# ``working`` | ``blocked`` (waits for an answer) | ``done`` (finished a turn)
# | ``failed`` (the turn ended in an error) | ``idle`` | ``unknown``. The state
# is herdr's reading of the agent and can be wrong. Clients keep an unknown
# value and show it without a chip.
HerdrStatus = Literal["working", "blocked", "done", "failed", "idle", "unknown"]

HerdrPlacement = Literal["workspace", "tab", "split_right", "split_down"]
HerdrTargetKind = Literal["workspace", "tab", "pane"]


class HerdrRect(BaseModel):
    """Position of a pane in its tab, in terminal cells."""

    x: int
    y: int
    width: int
    height: int


class HerdrPane(BaseModel):
    pane_id: str
    title: str
    # The agent in the pane as herdr names it (``claude``, ``codex``); ``None``
    # for a pane without an agent, which then has no ``status`` either.
    agent: str | None = None
    status: str | None = None
    # Changes each time the agent finishes a turn. A client that stored the
    # value it last showed can tell a new ``done`` from one the user has seen.
    turn: int | None = None
    cwd: str | None = None
    agent_session_id: str | None = None
    rect: HerdrRect | None = None


class HerdrTab(BaseModel):
    tab_id: str
    label: str
    # Size of the tab area that the pane ``rect`` values are placed in.
    width: int | None = None
    height: int | None = None
    panes: list[HerdrPane] = Field(default_factory=list)


class HerdrWorkspace(BaseModel):
    workspace_id: str
    label: str
    tabs: list[HerdrTab] = Field(default_factory=list)


class HerdrAgentKind(BaseModel):
    id: str
    label: str


class HerdrFeatures(BaseModel):
    """What this server can do. A client hides the action of a ``False`` flag."""

    new_workspace: bool = False
    new_tab: bool = False
    split: bool = False
    rename: bool = False
    # Close a whole workspace or tab. ``close_pane`` covers one pane.
    close_group: bool = False
    close_pane: bool = False
    # "Take control": resize the pane to the client's screen.
    control: bool = False
    # Keys typed in watch mode reach the pane one by one. Without it only a
    # submitted line and the keys of the key bar do.
    type_in_watch: bool = False
    # Agents that "Start agent" can launch in a pane.
    agents: list[HerdrAgentKind] = Field(default_factory=list)


class HerdrServer(BaseModel):
    server_id: str
    # ``tui`` (Herdr TUI) | ``core`` (herdr-core, the Herdr app).
    product: str
    # The herdr session name; ``default`` for the usual one.
    name: str
    version: str | None = None
    # ``ready`` | ``outdated`` (too old to support; ``message`` says what to
    # do, and the server has no workspaces).
    state: str = "ready"
    message: str | None = None
    features: HerdrFeatures = Field(default_factory=HerdrFeatures)
    workspaces: list[HerdrWorkspace] = Field(default_factory=list)


class HerdrSnapshot(BaseModel):
    """Payload of ``GET /api/herdr`` and of the ``herdr.changed`` event."""

    servers: list[HerdrServer] = Field(default_factory=list)


class HerdrPaneTarget(BaseModel):
    server_id: str = Field(min_length=1, max_length=200)
    pane_id: str = Field(min_length=1, max_length=200)


class HerdrOpenPaneResponse(BaseModel):
    # A terminal session that shows the pane. It is not in the session list
    # and it ends when the pane closes; stopping it never touches the pane.
    session_id: str


class HerdrControlRequest(HerdrPaneTarget):
    control: bool
    # Names the client that takes or gives back control. One id for one app
    # process or browser tab; the pane's state frame carries it back.
    client_id: str = Field(default="", max_length=64)


class HerdrPaneTextResponse(BaseModel):
    text: str
    # Older lines exist that the read did not return.
    truncated: bool = False


class HerdrCreateRequest(BaseModel):
    server_id: str = Field(min_length=1, max_length=200)
    placement: HerdrPlacement
    # ``tab``: the workspace that gets the tab.
    workspace_id: str | None = Field(default=None, max_length=200)
    # ``split_right`` / ``split_down``: the pane to split.
    pane_id: str | None = Field(default=None, max_length=200)
    cwd: str | None = Field(default=None, max_length=4096)
    label: str | None = Field(default=None, max_length=LABEL_MAX_CHARS, pattern=LABEL_PATTERN)
    # Start this agent in the new pane (an id from ``features.agents``).
    agent: str | None = Field(default=None, max_length=64)


class HerdrCreateResponse(BaseModel):
    pane_id: str
    # Set when the pane was made and the agent that was to run in it did not
    # start: a message for the user.
    agent_error: str | None = None


class HerdrStartAgentRequest(HerdrPaneTarget):
    agent: str = Field(min_length=1, max_length=64)


class HerdrRenameRequest(BaseModel):
    server_id: str = Field(min_length=1, max_length=200)
    target: HerdrTargetKind
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=LABEL_MAX_CHARS, pattern=LABEL_PATTERN)


class HerdrCloseRequest(BaseModel):
    server_id: str = Field(min_length=1, max_length=200)
    target: HerdrTargetKind
    id: str = Field(min_length=1, max_length=200)


class HerdrOkResponse(BaseModel):
    ok: bool = True


class HerdrAttention(BaseModel):
    """Payload of the one-time ``herdr.attention`` event: an agent in a pane
    needs the user. ``reason`` uses the session attention vocabulary
    (``awaiting_permission`` | ``turn_finished`` | ``turn_failed``)."""

    server_id: str
    pane_id: str
    reason: str
    title: str
    agent: str | None = None
