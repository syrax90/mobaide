"""Tests for the CLI-availability probe primitives in ``core.cli_launcher``.

Covers ``probe_cli_available`` (resolve-success → True, ``CliBinaryNotFoundError``
→ False, never spawns a subprocess), ``build_cli_capabilities`` (the shared
``GET /api/version`` builder that drives the Android mode-picker gate and must
report identical availability over both the direct and relay transports —
plus the cache-only ``models`` key), ``warm_cli_model_caches`` (the duck-typed
startup warm task), and the ``shim_safe_argv`` Windows-shim wrapper.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.core.cli_launcher import (
    CliBinaryNotFoundError,
    await_model_cache_warmup,
    build_cli_capabilities,
    probe_cli_available,
    shim_safe_argv,
    warm_cli_model_caches,
)
from tests.conftest import skip_if_not_linux, skip_if_not_windows


class _MissingError(CliBinaryNotFoundError):
    cli_name = "fake"
    _INSTALL_HINT = "fake CLI not found."


def _launcher(resolves_to: str | None, models=None, model_details=None):
    """A minimal ``CliLauncher`` stand-in.

    ``resolves_to=None`` makes ``resolve_binary`` raise ``CliBinaryNotFoundError``
    (the binary-missing case); a string makes it return that path. ``models`` /
    ``model_details`` seed ``cached_models`` / ``cached_model_details``; the
    ``None`` defaults mirror a launcher whose model cache is cold (or that never
    enumerates) — pinned explicitly because a bare ``MagicMock`` attribute would
    read as truthy.
    """
    launcher = MagicMock(name="launcher")
    launcher.cached_models = models
    launcher.cached_model_details = model_details
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

    def test_models_key_added_when_cached_models_truthy(self):
        """A launcher with a warm ``cached_models`` additionally carries the
        ``models`` list — as a fresh copy, so a caller mutating the capability
        payload can never corrupt the launcher's cache."""
        cached = ["Gemini 3.1 Pro (Low)", "Gemini 3.1 Flash"]
        launcher = _launcher("/bin/agy", models=cached)

        caps = build_cli_capabilities({"antigravity": launcher})

        assert caps == {
            "antigravity": {
                "available": True,
                "models": ["Gemini 3.1 Pro (Low)", "Gemini 3.1 Flash"],
            }
        }
        assert caps["antigravity"]["models"] is not cached

    def test_models_key_omitted_when_cached_models_none(self):
        """A cold cache (``cached_models is None``) yields the bare
        ``{"available": ...}`` shape — the key is omitted, never ``null``."""
        caps = build_cli_capabilities({"codex": _launcher("/bin/codex", models=None)})
        assert caps == {"codex": {"available": True}}

    def test_models_key_omitted_when_cached_models_empty(self):
        """An empty tuple is falsy: the key must be omitted — never emitted as
        an empty list — so clients fall back to their hardcoded lists on the
        same absent signal for old and new agents alike."""
        caps = build_cli_capabilities({"codex": _launcher("/bin/codex", models=())})
        assert caps == {"codex": {"available": True}}

    def test_models_key_omitted_when_launcher_lacks_attribute(self):
        """Model enumeration is duck-typed: a launcher without ``cached_models``
        at all (e.g. Claude's) is reported with availability only."""
        launcher = _launcher("/bin/claude")
        del launcher.cached_models  # getattr now raises AttributeError
        del launcher.cached_model_details
        caps = build_cli_capabilities({"claude": launcher})
        assert caps == {"claude": {"available": True}}

    def test_model_details_key_added_when_cached_details_truthy(self):
        """A launcher with warm per-model details additionally carries
        ``model_details`` — as fresh dict copies, so a caller mutating the
        capability payload can never corrupt the launcher's cache."""
        detail = {"id": "gpt-5.6-sol", "efforts": ["low", "ultra"]}
        launcher = _launcher(
            "/bin/codex", models=["gpt-5.6-sol"], model_details=(detail,)
        )

        caps = build_cli_capabilities({"codex": launcher})

        assert caps == {
            "codex": {
                "available": True,
                "models": ["gpt-5.6-sol"],
                "model_details": [{"id": "gpt-5.6-sol", "efforts": ["low", "ultra"]}],
            }
        }
        emitted = caps["codex"]["model_details"]
        assert isinstance(emitted, list)
        assert emitted[0] is not detail

    def test_model_details_key_omitted_when_none_or_empty(self):
        """Details follow the same omitted-never-empty contract as ``models`` —
        a cold or empty cache yields no ``model_details`` key at all."""
        caps = build_cli_capabilities(
            {
                "codex": _launcher("/bin/codex", models=["gpt-x"], model_details=None),
                "agy": _launcher("/bin/agy", models=["m"], model_details=()),
            }
        )
        assert caps == {
            "codex": {"available": True, "models": ["gpt-x"]},
            "agy": {"available": True, "models": ["m"]},
        }

    def test_model_details_key_omitted_when_launcher_lacks_attribute(self):
        """A launcher exposing ``cached_models`` but not ``cached_model_details``
        (e.g. Antigravity's) still gets its ``models`` key with no details."""
        launcher = _launcher("/bin/agy", models=["Gemini 3.1 Flash"])
        del launcher.cached_model_details
        caps = build_cli_capabilities({"antigravity": launcher})
        assert caps == {
            "antigravity": {"available": True, "models": ["Gemini 3.1 Flash"]}
        }


class TestWarmCliModelCaches:
    """``warm_cli_model_caches`` populates each launcher's model cache once at
    startup, off the request path. Support is duck-typed via ``list_models``,
    and each probe is isolated so one broken CLI can't stop the others.
    """

    async def test_calls_list_models_on_supporting_launchers(self):
        codex = _launcher("/bin/codex")
        codex.list_models = AsyncMock(return_value=["gpt-5.5"])
        antigravity = _launcher("/bin/agy")
        antigravity.list_models = AsyncMock(return_value=["Gemini 3.1 Flash"])

        await warm_cli_model_caches({"codex": codex, "antigravity": antigravity})

        codex.list_models.assert_awaited_once()
        antigravity.list_models.assert_awaited_once()

    async def test_skips_launchers_without_list_models(self):
        """A launcher that never enumerates (e.g. Claude's) is skipped — the
        warm task widens by duck-typing, not by changing the Protocol."""
        claude = _launcher("/bin/claude")
        del claude.list_models  # getattr now raises AttributeError
        codex = _launcher("/bin/codex")
        codex.list_models = AsyncMock(return_value=["gpt-5.5"])

        await warm_cli_model_caches({"claude": claude, "codex": codex})

        codex.list_models.assert_awaited_once()

    async def test_one_failing_launcher_does_not_stop_others(self):
        """A raising ``list_models`` is caught and logged per launcher; the
        remaining registry entries must still be warmed."""
        broken = _launcher("/bin/broken")
        broken.list_models = AsyncMock(side_effect=RuntimeError("boom"))
        healthy = _launcher("/bin/agy")
        healthy.list_models = AsyncMock(return_value=["Gemini 3.1 Flash"])

        await warm_cli_model_caches({"broken": broken, "healthy": healthy})

        broken.list_models.assert_awaited_once()
        healthy.list_models.assert_awaited_once()

    async def test_none_registry_is_tolerated(self):
        """Mirrors ``build_cli_capabilities``: a partially-built app state may
        carry no registry; the warm task must no-op rather than raise."""
        await warm_cli_model_caches(None)  # type: ignore[arg-type]
        await warm_cli_model_caches({})


class TestAwaitModelCacheWarmup:
    """``await_model_cache_warmup`` is the bounded mid-warmup wait both
    ``/api/version`` transports run: a probe landing right after agent start
    must not advertise a cold cache (indistinguishable from an old agent
    without enumeration), but a wedged warm task must not stall the endpoint
    either.
    """

    async def test_none_and_done_tasks_return_immediately(self):
        import asyncio

        await await_model_cache_warmup(None)
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        await await_model_cache_warmup(done)

    async def test_waits_for_a_pending_task(self):
        import asyncio

        ran = []

        async def _warm():
            await asyncio.sleep(0)
            ran.append(True)

        await await_model_cache_warmup(asyncio.create_task(_warm()), timeout=5)
        assert ran == [True]

    async def test_wedged_task_times_out_without_being_cancelled(self):
        import asyncio

        wedged = asyncio.create_task(asyncio.sleep(60))
        await await_model_cache_warmup(wedged, timeout=0.01)
        # ``shield`` must keep the timeout from killing the warm task itself —
        # a later probe may still profit from it finishing.
        assert not wedged.cancelled()
        wedged.cancel()

    async def test_warm_task_cancellation_does_not_error_the_handler(self):
        import asyncio

        warm = asyncio.create_task(asyncio.sleep(60))
        waiter = asyncio.create_task(await_model_cache_warmup(warm, timeout=5))
        await asyncio.sleep(0)
        warm.cancel()
        # The warm task dying (agent shutdown) degrades to the cold-cache
        # shape; the version handler awaiting it must not error.
        await waiter


class TestShimSafeArgv:
    """``shim_safe_argv`` wraps a Windows ``.cmd``/``.bat`` shim in ``cmd /c``
    (a shim is not directly CreateProcess-able); everything else — including
    ``.cmd``-suffixed paths on POSIX — passes through untouched.
    """

    def test_plain_binary_passes_through(self):
        assert shim_safe_argv("/usr/local/bin/agy", "models") == [
            "/usr/local/bin/agy", "models",
        ]

    def test_no_extra_args(self):
        assert shim_safe_argv("codex") == ["codex"]

    @skip_if_not_windows
    def test_cmd_shim_wrapped_on_windows(self):
        argv = shim_safe_argv(r"C:\npm\codex.cmd", "debug", "models", "--json")
        assert argv == ["cmd", "/c", r"C:\npm\codex.cmd", "debug", "models", "--json"]

    @skip_if_not_windows
    def test_bat_shim_wrapped_case_insensitively_on_windows(self):
        argv = shim_safe_argv(r"C:\npm\agy.BAT", "models")
        assert argv == ["cmd", "/c", r"C:\npm\agy.BAT", "models"]

    @skip_if_not_linux
    def test_cmd_suffix_not_wrapped_on_posix(self):
        """The wrap is win32-only: a POSIX file that merely ends in ``.cmd``
        must not be routed through a (nonexistent) ``cmd`` builtin."""
        assert shim_safe_argv("/opt/fake.cmd", "models") == ["/opt/fake.cmd", "models"]


class TestProbeWithRealLaunchers:
    """``probe_cli_available`` over the production launchers: the verdict must
    follow the same override → env → PATH resolution a real spawn uses, so an
    'enabled' mode in the UI implies a spawnable CLI.

    The autouse ``_pin_claude_binary`` / ``_pin_codex_binary`` /
    ``_pin_antigravity_binary`` fixtures pin the module-level resolvers to a
    literal name, so a default launcher resolves 'installed' here; the
    missing-binary cases re-patch the resolver to raise.
    """

    def test_claude_launcher_available_with_pinned_resolver(self):
        from mobaide.core.claude_launcher import ClaudeLauncher

        assert probe_cli_available(ClaudeLauncher()) is True

    def test_codex_launcher_available_with_pinned_resolver(self):
        from mobaide.core.codex_launcher import CodexLauncher

        assert probe_cli_available(CodexLauncher()) is True

    def test_antigravity_launcher_available_with_pinned_resolver(self):
        from mobaide.core.antigravity_launcher import AntigravityLauncher

        assert probe_cli_available(AntigravityLauncher()) is True

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
