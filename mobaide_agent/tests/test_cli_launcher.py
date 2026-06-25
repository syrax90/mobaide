"""Tests for the CLI-availability probe primitives in ``core.cli_launcher``.

Covers ``probe_cli_available`` (resolve-success → True, ``CliBinaryNotFoundError``
→ False, never spawns a subprocess) and ``build_cli_capabilities`` (the shared
``GET /api/version`` builder that drives the Android mode-picker gate and must
report identical availability over both the direct and relay transports).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mobaide.core.cli_launcher import (
    CliBinaryNotFoundError,
    build_cli_capabilities,
    probe_cli_available,
)


class _MissingError(CliBinaryNotFoundError):
    cli_name = "fake"
    _INSTALL_HINT = "fake CLI not found."


def _launcher(resolves_to: str | None):
    """A minimal ``CliLauncher`` stand-in.

    ``resolves_to=None`` makes ``resolve_binary`` raise ``CliBinaryNotFoundError``
    (the binary-missing case); a string makes it return that path.
    """
    launcher = MagicMock(name="launcher")
    if resolves_to is None:
        launcher.resolve_binary.side_effect = _MissingError(["PATH:fake"])
    else:
        launcher.resolve_binary.return_value = resolves_to
    return launcher


class TestProbeCliAvailable:
    def test_true_when_resolve_succeeds(self):
        assert probe_cli_available(_launcher("/usr/local/bin/fake")) is True

    def test_false_when_binary_not_found(self):
        assert probe_cli_available(_launcher(None)) is False

    def test_does_not_spawn_a_subprocess(self, monkeypatch):
        """The probe must reach a verdict purely via ``resolve_binary`` — no
        process spawn. Poison the subprocess entrypoints so any spawn fails the
        test loudly instead of silently shelling out on the event loop."""
        import subprocess

        def _boom(*_a, **_kw):
            raise AssertionError("probe_cli_available must not spawn a subprocess")

        monkeypatch.setattr(subprocess, "run", _boom)
        monkeypatch.setattr(subprocess, "Popen", _boom)

        assert probe_cli_available(_launcher("/bin/fake")) is True
        assert probe_cli_available(_launcher(None)) is False

    def test_only_catches_binary_not_found_not_arbitrary_errors(self):
        """A non-``CliBinaryNotFoundError`` from ``resolve_binary`` must
        propagate — the probe narrows its ``except`` to the missing-binary type
        so a genuine bug (e.g. a misconfigured override raising ``ValueError``)
        is never silently reported as 'available: false'."""
        launcher = MagicMock()
        launcher.resolve_binary.side_effect = RuntimeError("unexpected")
        with pytest.raises(RuntimeError):
            probe_cli_available(launcher)


class TestBuildCliCapabilities:
    def test_maps_each_launcher_to_available_flag(self):
        caps = build_cli_capabilities(
            {
                "claude": _launcher("/bin/claude"),
                "codex": _launcher(None),
            }
        )
        assert caps == {
            "claude": {"available": True},
            "codex": {"available": False},
        }

    def test_empty_registry_yields_empty_map(self):
        assert build_cli_capabilities({}) == {}

    def test_none_registry_is_tolerated(self):
        """``app_state.cli_launchers`` may be unset on a partially-built state;
        the builder coerces a falsy registry to ``{}`` rather than raising."""
        assert build_cli_capabilities(None) == {}  # type: ignore[arg-type]

    def test_preserves_registry_keys_for_future_clis(self):
        """A CLI added to the registry under any id is reported automatically —
        the gate is driven off the registry, not a hard-coded claude/codex set."""
        caps = build_cli_capabilities({"future-cli": _launcher("/bin/future")})
        assert caps == {"future-cli": {"available": True}}


class TestProbeWithRealLaunchers:
    """``probe_cli_available`` over the production launchers: the verdict must
    follow the same override → env → PATH resolution a real spawn uses, so an
    'enabled' mode in the UI implies a spawnable CLI.

    The autouse ``_pin_claude_binary`` / ``_pin_codex_binary`` fixtures pin the
    module-level resolvers to a literal name, so a default launcher resolves
    'installed' here; the missing-binary cases re-patch the resolver to raise.
    """

    def test_claude_launcher_available_with_pinned_resolver(self):
        from mobaide.core.claude_launcher import ClaudeLauncher

        assert probe_cli_available(ClaudeLauncher()) is True

    def test_codex_launcher_available_with_pinned_resolver(self):
        from mobaide.core.codex_launcher import CodexLauncher

        assert probe_cli_available(CodexLauncher()) is True

    def test_claude_launcher_unavailable_when_resolver_raises(self, monkeypatch):
        from mobaide.core import claude_launcher
        from mobaide.core.claude_launcher import (
            ClaudeBinaryNotFoundError,
            ClaudeLauncher,
        )

        def _raise(*_a, **_kw):
            raise ClaudeBinaryNotFoundError(["PATH:claude"])

        monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _raise)
        assert probe_cli_available(ClaudeLauncher()) is False

    def test_claude_launcher_honours_binary_override(self, tmp_path, monkeypatch):
        """The override must be threaded into the resolver so a configured
        ``claude_bin_override`` decides availability the same way it decides the
        spawn binary. The pinned resolver echoes its ``override`` argument so we
        can assert the launcher forwarded it."""
        from mobaide.core import claude_launcher
        from mobaide.core.claude_launcher import ClaudeLauncher

        captured: dict[str, object] = {}

        def _echo(override=None):
            captured["override"] = override
            return override or "claude"

        monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _echo)

        override_path = str(tmp_path / "custom-claude")
        launcher = ClaudeLauncher(claude_bin_override=override_path)
        assert launcher.resolve_binary() == override_path
        assert captured["override"] == override_path
        assert probe_cli_available(launcher) is True

    def test_claude_resolve_binary_caches_on_success(self, monkeypatch):
        """``resolve_binary`` delegates to the cached ``_resolve_binary``: the
        underlying module resolver runs once per launcher instance, so repeated
        probes don't re-stat PATH. (A miss is never cached — covered by the
        per-open freshness requirement — so only the success path memoises.)"""
        from mobaide.core import claude_launcher
        from mobaide.core.claude_launcher import ClaudeLauncher

        calls = {"n": 0}

        def _count(override=None):
            calls["n"] += 1
            return "claude"

        monkeypatch.setattr(claude_launcher, "_resolve_claude_binary", _count)

        launcher = ClaudeLauncher()
        assert launcher.resolve_binary() == "claude"
        assert launcher.resolve_binary() == "claude"
        assert calls["n"] == 1
