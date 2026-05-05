"""Pin the no-drift invariant between the wheel-packaged template and the
repo-root mirror.

``mobaide/agent/env.template`` is the canonical artifact shipped inside
the wheel (read at install time via ``importlib.resources``). The
repo-root ``mobaide_agent/.env.example`` is a generated mirror produced
by ``python -m mobaide.agent.config print-template``. They MUST match
byte-for-byte; any drift will manifest at install time as a confusing
mismatch between the prepared file and the documented example.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def _repo_env_example_path() -> Path:
    """Locate the repo-root ``mobaide_agent/.env.example``.

    Walks up from this test file until it finds the file. Done lazily so
    the test still works whether pytest is launched from the repo root
    or from inside ``mobaide_agent/``.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "mobaide_agent" / ".env.example"
        if candidate.exists():
            return candidate
        candidate = parent / ".env.example"
        if candidate.exists() and candidate.parent.name == "mobaide_agent":
            return candidate
    raise AssertionError(
        "Could not locate mobaide_agent/.env.example walking up from "
        f"{here}"
    )


class TestEnvTemplateMatchesExample:
    def test_wheel_template_byte_equals_repo_env_example(self):
        wheel_template = (
            files("mobaide.agent")
            .joinpath("env.template")
            .read_text(encoding="utf-8")
        )
        repo_example = _repo_env_example_path().read_text(encoding="utf-8")

        assert wheel_template == repo_example, (
            "mobaide/agent/env.template (in the wheel) and "
            "mobaide_agent/.env.example (in the repo) have drifted. "
            "Regenerate the mirror with: "
            "`python -m mobaide.agent.config print-template "
            "> mobaide_agent/.env.example`"
        )


class TestEnvTemplateContractAnchors:
    """Anchor a few invariants the install scripts and CLI rely on.

    These are intentionally narrow — we don't restate the entire template,
    only the keys the rest of the codebase reads at install or pair time.
    """

    def test_template_contains_jwt_secret_key_placeholder(self):
        text = (
            files("mobaide.agent")
            .joinpath("env.template")
            .read_text(encoding="utf-8")
        )
        # init-env substitutes JWT_SECRET_KEY=<urlsafe>; the placeholder
        # must be a bare empty assignment so the substitution is unambiguous.
        assert "JWT_SECRET_KEY=\n" in text

    def test_template_contains_default_relay_server_url(self):
        text = (
            files("mobaide.agent")
            .joinpath("env.template")
            .read_text(encoding="utf-8")
        )
        assert "RELAY_SERVER_URL=wss://mobaide.com" in text

    def test_template_contains_relay_pairing_placeholders(self):
        text = (
            files("mobaide.agent")
            .joinpath("env.template")
            .read_text(encoding="utf-8")
        )
        # ``mobaide-agent pair`` writes both keys; both must already be
        # present (uncommented) so render_template sees them as overridable.
        assert "RELAY_AGENT_ID=\n" in text
        assert "RELAY_AGENT_TOKEN=\n" in text
