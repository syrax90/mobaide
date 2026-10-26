"""Tests for ``mobaide.agent.windows_processes``.

The selection helpers run on synthetic process tables shaped like a real
Windows install (verified against a live host): the Scheduled-Task
supervisor runs the launcher through the venv ``python.exe`` stub, which
starts the base interpreter, which starts the agent through another stub.
``/End`` kills only the supervisor, leaving that tree orphaned.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from mobaide.agent.windows_processes import (
    ProcessInfo,
    ancestors,
    install_process_ids,
    runs_inside_install,
)

PREFIX = r"C:\Program Files\MobaideAgent"
STUB = PREFIX + r"\releases\0.3.18\venv\Scripts\python.exe"
CLI = PREFIX + r"\releases\0.3.18\venv\Scripts\mobaide-agent.exe"
BASE = r"C:\Python312\python.exe"
CONHOST = r"C:\Windows\System32\conhost.exe"
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
CMD = r"C:\Windows\System32\cmd.exe"
EXPLORER = r"C:\Windows\explorer.exe"
CLAUDE = r"C:\Users\me\.local\bin\claude.exe"

# PID 1 is the supervisor that ``/End`` already killed: absent from the table.
ORPHANED_AGENT = [
    ProcessInfo(10, 1, STUB, 100),
    ProcessInfo(11, 10, BASE, 101),
    ProcessInfo(12, 11, STUB, 102),
    ProcessInfo(13, 12, BASE, 103),
    ProcessInfo(14, 12, CONHOST, 103),
    ProcessInfo(15, 13, CLAUDE, 150),
]
OUTSIDE_CLI = [
    ProcessInfo(100, 4, EXPLORER, 50),
    ProcessInfo(101, 100, PWSH, 200),
    ProcessInfo(102, 101, CMD, 201),
    ProcessInfo(103, 102, CLI, 202),
    ProcessInfo(104, 103, STUB, 203),
    ProcessInfo(105, 104, BASE, 204),
]
# The same CLI typed into a terminal session the agent (13) is serving.
SESSION_CLI = [
    ProcessInfo(20, 13, PWSH, 300),
    ProcessInfo(21, 20, CMD, 301),
    ProcessInfo(22, 21, CLI, 302),
    ProcessInfo(23, 22, STUB, 303),
    ProcessInfo(24, 23, BASE, 304),
]


class TestAncestors:
    def test_walks_parent_chain_nearest_first(self) -> None:
        chain = ancestors(OUTSIDE_CLI, 105)

        assert [p.pid for p in chain] == [104, 103, 102, 101, 100]

    def test_stops_at_reused_parent_pid(self) -> None:
        # Explorer's parent exited and its PID now belongs to the agent stub,
        # which started long after explorer: not a real parent.
        table = ORPHANED_AGENT + [
            ProcessInfo(100, 12, EXPLORER, 50),
            ProcessInfo(101, 100, PWSH, 200),
        ]

        assert [p.pid for p in ancestors(table, 101)] == [100]

    def test_stops_on_parent_cycle(self) -> None:
        table = [ProcessInfo(1, 2, BASE, None), ProcessInfo(2, 1, BASE, None)]

        assert [p.pid for p in ancestors(table, 1)] == [2]


class TestRunsInsideInstall:
    def test_outside_terminal_is_not_inside(self) -> None:
        assert not runs_inside_install(ORPHANED_AGENT + OUTSIDE_CLI, 105, PREFIX)

    def test_agent_terminal_session_is_inside(self) -> None:
        assert runs_inside_install(ORPHANED_AGENT + SESSION_CLI, 24, PREFIX)

    def test_reused_parent_pid_does_not_make_it_inside(self) -> None:
        table = ORPHANED_AGENT + [
            ProcessInfo(100, 12, EXPLORER, 50),
            *OUTSIDE_CLI[1:],
        ]

        assert not runs_inside_install(table, 105, PREFIX)


class TestInstallProcessIds:
    def test_finds_orphaned_tree_but_not_the_calling_cli(self) -> None:
        pids = install_process_ids(ORPHANED_AGENT + OUTSIDE_CLI, PREFIX, keep_pid=105)

        assert pids == [10, 11, 12, 13, 14, 15]

    def test_matches_image_paths_case_insensitively(self) -> None:
        table = [ProcessInfo(10, 1, STUB.lower(), 100), *OUTSIDE_CLI]

        assert install_process_ids(table, PREFIX.upper(), keep_pid=105) == [10]

    def test_ignores_sibling_directory_sharing_the_prefix_name(self) -> None:
        table = [ProcessInfo(10, 1, PREFIX + r"Old\python.exe", 100), *OUTSIDE_CLI]

        assert install_process_ids(table, PREFIX, keep_pid=105) == []

    def test_child_with_reused_parent_pid_is_not_a_descendant(self) -> None:
        # Started before the stub that now holds its parent's PID.
        table = [ProcessInfo(10, 1, STUB, 100), ProcessInfo(30, 10, BASE, 90)]

        assert install_process_ids(table, PREFIX, keep_pid=999) == [10]

    def test_leaves_the_calling_clis_own_agent_alone(self) -> None:
        pids = install_process_ids(ORPHANED_AGENT + SESSION_CLI, PREFIX, keep_pid=24)

        assert not {10, 11, 12, 13, 20, 21, 22, 23, 24} & set(pids)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process table")
class TestWindowsProcessTable:
    def test_lists_this_process_with_parent_and_image(self) -> None:
        from mobaide.agent.windows_processes import list_processes

        me = {p.pid: p for p in list_processes()}[os.getpid()]

        assert me.ppid == os.getppid()
        assert me.created is not None
        images = {
            os.path.normcase(sys.executable),
            os.path.normcase(getattr(sys, "_base_executable", sys.executable)),
        }
        assert me.exe is not None and os.path.normcase(me.exe) in images

    def test_terminate_stops_process_and_waits_for_it(self) -> None:
        from mobaide.agent.windows_processes import terminate

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            assert terminate([proc.pid], timeout_s=10) == []
            assert proc.poll() is not None
        finally:
            proc.kill()
            proc.wait()
