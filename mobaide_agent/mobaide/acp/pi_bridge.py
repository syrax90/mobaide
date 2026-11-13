"""An ACP server in front of the Pi coding agent.

Pi has no ACP server of its own. Its documented integration surface is RPC
mode (``pi --mode rpc``): JSON lines on stdio with commands, responses and
session events. This bridge speaks ACP to Mobaide on its own stdio and RPC to
one ``pi`` child per session, so a Pi chat uses the generic chat session with
no Pi-specific code in it.

What Pi offers maps as follows. Model and thinking level are ACP config
options. ``steer`` is the ``_session/steering`` extension. Stored sessions
are Pi's own JSONL files, so a chat and ``pi`` in a terminal list and
continue the same sessions. Pi asks for no approval before a tool call; only
an extension's dialogs (``extension_ui_request``) become request cards.

It runs as ``python -I pi_bridge.py <pi command...>``: only the standard
library, no ``mobaide`` imports.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

PROTOCOL_VERSION = 1
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_AUTH_REQUIRED = -32000
ERR_NOT_FOUND = -32002

STEER_METHOD = "_session/steering"
VERSION_TIMEOUT = 10.0
# How long Pi gets for its orderly shutdown before it is killed.
STOP_GRACE = 1.2
TERMINATE_GRACE = 0.3
STDERR_TAIL_LINES = 20
PAGE_SIZE = 50
# A session file is read only this far for its title: sessions grow to many
# megabytes, and a list reads every file of the folder.
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 64 * 1024
TITLE_MAX = 80
TEXT_MAX = 48 * 1024
OUTPUT_MAX = 128 * 1024
# Pi sends the whole output so far with each chunk of a running command.
LIVE_OUTPUT_MAX = 16 * 1024
LIVE_OUTPUT_INTERVAL = 0.2
ERROR_MAX = 600
RESUME_ID_MAX = 200
RESUME_ID_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)
# The ids Pi itself takes for a new session (its ``--session-id``).
NEW_SESSION_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,198}[A-Za-z0-9])?")
WINDOWS_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
# What ``cmd`` reads as syntax outside double quotes.
CMD_SPECIAL = frozenset("&|<>^()%!,;=")

MODEL_OPTION = "model"
THINKING_OPTION = "thought_level"
THINKING_NAMES = {
    "off": "Off",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra high",
    "max": "Max",
}
COMPACT_COMMAND = "compact"
# A choice with this few short options shows as buttons, a longer one as a list.
BUTTON_OPTIONS_MAX = 4
BUTTON_LABEL_MAX = 40
# Pi's own text for a model with no credentials (a provider not signed in).
NO_CREDENTIALS_MARKERS = (
    "No API key found",
    "No models available",
    'Authentication failed for "',
    # A subscription login that Pi could not renew.
    "OAuth refresh failed for ",
)
SHELL_TOOLS = frozenset({"bash", "powershell"})
TOOL_KINDS = {
    "read": "read",
    "ls": "read",
    "bash": "execute",
    "powershell": "execute",
    "edit": "edit",
    "write": "edit",
    "grep": "search",
    "find": "search",
}
EXIT_CODE_NOTE = "Command exited with code "
# What Pi prints when ``--session-id`` names a session it has no file for,
# which is the case the bridge uses that option for.
NEW_ID_NOTE = "Warning: No project session found with id"


def _now_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cap(text: str, limit: int = TEXT_MAX) -> str:
    return text if len(text) <= limit else text[:limit]


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def _first_line(text: str, limit: int = ERROR_MAX) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""


def _title_from(text: str) -> str | None:
    line = " ".join(_first_line(text, 4 * TITLE_MAX).split())
    if not line:
        return None
    return line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 1].rstrip() + "…"


def _text_of(content: Any) -> str:
    """The text of a Pi message ``content`` (a string or content blocks)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    )


def agent_dir(env: Mapping[str, str] | None = None) -> str:
    """Pi's configuration folder (``PI_CODING_AGENT_DIR``, else ``~/.pi/agent``)."""
    source = os.environ if env is None else env
    custom = source.get("PI_CODING_AGENT_DIR")
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return os.path.join(os.path.expanduser("~"), ".pi", "agent")


def sessions_root(env: Mapping[str, str] | None = None) -> str:
    """Where Pi keeps its session files: ``PI_CODING_AGENT_SESSION_DIR``,
    the ``sessionDir`` of its settings, else ``sessions`` in its folder."""
    source = os.environ if env is None else env
    custom = source.get("PI_CODING_AGENT_SESSION_DIR")
    base = agent_dir(source)
    if not custom:
        try:
            with open(os.path.join(base, "settings.json"), encoding="utf-8") as handle:
                settings = json.load(handle)
            value = settings.get("sessionDir") if isinstance(settings, dict) else None
            custom = value if isinstance(value, str) and value.strip() else None
        except (OSError, ValueError):
            custom = None
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return os.path.join(base, "sessions")


def session_folder_name(cwd: str) -> str:
    """The folder Pi keeps the sessions of ``cwd`` in."""
    trimmed = cwd[1:] if cwd[:1] in ("/", "\\") else cwd
    return "--" + trimmed.replace("/", "-").replace("\\", "-").replace(":", "-") + "--"


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _session_files(root: str, cwd: str | None) -> Iterator[str]:
    """Session files under ``root``; with ``cwd``, only where its sessions
    can be (its own folder, and files kept flat in a custom root)."""
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return
    wanted: set[str] | None = None
    if cwd:
        wanted = {
            os.path.normcase(session_folder_name(folder)) for folder in (cwd, os.path.realpath(cwd))
        }
    for entry in entries:
        if entry.name.endswith(".jsonl"):
            yield entry.path
            continue
        try:
            if not entry.is_dir() or (
                wanted is not None and os.path.normcase(entry.name) not in wanted
            ):
                continue
            for child in os.scandir(entry.path):
                if child.name.endswith(".jsonl"):
                    yield child.path
        except OSError:
            continue


def _json_line(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _scan_lines(lines: list[bytes], found: dict[str, Any]) -> None:
    for raw in lines:
        if b'"session_info"' in raw:
            entry = _json_line(raw)
            if entry and entry.get("type") == "session_info":
                name = entry.get("name")
                found["name"] = name.strip() if isinstance(name, str) and name.strip() else None
        elif "first" not in found and b'"role":"user"' in raw:
            entry = _json_line(raw)
            message = entry.get("message") if entry and entry.get("type") == "message" else None
            if isinstance(message, dict) and message.get("role") == "user":
                found["first"] = _title_from(_text_of(message.get("content")))


def read_session_row(path: str) -> dict[str, Any] | None:
    """One stored session as an ACP ``session/list`` row, or None for a file
    that is not a Pi session. The title is the name the user gave it, else
    its first message."""
    try:
        size = os.path.getsize(path)
        mtime = os.path.getmtime(path)
        with open(path, "rb") as handle:
            head = handle.read(HEAD_BYTES)
            tail = b""
            if size > HEAD_BYTES:
                handle.seek(max(HEAD_BYTES, size - TAIL_BYTES))
                tail = handle.read(TAIL_BYTES)
    except OSError:
        return None
    lines = head.split(b"\n")
    header = _json_line(lines[0]) if lines else None
    if not header or header.get("type") != "session":
        return None
    session_id, cwd = header.get("id"), header.get("cwd")
    if not isinstance(session_id, str) or not isinstance(cwd, str) or not session_id or not cwd:
        return None
    if len(session_id) > RESUME_ID_MAX or not set(session_id) <= RESUME_ID_CHARS:
        return None
    found: dict[str, Any] = {}
    # The last line of a partial read can be cut: it is never parsed whole.
    _scan_lines(lines[1:-1] if size > len(head) else lines[1:], found)
    if tail:
        _scan_lines(tail.split(b"\n")[1:], found)
    return {
        "sessionId": session_id,
        "cwd": cwd,
        "title": found.get("name") or found.get("first"),
        "updatedAt": _now_iso(mtime),
        "_mtime": mtime,
        "_path": path,
    }


def list_sessions(root: str, cwd: str | None) -> list[dict[str, Any]]:
    """Stored sessions, newest first; with ``cwd``, those of that folder."""
    rows: list[dict[str, Any]] = []
    real = os.path.realpath(cwd) if cwd else None
    for path in _session_files(root, cwd):
        row = read_session_row(path)
        if row is None:
            continue
        if cwd and real and not (_same_path(row["cwd"], cwd) or _same_path(row["cwd"], real)):
            continue
        rows.append(row)
    rows.sort(key=lambda row: row["_mtime"], reverse=True)
    return rows


def find_session_file(root: str, session_id: str) -> str | None:
    suffix = f"_{session_id}.jsonl"
    fallback: list[str] = []
    for path in _session_files(root, None):
        name = os.path.basename(path)
        if name.endswith(suffix) or name == f"{session_id}.jsonl":
            row = read_session_row(path)
            if row is not None and row["sessionId"] == session_id:
                return path
        else:
            fallback.append(path)
    # A file the user renamed keeps its id in the header only.
    for path in fallback:
        row = read_session_row(path)
        if row is not None and row["sessionId"] == session_id:
            return path
    return None


def tool_kind(name: str) -> str:
    return TOOL_KINDS.get(name, "other")


def _arg(args: Any, *keys: str) -> str | None:
    if not isinstance(args, dict):
        return None
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def tool_title(name: str, args: Any) -> str:
    if name in SHELL_TOOLS:
        return _arg(args, "command") or ("PowerShell" if name == "powershell" else "Shell")
    path = _arg(args, "path", "file_path")
    if name == "read":
        return f"Read {path}" if path else "Read"
    if name == "edit":
        return f"Edit {path}" if path else "Edit"
    if name == "write":
        return f"Write {path}" if path else "Write"
    if name == "ls":
        return f"List {path}" if path else "List files"
    pattern = _arg(args, "pattern", "query")
    if name == "grep":
        return f"Search {pattern}" if pattern else "Search"
    if name == "find":
        return f"Find {pattern}" if pattern else "Find files"
    return name


def tool_locations(name: str, args: Any, cwd: str) -> list[dict[str, Any]]:
    if name not in ("read", "edit", "write", "ls"):
        return []
    path = _arg(args, "path", "file_path")
    if not path:
        return []
    location: dict[str, Any] = {"path": _abs_path(path, cwd)}
    offset = args.get("offset") if isinstance(args, dict) else None
    if isinstance(offset, int) and not isinstance(offset, bool) and offset > 0:
        location["line"] = offset
    return [location]


def _abs_path(path: str, cwd: str) -> str:
    expanded = os.path.expanduser(path)
    return os.path.normpath(expanded if os.path.isabs(expanded) else os.path.join(cwd, expanded))


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "content", "content": {"type": "text", "text": _cap(text)}}


def result_blocks(content: Any) -> list[dict[str, Any]]:
    """A Pi tool result's content as ACP tool content."""
    blocks: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
            blocks.append(_text_block(block["text"]))
        elif block.get("type") == "image" and isinstance(block.get("data"), str):
            blocks.append(
                {
                    "type": "content",
                    "content": {
                        "type": "image",
                        "data": block["data"],
                        "mimeType": block.get("mimeType") or "image/png",
                    },
                }
            )
    return blocks


def edit_diffs(name: str, args: Any, cwd: str) -> list[dict[str, Any]]:
    """The change of an ``edit`` or ``write`` call as ACP ``diff`` content."""
    path = _arg(args, "path", "file_path")
    if not path or not isinstance(args, dict):
        return []
    absolute = _abs_path(path, cwd)
    if name == "write":
        content = args.get("content")
        if not isinstance(content, str):
            return []
        return [{"type": "diff", "path": absolute, "oldText": None, "newText": content}]
    edits = args.get("edits")
    pairs = edits if isinstance(edits, list) else [args]
    diffs = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        old, new = pair.get("oldText"), pair.get("newText")
        if isinstance(old, str) and isinstance(new, str):
            diffs.append({"type": "diff", "path": absolute, "oldText": old, "newText": new})
    return diffs


def shell_result(result: Any, is_error: bool) -> tuple[str, int | None]:
    """``(output, exit code)`` of a finished shell tool call. A live result
    carries both; a stored one only the text, which ends with Pi's exit note
    when the command failed."""
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if isinstance(structured, dict) and isinstance(structured.get("output"), str):
        code = structured.get("exit_code")
        return structured["output"], code if isinstance(code, int) and not isinstance(code, bool) else None
    text = _text_of(result.get("content") if isinstance(result, dict) else None)
    head, sep, note = text.rpartition(EXIT_CODE_NOTE)
    if sep and note.strip().lstrip("-").isdigit():
        return head.rstrip("\n"), int(note.strip())
    return text, None if is_error else 0


def prompt_parts(blocks: Any) -> tuple[str, list[dict[str, str]]]:
    """An ACP prompt as Pi's ``message`` text and ``images``."""
    texts: list[str] = []
    images: list[dict[str, str]] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif block.get("type") == "image" and isinstance(block.get("data"), str):
            images.append(
                {
                    "type": "image",
                    "data": block["data"],
                    "mimeType": block.get("mimeType") or "image/png",
                }
            )
    return "\n".join(texts), images


def config_options(
    models: list[dict[str, Any]], model: dict[str, Any] | None, levels: list[str], level: str | None
) -> list[dict[str, Any]]:
    """Pi's model and thinking level as ACP config options. The models of the
    current provider come first; other providers follow as groups."""
    options: list[dict[str, Any]] = []
    current = _model_value(model)
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in models:
        value = _model_value(entry)
        if value is None:
            continue
        name = entry.get("name") if isinstance(entry.get("name"), str) and entry["name"] else entry["id"]
        groups.setdefault(entry["provider"], []).append({"value": value, "name": name})
    if current is not None and groups:
        own = model["provider"] if model else ""
        ordered = sorted(groups, key=lambda provider: (provider != own, provider))
        if not any(v["value"] == current for values in groups.values() for v in values):
            groups.setdefault(own, []).insert(0, {"value": current, "name": model["id"] if model else current})
            ordered = sorted(groups, key=lambda provider: (provider != own, provider))
        if len(ordered) == 1:
            choices: list[dict[str, Any]] = groups[ordered[0]]
        else:
            choices = [{"name": provider, "options": groups[provider]} for provider in ordered]
        options.append(
            {
                "id": MODEL_OPTION,
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": current,
                "options": choices,
            }
        )
    if level in levels and len(levels) > 1:
        options.append(
            {
                "id": THINKING_OPTION,
                "name": "Thinking",
                "category": "thought_level",
                "type": "select",
                "currentValue": level,
                "options": [{"value": lv, "name": THINKING_NAMES.get(lv, lv)} for lv in levels],
            }
        )
    return options


def _model_value(model: Any) -> str | None:
    if not isinstance(model, dict):
        return None
    provider, model_id = model.get("provider"), model.get("id")
    if not isinstance(provider, str) or not isinstance(model_id, str) or not provider or not model_id:
        return None
    # Pi reports this placeholder while no provider is signed in.
    if provider == "unknown" and model_id == "unknown":
        return None
    return f"{provider}/{model_id}"


def spawn_command(argv: list[str]) -> list[str] | str:
    """``argv`` as ``subprocess`` should get it. A Windows ``.cmd`` shim runs
    through ``cmd /c``, which drops the first and last quote of a line that
    holds more than two: a shim and a session file that both sit in a path
    with a space would break. ``/s`` with one outer pair of quotes keeps
    every inner quote. Each argument that holds ``cmd`` syntax is quoted too:
    Pi names a session folder after its project folder, and a ``&`` from that
    name would otherwise end the command and start another."""
    shell = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if argv else ""
    if sys.platform == "win32" and len(argv) > 2 and shell in ("cmd", "cmd.exe") and argv[1].lower() == "/c":
        return f'cmd /d /s /c "{" ".join(_cmd_argument(arg) for arg in argv[2:])}"'
    return argv


def _cmd_argument(arg: str) -> str:
    if '"' in arg:
        # No file path holds one, and no quoting keeps it apart from syntax.
        raise ValueError("A command argument holds a double quote")
    quoted = subprocess.list2cmdline([arg])
    if quoted.startswith('"') or not CMD_SPECIAL.intersection(arg):
        return quoted
    # A backslash before the closing quote would escape it.
    doubled = "\\" * (len(arg) - len(arg.rstrip("\\")))
    return f'"{arg}{doubled}"'


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill ``proc`` and, on Windows, what it started: there ``pi`` runs
    under a ``cmd`` shim, and killing the shim alone leaves it running."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                creationflags=WINDOWS_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


class _Rpc(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Pi:
    """One ``pi --mode rpc`` child. Its output lines reach the bridge's
    queue from a reader thread; writes come from the bridge's own thread."""

    def __init__(self, argv: list[str], cwd: str, events: queue.SimpleQueue[tuple[Any, ...]]) -> None:
        self.cwd = cwd
        self.stderr_tail: list[str] = []
        self.proc = subprocess.Popen(
            spawn_command(argv),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=WINDOWS_NO_WINDOW,
        )
        self._events = events
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        # LF only: Pi's JSON can hold other Unicode line separators.
        for raw in iter(self.proc.stdout.readline, b""):
            record = _json_line(raw)
            if record is not None:
                self._events.put(("pi", self, record))
        self.proc.wait()
        # The exit message quotes the last error line: let it arrive first.
        self._stderr_reader.join(1.0)
        self._events.put(("pi_exit", self, self.proc.returncode))

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if NEW_ID_NOTE in line:
                # Expected, and not the reason of a later exit: the client
                # quotes the last error line when the process ends.
                continue
            try:
                sys.stderr.buffer.write(raw)
                sys.stderr.buffer.flush()
            except (OSError, ValueError):
                pass
            if line:
                self.stderr_tail.append(line)
                del self.stderr_tail[:-STDERR_TAIL_LINES]

    def send(self, command: dict[str, Any]) -> bool:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def stop(self) -> None:
        """Ask for an orderly shutdown (stdin EOF); kill what is left after
        the grace period."""
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except (OSError, ValueError):
            pass
        threading.Thread(target=self.reap, args=(STOP_GRACE,), daemon=True).start()

    def reap(self, grace: float) -> None:
        """Wait ``grace`` seconds for the exit, then end the process: first
        with a signal Pi answers by stopping the commands it runs."""
        proc = self.proc
        try:
            proc.wait(max(0.0, grace))
            return
        except subprocess.TimeoutExpired:
            pass
        if sys.platform != "win32":
            proc.terminate()
            try:
                proc.wait(TERMINATE_GRACE)
                return
            except subprocess.TimeoutExpired:
                pass
        kill_tree(proc)


class Bridge:
    def __init__(self, pi_argv: list[str]) -> None:
        self._pi_argv = pi_argv
        # ``SimpleQueue``: the SIGTERM handler puts into it on the thread that
        # reads it, and a ``Queue`` would deadlock on its own lock there.
        self._events: queue.SimpleQueue[tuple[Any, ...]] = queue.SimpleQueue()
        # Children that were told to stop; the bridge outlives none of them.
        self._stopped: list[_Pi] = []
        self._out_lock = threading.Lock()
        self._pi: _Pi | None = None
        self._session_id: str | None = None
        self._seq = 0
        self._pi_calls: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._client_calls: dict[str, Callable[[Any], None]] = {}
        # The client request that waits for the session to start.
        self._setup_id: Any = None
        self._prompt_id: Any = None
        self._streaming = False
        # Counts runs, so an answer that arrives late knows its run is over.
        self._run = 0
        self._turn_error: str | None = None
        self._turn_stop: str | None = None
        self._stop_asked = False
        self._models: list[dict[str, Any]] = []
        self._model: dict[str, Any] | None = None
        self._levels: list[str] = []
        self._level: str | None = None
        self._named = False
        self._titled = False
        self._message = 0
        self._streamed: set[int] = set()
        self._tools: dict[str, dict[str, Any]] = {}
        self._compaction: str | None = None

    def _write(self, message: dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._out_lock:
            try:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            except (OSError, ValueError):
                self._events.put(("eof",))

    def _reply(self, request_id: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _fail(self, request_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def _update(self, update: dict[str, Any]) -> None:
        self._write(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"sessionId": self._session_id, "update": update},
            }
        )

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq}"

    def _ask_client(self, method: str, params: dict[str, Any], on_done: Callable[[Any], None]) -> None:
        request_id = self._next("b")
        self._client_calls[request_id] = on_done
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def _read_client(self) -> None:
        # Straight from the descriptor: a thread that waits inside the
        # buffered ``sys.stdin`` holds its lock, and the interpreter aborts
        # when it shuts down while that lock is held (Pi exited, SIGTERM).
        fd = sys.stdin.fileno()
        pending = b""
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            *lines, pending = (pending + chunk).split(b"\n")
            for raw in lines:
                message = _json_line(raw)
                if message is not None:
                    self._events.put(("client", message))
        self._events.put(("eof",))

    def run(self) -> int:
        threading.Thread(target=self._read_client, daemon=True).start()
        while True:
            event = self._events.get()
            kind = event[0]
            if kind == "eof":
                break
            if kind == "client":
                self._on_client(event[1])
            elif kind == "pi" and event[1] is self._pi:
                self._on_pi(event[2])
            elif kind == "pi_exit" and event[1] is self._pi:
                return self._on_pi_exit(event[1], event[2])
        self._close()
        self._reap_stopped()
        return 0

    def _reap_stopped(self) -> None:
        """Wait for every stopped child inside one grace period. Without
        this the bridge would exit first, and the reaper threads with it."""
        deadline = time.monotonic() + STOP_GRACE
        for pi in self._stopped:
            pi.reap(deadline - time.monotonic())

    def _on_client(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            waiter = self._client_calls.pop(str(message.get("id")), None)
            if waiter is not None:
                waiter(message.get("result") if "error" not in message else None)
            return
        raw_params = message.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if "id" not in message:
            if method == "session/cancel":
                self._cancel()
            return
        request_id = message["id"]
        try:
            self._on_request(request_id, method, params)
        except _Rpc as exc:
            self._fail(request_id, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - one bad request must not end the bridge
            self._fail(request_id, ERR_INTERNAL, str(exc) or type(exc).__name__)

    def _on_request(self, request_id: Any, method: str, params: dict[str, Any]) -> None:
        if method == "initialize":
            self._reply(request_id, self._initialize())
        elif method == "session/new":
            self._start(request_id, self._folder(params), None)
        elif method == "session/load":
            self._load(request_id, params)
        elif method == "session/list":
            self._reply(request_id, self._list(params))
        elif method == "session/prompt":
            self._prompt(request_id, params)
        elif method == "session/set_config_option":
            self._set_config(request_id, params)
        elif method == STEER_METHOD:
            self._steer(request_id, params)
        elif method == "session/close":
            self._close()
            self._reply(request_id, {})
        else:
            raise _Rpc(ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    def _initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": True},
                "sessionCapabilities": {"list": {}, "close": {}},
            },
            "agentInfo": {"name": "pi", "title": "Pi", "version": self._pi_version()},
            "authMethods": [],
            "_meta": {"steering": {"supported": True}},
        }

    def _pi_version(self) -> str:
        try:
            done = subprocess.run(
                spawn_command([*self._pi_argv, "--version"]),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=VERSION_TIMEOUT,
                creationflags=WINDOWS_NO_WINDOW,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return ""
        return _first_line(done.stdout.decode("utf-8", "replace"), 40)

    @staticmethod
    def _folder(params: dict[str, Any]) -> str:
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            raise _Rpc(ERR_INVALID_PARAMS, "cwd must be an absolute path")
        if not os.path.isdir(cwd):
            raise _Rpc(ERR_INVALID_PARAMS, f"The folder does not exist: {cwd}")
        return cwd

    def _list(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = params.get("cwd") if isinstance(params.get("cwd"), str) else None
        cursor = params.get("cursor")
        start = int(cursor) if isinstance(cursor, str) and cursor.isdigit() else 0
        rows = list_sessions(sessions_root(), cwd)
        page = [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in rows[start : start + PAGE_SIZE]
        ]
        result: dict[str, Any] = {"sessions": page}
        if start + PAGE_SIZE < len(rows):
            result["nextCursor"] = str(start + PAGE_SIZE)
        return result

    def _load(self, request_id: Any, params: dict[str, Any]) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise _Rpc(ERR_INVALID_PARAMS, "sessionId is required")
        if len(session_id) > RESUME_ID_MAX or not set(session_id) <= RESUME_ID_CHARS:
            raise _Rpc(ERR_NOT_FOUND, "Pi has no stored session with this id")
        cwd = self._folder(params)
        path = find_session_file(sessions_root(), session_id)
        if path is not None:
            # By file path: an id from another folder makes Pi ask on stdin
            # whether to fork the session.
            self._start(request_id, cwd, ["--session", path])
        elif NEW_SESSION_ID.fullmatch(session_id) is None:
            raise _Rpc(ERR_NOT_FOUND, "Pi has no stored session with this id")
        else:
            # Pi stores a session with its first message. One that was opened
            # and never used (a restart before the first message) has no
            # file: Pi makes it again under the same id.
            self._start(request_id, cwd, ["--session-id", session_id])

    def _start(self, request_id: Any, cwd: str, session: list[str] | None) -> None:
        if self._setup_id is not None:
            raise _Rpc(ERR_INVALID_PARAMS, "A session is already starting")
        self._close()
        argv = [*self._pi_argv, "--mode", "rpc", *(session or [])]
        try:
            pi = _Pi(argv, cwd, self._events)
        except (OSError, ValueError) as exc:
            raise _Rpc(ERR_INTERNAL, f"Could not start Pi: {exc}") from None
        self._pi = pi
        self._setup_id = request_id
        commands: dict[str, dict[str, Any]] = {
            "state": {"type": "get_state"},
            "models": {"type": "get_available_models"},
            "levels": {"type": "get_available_thinking_levels"},
            "commands": {"type": "get_commands"},
        }
        if session:
            commands["messages"] = {"type": "get_messages"}
        self._gather(commands, lambda results: self._started(request_id, results, bool(session)))

    def _call(self, command: dict[str, Any], on_done: Callable[[dict[str, Any]], None]) -> None:
        pi = self._pi
        if pi is None:
            on_done({"success": False, "error": "Pi is not running"})
            return
        call_id = self._next("c")
        self._pi_calls[call_id] = on_done
        if not pi.send({**command, "id": call_id}):
            # The exit that follows fails every waiting call.
            return

    def _gather(
        self, commands: dict[str, dict[str, Any]], on_done: Callable[[dict[str, dict[str, Any]]], None]
    ) -> None:
        results: dict[str, dict[str, Any]] = {}

        def collect(key: str, response: dict[str, Any]) -> None:
            results[key] = response
            if len(results) == len(commands):
                on_done(results)

        for key, command in commands.items():
            self._call(command, lambda response, key=key: collect(key, response))

    @staticmethod
    def _data(response: dict[str, Any] | None) -> dict[str, Any]:
        data = response.get("data") if response and response.get("success") else None
        return data if isinstance(data, dict) else {}

    def _started(self, request_id: Any, results: dict[str, dict[str, Any]], loaded: bool) -> None:
        state = self._data(results.get("state"))
        session_id = state.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            error = results.get("state", {}).get("error")
            self._close()
            self._fail(request_id, ERR_INTERNAL, _first_line(str(error or "")) or "Pi did not start a session")
            return
        models = self._data(results.get("models")).get("models")
        self._models = [
            m for m in models if isinstance(m, dict) and _model_value(m) is not None
        ] if isinstance(models, list) else []
        if not self._models:
            # No provider is signed in: Pi would refuse every prompt.
            self._close()
            self._fail(request_id, ERR_AUTH_REQUIRED, "Authentication required")
            return
        self._session_id = session_id
        self._apply_state(state)
        levels = self._data(results.get("levels")).get("levels")
        self._levels = [lv for lv in levels if isinstance(lv, str)] if isinstance(levels, list) else []
        if self._model is None:
            # The default model's provider is not signed in, but another one
            # is: take its first model, or Pi would refuse every prompt and
            # the chat would have no model to show.
            first = self._models[0]

            def refreshed(fresh: dict[str, dict[str, Any]]) -> None:
                self._refresh_config(fresh)
                self._finish_start(request_id, results, loaded)

            def selected(_response: dict[str, Any]) -> None:
                self._gather(
                    {"state": {"type": "get_state"}, "levels": {"type": "get_available_thinking_levels"}},
                    refreshed,
                )

            self._call({"type": "set_model", "provider": first["provider"], "modelId": first["id"]}, selected)
            return
        self._finish_start(request_id, results, loaded)

    def _finish_start(self, request_id: Any, results: dict[str, dict[str, Any]], loaded: bool) -> None:
        self._setup_id = None
        state = self._data(results.get("state"))
        session_id = self._session_id
        name = state.get("sessionName")
        self._named = isinstance(name, str) and bool(name.strip())
        self._titled = self._named
        result: dict[str, Any] = {"configOptions": self._config()}
        if loaded:
            messages = self._data(results.get("messages")).get("messages")
            first = self._replay(messages if isinstance(messages, list) else [])
            title = name if self._named else first
            if title:
                self._update({"sessionUpdate": "session_info_update", "title": title})
            # A stored session keeps its title: a later message is not its topic.
            self._titled = self._named or first is not None
        else:
            result["sessionId"] = session_id
        self._reply(request_id, result)
        self._send_commands(self._data(results.get("commands")).get("commands"))

    def _apply_state(self, state: dict[str, Any]) -> None:
        model = state.get("model")
        self._model = model if _model_value(model) is not None else None
        level = state.get("thinkingLevel")
        self._level = level if isinstance(level, str) else None

    def _config(self) -> list[dict[str, Any]]:
        return config_options(self._models, self._model, self._levels, self._level)

    def _send_commands(self, raw: Any) -> None:
        commands = [
            {
                "name": COMPACT_COMMAND,
                "description": "Summarize the conversation so far to free up context",
                "input": {"hint": "what the summary must keep (optional)"},
            }
        ]
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            description = entry.get("description")
            commands.append(
                {
                    "name": entry["name"],
                    "description": description if isinstance(description, str) else "",
                }
            )
        self._update({"sessionUpdate": "available_commands_update", "availableCommands": commands})

    def _close(self) -> None:
        pi, self._pi = self._pi, None
        if pi is not None:
            pi.stop()
            self._stopped = [old for old in self._stopped if old.proc.poll() is None]
            self._stopped.append(pi)
        self._session_id = None
        self._pi_calls.clear()
        self._prompt_id = None
        self._setup_id = None
        self._streaming = False
        self._tools.clear()
        self._compaction = None

    def _on_pi_exit(self, pi: _Pi, returncode: int | None) -> int:
        detail = pi.stderr_tail[-1][:ERROR_MAX] if pi.stderr_tail else ""
        message = f"Pi exited with code {returncode}" + (f": {detail}" if detail else "")
        for request_id in (self._setup_id, self._prompt_id):
            if request_id is not None:
                self._fail(request_id, ERR_INTERNAL, message)
        self._reap_stopped()
        # The session cannot go on without its process: the client sees the
        # exit of this bridge and offers a restart.
        return returncode if isinstance(returncode, int) and returncode != 0 else 1

    def _require_session(self, params: dict[str, Any]) -> _Pi:
        if self._pi is None or self._session_id is None or params.get("sessionId") != self._session_id:
            raise _Rpc(ERR_INVALID_PARAMS, "Unknown session")
        return self._pi

    def _prompt(self, request_id: Any, params: dict[str, Any]) -> None:
        self._require_session(params)
        if self._prompt_id is not None:
            raise _Rpc(ERR_INVALID_PARAMS, "A turn is already running")
        text, images = prompt_parts(params.get("prompt"))
        if not text.strip() and not images:
            raise _Rpc(ERR_INVALID_PARAMS, "The message is empty")
        command, _, rest = text.strip().partition(" ")
        if command == f"/{COMPACT_COMMAND}" and not images:
            self._compact(request_id, rest.strip())
            return
        self._prompt_id = request_id
        self._turn_error = None
        self._turn_stop = None
        self._stop_asked = False
        if not self._titled:
            title = _title_from(text)
            if title:
                self._titled = True
                self._update({"sessionUpdate": "session_info_update", "title": title})
        prompt: dict[str, Any] = {"type": "prompt", "message": text}
        if images:
            prompt["images"] = images
        if self._streaming:
            # A run an extension started: the message waits for its end.
            prompt["streamingBehavior"] = "followUp"
        self._call(prompt, lambda response: self._prompt_accepted(request_id, response))

    def _prompt_accepted(self, request_id: Any, response: dict[str, Any]) -> None:
        if self._prompt_id != request_id:
            return
        if not response.get("success"):
            self._prompt_id = None
            self._fail_turn(request_id, str(response.get("error") or "Pi did not accept the message"))
            return
        if self._data(response).get("disposition") == "handled" and not self._streaming:
            self._prompt_id = None
            self._reply(request_id, {"stopReason": "end_turn"})

    def _fail_turn(self, request_id: Any, error: str) -> None:
        if any(marker in error for marker in NO_CREDENTIALS_MARKERS):
            self._fail(request_id, ERR_AUTH_REQUIRED, "Authentication required")
        else:
            self._fail(request_id, ERR_INTERNAL, _first_line(error) or "The turn failed")

    def _settled(self, aborted: bool) -> None:
        self._streaming = False
        self._run += 1
        # A call the model named but Pi never ran (its request failed) has no
        # result to wait for. After a stop the client marks them itself.
        if not aborted:
            for tool in self._tools.values():
                self._update({"sessionUpdate": "tool_call_update", "toolCallId": tool["id"], "status": "failed"})
        self._tools.clear()
        request_id, self._prompt_id = self._prompt_id, None
        if request_id is None:
            return
        if aborted:
            self._reply(request_id, {"stopReason": "cancelled"})
        elif self._turn_error is not None:
            self._fail_turn(request_id, self._turn_error)
        elif self._turn_stop == "length":
            self._reply(request_id, {"stopReason": "max_tokens"})
        else:
            self._reply(request_id, {"stopReason": "end_turn"})

    def _compact(self, request_id: Any, instructions: str) -> None:
        if self._streaming:
            raise _Rpc(ERR_INVALID_PARAMS, "Wait for the turn to end, then compact")
        command: dict[str, Any] = {"type": "compact"}
        if instructions:
            command["customInstructions"] = instructions
        self._prompt_id = request_id
        self._stop_asked = False

        def done(response: dict[str, Any]) -> None:
            if self._prompt_id != request_id:
                return
            self._prompt_id = None
            if response.get("success"):
                self._reply(request_id, {"stopReason": "end_turn"})
            elif self._stop_asked:
                self._end_compaction("cancelled", "")
                self._reply(request_id, {"stopReason": "cancelled"})
            else:
                error = _first_line(str(response.get("error") or "")) or "Pi could not compact the conversation"
                self._end_compaction("failed", error)
                self._fail(request_id, ERR_INTERNAL, error)

        self._call(command, done)

    def _cancel(self) -> None:
        if self._pi is None:
            return
        self._stop_asked = True
        # Queued messages first: Pi continues with them after an abort.
        self._call({"type": "clear_queue"}, lambda _response: None)
        self._call({"type": "abort"}, lambda _response: None)

    def _steer(self, request_id: Any, params: dict[str, Any]) -> None:
        self._require_session(params)
        text, images = prompt_parts(params.get("prompt"))
        if not self._streaming or (not text.strip() and not images):
            self._reply(request_id, {"outcome": "promptRequired"})
            return
        command: dict[str, Any] = {"type": "steer", "message": text}
        if images:
            command["images"] = images
        run = self._run

        def done(response: dict[str, Any]) -> None:
            if not response.get("success"):
                self._fail(request_id, ERR_INTERNAL, _first_line(str(response.get("error") or "")) or "Pi refused the message")
            elif run != self._run:
                # The run ended first: the message must not wait in Pi's
                # queue for a prompt that the client sends anyway.
                self._call({"type": "clear_queue"}, lambda _response: None)
                self._reply(request_id, {"outcome": "promptRequired"})
            else:
                self._reply(request_id, {"outcome": "injected"})

        self._call(command, done)

    def _set_config(self, request_id: Any, params: dict[str, Any]) -> None:
        self._require_session(params)
        option, value = params.get("configId"), params.get("value")
        if not isinstance(value, str):
            raise _Rpc(ERR_INVALID_PARAMS, "Invalid value")
        if option == MODEL_OPTION:
            provider, _, model_id = value.partition("/")
            command = {"type": "set_model", "provider": provider, "modelId": model_id}
        elif option == THINKING_OPTION:
            command = {"type": "set_thinking_level", "level": value}
        else:
            raise _Rpc(ERR_INVALID_PARAMS, "Unknown setting")

        def changed(response: dict[str, Any]) -> None:
            if not response.get("success"):
                self._fail(request_id, ERR_INTERNAL, _first_line(str(response.get("error") or "")) or "Pi refused the change")
                return
            # A model has its own thinking levels, and Pi keeps the level in range.
            self._gather(
                {"state": {"type": "get_state"}, "levels": {"type": "get_available_thinking_levels"}},
                refreshed,
            )

        def refreshed(results: dict[str, dict[str, Any]]) -> None:
            self._refresh_config(results)
            self._reply(request_id, {"configOptions": self._config()})

        self._call(command, changed)

    def _refresh_config(self, results: dict[str, dict[str, Any]]) -> None:
        state = self._data(results.get("state"))
        if state:
            self._apply_state(state)
        levels = self._data(results.get("levels")).get("levels")
        if isinstance(levels, list):
            self._levels = [lv for lv in levels if isinstance(lv, str)]

    def _on_pi(self, record: dict[str, Any]) -> None:
        kind = record.get("type")
        if kind == "response":
            waiter = self._pi_calls.pop(str(record.get("id")), None)
            if waiter is not None:
                waiter(record)
        elif kind == "extension_ui_request":
            self._on_dialog(record)
        elif self._session_id is not None and isinstance(kind, str):
            self._on_event(kind, record)

    def _on_event(self, kind: str, record: dict[str, Any]) -> None:
        if kind == "agent_start":
            self._streaming = True
        elif kind == "agent_settled":
            self._settled(record.get("aborted") is True)
        elif kind == "message_start":
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                self._message += 1
                self._streamed = set()
        elif kind == "message_update":
            event = record.get("assistantMessageEvent")
            if isinstance(event, dict):
                self._on_delta(event)
        elif kind == "message_end":
            message = record.get("message")
            if isinstance(message, dict):
                self._on_message_end(message)
        elif kind == "tool_execution_start":
            self._tool_started(record)
        elif kind == "tool_execution_update":
            self._tool_progress(record)
        elif kind == "tool_execution_end":
            self._tool_finished(record)
        elif kind == "auto_retry_start":
            self._notice(
                "warning",
                f"The model request failed. Pi tries again ({record.get('attempt')} of {record.get('maxAttempts')}).",
                _first_line(str(record.get("errorMessage") or "")),
            )
        elif kind == "compaction_start":
            self._compaction = self._next("k")
            self._update(
                {"sessionUpdate": "compaction_update", "compactionId": self._compaction, "status": "in_progress"}
            )
        elif kind == "compaction_end":
            result = record.get("result")
            if record.get("aborted") is True:
                self._end_compaction("cancelled", "")
            elif isinstance(result, dict):
                self._end_compaction("completed", str(result.get("summary") or ""))
            else:
                self._end_compaction("failed", _first_line(str(record.get("errorMessage") or "")))
        elif kind == "session_info_changed":
            name = record.get("name")
            self._named = isinstance(name, str) and bool(name.strip())
            if self._named:
                self._titled = True
                self._update({"sessionUpdate": "session_info_update", "title": name})
        elif kind == "thinking_level_changed":
            level = record.get("level")
            if isinstance(level, str) and level != self._level:
                self._level = level
                self._update({"sessionUpdate": "config_option_update", "configOptions": self._config()})
        elif kind == "extension_error":
            path = record.get("extensionPath")
            name = os.path.basename(path) if isinstance(path, str) else "An extension"
            self._notice("warning", f"A Pi extension failed: {name}", _first_line(str(record.get("error") or "")))

    def _notice(self, severity: str, title: str, description: str = "") -> None:
        self._update(
            {"sessionUpdate": "notice", "severity": severity, "title": title, "description": description}
        )

    def _end_compaction(self, status: str, text: str) -> None:
        compaction, self._compaction = self._compaction, None
        if compaction is None:
            return
        update: dict[str, Any] = {
            "sessionUpdate": "compaction_update",
            "compactionId": compaction,
            "status": status,
        }
        if text:
            update["summary"] = [{"type": "text", "text": _cap(text)}]
        self._update(update)

    def _chunk(self, kind: str, text: str, index: int) -> None:
        if text:
            self._update(
                {
                    "sessionUpdate": kind,
                    "content": {"type": "text", "text": text},
                    # Per content block: text after a thought is a new item.
                    "messageId": f"m{self._message}.{index}",
                }
            )

    def _on_delta(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        raw_index = event.get("contentIndex")
        index = raw_index if isinstance(raw_index, int) else 0
        if kind in ("text_delta", "thinking_delta"):
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                self._streamed.add(index)
                self._chunk(
                    "agent_message_chunk" if kind == "text_delta" else "agent_thought_chunk", delta, index
                )
        elif kind == "toolcall_start":
            pi_id, name = event.get("id"), event.get("toolName")
            if isinstance(pi_id, str) and isinstance(name, str):
                self._announce(pi_id, name, None)
        elif kind == "toolcall_end":
            call = event.get("toolCall")
            if isinstance(call, dict) and isinstance(call.get("id"), str) and isinstance(call.get("name"), str):
                self._announce(call["id"], call["name"], call.get("arguments"))

    def _on_message_end(self, message: dict[str, Any]) -> None:
        role = message.get("role")
        if role == "custom" and message.get("display") is True:
            self._message += 1
            self._chunk("agent_message_chunk", _text_of(message.get("content")), 0)
            return
        if role != "assistant":
            return
        content = message.get("content")
        for index, block in enumerate(content if isinstance(content, list) else []):
            if not isinstance(block, dict):
                continue
            # A provider that does not stream sends its blocks only here.
            if index not in self._streamed:
                if block.get("type") == "text":
                    self._chunk("agent_message_chunk", str(block.get("text") or ""), index)
                elif block.get("type") == "thinking":
                    self._chunk("agent_thought_chunk", str(block.get("thinking") or ""), index)
            if block.get("type") == "toolCall" and isinstance(block.get("id"), str):
                self._announce(block["id"], str(block.get("name") or "tool"), block.get("arguments"))
        stop = message.get("stopReason")
        self._turn_stop = stop if isinstance(stop, str) else None
        if stop == "error":
            self._turn_error = str(message.get("errorMessage") or "The model request failed")
        elif stop != "aborted":
            # A retry that worked replaces the error before it.
            self._turn_error = None
        self._report_usage(message.get("usage"))

    def _report_usage(self, usage: Any) -> None:
        window = self._model.get("contextWindow") if self._model else None
        used = usage.get("totalTokens") if isinstance(usage, dict) else None
        if (
            isinstance(used, int)
            and isinstance(window, int)
            and not isinstance(used, bool)
            and used > 0
            and window > 0
        ):
            self._update({"sessionUpdate": "usage_update", "used": used, "size": window})

    def _announce(self, pi_id: str, name: str, args: Any) -> dict[str, Any]:
        """Make the tool call known to the client, and complete what it
        knows once the arguments have arrived whole."""
        cwd = self._pi.cwd if self._pi else os.getcwd()
        tool = self._tools.get(pi_id)
        if tool is None:
            tool = self._tools[pi_id] = {"id": self._next("pi-"), "name": name, "args": None, "at": 0.0}
            update: dict[str, Any] = {
                "sessionUpdate": "tool_call",
                "toolCallId": tool["id"],
                "title": tool_title(name, args),
                "kind": tool_kind(name),
                "status": "pending",
            }
            if isinstance(args, dict):
                tool["args"] = args
                update["rawInput"] = args
                update["locations"] = tool_locations(name, args, cwd)
            self._update(update)
        elif tool["args"] is None and isinstance(args, dict):
            tool["args"] = args
            self._update(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool["id"],
                    "title": tool_title(name, args),
                    "rawInput": args,
                    "locations": tool_locations(name, args, cwd),
                }
            )
        return tool

    def _tool_started(self, record: dict[str, Any]) -> None:
        pi_id, name = record.get("toolCallId"), record.get("toolName")
        if not isinstance(pi_id, str) or not isinstance(name, str):
            return
        tool = self._announce(pi_id, name, record.get("args"))
        self._update({"sessionUpdate": "tool_call_update", "toolCallId": tool["id"], "status": "in_progress"})

    def _tool_progress(self, record: dict[str, Any]) -> None:
        tool = self._tools.get(str(record.get("toolCallId")))
        partial = record.get("partialResult")
        if tool is None or not isinstance(partial, dict):
            return
        now = time.monotonic()
        if now - tool["at"] < LIVE_OUTPUT_INTERVAL:
            return
        text = _text_of(partial.get("content"))
        if not text:
            return
        tool["at"] = now
        update: dict[str, Any] = {"sessionUpdate": "tool_call_update", "toolCallId": tool["id"]}
        if tool["name"] in SHELL_TOOLS:
            update["_meta"] = {"terminal_output": {"data": _tail(text, LIVE_OUTPUT_MAX)}}
        else:
            update["content"] = [_text_block(_tail(text, LIVE_OUTPUT_MAX))]
        self._update(update)

    def _tool_finished(self, record: dict[str, Any]) -> None:
        pi_id = record.get("toolCallId")
        tool = self._tools.pop(pi_id, None) if isinstance(pi_id, str) else None
        if tool is None:
            return
        self._update(self._tool_result(tool, record.get("result"), record.get("isError") is True))

    def _tool_result(self, tool: dict[str, Any], result: Any, is_error: bool) -> dict[str, Any]:
        cwd = self._pi.cwd if self._pi else os.getcwd()
        name, args = tool["name"], tool["args"]
        update: dict[str, Any] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool["id"],
            "status": "failed" if is_error else "completed",
        }
        if name in SHELL_TOOLS:
            output, code = shell_result(result, is_error)
            update["_meta"] = {
                "terminal_output": {"data": _tail(output, OUTPUT_MAX)},
                "terminal_exit": {"exit_code": code},
            }
            return update
        diffs = [] if is_error else edit_diffs(name, args, cwd)
        if diffs:
            update["content"] = diffs
        else:
            update["content"] = result_blocks(result.get("content") if isinstance(result, dict) else None)
        return update

    def _replay(self, messages: list[Any]) -> str | None:
        """Send a stored conversation to the client, as ``session/load``
        asks. Returns a title from its first user message."""
        first: str | None = None
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            self._message += 1
            if role == "user":
                if first is None:
                    first = _title_from(_text_of(message.get("content")))
                self._replay_user(message.get("content"))
            elif role == "assistant":
                content = message.get("content")
                for index, block in enumerate(content if isinstance(content, list) else []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        self._chunk("agent_message_chunk", str(block.get("text") or ""), index)
                    elif block.get("type") == "thinking":
                        self._chunk("agent_thought_chunk", str(block.get("thinking") or ""), index)
                    elif block.get("type") == "toolCall" and isinstance(block.get("id"), str):
                        self._announce(block["id"], str(block.get("name") or "tool"), block.get("arguments") or {})
                self._report_usage(message.get("usage"))
            elif role == "toolResult":
                tool = self._tools.pop(str(message.get("toolCallId")), None)
                if tool is not None:
                    self._update(self._tool_result(tool, message, message.get("isError") is True))
            elif role == "bashExecution":
                self._replay_shell(message)
            elif role == "compactionSummary":
                self._compaction = self._next("k")
                self._end_compaction("completed", str(message.get("summary") or ""))
            elif role == "branchSummary":
                self._notice("info", "Summary of another branch", _cap(str(message.get("summary") or ""), 2000))
            elif role == "custom" and message.get("display") is True:
                self._chunk("agent_message_chunk", _text_of(message.get("content")), 0)
        # A call with no stored result was cut off (a stop, a crash).
        for tool in self._tools.values():
            self._update({"sessionUpdate": "tool_call_update", "toolCallId": tool["id"], "status": "failed"})
        self._tools.clear()
        return first

    def _replay_user(self, content: Any) -> None:
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
                payload: dict[str, Any] = {"type": "text", "text": block["text"]}
            elif block.get("type") == "image":
                payload = {"type": "image", "mimeType": block.get("mimeType") or "image/png"}
            else:
                continue
            self._update(
                {"sessionUpdate": "user_message_chunk", "content": payload, "messageId": f"u{self._message}"}
            )

    def _replay_shell(self, message: dict[str, Any]) -> None:
        """A command the user ran with ``!`` in Pi's terminal UI."""
        command = message.get("command")
        if not isinstance(command, str):
            return
        tool_id = self._next("pi-")
        code = message.get("exitCode")
        self._update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_id,
                "title": command,
                "kind": "execute",
                "status": "failed" if message.get("cancelled") is True else "completed",
                "rawInput": {"command": command},
                "_meta": {
                    "terminal_output": {"data": _tail(str(message.get("output") or ""), OUTPUT_MAX)},
                    "terminal_exit": {"exit_code": code if isinstance(code, int) else None},
                },
            }
        )

    def _on_dialog(self, record: dict[str, Any]) -> None:
        dialog_id, method = record.get("id"), record.get("method")
        if not isinstance(dialog_id, str):
            return
        if self._session_id is None:
            # Asked while Pi starts: there is no chat to show it in yet.
            if method in ("confirm", "select", "input", "editor"):
                self._answer_dialog(dialog_id, None)
            return
        title = str(record.get("title") or "")
        if method == "notify":
            severity = record.get("notifyType")
            self._notice(
                severity if severity in ("info", "warning", "error") else "info",
                str(record.get("message") or ""),
            )
        elif method == "confirm":
            self._ask_buttons(
                dialog_id,
                "\n".join(part for part in (title, str(record.get("message") or "")) if part),
                ["Yes", "No"],
                lambda picked: {"confirmed": picked == "Yes"},
            )
        elif method == "select":
            raw_options = record.get("options")
            options = [o for o in raw_options if isinstance(o, str) and o] if isinstance(raw_options, list) else []
            if not options:
                self._answer_dialog(dialog_id, None)
            elif len(options) <= BUTTON_OPTIONS_MAX and all(len(o) <= BUTTON_LABEL_MAX for o in options):
                self._ask_buttons(dialog_id, title, options, lambda picked: {"value": picked})
            else:
                self._ask_form(dialog_id, title, {"type": "string", "title": "Choice", "enum": options})
        elif method in ("input", "editor"):
            field: dict[str, Any] = {"type": "string", "title": "Answer"}
            if isinstance(record.get("placeholder"), str) and record["placeholder"]:
                field["description"] = record["placeholder"]
            if isinstance(record.get("prefill"), str):
                field["default"] = record["prefill"]
            self._ask_form(dialog_id, title, field)
        # The other methods set parts of Pi's terminal UI: nothing to show.

    def _answer_dialog(self, dialog_id: str, answer: dict[str, Any] | None) -> None:
        if self._pi is not None:
            self._pi.send(
                {"type": "extension_ui_response", "id": dialog_id, **(answer or {"cancelled": True})}
            )

    def _ask_buttons(
        self,
        dialog_id: str,
        text: str,
        options: list[str],
        answer: Callable[[str], dict[str, Any]],
    ) -> None:
        """A dialog as an approval card: its first line is the title, the
        rest the body, and each option a button."""
        lines = [line for line in text.splitlines() if line.strip()]
        call: dict[str, Any] = {"title": lines[0].strip() if lines else "Pi has a question", "kind": "other"}
        if len(lines) > 1:
            call["content"] = [_text_block("\n".join(lines[1:]))]

        def done(result: Any) -> None:
            outcome = result.get("outcome") if isinstance(result, dict) else None
            picked = outcome.get("optionId") if isinstance(outcome, dict) else None
            if isinstance(outcome, dict) and outcome.get("outcome") == "selected" and picked in ids:
                self._answer_dialog(dialog_id, answer(ids[picked]))
            else:
                self._answer_dialog(dialog_id, None)

        ids = {f"o{index}": option for index, option in enumerate(options)}
        self._ask_client(
            "session/request_permission",
            {
                "sessionId": self._session_id,
                "toolCall": call,
                # Pi preselects the first option, so it is the main button;
                # the others stay neutral, whatever their labels say.
                "options": [
                    {
                        "optionId": option_id,
                        "name": option,
                        "kind": "allow_once" if index == 0 else "allow_always",
                    }
                    for index, (option_id, option) in enumerate(ids.items())
                ],
            },
            done,
        )

    def _ask_form(self, dialog_id: str, title: str, field: dict[str, Any]) -> None:
        def done(result: Any) -> None:
            content = result.get("content") if isinstance(result, dict) else None
            value = content.get("value") if isinstance(content, dict) else None
            if isinstance(result, dict) and result.get("action") == "accept" and isinstance(value, str):
                self._answer_dialog(dialog_id, {"value": value})
            else:
                self._answer_dialog(dialog_id, None)

        self._ask_client(
            "elicitation/create",
            {
                "sessionId": self._session_id,
                "mode": "form",
                "message": title or "Pi has a question",
                "requestedSchema": {
                    "type": "object",
                    "properties": {"value": field},
                    "required": ["value"],
                },
            },
            done,
        )


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("usage: pi_bridge.py <pi command...>\n")
        return 2
    bridge = Bridge(argv)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: bridge._events.put(("eof",)))
    return bridge.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
