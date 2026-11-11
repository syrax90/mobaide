"""Pins on the ``mobaide-agent`` command that ``deploy/install.sh`` puts on PATH.

The generated shim runs under ``/bin/sh`` (its shebang) with ``PATH`` limited to
a stub directory, so ``id``, ``sudo`` and ``runuser`` are fakes and nothing ever
switches user. Paths are spelled the way the shell sees them: Git Bash on
Windows wants ``/c/...``, not ``C:\\...``.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from test_install_script_trap import _BASH, _read_install_sh_text, _sh_function_body


pytestmark = pytest.mark.skipif(_BASH is None, reason="bash not available")

_INSTALL_URL = "https://get.example.test/install.sh"


def _install_sh_constant(name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"$', _read_install_sh_text(), re.M)
    assert match is not None, f"top-level {name}=... not found in install.sh"
    return match.group(1)


def _sh_path(path: Path) -> str:
    """``path`` as the shell spells it (Git Bash: ``C:\\x`` -> ``/c/x``)."""
    if sys.platform != "win32":
        return str(path)
    posix = path.as_posix()
    return f"/{posix[0].lower()}{posix[2:]}"


def _functions(*names: str) -> str:
    return "".join(f"{n}() {{\n{_sh_function_body(n)}\n}}\n" for n in names)


def _globals(prefix: Path, run_user: str) -> str:
    return (
        f"MOBAIDE_AGENT_PREFIX={shlex.quote(_sh_path(prefix))}\n"
        f"RUN_USER={shlex.quote(run_user)}\n"
        f"MOBAIDE_AGENT_INSTALL_URL={shlex.quote(_INSTALL_URL)}\n"
        f"CLI_SHIM_MARKER={shlex.quote(_install_sh_constant('CLI_SHIM_MARKER'))}\n"
    )


def _bash(
    script: str, workdir: Path, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` from a file: Git Bash rebuilds argv from the Windows
    command line and loses backslashes, which ``sh_single_quote``'s sed
    expression depends on, so ``bash -c`` would test a different script."""
    assert _BASH is not None
    harness = workdir / "harness.sh"
    harness.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run(
        [_BASH, str(harness)], capture_output=True, text=True, cwd=cwd
    )


def _write_shim(
    target: Path, prefix: Path, run_user: str = "mobaide", cwd: Path | None = None
) -> Path:
    harness = (
        "set -eu\n"
        + _globals(prefix, run_user)
        + _functions("sh_single_quote", "write_cli_shim")
        + f"write_cli_shim {shlex.quote(_sh_path(target))}\n"
    )
    proc = _bash(harness, target.parent, cwd=cwd)
    assert proc.returncode == 0, f"write_cli_shim failed: {proc.stderr}"
    assert target.is_file()
    return target


def _make_release(prefix: Path, version: str, rc: int = 0) -> None:
    """A stand-in console script that reports how it was called."""
    cli = prefix / "releases" / version / "venv" / "bin" / "mobaide-agent"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(
        "#!/bin/sh\n"
        f'echo "RAN {version}"\n'
        'echo "PREFIX=$MOBAIDE_AGENT_PREFIX"\n'
        'for arg in "$@"; do echo "ARG=<$arg>"; done\n'
        f"exit {rc}\n",
        encoding="utf-8",
        newline="\n",
    )
    cli.chmod(0o755)


def _set_version(prefix: Path, version: str) -> None:
    (prefix / "current_version").write_text(
        f"{version}\n", encoding="utf-8", newline="\n"
    )


def _cli_path(prefix: Path, version: str) -> str:
    return f"{_sh_path(prefix)}/releases/{version}/venv/bin/mobaide-agent"


def _stub_bin(
    tmp_path: Path, uid: int, *, sudo: bool = True, runuser: bool = False
) -> Path:
    """The only PATH the shim sees: a fake ``id``, a ``cat`` and, optionally,
    ``sudo`` / ``runuser`` fakes that print their argv instead of switching
    user."""
    stub = tmp_path / "stub-bin"
    stub.mkdir()
    scripts = {
        "id": f"#!/bin/sh\necho {uid}\n",
        "cat": '#!/bin/sh\nexec /bin/cat "$@"\n',
    }
    record = '#!/bin/sh\necho "{0}"\nfor arg in "$@"; do echo "ARG=<$arg>"; done\n'
    if sudo:
        scripts["sudo"] = record.format("SUDO")
    if runuser:
        scripts["runuser"] = record.format("RUNUSER")
    for name, text in scripts.items():
        path = stub / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    return stub


def _run_shim(
    shim: Path,
    stub: Path,
    *args: str,
    env_prefix: Path | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    env = {k: v for k, v in os.environ.items() if k != "MOBAIDE_AGENT_PREFIX"}
    if env_prefix is not None:
        env["MOBAIDE_AGENT_PREFIX"] = _sh_path(env_prefix)
    return subprocess.run(
        [
            _BASH, "-c", 'PATH="$1"; shift; exec /bin/sh "$@"', "shim-harness",
            _sh_path(stub), _sh_path(shim), *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def _args(stdout: str) -> list[str]:
    return re.findall(r"^ARG=<(.*)>$", stdout, re.M)


class TestGeneratedShim:
    def test_runs_the_current_release_with_the_args_and_the_prefix(self, tmp_path):
        prefix = tmp_path / "mobaide"
        _make_release(prefix, "0.2.0", rc=3)
        _set_version(prefix, "0.2.0")
        shim = _write_shim(tmp_path / "mobaide-agent", prefix)

        result = _run_shim(shim, _stub_bin(tmp_path, uid=1000), "show-qr", "a b")

        assert result.returncode == 3, result.stderr
        assert "RAN 0.2.0" in result.stdout
        assert f"PREFIX={_sh_path(prefix)}" in result.stdout
        assert _args(result.stdout) == ["show-qr", "a b"]
        assert "SUDO" not in result.stdout
        if sys.platform != "win32":
            assert shim.stat().st_mode & 0o777 == 0o755

    def test_prefix_from_the_environment_wins_over_the_baked_one(self, tmp_path):
        baked = tmp_path / "baked"
        _make_release(baked, "0.2.0")
        _set_version(baked, "0.2.0")
        other = tmp_path / "other"
        _make_release(other, "0.5.0")
        _set_version(other, "0.5.0")
        shim = _write_shim(tmp_path / "mobaide-agent", baked)

        result = _run_shim(shim, _stub_bin(tmp_path, uid=1000), env_prefix=other)

        assert "RAN 0.5.0" in result.stdout, result.stderr
        assert f"PREFIX={_sh_path(other)}" in result.stdout

    @pytest.mark.parametrize("version", [None, "0.9.9"], ids=["no-version", "release-gone"])
    def test_no_install_exits_127_with_the_ways_out(self, tmp_path, version):
        prefix = tmp_path / "mobaide"
        _make_release(prefix, "0.2.0")
        if version is not None:
            _set_version(prefix, version)
        shim = _write_shim(tmp_path / "mobaide-agent", prefix)

        result = _run_shim(shim, _stub_bin(tmp_path, uid=1000), "show-qr")

        assert result.returncode == 127, result.stderr
        assert "RAN" not in result.stdout
        assert f"no Mobaide agent is installed at {_sh_path(prefix)}" in result.stderr
        assert f"curl -fsSL {_INSTALL_URL} | bash" in result.stderr
        assert f"sudo rm {_sh_path(shim)}" in result.stderr

    def test_baked_prefix_is_a_literal_not_shell_code(self, tmp_path):
        prefix = tmp_path / "o'neil $(touch pwned) $HOME"
        _make_release(prefix, "0.2.0")
        _set_version(prefix, "0.2.0")
        shim = _write_shim(tmp_path / "mobaide-agent", prefix, cwd=tmp_path)

        result = _run_shim(shim, _stub_bin(tmp_path, uid=1000), cwd=tmp_path)

        assert "RAN 0.2.0" in result.stdout, result.stderr
        assert f"PREFIX={_sh_path(prefix)}" in result.stdout
        assert not (tmp_path / "pwned").exists()


class TestShimTempFile:
    """``write_cli_shim`` runs as root into a directory the agent's user may
    be able to write, so its temp file must not have a guessable name."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_does_not_write_through_a_symlink_at_the_old_temp_name(self, tmp_path):
        victim = tmp_path / "victim"
        victim.write_text("precious\n", encoding="utf-8")
        victim.chmod(0o600)
        target = tmp_path / "bin" / "mobaide-agent"
        target.parent.mkdir()
        harness = (
            "set -eu\n"
            + _globals(tmp_path / "mobaide", "mobaide")
            + _functions("sh_single_quote", "write_cli_shim")
            + f"target={shlex.quote(_sh_path(target))}\n"
            + f'ln -s {shlex.quote(_sh_path(victim))} "$target.tmp.$$"\n'
            + 'write_cli_shim "$target"\n'
        )

        proc = _bash(harness, tmp_path)

        assert proc.returncode == 0, proc.stderr
        assert victim.read_text(encoding="utf-8") == "precious\n"
        assert victim.stat().st_mode & 0o777 == 0o600
        assert not target.is_symlink()
        assert _install_sh_constant("CLI_SHIM_MARKER") in target.read_text(
            encoding="utf-8"
        )

class TestShimAsRoot:
    def test_drops_to_the_run_user_through_sudo(self, tmp_path):
        prefix = tmp_path / "mobaide"
        _make_release(prefix, "0.2.0")
        _set_version(prefix, "0.2.0")
        shim = _write_shim(tmp_path / "mobaide-agent", prefix, run_user="mobaide")

        result = _run_shim(
            shim, _stub_bin(tmp_path, uid=0, runuser=True), "re-pair", "--fresh"
        )

        assert result.returncode == 0, result.stderr
        assert "RAN" not in result.stdout
        assert result.stdout.startswith("SUDO\n")
        assert _args(result.stdout) == [
            "-u", "mobaide", "-H", "--", "env",
            f"MOBAIDE_AGENT_PREFIX={_sh_path(prefix)}",
            _cli_path(prefix, "0.2.0"), "re-pair", "--fresh",
        ]

    def test_falls_back_to_runuser_without_sudo(self, tmp_path):
        prefix = tmp_path / "mobaide"
        _make_release(prefix, "0.2.0")
        _set_version(prefix, "0.2.0")
        shim = _write_shim(tmp_path / "mobaide-agent", prefix, run_user="mobaide")

        result = _run_shim(
            shim, _stub_bin(tmp_path, uid=0, sudo=False, runuser=True), "show-qr"
        )

        assert result.stdout.startswith("RUNUSER\n"), result.stderr
        assert _args(result.stdout) == [
            "-u", "mobaide", "--", "env",
            f"MOBAIDE_AGENT_PREFIX={_sh_path(prefix)}",
            _cli_path(prefix, "0.2.0"), "show-qr",
        ]

    def test_refuses_without_a_way_to_switch_user(self, tmp_path):
        prefix = tmp_path / "mobaide"
        _make_release(prefix, "0.2.0")
        _set_version(prefix, "0.2.0")
        shim = _write_shim(tmp_path / "mobaide-agent", prefix, run_user="mobaide")

        result = _run_shim(shim, _stub_bin(tmp_path, uid=0, sudo=False), "re-pair")

        assert result.returncode == 1
        assert "RAN" not in result.stdout
        assert "run this as mobaide" in result.stderr

    @pytest.mark.parametrize("run_user", ["", "root"])
    def test_runs_directly_when_the_agent_itself_runs_as_root(
        self, tmp_path, run_user
    ):
        prefix = tmp_path / "mobaide"
        _make_release(prefix, "0.2.0")
        _set_version(prefix, "0.2.0")
        shim = _write_shim(tmp_path / "mobaide-agent", prefix, run_user=run_user)

        result = _run_shim(shim, _stub_bin(tmp_path, uid=0), "show-qr")

        assert "RAN 0.2.0" in result.stdout, result.stderr
        assert "SUDO" not in result.stdout


def _install_cli_shim(
    tmp_path: Path, prefix: Path, system_shim: Path, uid: int
) -> subprocess.CompletedProcess[str]:
    """Run ``install_cli_shim`` with ``CLI_SHIM_PATH`` moved under tmp_path
    and ``id -u`` answering ``uid``."""
    harness = (
        "set -eu\n"
        + _globals(prefix, "mobaide")
        + f"CLI_SHIM_PATH={shlex.quote(_sh_path(system_shim))}\n"
        + "info() { printf '[INFO]  %s\\n' \"$*\"; }\n"
        + "warn() { printf '[WARN]  %s\\n' \"$*\" >&2; }\n"
        + f"id() {{ echo {uid}; }}\n"
        + _functions(
            "sh_single_quote", "write_cli_shim", "cli_shim_target_is_ours",
            "install_cli_shim",
        )
        + "install_cli_shim\n"
        + "printf 'CLI_SHIM=<%s>\\n' \"$CLI_SHIM\"\n"
        + "printf 'CLI_CMD=<%s>\\n' \"$CLI_CMD\"\n"
    )
    return _bash(harness, tmp_path)


def _outputs(stdout: str) -> dict[str, str]:
    return dict(re.findall(r"^(CLI_SHIM|CLI_CMD)=<(.*)>$", stdout, re.M))


class TestInstallCliShim:
    @pytest.fixture
    def prefix(self, tmp_path) -> Path:
        prefix = tmp_path / "mobaide"
        prefix.mkdir()
        return prefix

    @pytest.fixture
    def system_shim(self, tmp_path) -> Path:
        return tmp_path / "usr-local-bin" / "mobaide-agent"

    def _marker(self) -> str:
        return _install_sh_constant("CLI_SHIM_MARKER")

    def test_root_install_puts_the_bare_command_on_path(
        self, tmp_path, prefix, system_shim
    ):
        result = _install_cli_shim(tmp_path, prefix, system_shim, uid=0)

        assert result.returncode == 0, result.stderr
        assert _outputs(result.stdout) == {
            "CLI_SHIM": _sh_path(system_shim),
            "CLI_CMD": "mobaide-agent",
        }
        text = system_shim.read_text(encoding="utf-8")
        assert self._marker() in text
        assert f"default_prefix='{_sh_path(prefix)}'" in text
        assert not (prefix / "bin" / "mobaide-agent").exists()

    def test_root_install_rewrites_its_own_earlier_shim(
        self, tmp_path, prefix, system_shim
    ):
        system_shim.parent.mkdir()
        system_shim.write_text(
            f"#!/bin/sh\n# {self._marker()}, written by the Mobaide agent "
            "installer.\ndefault_prefix='/old/prefix'\n",
            encoding="utf-8",
        )

        result = _install_cli_shim(tmp_path, prefix, system_shim, uid=0)

        assert _outputs(result.stdout)["CLI_CMD"] == "mobaide-agent", result.stderr
        text = system_shim.read_text(encoding="utf-8")
        assert "/old/prefix" not in text
        assert f"default_prefix='{_sh_path(prefix)}'" in text

    def test_root_install_leaves_a_foreign_command_alone(
        self, tmp_path, prefix, system_shim
    ):
        system_shim.parent.mkdir()
        foreign = "#!/bin/sh\necho someone else's mobaide-agent\n"
        system_shim.write_text(foreign, encoding="utf-8")

        result = _install_cli_shim(tmp_path, prefix, system_shim, uid=0)

        assert result.returncode == 0, result.stderr
        assert system_shim.read_text(encoding="utf-8") == foreign
        fallback = f"{_sh_path(prefix)}/bin/mobaide-agent"
        assert _outputs(result.stdout) == {"CLI_SHIM": fallback, "CLI_CMD": fallback}
        assert self._marker() in (prefix / "bin" / "mobaide-agent").read_text(
            encoding="utf-8"
        )
        assert "not written by this installer" in result.stderr

    def test_non_root_install_prints_the_full_path(
        self, tmp_path, prefix, system_shim
    ):
        result = _install_cli_shim(tmp_path, prefix, system_shim, uid=501)

        assert result.returncode == 0, result.stderr
        fallback = f"{_sh_path(prefix)}/bin/mobaide-agent"
        assert _outputs(result.stdout) == {"CLI_SHIM": fallback, "CLI_CMD": fallback}
        assert (prefix / "bin" / "mobaide-agent").is_file()
        assert not system_shim.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_root_install_replaces_a_hand_made_symlink_into_a_release(
        self, tmp_path, prefix, system_shim
    ):
        _make_release(prefix, "0.2.0")
        console_script = prefix / "releases" / "0.2.0" / "venv" / "bin" / "mobaide-agent"
        before = console_script.read_text(encoding="utf-8")
        system_shim.parent.mkdir()
        system_shim.symlink_to(console_script)

        result = _install_cli_shim(tmp_path, prefix, system_shim, uid=0)

        assert _outputs(result.stdout)["CLI_CMD"] == "mobaide-agent", result.stderr
        assert not system_shim.is_symlink()
        assert self._marker() in system_shim.read_text(encoding="utf-8")
        assert console_script.read_text(encoding="utf-8") == before

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
    def test_root_install_leaves_a_symlink_elsewhere_alone(
        self, tmp_path, prefix, system_shim
    ):
        elsewhere = tmp_path / "other-tool"
        elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
        system_shim.parent.mkdir()
        system_shim.symlink_to(elsewhere)

        result = _install_cli_shim(tmp_path, prefix, system_shim, uid=0)

        assert _outputs(result.stdout)["CLI_CMD"] == (
            f"{_sh_path(prefix)}/bin/mobaide-agent"
        ), result.stderr
        assert system_shim.is_symlink()
        assert elsewhere.read_text(encoding="utf-8") == "#!/bin/sh\n"


class TestUninstallHelperShimLine:
    """The root helper ``install_systemd_uninstall_helper`` writes deletes
    ``/usr/local/bin/mobaide-agent``, but only a file carrying the marker."""

    @staticmethod
    def _run_helper_line(tmp_path: Path, system_shim: Path) -> None:
        lines = [
            line
            for line in _sh_function_body("install_systemd_uninstall_helper").splitlines()
            if "CLI_SHIM_MARKER" in line
        ]
        assert len(lines) == 1, lines
        helper = tmp_path / "helper.sh"
        # Expand the line exactly as the helper's unquoted heredoc does.
        harness = (
            f"CLI_SHIM_MARKER={shlex.quote(_install_sh_constant('CLI_SHIM_MARKER'))}\n"
            f"CLI_SHIM_PATH={shlex.quote(_sh_path(system_shim))}\n"
            f"cat > {shlex.quote(_sh_path(helper))} <<EOF\n{lines[0]}\nEOF\n"
            f"/bin/sh {shlex.quote(_sh_path(helper))}\n"
        )
        proc = _bash(harness, tmp_path)
        assert proc.returncode == 0, proc.stderr

    def test_removes_the_installers_shim(self, tmp_path):
        prefix = tmp_path / "mobaide"
        system_shim = _write_shim(tmp_path / "mobaide-agent", prefix)

        self._run_helper_line(tmp_path, system_shim)

        assert not system_shim.exists()

    def test_keeps_a_foreign_command(self, tmp_path):
        system_shim = tmp_path / "mobaide-agent"
        system_shim.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

        self._run_helper_line(tmp_path, system_shim)

        assert system_shim.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_main_installs_the_shim_before_the_qr_step_uses_it() -> None:
    """``show_pairing_qr_from_service`` renders through ``$CLI_SHIM`` and
    ``print_completion`` prints ``$CLI_CMD``; under ``set -u`` either would
    abort the install if ``install_cli_shim`` had not run yet."""
    body = _sh_function_body("main")
    shim_at = body.find("install_cli_shim")
    assert shim_at != -1, "main must call install_cli_shim. Body:\n" + body
    assert body.find("install_launchd_plist") < shim_at, body
    assert body.find("install_systemd_unit") < shim_at, body
    assert shim_at < body.find("show_pairing_qr_from_service"), body
    assert shim_at < body.find("print_completion"), body
