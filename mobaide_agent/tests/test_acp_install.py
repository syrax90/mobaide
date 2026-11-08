"""Pinned adapter installs (``mobaide/acp/install.py``) against a fake
``npm``: one install per version, the npm flags and environment, a ``bin``
entry that must stay inside its package, failures that leave nothing behind
(a timed-out npm is killed with its children), leftovers of unfinished
installs, and the pruning of other versions."""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from mobaide.acp import install as install_mod
from mobaide.acp.install import AdapterInstaller, AdapterInstallError, NpmAdapter
from tests.conftest import skip_if_not_linux

pytestmark = skip_if_not_linux

ADAPTER = NpmAdapter("@agentclientprotocol/codex-acp", "2.5.0", "codex-acp")

# Fake ``npm install --prefix <dir> <package>@<version>``: logs argv, cwd and
# inherited npm_config_* names, then lays out the package. Env knobs: FAKE_NPM_BIN
# (package.json ``bin`` path), FAKE_NPM_FAIL (exit 1), FAKE_NPM_SLEEP (stall),
# FAKE_NPM_CHILD (start a long-lived child, write its pid there).
FAKE_NPM = r"""#!{python}
import json, os, sys, time
args = sys.argv[1:]
with open(os.environ["FAKE_NPM_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({
        "argv": args,
        "cwd": os.getcwd(),
        "npm_config": sorted(k for k in os.environ if k.lower().startswith("npm_config_")),
    }) + "\n")
if os.environ.get("FAKE_NPM_CHILD"):
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.environ["FAKE_NPM_CHILD"], "w", encoding="utf-8") as f:
        f.write(str(child.pid))
if os.environ.get("FAKE_NPM_SLEEP"):
    time.sleep(float(os.environ["FAKE_NPM_SLEEP"]))
if os.environ.get("FAKE_NPM_FAIL"):
    sys.stderr.write("npm ERR! code E404\nnpm ERR! 404 Not Found - GET https://registry.npmjs.org/x\n\n")
    sys.exit(1)
prefix = args[args.index("--prefix") + 1]
package, _, version = args[-1].rpartition("@")
package_dir = os.path.join(prefix, "node_modules", *package.split("/"))
bin_rel = os.environ.get("FAKE_NPM_BIN", "dist/index.js")
target = os.path.join(package_dir, bin_rel)
os.makedirs(package_dir, exist_ok=True)
os.makedirs(os.path.dirname(target), exist_ok=True)
with open(target, "w", encoding="utf-8") as f:
    f.write("console.log('adapter')\n")
with open(os.path.join(package_dir, "package.json"), "w", encoding="utf-8") as f:
    json.dump({"name": package, "version": version, "bin": {os.environ["FAKE_NPM_BIN_NAME"]: bin_rel}}, f)
"""


def fake_npm(directory: Path) -> str:
    """Write the fake ``npm`` into ``directory``; returns its path."""
    path = directory / "npm"
    path.write_text(FAKE_NPM.replace("{python}", sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def npm_env(log: Path, bin_name: str, **extra: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "FAKE_NPM_LOG": str(log),
        "FAKE_NPM_BIN_NAME": bin_name,
        **extra,
    }


def _calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def npm(tmp_path) -> str:
    tools = tmp_path / "tools"
    tools.mkdir()
    return fake_npm(tools)


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "acp-adapters"


@pytest.fixture
def log(tmp_path) -> Path:
    return tmp_path / "npm.log"


def _gone(pid: int, timeout: float = 5.0) -> bool:
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True
    _, alive = psutil.wait_procs([proc], timeout=timeout)
    return not alive


def _package_dir(base: Path) -> Path:
    return base / "node_modules" / "@agentclientprotocol" / "codex-acp"


class TestInstall:
    def test_installs_the_pinned_version_without_scripts_or_optional_packages(
        self, npm, root, log
    ):
        """The flags override the npm_config_* of the environment, which still
        reaches npm, so a registry or proxy set there applies."""
        installer = AdapterInstaller(ADAPTER, root)
        env = npm_env(
            log,
            ADAPTER.bin_name,
            npm_config_ignore_scripts="false",
            NPM_CONFIG_OMIT="dev",
            npm_config_registry="https://registry.example.test/",
        )

        entry = installer.install(npm, env)

        assert entry == (_package_dir(root / "codex-acp-2.5.0") / "dist" / "index.js").resolve()
        assert (root / "codex-acp-2.5.0" / install_mod.INSTALLED_MARKER).read_text() == "2.5.0"
        [call] = _calls(log)
        argv = call["argv"]
        assert argv[0] == "install"
        assert argv[-1] == "@agentclientprotocol/codex-acp@2.5.0"
        assert {"--ignore-scripts", "--omit=optional"} <= set(argv)
        staging = Path(argv[argv.index("--prefix") + 1])
        assert staging.parent == root
        assert staging.name.startswith(f".staging-codex-acp-2.5.0-{os.getpid()}-")
        assert os.path.realpath(call["cwd"]) == os.path.realpath(root)
        assert call["npm_config"] == [
            "NPM_CONFIG_OMIT",
            "npm_config_ignore_scripts",
            "npm_config_registry",
        ]

    @pytest.mark.parametrize(
        ("resolve_before", "expected"),
        [("2026-10-30T00:00:00Z", ["--before=2026-10-30T00:00:00Z"]), (None, [])],
        ids=["pinned-date", "no-date"],
    )
    def test_dependencies_resolve_as_published_by_the_pinned_date(
        self, npm, root, log, resolve_before, expected
    ):
        adapter = NpmAdapter(ADAPTER.package, ADAPTER.version, ADAPTER.bin_name, resolve_before)

        AdapterInstaller(adapter, root).install(npm, npm_env(log, ADAPTER.bin_name))

        [call] = _calls(log)
        assert [arg for arg in call["argv"] if arg.startswith("--before")] == expected

    def test_a_complete_install_is_reused_without_npm(self, npm, root, log):
        first = AdapterInstaller(ADAPTER, root).install(npm, npm_env(log, ADAPTER.bin_name))

        again = AdapterInstaller(ADAPTER, root)

        assert again.entry() == first
        assert again.install(npm, npm_env(log, ADAPTER.bin_name)) == first
        assert len(_calls(log)) == 1

    def test_an_install_without_the_marker_is_done_again(self, npm, root, log):
        """A copy that an interrupted install left behind is never run."""
        half = root / "codex-acp-2.5.0"
        package = _package_dir(half)
        (package / "dist").mkdir(parents=True)
        (package / "dist" / "index.js").write_text("// partial\n", encoding="utf-8")
        (package / "package.json").write_text(
            json.dumps({"bin": {"codex-acp": "dist/index.js"}}), encoding="utf-8"
        )
        installer = AdapterInstaller(ADAPTER, root)

        assert installer.entry() is None

        entry = installer.install(npm, npm_env(log, ADAPTER.bin_name))

        assert len(_calls(log)) == 1
        assert entry.read_text(encoding="utf-8") == "console.log('adapter')\n"
        assert (half / install_mod.INSTALLED_MARKER).is_file()

    def test_threads_that_install_at_once_run_npm_once(self, npm, root, log):
        installer = AdapterInstaller(ADAPTER, root)
        env = npm_env(log, ADAPTER.bin_name, FAKE_NPM_SLEEP="0.3")

        with ThreadPoolExecutor(max_workers=3) as pool:
            entries = list(pool.map(lambda _: installer.install(npm, env), range(3)))

        assert len(set(entries)) == 1
        assert len(_calls(log)) == 1

    def test_a_bin_given_as_the_package_name_string_is_accepted(self, root, tmp_path):
        """``"bin": "cli.js"`` is npm's short form for a bin named like the package."""
        done = root / "codex-acp-2.5.0"
        package = _package_dir(done)
        package.mkdir(parents=True)
        (package / "cli.js").write_text("// adapter\n", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({"bin": "cli.js"}), encoding="utf-8")
        (done / install_mod.INSTALLED_MARKER).write_text("2.5.0", encoding="utf-8")

        assert AdapterInstaller(ADAPTER, root).entry() == (package / "cli.js").resolve()


class TestBinMustStayInsideThePackage:
    @pytest.mark.parametrize(
        "bin_path",
        ["../../../outside.js", "OUTSIDE_ABSOLUTE"],
        ids=["parent-path", "absolute-path"],
    )
    def test_a_bin_outside_the_package_fails_the_install(
        self, npm, root, log, tmp_path, bin_path
    ):
        if bin_path == "OUTSIDE_ABSOLUTE":
            bin_path = str(tmp_path / "elsewhere" / "outside.js")
        installer = AdapterInstaller(ADAPTER, root)

        with pytest.raises(AdapterInstallError, match="has no codex-acp command"):
            installer.install(npm, npm_env(log, ADAPTER.bin_name, FAKE_NPM_BIN=bin_path))

        assert installer.entry() is None
        assert os.listdir(root) == []

    def test_a_bin_symlinked_out_of_the_package_is_not_run(self, root, tmp_path):
        outside = tmp_path / "outside.js"
        outside.write_text("// not the adapter\n", encoding="utf-8")
        done = root / "codex-acp-2.5.0"
        package = _package_dir(done)
        (package / "dist").mkdir(parents=True)
        (package / "dist" / "index.js").symlink_to(outside)
        (package / "package.json").write_text(
            json.dumps({"bin": {"codex-acp": "dist/index.js"}}), encoding="utf-8"
        )
        (done / install_mod.INSTALLED_MARKER).write_text("2.5.0", encoding="utf-8")

        assert AdapterInstaller(ADAPTER, root).entry() is None

    def test_a_package_without_the_command_fails_the_install(self, npm, root, log):
        installer = AdapterInstaller(ADAPTER, root)

        with pytest.raises(AdapterInstallError, match="has no codex-acp command"):
            installer.install(npm, npm_env(log, "some-other-bin"))

        assert os.listdir(root) == []


class TestFailures:
    def test_npm_failure_reports_its_last_line_and_leaves_nothing(self, npm, root, log):
        installer = AdapterInstaller(ADAPTER, root)

        with pytest.raises(AdapterInstallError) as excinfo:
            installer.install(npm, npm_env(log, ADAPTER.bin_name, FAKE_NPM_FAIL="1"))

        assert str(excinfo.value) == (
            "Could not download @agentclientprotocol/codex-acp@2.5.0: "
            "npm ERR! 404 Not Found - GET https://registry.npmjs.org/x"
        )
        assert os.listdir(root) == []
        assert installer.entry() is None

    def test_slow_npm_times_out(self, npm, root, log, monkeypatch):
        monkeypatch.setattr(install_mod, "INSTALL_TIMEOUT", 0.3)
        installer = AdapterInstaller(ADAPTER, root)

        with pytest.raises(AdapterInstallError, match="took too long"):
            installer.install(npm, npm_env(log, ADAPTER.bin_name, FAKE_NPM_SLEEP="5"))

        assert os.listdir(root) == []

    @pytest.mark.timeout(30)
    def test_a_timed_out_npm_is_killed_with_its_children(self, npm, root, log, tmp_path, monkeypatch):
        """A child that npm started holds its output pipe: left running, the
        install would wait for it after the timeout."""
        monkeypatch.setattr(install_mod, "INSTALL_TIMEOUT", 1.5)
        pid_file = tmp_path / "child.pid"
        installer = AdapterInstaller(ADAPTER, root)

        with pytest.raises(AdapterInstallError, match="took too long"):
            installer.install(
                npm,
                npm_env(log, ADAPTER.bin_name, FAKE_NPM_SLEEP="60", FAKE_NPM_CHILD=str(pid_file)),
            )

        assert _gone(int(pid_file.read_text(encoding="utf-8")))
        assert os.listdir(root) == []

    def test_a_folder_it_cannot_create_is_an_install_error(self, npm, log, tmp_path):
        blocker = tmp_path / "state"
        blocker.write_text("not a folder", encoding="utf-8")
        installer = AdapterInstaller(ADAPTER, blocker / "acp-adapters")

        with pytest.raises(
            AdapterInstallError, match=r"^Could not install @agentclientprotocol/codex-acp@2\.5\.0: "
        ):
            installer.install(npm, npm_env(log, ADAPTER.bin_name))

        assert _calls(log) == []

    def test_missing_npm(self, root, log, tmp_path):
        installer = AdapterInstaller(ADAPTER, root)

        with pytest.raises(AdapterInstallError, match="Could not run npm"):
            installer.install(str(tmp_path / "no-npm"), npm_env(log, ADAPTER.bin_name))

    def test_a_failed_install_can_be_retried(self, npm, root, log):
        installer = AdapterInstaller(ADAPTER, root)
        with pytest.raises(AdapterInstallError):
            installer.install(npm, npm_env(log, ADAPTER.bin_name, FAKE_NPM_FAIL="1"))

        entry = installer.install(npm, npm_env(log, ADAPTER.bin_name))

        assert installer.entry() == entry
        assert len(_calls(log)) == 2


class TestStaleStaging:
    def test_only_old_leftovers_of_this_adapter_are_removed(self, npm, root, log):
        """A recent staging folder may belong to an install another agent
        process runs right now."""
        old = time.time() - install_mod.STALE_STAGING_SECONDS - 60
        names = {
            "stale": ".staging-codex-acp-2.5.0-111-deadbeef",
            "fresh": ".staging-codex-acp-2.5.0-222-cafebabe",
            "other": ".staging-claude-agent-acp-0.101.0-333-0badf00d",
        }
        for key, name in names.items():
            folder = root / name / "node_modules"
            folder.mkdir(parents=True)
            if key != "fresh":
                os.utime(root / name, (old, old))

        AdapterInstaller(ADAPTER, root).install(npm, npm_env(log, ADAPTER.bin_name))

        assert sorted(os.listdir(root)) == sorted(
            [names["fresh"], names["other"], "codex-acp-2.5.0"]
        )


class TestPrune:
    def test_other_versions_go_and_other_adapters_stay(self, npm, root, log):
        for name in ("codex-acp-2.4.0", "codex-acp-extra-1.0.0", "claude-agent-acp-0.101.0"):
            (root / name).mkdir(parents=True)
            (root / name / "keep.txt").write_text(name, encoding="utf-8")

        AdapterInstaller(ADAPTER, root).install(npm, npm_env(log, ADAPTER.bin_name))

        assert sorted(os.listdir(root)) == [
            "claude-agent-acp-0.101.0",
            "codex-acp-2.5.0",
            "codex-acp-extra-1.0.0",
        ]
