"""Unit tests for the hardened git runner in ``mobaide.core.git_exec``.

``run_capped`` is exercised with ``sys.executable -c`` scripts so timeout,
byte cap and process-tree kill behave identically on every OS without a
fake ``git`` on PATH.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time

import psutil
import pytest

from mobaide.core import git_exec
from mobaide.core.git_exec import (
    GIT_ARGV_PREFIX,
    CmdResult,
    GitCwdMissingError,
    GitUnavailableError,
    ProcessHandle,
    git_binary,
    git_env,
    git_version,
    reset_caches,
    run_capped,
    run_git,
)

GIT_BIN = shutil.which("git")
requires_git = pytest.mark.skipif(GIT_BIN is None, reason="git binary not on PATH")


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _process_gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


class TestRunCapped:
    def test_captures_both_streams_and_exit_code(self):
        result = run_capped(
            _py(
                "import sys; sys.stdout.write('out'); "
                "sys.stderr.write('err'); sys.exit(3)"
            ),
            cwd=None, timeout=30.0, max_bytes=1024,
        )

        assert result == CmdResult(rc=3, stdout=b"out", stderr=b"err")

    def test_bytes_mode_preserves_crlf(self):
        result = run_capped(
            _py("import sys; sys.stdout.buffer.write(b'a\\r\\nb\\r\\n')"),
            cwd=None, timeout=30.0, max_bytes=1024,
        )

        assert result.stdout == b"a\r\nb\r\n"

    def test_large_stderr_does_not_deadlock_stdout(self):
        result = run_capped(
            _py(
                "import sys; sys.stderr.write('e' * 200000); "
                "sys.stderr.flush(); sys.stdout.write('done')"
            ),
            cwd=None, timeout=30.0, max_bytes=1024,
        )

        assert result.rc == 0
        assert result.stdout == b"done"
        assert len(result.stderr) == 64 * 1024

    def test_max_bytes_truncates_and_flags(self):
        result = run_capped(
            _py("import sys; sys.stdout.write('x' * 300000); sys.stdout.flush()"),
            cwd=None, timeout=30.0, max_bytes=1000,
        )

        assert result.truncated is True
        assert result.timed_out is False
        assert len(result.stdout) == 1000

    def test_timeout_kills_process_and_flags(self):
        started = time.monotonic()

        result = run_capped(
            _py("import time; time.sleep(60)"),
            cwd=None, timeout=0.5, max_bytes=1024,
        )

        assert result.timed_out is True
        assert result.rc != 0
        assert time.monotonic() - started < 30

    def test_timeout_kills_grandchild_holding_the_pipe(self):
        code = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(p.pid, flush=True)\n"
            "time.sleep(60)\n"
        )

        result = run_capped(_py(code), cwd=None, timeout=2.0, max_bytes=1024)

        assert result.timed_out is True
        grandchild = int(result.stdout.split()[0])
        deadline = time.monotonic() + 10
        while not _process_gone(grandchild):
            if time.monotonic() > deadline:
                pytest.fail(f"grandchild {grandchild} survived the tree kill")
            time.sleep(0.05)

    def test_unspawnable_binary_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            run_capped(
                [str(tmp_path / "no-such-binary")],
                cwd=None, timeout=5.0, max_bytes=1024,
            )

    def test_stdin_larger_than_the_pipe_buffer_is_delivered_whole(self):
        payload = b"p" * 500_000

        result = run_capped(
            _py("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"),
            cwd=None, timeout=30.0, max_bytes=1_000_000, stdin=payload,
        )

        assert result == CmdResult(rc=0, stdout=payload, stderr=b"")

    def test_no_stdin_is_an_immediate_eof(self):
        result = run_capped(
            _py("import sys; sys.stdout.write(repr(sys.stdin.read()))"),
            cwd=None, timeout=30.0, max_bytes=1024,
        )

        assert result == CmdResult(rc=0, stdout=b"''", stderr=b"")

    def test_child_that_never_reads_stdin_still_times_out(self):
        started = time.monotonic()

        result = run_capped(
            _py("import time; time.sleep(60)"),
            cwd=None, timeout=0.5, max_bytes=1024, stdin=b"z" * 500_000,
        )

        assert result.timed_out is True
        assert time.monotonic() - started < 30

    def test_child_that_exits_before_reading_stdin_is_not_an_error(self):
        result = run_capped(
            _py("import sys; sys.exit(4)"),
            cwd=None, timeout=30.0, max_bytes=1024, stdin=b"z" * 500_000,
        )

        assert result == CmdResult(rc=4, stdout=b"", stderr=b"")


class TestGitEnv:
    def test_scrubs_redirecting_keys_and_forces_locale(self, monkeypatch):
        scrubbed = (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE",
            "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_CONFIG_PARAMETERS",
        )
        for key in scrubbed:
            monkeypatch.setenv(key, "set")
        monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
        monkeypatch.setenv("KEEP_ME", "yes")

        env = git_env({"EXTRA": "1"})

        assert not any(key in env for key in scrubbed)
        assert env["LC_ALL"] == "C"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert env["KEEP_ME"] == "yes"
        assert env["EXTRA"] == "1"

    def test_scrubs_every_git_prefixed_key_but_keeps_the_rest(self, monkeypatch):
        injected = (
            "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_TRACE", "GIT_CONFIG_GLOBAL",
        )
        for key in injected:
            monkeypatch.setenv(key, "set")
        monkeypatch.setenv("HOME", "/home/keep")
        monkeypatch.setenv("PATH", "/usr/bin")

        env = git_env()

        assert not any(key in env for key in injected)
        assert {k for k in env if k.startswith("GIT_")} == {
            "GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS"}
        assert (env["HOME"], env["PATH"], env["LC_ALL"]) == ("/home/keep", "/usr/bin", "C")


class TestGitBinary:
    def test_hit_is_cached_but_miss_is_retried(self, monkeypatch):
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: None)
        assert git_binary() is None

        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: "/opt/git")
        assert git_binary() == "/opt/git"

        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: None)
        assert git_binary() == "/opt/git"

        reset_caches()
        assert git_binary() is None


class TestRunGit:
    async def test_no_binary_raises_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: None)

        with pytest.raises(GitUnavailableError):
            await run_git(["status"], cwd=str(tmp_path), timeout=5.0, max_bytes=1024)

    @requires_git
    async def test_missing_cwd_raises_cwd_missing(self, tmp_path):
        with pytest.raises(GitCwdMissingError):
            await run_git(
                ["status"], cwd=str(tmp_path / "gone"), timeout=5.0, max_bytes=1024
            )

    @requires_git
    async def test_stdin_reaches_git(self, tmp_path):
        result = await run_git(
            ["hash-object", "--stdin"],
            cwd=str(tmp_path), timeout=5.0, max_bytes=1024, stdin=b"hello\n",
        )

        assert result.rc == 0
        assert result.stdout.strip() == b"ce013625030ba8dba906f756967f9e9ca394464a"

    async def test_prefixes_argv_and_scrubs_env(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run_capped(
            argv, *, cwd, timeout, max_bytes, env, stdin=None,
            on_stderr=None, handle=None,
        ):
            captured.update(argv=list(argv), cwd=cwd, env=env, stdin=stdin)
            return CmdResult(rc=0, stdout=b"", stderr=b"")

        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: "/opt/git")
        monkeypatch.setattr(git_exec, "run_capped", fake_run_capped)
        monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")

        await run_git(
            ["status", "--", "a b"],
            cwd="/repo", timeout=1.0, max_bytes=10, env_extra={"X": "1"},
        )

        assert captured["argv"] == ["/opt/git", *GIT_ARGV_PREFIX, "status", "--", "a b"]
        assert captured["cwd"] == "/repo"
        env = captured["env"]
        assert "GIT_DIR" not in env
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert env["X"] == "1"


class TestGitVersion:
    @requires_git
    async def test_real_git_parses_and_caches(self, monkeypatch):
        version = await git_version()

        assert isinstance(version, tuple) and len(version) == 3
        assert all(isinstance(part, int) for part in version)
        assert version >= (2, 0, 0)

        monkeypatch.setattr(
            git_exec, "run_capped", lambda *_a, **_k: pytest.fail("cache miss")
        )
        assert await git_version() == version

    async def test_parses_vendor_suffix(self, monkeypatch):
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: "/opt/git")
        monkeypatch.setattr(
            git_exec, "run_capped",
            lambda *_a, **_k: CmdResult(
                rc=0, stdout=b"git version 2.39.2.windows.1\n", stderr=b""
            ),
        )

        assert await git_version() == (2, 39, 2)

    async def test_failure_is_none_and_not_cached(self, monkeypatch):
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: "/opt/git")
        monkeypatch.setattr(
            git_exec, "run_capped",
            lambda *_a, **_k: CmdResult(rc=1, stdout=b"", stderr=b"boom"),
        )
        assert await git_version() is None

        monkeypatch.setattr(
            git_exec, "run_capped",
            lambda *_a, **_k: CmdResult(rc=0, stdout=b"git version 2.15\n", stderr=b""),
        )
        assert await git_version() == (2, 15, 0)

    async def test_no_binary_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: None)

        with pytest.raises(GitUnavailableError):
            await git_version()


class TestStreamingAndCancel:
    def test_on_stderr_sees_every_chunk_past_the_retained_cap(self):
        seen: list[bytes] = []

        result = run_capped(
            _py("import sys; sys.stderr.write('e' * 200000); sys.stderr.flush()"),
            cwd=None, timeout=30.0, max_bytes=1024, on_stderr=seen.append,
        )

        assert result.rc == 0
        assert len(result.stderr) == 64 * 1024
        assert sum(len(chunk) for chunk in seen) == 200000

    def test_a_raising_on_stderr_does_not_lose_the_result(self):
        def boom(_chunk: bytes) -> None:
            raise RuntimeError("callback bug")

        result = run_capped(
            _py("import sys; sys.stderr.write('err'); sys.stdout.write('out')"),
            cwd=None, timeout=30.0, max_bytes=1024, on_stderr=boom,
        )

        assert result == CmdResult(rc=0, stdout=b"out", stderr=b"err")

    def test_cancel_before_spawn_kills_the_process_as_soon_as_it_attaches(self):
        handle = ProcessHandle()
        handle.cancel()
        started = time.monotonic()

        result = run_capped(
            _py("import time; time.sleep(60)"),
            cwd=None, timeout=30.0, max_bytes=1024, handle=handle,
        )

        assert handle.cancelled is True
        assert result.timed_out is False
        assert result.rc != 0
        assert time.monotonic() - started < 30

    async def test_cancel_after_spawn_kills_the_whole_tree(self, tmp_path):
        marker = tmp_path / "started"
        code = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        handle = ProcessHandle()
        started = time.monotonic()
        running = asyncio.create_task(asyncio.to_thread(
            run_capped, _py(code), cwd=None, timeout=30.0, max_bytes=1024, handle=handle,
        ))
        while not marker.exists() or not marker.read_text():
            await asyncio.sleep(0.02)

        await asyncio.to_thread(handle.cancel)
        result = await running

        assert result.timed_out is False
        assert result.rc != 0
        assert time.monotonic() - started < 30
        grandchild = int(marker.read_text())
        deadline = time.monotonic() + 10
        while not _process_gone(grandchild):
            if time.monotonic() > deadline:
                pytest.fail(f"grandchild {grandchild} survived the cancel")
            await asyncio.sleep(0.05)

    def test_cancel_after_exit_is_harmless(self):
        handle = ProcessHandle()
        result = run_capped(
            _py("pass"), cwd=None, timeout=30.0, max_bytes=1024, handle=handle
        )

        handle.cancel()

        assert result.rc == 0
        assert handle.cancelled is True

    async def test_run_git_hands_the_stream_and_handle_to_the_runner(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run_capped(argv, *, cwd, timeout, max_bytes, env, stdin=None,
                            on_stderr=None, handle=None):
            captured.update(on_stderr=on_stderr, handle=handle)
            return CmdResult(rc=0, stdout=b"", stderr=b"")

        def on_chunk(_chunk: bytes) -> None:
            pass

        monkeypatch.setattr(git_exec.shutil, "which", lambda _name: "/opt/git")
        monkeypatch.setattr(git_exec, "run_capped", fake_run_capped)
        handle = ProcessHandle()

        await run_git(
            ["fetch"], cwd="/repo", timeout=1.0, max_bytes=10,
            on_stderr=on_chunk, handle=handle,
        )

        assert captured["on_stderr"] is on_chunk
        assert captured["handle"] is handle
