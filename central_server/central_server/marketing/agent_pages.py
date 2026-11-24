"""The per-agent pages at ``/agents/<slug>``.

One shared layout (``marketing_agent.html``) renders every page; each agent's
own copy lives in ``agents/<slug>.html``, which extends it. The router, the
loader, the sitemap and the security-header bucket all read this registry, so
adding an agent here is what puts its page on all four.

The demo clips are ``static/site/video/<slug>-<platform>.mp4`` with a poster
frame beside each (``.webp``). Their sizes and lengths are restated here for the
``<video>`` boxes and the VideoObject JSON-LD; re-encoding a clip means updating
its entry (``ffprobe`` gives both).
"""
from __future__ import annotations

from dataclasses import dataclass

# When the clips were recorded. Google wants an upload date on every
# VideoObject; a clip that is re-recorded gets the new date.
DEMO_UPLOAD_DATE = "2026-11-24T00:00:00+00:00"


@dataclass(frozen=True)
class DemoClip:
    platform: str
    tab_label: str
    # "in the web app": completes "Claude Code in Mobaide ..." in the video's
    # name and accessible label.
    where: str
    heading: str
    width: int
    height: int
    seconds: int

    @property
    def iso_duration(self) -> str:
        return f"PT{self.seconds}S"


@dataclass(frozen=True)
class AgentPage:
    slug: str
    name: str
    # What the hero chip and the "other agents" links say; for most agents the
    # product name itself.
    label: str
    # Who makes the agent, for the "is this the official app" answer.
    maker: str
    clips: tuple[DemoClip, ...]

    @property
    def path(self) -> str:
        return f"/agents/{self.slug}"

    @property
    def template(self) -> str:
        return f"agents/{self.slug}.html"


def _clips(web: int, android: int, ios: int, ios_height: int = 1566) -> tuple[DemoClip, ...]:
    # Web first: it is the tab the page opens on.
    return (
        DemoClip("web", "Web app", "in the web app", "In the web app", 1920, 1080, web),
        DemoClip("android", "Android", "on Android", "On Android", 720, 1600, android),
        DemoClip("ios", "iPhone", "on iPhone", "On iPhone", 720, ios_height, ios),
    )


AGENT_PAGES: tuple[AgentPage, ...] = (
    AgentPage("claude-code", "Claude Code", "Claude Code", "Anthropic", _clips(29, 29, 28)),
    AgentPage("codex", "Codex CLI", "Codex CLI", "OpenAI", _clips(29, 29, 29)),
    AgentPage("antigravity", "Antigravity CLI", "Antigravity CLI", "Google", _clips(29, 28, 29)),
    AgentPage("opencode", "OpenCode", "OpenCode", "the makers of OpenCode", _clips(29, 29, 29)),
    AgentPage("github-copilot", "GitHub Copilot CLI", "GitHub Copilot CLI", "GitHub", _clips(29, 28, 29)),
    AgentPage("cursor", "Cursor CLI", "Cursor CLI", "Anysphere, the maker of Cursor", _clips(29, 29, 28, ios_height=1562)),
    AgentPage("junie", "Junie CLI", "Junie CLI", "JetBrains", _clips(29, 29, 29)),
    AgentPage("pi", "Pi", "Pi coding agent", "the makers of Pi", _clips(29, 29, 29)),
)

AGENT_PAGES_BY_SLUG = {page.slug: page for page in AGENT_PAGES}

AGENT_PAGE_PATHS = frozenset(page.path for page in AGENT_PAGES)
