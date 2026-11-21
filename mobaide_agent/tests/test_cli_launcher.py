"""Tests for the CLI-availability probe primitives in ``core.cli_launcher``.

Covers ``probe_cli_available`` (resolve-success → True, ``CliBinaryNotFoundError``
→ False, never spawns a subprocess), ``build_cli_capabilities`` (the shared
``GET /api/version`` builder that drives the Android mode-picker gate and must
report identical availability over both the direct and relay transports —
plus the cache-only ``models`` key), ``warm_cli_model_caches`` (the duck-typed
startup warm task), and the ``shim_safe_argv`` Windows-shim wrapper.
"""
from __future__ import annotations

import os
import stat
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest

from mobaide.core import cli_launcher
from mobaide.core.cli_launcher import (
    CliBinaryNotFoundError,
    await_model_cache_warmup,
    build_cli_capabilities,
    cli_help_text,
    cli_version,
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


    def test_none_registry_is_tolerated(self):
        """``app_state.cli_launchers`` may be unset on a partially-built state;
        the builder coerces a falsy registry to ``{}`` rather than raising."""
        assert build_cli_capabilities(None) == {}  # type: ignore[arg-type]


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


    def test_models_key_omitted_when_cached_models_empty(self):
        """An empty tuple is falsy: the key must be omitted — never emitted as
        an empty list — so clients fall back to their hardcoded lists on the
        same absent signal for old and new agents alike."""
        caps = build_cli_capabilities({"codex": _launcher("/bin/codex", models=())})
        assert caps == {"codex": {"available": True}}


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


    def test_models_pending_marks_only_cold_enumeration_capable_launchers(self):
        """``models_pending=True`` (warm task still running) marks a launcher
        that duck-types ``list_models`` and whose own cache is still cold —
        telling clients the absent ``models``/``model_details`` are a cache
        still warming, not an agent without enumeration. It must never mark a
        launcher that cannot enumerate, NOR one whose own enumeration already
        memoized a result: the warm task probes sequentially, and a finished
        CLI must not read as pending (and stay un-reconciled) merely because a
        sibling's probe is still running."""
        cold = _launcher("/bin/codex")
        cold.list_models = AsyncMock()
        warm = _launcher("/bin/agy", models=["Gemini 3.1 Flash"])
        warm.list_models = AsyncMock()
        claude = _launcher("/bin/claude")
        del claude.list_models  # getattr now raises AttributeError

        caps = build_cli_capabilities(
            {"codex": cold, "antigravity": warm, "claude": claude},
            models_pending=True,
        )

        assert caps == {
            "codex": {"available": True, "models_pending": True},
            "antigravity": {"available": True, "models": ["Gemini 3.1 Flash"]},
            "claude": {"available": True},
        }

    def test_models_pending_omitted_never_false_by_default(self):
        """The marker follows the omitted-never-false convention: a finished
        (or absent) warm task emits no key at all."""
        codex = _launcher("/bin/codex", models=["gpt-5.6-sol"])
        codex.list_models = AsyncMock()

        caps = build_cli_capabilities({"codex": codex})

        assert caps == {"codex": {"available": True, "models": ["gpt-5.6-sol"]}}


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


    @skip_if_not_windows
    def test_cmd_shim_in_a_spaced_folder_gets_a_spaced_argument(self, tmp_path):
        """A shim under ``C:\\Program Files`` with one more quoted argument
        made ``cmd /c`` drop the outer quotes and run ``C:\\Program``."""
        folder = tmp_path / "Program Files (x86)"
        folder.mkdir()
        shim = folder / "npm.cmd"
        shim.write_text("@echo off\r\necho [%~1] [%~2]\r\nexit /b 3\r\n", encoding="ascii")
        spaced = str(tmp_path / "Mobaide Agent" / "acp-adapters")

        proc = subprocess.run(
            shim_safe_argv(str(shim), "install", spaced), capture_output=True, text=True,
        )

        assert proc.stderr == ""
        assert proc.stdout.strip() == f"[install] [{spaced}]"
        assert proc.returncode == 3


    @skip_if_not_linux
    def test_cmd_suffix_not_wrapped_on_posix(self):
        """The wrap is win32-only: a POSIX file that merely ends in ``.cmd``
        must not be routed through a (nonexistent) ``cmd`` builtin."""
        assert shim_safe_argv("/opt/fake.cmd", "models") == ["/opt/fake.cmd", "models"]


def _fake_cli(path, body: str):
    """A POSIX script standing in for ``<cli> --version``."""
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@skip_if_not_linux
class TestCliVersion:
    """``cli_version`` gates Code Mode: Claude Code refuses it below a minimum
    version and OpenCode 1 refuses the rule that turns it off."""

    @pytest.mark.parametrize(
        ("output", "version"),
        [
            ("2.1.323 (Claude Code)", (2, 1, 323)),
            ("opencode 1.4", (1, 4, 0)),
            ("v0.70.2-beta.1", (0, 70, 2)),
        ],
        ids=["claude-style", "no-patch", "prefixed"],
    )
    def test_reads_the_first_version_in_the_output(self, tmp_path, output, version):
        cli = _fake_cli(tmp_path / "cli", f'echo "{output}"\n')

        assert cli_version(cli) == version

    @pytest.mark.parametrize(
        "body",
        ['echo "1.2.3"; exit 1\n', 'echo "no version here"\n'],
        ids=["failed-run", "no-version"],
    )
    def test_none_when_the_cli_reports_no_version(self, tmp_path, body):
        assert cli_version(_fake_cli(tmp_path / "cli", body)) is None

    def test_none_for_a_missing_cli(self, tmp_path):
        assert cli_version(str(tmp_path / "gone")) is None

    def test_runs_the_cli_with_the_launch_env(self, tmp_path):
        """An npm install is a Node script: its ``node`` is found only on the
        PATH the launcher builds, not on the agent's own PATH."""
        node_dir = tmp_path / "node-bin"
        node_dir.mkdir()
        _fake_cli(node_dir / "mobaide-fake-node", 'echo "4.5.6"\n')
        cli = tmp_path / "cli"
        cli.write_text("#!/usr/bin/env mobaide-fake-node\n", encoding="utf-8")
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
        env = {**os.environ, "PATH": f"{node_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"}

        assert cli_version(str(cli), env) == (4, 5, 6)

    def test_asks_again_only_after_the_cli_file_changes(self, tmp_path):
        """An update replaces the file: a new mtime must not reuse the
        version of the CLI it replaced."""
        runs = tmp_path / "runs"
        cli = _fake_cli(tmp_path / "cli", f'echo run >> "{runs}"\necho "1.0.0"\n')

        assert cli_version(cli) == (1, 0, 0)
        assert cli_version(cli) == (1, 0, 0)
        assert runs.read_text().count("run") == 1

        mtime = os.stat(cli).st_mtime_ns
        os.utime(cli, ns=(mtime + 1_000_000_000, mtime + 1_000_000_000))

        assert cli_version(cli) == (1, 0, 0)
        assert runs.read_text().count("run") == 2


@skip_if_not_linux
class TestCliHelpText:
    """The Junie launcher adds ``--config-location`` only when the help of
    the binary it will run lists it."""

    def test_reads_stdout_and_stderr_with_the_launch_env(self, tmp_path):
        cli = _fake_cli(
            tmp_path / "cli",
            'echo "$1 --config-location=<path>"\necho "--brave $MOBAIDE_FAKE_ENV" >&2\n',
        )

        text = cli_help_text(cli, {**os.environ, "MOBAIDE_FAKE_ENV": "from-launch-env"})

        assert text is not None
        assert "--help --config-location=<path>" in text
        assert "--brave from-launch-env" in text

    def test_none_for_a_missing_cli(self, tmp_path):
        assert cli_help_text(str(tmp_path / "gone")) is None


class TestRequireCodeModeVersion:
    """The shared Code Mode gate: a CLI older than the minimum stops the
    launch with what to do; a version that cannot be read does not."""

    @staticmethod
    def _reports(monkeypatch, version):
        asked: list[tuple[str, dict[str, str] | None]] = []

        def _version(cli_bin, env=None):
            asked.append((cli_bin, env))
            return version

        monkeypatch.setattr(cli_launcher, "cli_version", _version)
        return asked

    async def test_an_older_cli_fails_with_the_versions_and_the_update_command(
        self, monkeypatch,
    ):
        self._reports(monkeypatch, (1, 0, 9))

        with pytest.raises(ValueError) as excinfo:
            await cli_launcher.require_code_mode_version(
                "/opt/pi", {"PATH": "/opt"}, (1, 1, 0),
                product="Pi", update_command="pi update",
            )

        assert str(excinfo.value) == (
            "Code Mode needs Pi 1.1.0 or later. This PC has 1.0.9: "
            "run `pi update` there, or turn Code Mode off."
        )

    @pytest.mark.parametrize(
        "version", [(2, 1, 323), (2, 2, 0), None], ids=["minimum", "newer", "unknown"],
    )
    async def test_passes_the_minimum_a_newer_or_an_unknown_version(
        self, monkeypatch, version,
    ):
        self._reports(monkeypatch, version)

        await cli_launcher.require_code_mode_version(
            "/opt/claude", {}, (2, 1, 323),
            product="Claude Code", update_command="claude update",
        )

    async def test_asks_the_given_binary_with_the_launch_env(self, monkeypatch):
        """The launch env carries the PATH an npm install needs for ``node``."""
        asked = self._reports(monkeypatch, (9, 0, 0))
        env = {"PATH": "/opt/node/bin"}

        await cli_launcher.require_code_mode_version(
            "/opt/claude", env, (2, 1, 323),
            product="Claude Code", update_command="claude update",
        )

        assert asked == [("/opt/claude", env)]


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
