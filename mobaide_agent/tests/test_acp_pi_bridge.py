"""The Pi bridge (``mobaide/acp/pi_bridge.py``): where Pi keeps its stored
sessions and how they list, Pi's models and thinking levels as ACP config
options, its tool calls as ACP tool content, and the bridge itself, run as
the chat runs it (``python -I pi_bridge.py <pi>``) against a scripted Pi RPC
child: session start, turns, steering, config changes, stored sessions,
extension dialogs and the end of the Pi process."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from mobaide.acp import pi_bridge
from tests.conftest import skip_if_not_linux

pytestmark = pytest.mark.timeout(60)

BRIDGE = pi_bridge.__file__
READ_TIMEOUT = 20.0

# A Pi in RPC mode that plays the scenario of ``FAKE_PI_SCENARIO`` and logs
# every command to ``FAKE_PI_LOG``. A prompt plays the turn named by its first
# word; it has no model while ``signed_out_marker`` exists. ``at_eof`` (``slow``,
# ``stay``, ``ignore_terminate``) is how it takes the end of its input.
FAKE_PI = r'''
import json
import os
import signal
import sys
import time

SCENARIO = json.loads(os.environ.get("FAKE_PI_SCENARIO") or "{}")
LEVELS = ["off", "low", "medium", "high"]


def log(record):
    with open(os.environ["FAKE_PI_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def emit(record):
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()


def fail(code):
    sys.stderr.write("fatal: pi broke\n")
    sys.stderr.flush()
    return code


def terminated(*_):
    log({"terminated": True})
    os._exit(0)


def main():
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("1.5.2")
        return 0
    log({"argv": argv, "cwd": os.getcwd(), "pid": os.getpid()})
    at_eof = SCENARIO.get("at_eof")
    if at_eof == "stay":
        signal.signal(signal.SIGTERM, terminated)
    elif at_eof == "ignore_terminate":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if "exit_on_start" in SCENARIO:
        return fail(SCENARIO["exit_on_start"])
    marker = SCENARIO.get("signed_out_marker")
    models = [] if marker and os.path.exists(marker) else SCENARIO.get("models", [])
    state = {
        "sessionId": SCENARIO.get("session_id", "pi-session-1"),
        "sessionName": SCENARIO.get("session_name"),
        "model": SCENARIO.get("model")
        or (models[0] if models else {"provider": "unknown", "id": "unknown"}),
        "thinkingLevel": "medium",
    }

    def levels():
        return state["model"].get("levels", LEVELS)

    for record in SCENARIO.get("start_events", []):
        emit(record)
    held = None
    for raw in sys.stdin:
        command = json.loads(raw)
        log(command)
        kind = command.get("type")

        def respond(success=True, **extra):
            emit({"type": "response", "id": command.get("id"), "command": kind, "success": success, **extra})

        if kind == "get_state":
            respond(data=state)
        elif kind == "get_available_models":
            respond(data={"models": models})
        elif kind == "get_available_thinking_levels":
            respond(data={"levels": levels()})
        elif kind == "get_commands":
            respond(data={"commands": SCENARIO.get("commands", [])})
        elif kind == "get_messages":
            respond(data={"messages": SCENARIO.get("messages", [])})
        elif kind == "set_model":
            found = [m for m in models if (m["provider"], m["id"]) == (command["provider"], command["modelId"])]
            if not found:
                respond(False, error="Model not found: " + command["modelId"] + "\nRun pi --list-models")
                continue
            state["model"] = found[0]
            if state["thinkingLevel"] not in levels():
                state["thinkingLevel"] = levels()[0]
            respond(data=found[0])
        elif kind == "set_thinking_level":
            state["thinkingLevel"] = command["level"]
            respond()
        elif kind == "compact":
            script = SCENARIO.get("compact", {})
            for record in script.get("events", []):
                emit(record)
            if "error" in script:
                respond(False, error=script["error"])
            else:
                respond(data={})
        elif kind == "prompt":
            words = command["message"].split()
            turn = SCENARIO.get("turns", {}).get(words[0] if words else "", {})
            if "refuse" in turn:
                respond(False, error=turn["refuse"])
                continue
            if "data" in turn:
                respond(data=turn["data"])
            else:
                respond()
            for record in turn.get("events", []):
                emit(record)
            if "exit" in turn:
                return fail(turn["exit"])
            if turn.get("hold"):
                held = turn
            elif turn.get("settle", True):
                emit({"type": "agent_settled"})
        elif kind == "steer":
            if held is not None and held.get("steer") == "late":
                emit({"type": "agent_settled"})
                respond()
            else:
                respond()
                emit({"type": "agent_settled"})
            held = None
        elif kind == "abort":
            respond()
            if held is not None:
                emit({"type": "agent_settled", "aborted": True})
                held = None
        elif kind == "extension_ui_response":
            if held is not None:
                emit({"type": "agent_settled"})
                held = None
        else:
            respond()
    if at_eof in ("stay", "ignore_terminate"):
        # Not for ever: a bridge that leaves it behind must not leak it.
        time.sleep(30)
        return 0
    if at_eof == "slow":
        time.sleep(0.3)
    log({"eof": True})
    return 0


sys.exit(main())
'''

SONNET = {"provider": "anthropic", "id": "sonnet", "name": "Sonnet", "contextWindow": 200000}
HAIKU = {"provider": "anthropic", "id": "haiku", "name": "Haiku", "levels": ["off"]}
GPT = {"provider": "openai", "id": "gpt", "name": "GPT"}
LLAMA = {"provider": "openrouter", "id": "meta/llama"}

MODEL_OPTION = {
    "id": "model",
    "name": "Model",
    "category": "model",
    "type": "select",
    "currentValue": "anthropic/sonnet",
    "options": [
        {
            "name": "anthropic",
            "options": [
                {"value": "anthropic/sonnet", "name": "Sonnet"},
                {"value": "anthropic/haiku", "name": "Haiku"},
            ],
        },
        {"name": "openai", "options": [{"value": "openai/gpt", "name": "GPT"}]},
    ],
}
THINKING_OPTION = {
    "id": "thought_level",
    "name": "Thinking",
    "category": "thought_level",
    "type": "select",
    "currentValue": "medium",
    "options": [
        {"value": "off", "name": "Off"},
        {"value": "low", "name": "Low"},
        {"value": "medium", "name": "Medium"},
        {"value": "high", "name": "High"},
    ],
}

AGENT_START = {"type": "agent_start"}
MESSAGE_START = {"type": "message_start", "message": {"role": "assistant"}}


def _delta(kind: str, text: str, index: int = 0) -> dict[str, Any]:
    return {
        "type": "message_update",
        "assistantMessageEvent": {"type": kind, "contentIndex": index, "delta": text},
    }


def _message_end(content: list[dict[str, Any]], stop: str = "stop", **extra: Any) -> dict[str, Any]:
    return {
        "type": "message_end",
        "message": {"role": "assistant", "content": content, "stopReason": stop, **extra},
    }


def _says(text: str) -> list[dict[str, Any]]:
    return [
        AGENT_START,
        MESSAGE_START,
        _delta("text_delta", text),
        _message_end([{"type": "text", "text": text}]),
    ]


def _session_lines(session_id: str, cwd: str, *entries: dict[str, Any]) -> str:
    header = {"type": "session", "version": 3, "id": session_id, "cwd": cwd}
    # Compact JSON, as Pi writes it.
    return "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in (header, *entries))


def _write_session(
    root: Path,
    cwd: str,
    session_id: str,
    *entries: dict[str, Any],
    file_name: str | None = None,
    mtime: float | None = None,
) -> Path:
    folder = root / pi_bridge.session_folder_name(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (file_name or f"2026-11-13T10-00-00-000Z_{session_id}.jsonl")
    path.write_text(_session_lines(session_id, cwd, *entries), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _user(text: Any) -> dict[str, Any]:
    return {"type": "message", "message": {"role": "user", "content": text}}


def _named(name: str) -> dict[str, Any]:
    return {"type": "session_info", "name": name}


class TestSessionsRoot:
    def test_the_session_dir_of_the_env_wins(self, tmp_path):
        (tmp_path / "settings.json").write_text(json.dumps({"sessionDir": "/from/settings"}))
        env = {
            "PI_CODING_AGENT_DIR": str(tmp_path),
            "PI_CODING_AGENT_SESSION_DIR": str(tmp_path / "custom"),
        }

        assert pi_bridge.sessions_root(env) == str(tmp_path / "custom")

    def test_the_session_dir_of_pi_settings(self, tmp_path):
        (tmp_path / "settings.json").write_text(
            json.dumps({"sessionDir": str(tmp_path / "elsewhere")})
        )

        root = pi_bridge.sessions_root({"PI_CODING_AGENT_DIR": str(tmp_path)})

        assert root == str(tmp_path / "elsewhere")

    @pytest.mark.parametrize(
        "settings",
        [None, "{not json", '["a list"]', '{"sessionDir": "  "}', '{"sessionDir": 5}'],
        ids=["no-file", "unreadable", "not-object", "blank", "not-a-string"],
    )
    def test_without_a_usable_setting_it_is_the_sessions_folder(self, tmp_path, settings):
        if settings is not None:
            (tmp_path / "settings.json").write_text(settings)

        root = pi_bridge.sessions_root({"PI_CODING_AGENT_DIR": str(tmp_path)})

        assert root == os.path.join(str(tmp_path), "sessions")

    def test_without_an_agent_dir_it_is_in_the_home_folder(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        assert pi_bridge.agent_dir({}) == os.path.join(str(tmp_path), ".pi", "agent")
        assert pi_bridge.sessions_root({}) == os.path.join(str(tmp_path), ".pi", "agent", "sessions")


class TestSessionFolderName:
    @pytest.mark.parametrize(
        ("cwd", "name"),
        [
            ("/Users/me/work/app", "--Users-me-work-app--"),
            ("C:\\Users\\me\\app", "--C--Users-me-app--"),
            ("\\\\server\\share", "---server-share--"),
        ],
        ids=["posix", "windows-drive", "windows-share"],
    )
    def test_the_folder_of_a_working_directory(self, cwd, name):
        assert pi_bridge.session_folder_name(cwd) == name


class TestReadSessionRow:
    def test_the_title_is_the_first_user_message(self, tmp_path):
        path = _write_session(
            tmp_path,
            "/work/app",
            "abc-1",
            _user("\n  Fix   the login\tbug  \nand the tests"),
            _user("Second message"),
            mtime=1767323045,
        )

        row = pi_bridge.read_session_row(str(path))

        assert row == {
            "sessionId": "abc-1",
            "cwd": "/work/app",
            "title": "Fix the login bug",
            "updatedAt": "2026-01-02T03:04:05Z",
            "_mtime": 1767323045,
            "_path": str(path),
        }

    def test_a_message_of_content_blocks_gives_its_text(self, tmp_path):
        path = _write_session(
            tmp_path,
            "/work/app",
            "abc-1",
            _user([{"type": "image", "data": "AAAA"}, {"type": "text", "text": "What is this?"}]),
        )

        assert pi_bridge.read_session_row(str(path))["title"] == "What is this?"

    def test_a_long_first_message_is_cut_to_the_title_length(self, tmp_path):
        path = _write_session(tmp_path, "/work/app", "abc-1", _user("word " * 40))

        title = pi_bridge.read_session_row(str(path))["title"]

        assert len(title) <= pi_bridge.TITLE_MAX
        assert title.startswith("word word") and title.endswith("word…")

    def test_the_name_the_user_gave_wins_and_the_last_one_counts(self, tmp_path):
        path = _write_session(
            tmp_path,
            "/work/app",
            "abc-1",
            _named("First name"),
            _user("Fix the bug"),
            _named("  Login work  "),
        )

        assert pi_bridge.read_session_row(str(path))["title"] == "Login work"

    def test_a_name_the_user_cleared_gives_the_first_message_again(self, tmp_path):
        path = _write_session(
            tmp_path, "/work/app", "abc-1", _named("Login work"), _user("Fix the bug"), _named("")
        )

        assert pi_bridge.read_session_row(str(path))["title"] == "Fix the bug"

    def test_a_session_with_no_message_has_no_title(self, tmp_path):
        path = _write_session(tmp_path, "/work/app", "abc-1")

        assert pi_bridge.read_session_row(str(path))["title"] is None

    def test_a_name_at_the_end_of_a_large_file_is_found(self, tmp_path):
        """Only the head and the tail of a session file are read."""
        filler = {"type": "message", "message": {"role": "assistant", "content": "x" * 1000}}
        path = _write_session(
            tmp_path,
            "/work/app",
            "abc-1",
            _user("Fix the bug"),
            *([filler] * 400),
            _named("Named late"),
        )
        assert path.stat().st_size > pi_bridge.HEAD_BYTES + pi_bridge.TAIL_BYTES

        assert pi_bridge.read_session_row(str(path))["title"] == "Named late"

    @pytest.mark.parametrize(
        "header",
        [
            {"type": "message", "id": "abc-1", "cwd": "/work/app"},
            {"type": "session", "cwd": "/work/app"},
            {"type": "session", "id": "abc-1"},
            {"type": "session", "id": "abc 1; rm", "cwd": "/work/app"},
            {"type": "session", "id": "a" * 201, "cwd": "/work/app"},
        ],
        ids=["not-a-session", "no-id", "no-cwd", "id-not-safe", "id-too-long"],
    )
    def test_a_file_that_is_not_a_pi_session_is_no_row(self, tmp_path, header):
        path = tmp_path / "file.jsonl"
        path.write_text(json.dumps(header) + "\n" + json.dumps(_user("hi")) + "\n")

        assert pi_bridge.read_session_row(str(path)) is None

    def test_a_missing_file_is_no_row(self, tmp_path):
        assert pi_bridge.read_session_row(str(tmp_path / "gone.jsonl")) is None


class TestListSessions:
    def test_newest_first(self, tmp_path):
        _write_session(tmp_path, "/work/app", "old", _user("Old"), mtime=1000)
        _write_session(tmp_path, "/work/app", "new", _user("New"), mtime=3000)
        _write_session(tmp_path, "/work/other", "mid", _user("Mid"), mtime=2000)

        rows = pi_bridge.list_sessions(str(tmp_path), None)

        assert [row["sessionId"] for row in rows] == ["new", "mid", "old"]

    def test_with_a_folder_only_its_sessions(self, tmp_path):
        app = tmp_path / "work" / "app"
        app.mkdir(parents=True)
        root = tmp_path / "sessions"
        _write_session(root, str(app), "mine", _user("Mine"))
        _write_session(root, str(tmp_path / "work" / "other"), "theirs", _user("Theirs"))

        rows = pi_bridge.list_sessions(str(root), str(app))

        assert [row["sessionId"] for row in rows] == ["mine"]

    def test_files_kept_flat_in_a_custom_root_are_filtered_by_their_folder(self, tmp_path):
        """With ``sessionDir`` set, Pi keeps every session in one folder."""
        app = tmp_path / "work" / "app"
        app.mkdir(parents=True)
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "a_mine.jsonl").write_text(_session_lines("mine", str(app), _user("Mine")))
        (root / "b_theirs.jsonl").write_text(_session_lines("theirs", "/work/other", _user("Theirs")))
        (root / "notes.txt").write_text("not a session")

        rows = pi_bridge.list_sessions(str(root), str(app))

        assert [row["sessionId"] for row in rows] == ["mine"]

    def test_a_file_in_the_folder_of_another_working_directory_is_left_out(self, tmp_path):
        """The header decides: a file moved into the folder is not its session."""
        app = tmp_path / "work" / "app"
        app.mkdir(parents=True)
        root = tmp_path / "sessions"
        folder = root / pi_bridge.session_folder_name(str(app))
        folder.mkdir(parents=True)
        (folder / "moved.jsonl").write_text(_session_lines("moved", "/work/other", _user("Moved")))

        assert pi_bridge.list_sessions(str(root), str(app)) == []

    def test_a_root_that_does_not_exist_lists_nothing(self, tmp_path):
        assert pi_bridge.list_sessions(str(tmp_path / "nowhere"), None) == []


class TestFindSessionFile:
    def test_the_file_pi_named_after_the_id(self, tmp_path):
        _write_session(tmp_path, "/work/other", "abc-2", _user("Other"))
        path = _write_session(tmp_path, "/work/app", "abc-1", _user("Mine"))

        assert pi_bridge.find_session_file(str(tmp_path), "abc-1") == str(path)

    def test_a_file_named_by_the_bare_id(self, tmp_path):
        path = _write_session(tmp_path, "/work/app", "abc-1", file_name="abc-1.jsonl")

        assert pi_bridge.find_session_file(str(tmp_path), "abc-1") == str(path)

    def test_a_file_the_user_renamed_is_found_by_its_header(self, tmp_path):
        path = _write_session(tmp_path, "/work/app", "abc-1", file_name="my-notes.jsonl")

        assert pi_bridge.find_session_file(str(tmp_path), "abc-1") == str(path)

    def test_a_name_that_matches_with_another_id_inside_is_not_it(self, tmp_path):
        folder = tmp_path / "--work-app--"
        folder.mkdir()
        (folder / "2026_abc-1.jsonl").write_text(_session_lines("zzz-9", "/work/app"))

        assert pi_bridge.find_session_file(str(tmp_path), "abc-1") is None

    def test_an_unknown_id(self, tmp_path):
        _write_session(tmp_path, "/work/app", "abc-1")

        assert pi_bridge.find_session_file(str(tmp_path), "abc-2") is None


class TestConfigOptions:
    def test_one_provider_is_a_flat_list(self):
        options = pi_bridge.config_options([SONNET, HAIKU], SONNET, ["off", "high"], "high")

        assert options[0]["currentValue"] == "anthropic/sonnet"
        assert options[0]["options"] == [
            {"value": "anthropic/sonnet", "name": "Sonnet"},
            {"value": "anthropic/haiku", "name": "Haiku"},
        ]
        assert options[1]["currentValue"] == "high"

    def test_several_providers_are_groups_with_the_current_one_first(self):
        models = [SONNET, {"provider": "bedrock", "id": "nova"}, GPT, LLAMA]

        (model,) = pi_bridge.config_options(models, GPT, [], None)

        assert model["currentValue"] == "openai/gpt"
        assert [group["name"] for group in model["options"]] == [
            "openai",
            "anthropic",
            "bedrock",
            "openrouter",
        ]
        # A model with no display name shows its id.
        assert model["options"][3]["options"] == [
            {"value": "openrouter/meta/llama", "name": "meta/llama"}
        ]

    def test_a_current_model_pi_does_not_list_is_still_a_choice(self):
        current = {"provider": "anthropic", "id": "opus"}

        (model,) = pi_bridge.config_options([SONNET], current, [], None)

        assert model["currentValue"] == "anthropic/opus"
        assert model["options"] == [
            {"value": "anthropic/opus", "name": "opus"},
            {"value": "anthropic/sonnet", "name": "Sonnet"},
        ]

    @pytest.mark.parametrize(
        "current",
        [None, {"provider": "unknown", "id": "unknown"}, {"provider": "anthropic"}],
        ids=["none", "pi-placeholder", "no-id"],
    )
    def test_without_a_current_model_there_is_no_model_option(self, current):
        assert pi_bridge.config_options([SONNET, GPT], current, [], None) == []

    def test_entries_that_are_no_models_are_left_out(self):
        models = [SONNET, {"provider": "unknown", "id": "unknown"}, {"id": "orphan"}, "junk"]

        (model,) = pi_bridge.config_options(models, SONNET, [], None)

        assert model["options"] == [{"value": "anthropic/sonnet", "name": "Sonnet"}]

    def test_thinking_levels_show_with_their_names(self):
        (thinking,) = pi_bridge.config_options([], None, ["off", "xhigh", "max", "turbo"], "xhigh")

        assert thinking["id"] == "thought_level"
        assert thinking["category"] == "thought_level"
        assert thinking["currentValue"] == "xhigh"
        assert thinking["options"] == [
            {"value": "off", "name": "Off"},
            {"value": "xhigh", "name": "Extra high"},
            {"value": "max", "name": "Max"},
            {"value": "turbo", "name": "turbo"},
        ]

    @pytest.mark.parametrize(
        ("levels", "level"),
        [(["off"], "off"), (["off", "high"], "medium"), (["off", "high"], None), ([], "off")],
        ids=["one-level", "level-not-listed", "no-level", "no-levels"],
    )
    def test_nothing_to_choose_gives_no_thinking_option(self, levels, level):
        assert pi_bridge.config_options([], None, levels, level) == []


class TestToolCalls:
    @pytest.mark.parametrize(
        ("name", "args", "title"),
        [
            ("bash", {"command": "ls -la"}, "ls -la"),
            ("bash", {}, "Shell"),
            ("read", {"file_path": "b.py"}, "Read b.py"),
            ("grep", {"pattern": "TODO"}, "Search TODO"),
            ("web_fetch", {"url": "https://example.com"}, "web_fetch"),
        ],
        ids=["command", "no-command", "path-alias", "pattern", "tool-of-an-extension"],
    )
    def test_title(self, name, args, title):
        assert pi_bridge.tool_title(name, args) == title

    def test_a_relative_file_is_located_in_the_session_folder(self, tmp_path):
        locations = pi_bridge.tool_locations("read", {"path": "src/../a.py", "offset": 12}, str(tmp_path))

        assert locations == [{"path": os.path.join(str(tmp_path), "a.py"), "line": 12}]

    def test_an_absolute_file_keeps_its_path(self, tmp_path):
        target = str(tmp_path / "other" / "a.py")

        assert pi_bridge.tool_locations("edit", {"path": target}, "/work/app") == [{"path": target}]

    @pytest.mark.parametrize("offset", [0, -3, True, "12"], ids=["zero", "negative", "bool", "string"])
    def test_an_offset_that_is_no_line_number_is_left_out(self, tmp_path, offset):
        locations = pi_bridge.tool_locations("read", {"path": "a.py", "offset": offset}, str(tmp_path))

        assert locations == [{"path": os.path.join(str(tmp_path), "a.py")}]

    @pytest.mark.parametrize(
        ("name", "args"),
        [("bash", {"path": "a.py"}), ("grep", {"path": "src"}), ("read", {}), ("read", None)],
        ids=["shell", "search", "no-path", "no-args"],
    )
    def test_a_call_without_a_file_has_no_location(self, name, args):
        assert pi_bridge.tool_locations(name, args, "/work/app") == []


class TestEditDiffs:
    def test_a_write_is_a_new_file(self, tmp_path):
        diffs = pi_bridge.edit_diffs("write", {"path": "notes.md", "content": "hi\n"}, str(tmp_path))

        assert diffs == [
            {
                "type": "diff",
                "path": os.path.join(str(tmp_path), "notes.md"),
                "oldText": None,
                "newText": "hi\n",
            }
        ]

    def test_an_edit_with_one_replacement(self, tmp_path):
        args = {"path": "a.py", "oldText": "x = 1", "newText": "x = 2"}

        diffs = pi_bridge.edit_diffs("edit", args, str(tmp_path))

        assert diffs == [
            {
                "type": "diff",
                "path": os.path.join(str(tmp_path), "a.py"),
                "oldText": "x = 1",
                "newText": "x = 2",
            }
        ]

    def test_an_edit_with_several_replacements_is_one_diff_each(self, tmp_path):
        args = {
            "path": "a.py",
            "edits": [
                {"oldText": "a", "newText": "b"},
                {"oldText": "c"},
                "junk",
                {"oldText": "d", "newText": ""},
            ],
        }

        diffs = pi_bridge.edit_diffs("edit", args, str(tmp_path))

        assert [(d["oldText"], d["newText"]) for d in diffs] == [("a", "b"), ("d", "")]
        assert {d["path"] for d in diffs} == {os.path.join(str(tmp_path), "a.py")}

    @pytest.mark.parametrize(
        ("name", "args"),
        [
            ("write", {"content": "hi"}),
            ("write", {"path": "a.py", "content": None}),
            ("edit", {"path": "a.py"}),
            ("edit", None),
        ],
        ids=["write-no-path", "write-no-content", "edit-no-texts", "no-args"],
    )
    def test_a_call_without_a_whole_change_has_no_diff(self, name, args):
        assert pi_bridge.edit_diffs(name, args, "/work/app") == []


def _text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


class TestShellResult:
    def test_a_live_result_carries_output_and_exit_code(self):
        result = {
            **_text_result("ignored"),
            "structuredContent": {"output": "file-a\n", "exit_code": 3},
        }

        assert pi_bridge.shell_result(result, True) == ("file-a\n", 3)

    def test_a_live_result_without_a_code(self):
        result = {"structuredContent": {"output": "partial", "exit_code": None}}

        assert pi_bridge.shell_result(result, True) == ("partial", None)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("boom\n\nCommand exited with code 2", ("boom", 2)),
            ("Command exited with code -1", ("", -1)),
            (
                "Command exited with code 1 was printed\n\nCommand exited with code 7",
                ("Command exited with code 1 was printed", 7),
            ),
        ],
        ids=["output-and-note", "note-only", "the-last-note-counts"],
    )
    def test_a_stored_result_ends_with_pi_exit_note(self, text, expected):
        assert pi_bridge.shell_result(_text_result(text), True) == expected

    def test_a_stored_result_without_a_note_succeeded(self):
        assert pi_bridge.shell_result(_text_result("file-a\n"), False) == ("file-a\n", 0)

    def test_a_failure_without_a_note_has_no_exit_code(self):
        """A command that was stopped, or one that never started."""
        assert pi_bridge.shell_result(_text_result("Command aborted"), True) == (
            "Command aborted",
            None,
        )

    def test_text_that_only_mentions_the_note_is_output(self):
        text = "Command exited with code unknown"

        assert pi_bridge.shell_result(_text_result(text), False) == (text, 0)

    def test_no_result_at_all(self):
        assert pi_bridge.shell_result(None, True) == ("", None)


class TestPromptParts:
    def test_text_blocks_join_and_images_go_separately(self):
        blocks = [
            {"type": "text", "text": "Look at this"},
            {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"},
            {"type": "text", "text": "and this"},
            {"type": "image", "data": "BBBB"},
            {"type": "resource_link", "uri": "file:///a"},
            {"type": "image"},
            "junk",
        ]

        text, images = pi_bridge.prompt_parts(blocks)

        assert text == "Look at this\nand this"
        assert images == [
            {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"},
            {"type": "image", "data": "BBBB", "mimeType": "image/png"},
        ]

    def test_a_prompt_that_is_no_list_is_empty(self):
        assert pi_bridge.prompt_parts("hello") == ("", [])


class TestResultBlocks:
    def test_text_and_images_become_tool_content(self):
        content = [
            {"type": "text", "text": "Found 2 files"},
            {"type": "text", "text": ""},
            {"type": "image", "data": "AAAA"},
            {"type": "audio", "data": "CCCC"},
        ]

        assert pi_bridge.result_blocks(content) == [
            {"type": "content", "content": {"type": "text", "text": "Found 2 files"}},
            {
                "type": "content",
                "content": {"type": "image", "data": "AAAA", "mimeType": "image/png"},
            },
        ]

    def test_a_long_text_is_capped(self):
        (block,) = pi_bridge.result_blocks([{"type": "text", "text": "x" * (pi_bridge.TEXT_MAX + 10)}])

        assert len(block["content"]["text"]) == pi_bridge.TEXT_MAX

    def test_no_content(self):
        assert pi_bridge.result_blocks(None) == []


class TestSpawnCommand:
    def test_an_argv_is_passed_as_it_is(self):
        argv = ["/opt/pi/bin/pi", "--mode", "rpc"]

        assert pi_bridge.spawn_command(argv) == argv

    def test_a_windows_shell_named_by_its_file_gets_the_same_line(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        argv = ["C:\\Windows\\System32\\cmd.exe", "/c", "C:\\npm\\pi.cmd", "--session", "C:\\s\\a&b.jsonl"]

        assert pi_bridge.spawn_command(argv) == (
            'cmd /d /s /c "C:\\npm\\pi.cmd --session "C:\\s\\a&b.jsonl""'
        )

    def test_a_windows_shim_keeps_the_quotes_of_paths_with_spaces(self, monkeypatch):
        """``cmd /c`` drops the first and last quote of a line with more
        than two, so the whole line goes in one outer pair after ``/s``."""
        monkeypatch.setattr(sys, "platform", "win32")
        argv = [
            "cmd",
            "/c",
            "C:\\Program Files\\nodejs\\pi.cmd",
            "--mode",
            "rpc",
            "--session",
            "C:\\Users\\Me Too\\s.jsonl",
        ]

        line = pi_bridge.spawn_command(argv)

        assert line == (
            'cmd /d /s /c ""C:\\Program Files\\nodejs\\pi.cmd" --mode rpc '
            '--session "C:\\Users\\Me Too\\s.jsonl""'
        )

    def test_a_windows_shim_keeps_the_cmd_syntax_of_a_path_inside_quotes(self, monkeypatch):
        """Pi names a session folder after the project folder: outside
        quotes, a ``&`` of that name would end the command and start another."""
        monkeypatch.setattr(sys, "platform", "win32")
        argv = [
            "cmd",
            "/c",
            "C:\\Program Files\\nodejs\\pi.cmd",
            "--mode",
            "rpc",
            "--session",
            "C:\\s\\--C--a&calc&b--\\x.jsonl",
        ]

        line = pi_bridge.spawn_command(argv)

        assert line == (
            'cmd /d /s /c ""C:\\Program Files\\nodejs\\pi.cmd" --mode rpc '
            '--session "C:\\s\\--C--a&calc&b--\\x.jsonl""'
        )

    @pytest.mark.parametrize(
        ("argument", "quoted"),
        [
            ("C:\\a&b\\", '"C:\\a&b\\\\"'),
            ("C:\\Me Too\\a&b\\", '"C:\\Me Too\\a&b\\\\"'),
        ],
        ids=["cmd-syntax", "cmd-syntax-and-a-space"],
    )
    def test_a_backslash_at_the_end_does_not_escape_the_closing_quote(
        self, monkeypatch, argument, quoted
    ):
        monkeypatch.setattr(sys, "platform", "win32")

        line = pi_bridge.spawn_command(["cmd", "/c", "pi.cmd", "--session-dir", argument])

        assert line == f'cmd /d /s /c "pi.cmd --session-dir {quoted}"'

    def test_a_windows_shim_argument_with_a_double_quote_is_refused(self, monkeypatch):
        """No quoting keeps it apart from ``cmd`` syntax, so Pi does not start."""
        monkeypatch.setattr(sys, "platform", "win32")
        argv = ["cmd", "/c", "pi.cmd", "--mode", "rpc", "--session", 'C:\\s\\a"&calc&"b.jsonl']

        with pytest.raises(ValueError):
            pi_bridge.spawn_command(argv)

    def test_a_windows_executable_is_passed_as_it_is(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        argv = ["C:\\pi\\pi.exe", "--mode", "rpc"]

        assert pi_bridge.spawn_command(argv) == argv


def test_main_without_a_pi_command_is_a_usage_error(capsys):
    assert pi_bridge.main([]) == 2
    assert "usage" in capsys.readouterr().err


class BridgeClient:
    """The chat's side of the bridge: sends requests, and keeps what the
    bridge sends in between (session updates and its own requests)."""

    def __init__(self, proc: asyncio.subprocess.Process, log_path: Path) -> None:
        self.proc = proc
        self.updates: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self._responses: dict[Any, dict[str, Any]] = {}
        self._log_path = log_path
        self._last_id = 0

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n")

    def start(self, method: str, params: dict[str, Any] | None = None) -> int:
        self._last_id += 1
        self.send({"id": self._last_id, "method": method, "params": params or {}})
        return self._last_id

    async def _read(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        raw = await asyncio.wait_for(self.proc.stdout.readline(), READ_TIMEOUT)
        assert raw, "the bridge closed its output"
        message = json.loads(raw)
        if "method" not in message:
            self._responses[message["id"]] = message
        elif "id" in message:
            self.requests.append(message)
        else:
            assert message["method"] == "session/update"
            self.updates.append(message["params"]["update"])
        return message

    async def response(self, request_id: int) -> dict[str, Any]:
        while request_id not in self._responses:
            await self._read()
        return self._responses.pop(request_id)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.response(self.start(method, params))

    async def result(self, method: str, params: dict[str, Any] | None = None) -> Any:
        message = await self.call(method, params)
        assert "error" not in message, message
        return message["result"]

    async def error(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message = await self.call(method, params)
        assert "result" not in message, message
        return message["error"]

    async def update(self, kind: str) -> dict[str, Any]:
        """The next session update of ``kind`` that was not seen yet."""
        seen = len(self.updates)
        while True:
            for update in self.updates[seen:]:
                if update["sessionUpdate"] == kind:
                    return update
            seen = len(self.updates)
            await self._read()

    async def request(self) -> dict[str, Any]:
        """The next request the bridge makes."""
        seen = len(self.requests)
        while len(self.requests) == seen:
            await self._read()
        return self.requests[-1]

    def of_kind(self, *kinds: str) -> list[dict[str, Any]]:
        return [update for update in self.updates if update["sessionUpdate"] in kinds]

    def pi_log(self) -> list[dict[str, Any]]:
        if not self._log_path.exists():
            return []
        return [json.loads(line) for line in self._log_path.read_text(encoding="utf-8").splitlines()]

    def pi_commands(self, *kinds: str) -> list[dict[str, Any]]:
        """What Pi got, without the ids the bridge gave its calls."""
        return [
            {key: value for key, value in record.items() if key != "id"}
            for record in self.pi_log()
            if record.get("type") in kinds
        ]

    async def new_session(self, cwd: Path) -> str:
        result = await self.result("session/new", {"cwd": str(cwd)})
        await self.update("available_commands_update")
        return result["sessionId"]

    def prompt(self, session_id: str, text: str) -> int:
        return self.start(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        )

    async def turn(self, session_id: str, text: str) -> dict[str, Any]:
        return await self.response(self.prompt(session_id, text))

    async def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        return await asyncio.wait_for(self.proc.wait(), READ_TIMEOUT)


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "work" / "app"
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def pi_home(tmp_path) -> Path:
    folder = tmp_path / "pi-agent"
    folder.mkdir()
    return folder


@pytest.fixture
async def bridge(tmp_path, pi_home):
    """Starts the bridge in front of a fake Pi that plays ``scenario``."""
    fake_pi = tmp_path / "fake_pi.py"
    fake_pi.write_text(FAKE_PI, encoding="utf-8")
    clients: list[BridgeClient] = []

    async def start(**scenario: Any) -> BridgeClient:
        scenario.setdefault("models", [SONNET, HAIKU, GPT])
        log_path = tmp_path / f"pi-{len(clients)}.log"
        env = {
            **os.environ,
            "PI_CODING_AGENT_DIR": str(pi_home),
            "FAKE_PI_SCENARIO": json.dumps(scenario),
            "FAKE_PI_LOG": str(log_path),
        }
        env.pop("PI_CODING_AGENT_SESSION_DIR", None)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            BRIDGE,
            sys.executable,
            "-I",
            str(fake_pi),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env=env,
        )
        client = BridgeClient(proc, log_path)
        clients.append(client)
        return client

    yield start
    for client in clients:
        if client.proc.returncode is None:
            client.proc.kill()
            await client.proc.wait()


async def _is_gone(pid: int) -> bool:
    """Whether the process is gone. The system clears a killed one a moment
    after the bridge that started it has exited."""
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


class TestInitialize:
    async def test_it_offers_stored_sessions_images_and_steering(self, bridge):
        client = await bridge()

        result = await client.result("initialize", {"protocolVersion": 1})

        assert result == {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {"image": True},
                "sessionCapabilities": {"list": {}, "close": {}},
            },
            # The version of the ``pi`` the bridge was started with.
            "agentInfo": {"name": "pi", "title": "Pi", "version": "1.5.2"},
            "authMethods": [],
            "_meta": {"steering": {"supported": True}},
        }

    async def test_a_method_it_does_not_have(self, bridge):
        client = await bridge()

        error = await client.error("session/fork", {})

        assert error == {"code": -32601, "message": "Method not found: session/fork"}

    async def test_the_end_of_its_input_ends_the_bridge_and_its_pi(self, bridge, work):
        client = await bridge()
        await client.new_session(work)

        assert await client.close() == 0

        assert client.pi_log()[-1] == {"eof": True}

    @pytest.mark.parametrize("closed_first", [False, True], ids=["open-session", "closed-session"])
    async def test_the_bridge_waits_for_a_pi_that_ends_late(self, bridge, work, closed_first):
        """Pi saves its session on the way out. A bridge that exits first
        takes the threads that watch its Pi with it."""
        client = await bridge(at_eof="slow")
        session_id = await client.new_session(work)
        if closed_first:
            await client.result("session/close", {"sessionId": session_id})

        assert await client.close() == 0

        assert client.pi_log()[-1] == {"eof": True}

    @skip_if_not_linux
    @pytest.mark.parametrize(
        ("at_eof", "got_the_signal"),
        [("stay", True), ("ignore_terminate", False)],
        ids=["ends-on-terminate", "must-be-killed"],
    )
    async def test_a_pi_that_stays_after_the_end_of_its_input_is_ended(
        self, bridge, work, at_eof, got_the_signal
    ):
        """First with the signal Pi answers by stopping the commands it
        runs, then by force: no Pi outlives its bridge."""
        client = await bridge(at_eof=at_eof)
        await client.new_session(work)
        pid = client.pi_log()[0]["pid"]

        assert await client.close() == 0

        assert await _is_gone(pid)
        assert ({"terminated": True} in client.pi_log()) is got_the_signal

    @skip_if_not_linux
    async def test_a_terminate_signal_ends_the_bridge_and_its_pi(self, bridge, work):
        """A stop from outside: the bridge takes the signal itself, so its Pi
        gets an orderly end too."""
        client = await bridge()
        await client.new_session(work)

        client.proc.send_signal(signal.SIGTERM)

        assert await asyncio.wait_for(client.proc.wait(), READ_TIMEOUT) == 0
        assert client.pi_log()[-1] == {"eof": True}


class TestNewSession:
    async def test_it_starts_pi_in_the_folder_and_reports_its_options(self, bridge, work):
        client = await bridge(
            commands=[
                {"name": "review", "description": "Review the diff"},
                {"name": "plan"},
                {"description": "no name"},
            ]
        )

        result = await client.result("session/new", {"cwd": str(work)})
        commands = await client.update("available_commands_update")

        assert result == {
            "sessionId": "pi-session-1",
            "configOptions": [MODEL_OPTION, THINKING_OPTION],
        }
        assert commands["availableCommands"] == [
            {
                "name": "compact",
                "description": "Summarize the conversation so far to free up context",
                "input": {"hint": "what the summary must keep (optional)"},
            },
            {"name": "review", "description": "Review the diff"},
            {"name": "plan", "description": ""},
        ]
        launch = client.pi_log()[0]
        assert launch["argv"] == ["--mode", "rpc"]
        assert os.path.realpath(launch["cwd"]) == os.path.realpath(work)

    @pytest.mark.parametrize(
        "models",
        [[], [{"provider": "unknown", "id": "unknown"}]],
        ids=["no-models", "only-the-placeholder"],
    )
    async def test_a_pi_with_no_provider_signed_in_needs_authentication(self, bridge, work, models):
        client = await bridge(models=models)

        error = await client.error("session/new", {"cwd": str(work)})

        assert error == {"code": -32000, "message": "Authentication required"}
        # The session did not start: nothing can be sent to it.
        refused = await client.error(
            "session/prompt",
            {"sessionId": "pi-session-1", "prompt": [{"type": "text", "text": "hello"}]},
        )
        assert refused == {"code": -32602, "message": "Unknown session"}

    async def test_after_a_sign_in_the_same_bridge_starts_a_session(self, bridge, work, tmp_path):
        """The chat keeps its agent process and asks again after the sign-in."""
        marker = tmp_path / "signed-out"
        marker.write_text("")
        client = await bridge(signed_out_marker=str(marker))
        assert (await client.error("session/new", {"cwd": str(work)}))["code"] == -32000

        marker.unlink()
        session_id = await client.new_session(work)

        assert session_id == "pi-session-1"
        assert [record["argv"] for record in client.pi_log() if "argv" in record] == [
            ["--mode", "rpc"],
            ["--mode", "rpc"],
        ]

    async def test_with_no_model_selected_it_starts_on_the_first_one_pi_has(self, bridge, work):
        """The provider of Pi's default model is signed out and another one
        is signed in: Pi would refuse every message."""
        client = await bridge(
            models=[HAIKU, SONNET, GPT], model={"provider": "unknown", "id": "unknown"}
        )

        result = await client.result("session/new", {"cwd": str(work)})

        assert result["sessionId"] == "pi-session-1"
        # Read again after the change: this model has one thinking level, so
        # there is nothing to choose.
        (model,) = result["configOptions"]
        assert model["currentValue"] == "anthropic/haiku"
        assert client.pi_commands("set_model") == [
            {"type": "set_model", "provider": "anthropic", "modelId": "haiku"}
        ]

    @pytest.mark.parametrize("cwd", ["relative/folder", None, 5], ids=["relative", "missing", "number"])
    async def test_a_folder_that_is_no_absolute_path_is_refused(self, bridge, cwd):
        client = await bridge()

        error = await client.error("session/new", {"cwd": cwd})

        assert error == {"code": -32602, "message": "cwd must be an absolute path"}
        assert client.pi_log() == []

    async def test_a_folder_that_does_not_exist_is_refused(self, bridge, work):
        client = await bridge()
        gone = str(work / "gone")

        error = await client.error("session/new", {"cwd": gone})

        assert error == {"code": -32602, "message": f"The folder does not exist: {gone}"}
        assert client.pi_log() == []

    async def test_a_pi_that_exits_while_it_starts_fails_the_session(self, bridge, work):
        client = await bridge(exit_on_start=4)

        error = await client.error("session/new", {"cwd": str(work)})

        assert error["code"] == -32603
        assert error["message"].startswith("Pi exited with code 4")
        assert await asyncio.wait_for(client.proc.wait(), READ_TIMEOUT) == 4

    async def test_a_dialog_of_an_extension_before_the_session_exists_is_cancelled(self, bridge, work):
        """There is no chat to show it in yet, and Pi waits for an answer."""
        client = await bridge(
            start_events=[
                {"type": "extension_ui_request", "id": "d0", "method": "confirm", "title": "Update?"}
            ],
            turns={"hello": {"events": _says("Hi")}},
        )

        session_id = await client.new_session(work)
        await client.turn(session_id, "hello")

        assert client.requests == []
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", "cancelled": True}
        ]

    async def test_close_ends_the_session_and_a_new_one_can_start(self, bridge, work):
        client = await bridge()
        session_id = await client.new_session(work)

        assert await client.result("session/close", {"sessionId": session_id}) == {}

        refused = await client.error(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "hello"}]},
        )
        assert refused == {"code": -32602, "message": "Unknown session"}
        assert await client.new_session(work) == "pi-session-1"
        assert [record["argv"] for record in client.pi_log() if "argv" in record] == [
            ["--mode", "rpc"],
            ["--mode", "rpc"],
        ]


BASH = {"command": "ls -la"}
TOOL_TURN = [
    AGENT_START,
    MESSAGE_START,
    _delta("thinking_delta", "List the files.", 0),
    _delta("text_delta", "Let me ", 1),
    _delta("text_delta", "look.", 1),
    {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "toolcall_start",
            "contentIndex": 2,
            "id": "t1",
            "toolName": "bash",
        },
    },
    {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "toolcall_end",
            "contentIndex": 2,
            "toolCall": {"type": "toolCall", "id": "t1", "name": "bash", "arguments": BASH},
        },
    },
    _message_end(
        [
            {"type": "thinking", "thinking": "List the files."},
            {"type": "text", "text": "Let me look."},
            {"type": "toolCall", "id": "t1", "name": "bash", "arguments": BASH},
        ],
        stop="toolUse",
        usage={"totalTokens": 1200},
    ),
    {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "bash", "args": BASH},
    {
        "type": "tool_execution_update",
        "toolCallId": "t1",
        "partialResult": {"content": [{"type": "text", "text": "file-a\n"}]},
    },
    {
        "type": "tool_execution_end",
        "toolCallId": "t1",
        "isError": False,
        "result": {
            "content": [{"type": "text", "text": "file-a\nfile-b\n"}],
            "structuredContent": {"output": "file-a\nfile-b\n", "exit_code": 0},
        },
    },
    MESSAGE_START,
    _delta("text_delta", "Two files."),
    _message_end([{"type": "text", "text": "Two files."}], usage={"totalTokens": 1500}),
]


# The model starts to write a call; whether Pi runs it is up to the turn.
NAMED_CALL = [
    AGENT_START,
    MESSAGE_START,
    {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "toolcall_start",
            "contentIndex": 0,
            "id": "t1",
            "toolName": "bash",
        },
    },
]


def _text(kind: str, text: str, message_id: str) -> dict[str, Any]:
    return {
        "sessionUpdate": kind,
        "content": {"type": "text", "text": text},
        "messageId": message_id,
    }


class TestPrompt:
    async def test_a_turn_streams_text_thoughts_and_a_shell_call(self, bridge, work):
        client = await bridge(turns={"list": {"events": TOOL_TURN}})
        session_id = await client.new_session(work)
        before = len(client.updates)

        response = await client.turn(session_id, "list the files\nof this folder")

        assert response["result"] == {"stopReason": "end_turn"}
        updates = client.updates[before:]
        tool_id = updates[5]["toolCallId"]
        assert updates == [
            {"sessionUpdate": "session_info_update", "title": "list the files"},
            _text("agent_thought_chunk", "List the files.", "m1.0"),
            _text("agent_message_chunk", "Let me ", "m1.1"),
            _text("agent_message_chunk", "look.", "m1.1"),
            {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_id,
                "title": "Shell",
                "kind": "execute",
                "status": "pending",
            },
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_id,
                "title": "ls -la",
                "rawInput": BASH,
                "locations": [],
            },
            {"sessionUpdate": "usage_update", "used": 1200, "size": 200000},
            {"sessionUpdate": "tool_call_update", "toolCallId": tool_id, "status": "in_progress"},
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_id,
                "_meta": {"terminal_output": {"data": "file-a\n"}},
            },
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_id,
                "status": "completed",
                "_meta": {
                    "terminal_output": {"data": "file-a\nfile-b\n"},
                    "terminal_exit": {"exit_code": 0},
                },
            },
            _text("agent_message_chunk", "Two files.", "m2.0"),
            {"sessionUpdate": "usage_update", "used": 1500, "size": 200000},
        ]
        assert client.pi_commands("prompt") == [
            {"type": "prompt", "message": "list the files\nof this folder"}
        ]

    async def test_only_the_first_message_names_the_session(self, bridge, work):
        client = await bridge(turns={"hello": {"events": _says("Hi")}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "hello there")
        await client.turn(session_id, "hello again")

        assert client.of_kind("session_info_update") == [
            {"sessionUpdate": "session_info_update", "title": "hello there"}
        ]

    async def test_a_session_pi_already_named_keeps_its_name(self, bridge, work):
        client = await bridge(session_name="Login work", turns={"hello": {"events": _says("Hi")}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "hello there")

        assert client.of_kind("session_info_update") == []

    async def test_a_name_set_in_pi_becomes_the_title(self, bridge, work):
        renamed = {"type": "session_info_changed", "name": "Login work"}
        client = await bridge(turns={"hello": {"events": [*_says("Hi"), renamed]}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "hello there")

        assert [update["title"] for update in client.of_kind("session_info_update")] == [
            "hello there",
            "Login work",
        ]

    async def test_images_go_to_pi_next_to_the_text(self, bridge, work):
        client = await bridge(turns={"what": {"events": _says("A cat")}})
        session_id = await client.new_session(work)

        response = await client.call(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [
                    {"type": "text", "text": "what is this"},
                    {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"},
                ],
            },
        )

        assert response["result"] == {"stopReason": "end_turn"}
        assert client.pi_commands("prompt") == [
            {
                "type": "prompt",
                "message": "what is this",
                "images": [{"type": "image", "data": "AAAA", "mimeType": "image/jpeg"}],
            }
        ]

    async def test_a_provider_that_does_not_stream_shows_its_message_at_the_end(self, bridge, work):
        events = [
            AGENT_START,
            MESSAGE_START,
            _message_end(
                [
                    {"type": "thinking", "thinking": "Hm."},
                    {"type": "text", "text": "All at once."},
                    {"type": "toolCall", "id": "t1", "name": "read", "arguments": {"path": "a.py"}},
                ],
                stop="toolUse",
            ),
        ]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "hello")

        assert client.of_kind("agent_thought_chunk", "agent_message_chunk") == [
            _text("agent_thought_chunk", "Hm.", "m1.0"),
            _text("agent_message_chunk", "All at once.", "m1.1"),
        ]
        (call,) = client.of_kind("tool_call")
        assert call["title"] == "Read a.py"
        assert call["kind"] == "read"
        assert call["rawInput"] == {"path": "a.py"}
        assert call["locations"] == [{"path": os.path.join(str(work), "a.py")}]

    async def test_a_finished_edit_shows_its_change(self, bridge, work):
        args = {"path": "a.py", "oldText": "x = 1", "newText": "x = 2"}
        events = [
            AGENT_START,
            {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "edit", "args": args},
            {
                "type": "tool_execution_end",
                "toolCallId": "t1",
                "isError": False,
                "result": {"content": [{"type": "text", "text": "Edited a.py"}]},
            },
        ]
        client = await bridge(turns={"fix": {"events": events}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "fix it")

        final = client.of_kind("tool_call_update")[-1]
        assert final["status"] == "completed"
        assert final["content"] == [
            {
                "type": "diff",
                "path": os.path.join(str(work), "a.py"),
                "oldText": "x = 1",
                "newText": "x = 2",
            }
        ]

    async def test_a_failed_edit_shows_pi_error_and_no_change(self, bridge, work):
        args = {"path": "a.py", "oldText": "x = 1", "newText": "x = 2"}
        events = [
            AGENT_START,
            {"type": "tool_execution_start", "toolCallId": "t1", "toolName": "edit", "args": args},
            {
                "type": "tool_execution_end",
                "toolCallId": "t1",
                "isError": True,
                "result": {"content": [{"type": "text", "text": "oldText not found"}]},
            },
        ]
        client = await bridge(turns={"fix": {"events": events}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "fix it")

        final = client.of_kind("tool_call_update")[-1]
        assert final["status"] == "failed"
        assert final["content"] == [
            {"type": "content", "content": {"type": "text", "text": "oldText not found"}}
        ]

    async def test_an_answer_cut_at_the_output_limit(self, bridge, work):
        events = [
            AGENT_START,
            MESSAGE_START,
            _delta("text_delta", "Part of"),
            _message_end([{"type": "text", "text": "Part of"}], stop="length"),
        ]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["result"] == {"stopReason": "max_tokens"}

    async def test_a_command_pi_handles_itself_ends_the_turn_at_once(self, bridge, work):
        """An extension command runs no model turn, so Pi never settles."""
        client = await bridge(
            turns={"/review": {"data": {"disposition": "handled"}, "settle": False}}
        )
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "/review")

        assert response["result"] == {"stopReason": "end_turn"}

    async def test_a_failed_model_request_fails_the_turn_with_its_first_line(self, bridge, work):
        events = [
            AGENT_START,
            MESSAGE_START,
            _message_end([], stop="error", errorMessage="\n429 rate limit reached\nTry again later"),
        ]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["error"] == {"code": -32603, "message": "429 rate limit reached"}

    async def test_a_retry_that_worked_ends_the_turn_well(self, bridge, work):
        events = [
            AGENT_START,
            MESSAGE_START,
            _message_end([], stop="error", errorMessage="529 overloaded"),
            {
                "type": "auto_retry_start",
                "attempt": 1,
                "maxAttempts": 3,
                "errorMessage": "529 overloaded\ndetails",
            },
            MESSAGE_START,
            _delta("text_delta", "Hi"),
            _message_end([{"type": "text", "text": "Hi"}]),
        ]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["result"] == {"stopReason": "end_turn"}
        (notice,) = client.of_kind("notice")
        assert notice["severity"] == "warning"
        assert notice["description"] == "529 overloaded"

    async def test_a_model_without_credentials_needs_authentication(self, bridge, work):
        events = [
            AGENT_START,
            MESSAGE_START,
            _message_end([], stop="error", errorMessage="No API key found for anthropic."),
        ]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["error"] == {"code": -32000, "message": "Authentication required"}

    async def test_a_login_pi_could_not_renew_needs_authentication(self, bridge, work):
        """Pi refuses the message itself. With this code the chat offers the
        sign-in and sends the message again after it."""
        refusal = 'Authentication failed for "openai-codex". Sign in again with /login.'
        client = await bridge(turns={"hello": {"refuse": refusal}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["error"] == {"code": -32000, "message": "Authentication required"}

    async def test_a_subscription_login_pi_could_not_renew_needs_authentication(self, bridge, work):
        """Pi accepts the message; the model request then fails with the
        text of its token renewal."""
        failed = _message_end(
            [],
            stop="error",
            errorMessage='OAuth refresh failed for openai-codex: OpenAI Codex token refresh failed (401): {',
        )
        client = await bridge(turns={"hello": {"events": [failed]}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["error"] == {"code": -32000, "message": "Authentication required"}

    async def test_a_call_the_model_named_but_pi_never_ran_fails_with_the_turn(self, bridge, work):
        """The model request broke while it wrote the call: no result will
        follow, and the call must not stay pending in the chat."""
        failed = _message_end([], stop="error", errorMessage="529 overloaded")
        client = await bridge(turns={"list": {"events": [*NAMED_CALL, failed]}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "list the files")

        assert response["error"] == {"code": -32603, "message": "529 overloaded"}
        (call,) = client.of_kind("tool_call")
        assert call["status"] == "pending"
        assert client.of_kind("tool_call_update") == [
            {"sessionUpdate": "tool_call_update", "toolCallId": call["toolCallId"], "status": "failed"}
        ]

    async def test_a_message_pi_does_not_accept_fails_the_turn(self, bridge, work):
        client = await bridge(
            turns={"hello": {"refuse": "Pi is busy"}, "again": {"events": _says("Hi")}}
        )
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "hello")

        assert response["error"] == {"code": -32603, "message": "Pi is busy"}
        # The refused turn is over: the next one runs.
        assert (await client.turn(session_id, "again"))["result"] == {"stopReason": "end_turn"}

    @pytest.mark.parametrize(
        "prompt",
        [[], [{"type": "text", "text": "  \n "}], [{"type": "resource_link", "uri": "file:///a"}]],
        ids=["no-blocks", "blank", "nothing-pi-takes"],
    )
    async def test_an_empty_message_is_refused(self, bridge, work, prompt):
        client = await bridge()
        session_id = await client.new_session(work)

        error = await client.error("session/prompt", {"sessionId": session_id, "prompt": prompt})

        assert error == {"code": -32602, "message": "The message is empty"}
        assert client.pi_commands("prompt") == []

    async def test_a_message_for_another_session_is_refused(self, bridge, work):
        client = await bridge()
        await client.new_session(work)

        error = await client.error(
            "session/prompt",
            {"sessionId": "someone-else", "prompt": [{"type": "text", "text": "hello"}]},
        )

        assert error == {"code": -32602, "message": "Unknown session"}

    async def test_a_second_message_while_a_turn_runs_is_refused(self, bridge, work):
        client = await bridge(turns={"wait": {"events": _says("Working"), "hold": True}})
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("agent_message_chunk")

        error = await client.error(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "wait more"}]},
        )

        assert error == {"code": -32602, "message": "A turn is already running"}
        client.send({"method": "session/cancel", "params": {"sessionId": session_id}})
        assert (await client.response(running))["result"] == {"stopReason": "cancelled"}

    async def test_a_pi_that_exits_in_a_turn_fails_it_and_ends_the_bridge(self, bridge, work):
        client = await bridge(turns={"crash": {"events": [AGENT_START], "exit": 3}})
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "crash")

        assert response["error"]["code"] == -32603
        assert response["error"]["message"].startswith("Pi exited with code 3")
        # The chat sees the exit of its agent process and offers a restart.
        assert await asyncio.wait_for(client.proc.wait(), READ_TIMEOUT) == 3


class TestCancel:
    async def test_cancel_stops_the_turn_and_drops_what_was_queued(self, bridge, work):
        client = await bridge(turns={"wait": {"events": _says("Working"), "hold": True}})
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("agent_message_chunk")

        client.send({"method": "session/cancel", "params": {"sessionId": session_id}})

        assert (await client.response(running))["result"] == {"stopReason": "cancelled"}
        # Queued messages first: Pi would go on with them after the abort.
        assert client.pi_commands("clear_queue", "abort") == [
            {"type": "clear_queue"},
            {"type": "abort"},
        ]

    async def test_a_call_cut_off_by_a_stop_is_never_reported_as_failed(self, bridge, work):
        """The chat marks the calls of a stopped turn itself: not with the
        stop, and not with the end of the turn after it."""
        client = await bridge(
            turns={
                "wait": {"events": NAMED_CALL, "hold": True},
                "hello": {"events": _says("Hi")},
            }
        )
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("tool_call")

        client.send({"method": "session/cancel", "params": {"sessionId": session_id}})

        assert (await client.response(running))["result"] == {"stopReason": "cancelled"}
        assert (await client.turn(session_id, "hello"))["result"] == {"stopReason": "end_turn"}
        assert client.of_kind("tool_call_update") == []

    async def test_a_turn_runs_after_a_cancelled_one(self, bridge, work):
        client = await bridge(
            turns={
                "wait": {"events": _says("Working"), "hold": True},
                "hello": {"events": _says("Hi")},
            }
        )
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("agent_message_chunk")
        client.send({"method": "session/cancel", "params": {"sessionId": session_id}})
        await client.response(running)

        response = await client.turn(session_id, "hello")

        assert response["result"] == {"stopReason": "end_turn"}


def _steer(session_id: str, text: str) -> dict[str, Any]:
    return {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]}


class TestSteering:
    async def test_a_message_in_a_running_turn_is_injected(self, bridge, work):
        client = await bridge(turns={"wait": {"events": _says("Working"), "hold": True}})
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("agent_message_chunk")

        result = await client.result("_session/steering", _steer(session_id, "use the other file"))

        assert result == {"outcome": "injected"}
        assert (await client.response(running))["result"] == {"stopReason": "end_turn"}
        assert client.pi_commands("steer") == [{"type": "steer", "message": "use the other file"}]

    async def test_with_no_turn_running_the_chat_must_send_a_prompt(self, bridge, work):
        client = await bridge(turns={"hello": {"events": _says("Hi")}})
        session_id = await client.new_session(work)

        result = await client.result("_session/steering", _steer(session_id, "use the other file"))
        await client.turn(session_id, "hello")

        assert result == {"outcome": "promptRequired"}
        assert client.pi_commands("steer") == []

    async def test_an_empty_message_is_not_sent_to_pi(self, bridge, work):
        client = await bridge(turns={"wait": {"events": _says("Working"), "hold": True}})
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("agent_message_chunk")

        result = await client.result("_session/steering", _steer(session_id, "   "))

        assert result == {"outcome": "promptRequired"}
        client.send({"method": "session/cancel", "params": {"sessionId": session_id}})
        await client.response(running)
        assert client.pi_commands("steer") == []

    async def test_a_turn_that_ended_first_gives_the_message_back(self, bridge, work):
        """Pi took the message into its queue after the run was over: left
        there, it would run before the prompt the chat sends instead."""
        client = await bridge(
            turns={
                "wait": {"events": _says("Working"), "hold": True, "steer": "late"},
                "hello": {"events": _says("Hi")},
            }
        )
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "wait")
        await client.update("agent_message_chunk")

        result = await client.result("_session/steering", _steer(session_id, "use the other file"))

        assert result == {"outcome": "promptRequired"}
        assert (await client.response(running))["result"] == {"stopReason": "end_turn"}
        await client.turn(session_id, "hello")
        assert client.pi_commands("steer", "clear_queue", "prompt")[1:] == [
            {"type": "steer", "message": "use the other file"},
            {"type": "clear_queue"},
            {"type": "prompt", "message": "hello"},
        ]

    async def test_a_message_for_another_session_is_refused(self, bridge, work):
        client = await bridge()
        await client.new_session(work)

        error = await client.error("_session/steering", _steer("someone-else", "hello"))

        assert error == {"code": -32602, "message": "Unknown session"}


def _set(session_id: str, option: str, value: Any) -> dict[str, Any]:
    return {"sessionId": session_id, "configId": option, "value": value}


class TestConfig:
    async def test_a_model_of_another_provider(self, bridge, work):
        client = await bridge()
        session_id = await client.new_session(work)

        result = await client.result(
            "session/set_config_option", _set(session_id, "model", "openai/gpt")
        )

        model, thinking = result["configOptions"]
        assert model["currentValue"] == "openai/gpt"
        # The provider of the current model leads the list.
        assert [group["name"] for group in model["options"]] == ["openai", "anthropic"]
        assert thinking["currentValue"] == "medium"
        assert client.pi_commands("set_model") == [
            {"type": "set_model", "provider": "openai", "modelId": "gpt"}
        ]

    async def test_a_model_id_with_a_slash_keeps_it(self, bridge, work):
        client = await bridge(models=[SONNET, LLAMA])
        session_id = await client.new_session(work)

        result = await client.result(
            "session/set_config_option", _set(session_id, "model", "openrouter/meta/llama")
        )

        assert result["configOptions"][0]["currentValue"] == "openrouter/meta/llama"
        assert client.pi_commands("set_model") == [
            {"type": "set_model", "provider": "openrouter", "modelId": "meta/llama"}
        ]

    async def test_a_model_with_one_thinking_level_has_nothing_to_choose(self, bridge, work):
        """A model has its own levels, and Pi moves the level into range."""
        client = await bridge()
        session_id = await client.new_session(work)

        result = await client.result(
            "session/set_config_option", _set(session_id, "model", "anthropic/haiku")
        )

        assert [option["id"] for option in result["configOptions"]] == ["model"]
        assert result["configOptions"][0]["currentValue"] == "anthropic/haiku"

    async def test_a_thinking_level(self, bridge, work):
        client = await bridge()
        session_id = await client.new_session(work)

        result = await client.result(
            "session/set_config_option", _set(session_id, "thought_level", "high")
        )

        assert result["configOptions"] == [MODEL_OPTION, {**THINKING_OPTION, "currentValue": "high"}]
        assert client.pi_commands("set_thinking_level") == [
            {"type": "set_thinking_level", "level": "high"}
        ]

    async def test_a_change_pi_refuses_keeps_the_options(self, bridge, work):
        client = await bridge()
        session_id = await client.new_session(work)

        error = await client.error(
            "session/set_config_option", _set(session_id, "model", "anthropic/opus")
        )
        result = await client.result(
            "session/set_config_option", _set(session_id, "thought_level", "low")
        )

        assert error == {"code": -32603, "message": "Model not found: opus"}
        assert result["configOptions"][0]["currentValue"] == "anthropic/sonnet"

    @pytest.mark.parametrize(
        ("option", "value", "message"),
        [("mode", "plan", "Unknown setting"), ("model", 5, "Invalid value"), ("model", None, "Invalid value")],
        ids=["unknown-option", "number", "no-value"],
    )
    async def test_a_change_that_makes_no_sense_never_reaches_pi(
        self, bridge, work, option, value, message
    ):
        client = await bridge(turns={"hello": {"events": _says("Hi")}})
        session_id = await client.new_session(work)

        error = await client.error("session/set_config_option", _set(session_id, option, value))
        await client.turn(session_id, "hello")

        assert error == {"code": -32602, "message": message}
        assert client.pi_commands("set_model", "set_thinking_level") == []

    async def test_a_level_changed_in_pi_updates_the_option(self, bridge, work):
        events = [*_says("Hi"), {"type": "thinking_level_changed", "level": "low"}]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "hello")

        assert client.of_kind("config_option_update") == [
            {
                "sessionUpdate": "config_option_update",
                "configOptions": [MODEL_OPTION, {**THINKING_OPTION, "currentValue": "low"}],
            }
        ]


class TestCompact:
    async def test_the_compact_command_runs_pi_compaction(self, bridge, work):
        client = await bridge(
            compact={
                "events": [
                    {"type": "compaction_start"},
                    {"type": "compaction_end", "result": {"summary": "We fixed the login."}},
                ]
            }
        )
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "/compact keep the test names")

        assert response["result"] == {"stopReason": "end_turn"}
        started, done = client.of_kind("compaction_update")
        assert started == {
            "sessionUpdate": "compaction_update",
            "compactionId": started["compactionId"],
            "status": "in_progress",
        }
        assert done == {
            "sessionUpdate": "compaction_update",
            "compactionId": started["compactionId"],
            "status": "completed",
            "summary": [{"type": "text", "text": "We fixed the login."}],
        }
        assert client.pi_commands("compact", "prompt") == [
            {"type": "compact", "customInstructions": "keep the test names"}
        ]
        # A command is not the topic of the session.
        assert client.of_kind("session_info_update") == []

    async def test_without_instructions_pi_gets_none(self, bridge, work):
        client = await bridge()
        session_id = await client.new_session(work)

        await client.turn(session_id, "  /compact  ")

        assert client.pi_commands("compact") == [{"type": "compact"}]

    async def test_a_compaction_pi_refuses_fails_the_turn(self, bridge, work):
        client = await bridge(
            compact={"events": [{"type": "compaction_start"}], "error": "Nothing to compact\nmore"}
        )
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "/compact")

        assert response["error"] == {"code": -32603, "message": "Nothing to compact"}
        failed = client.of_kind("compaction_update")[-1]
        assert failed["status"] == "failed"
        assert failed["summary"] == [{"type": "text", "text": "Nothing to compact"}]

    async def test_a_compaction_pi_starts_itself_shows_in_the_turn(self, bridge, work):
        events = [
            *_says("Working"),
            {"type": "compaction_start"},
            {"type": "compaction_end", "aborted": True},
            {"type": "compaction_start"},
            {"type": "compaction_end", "errorMessage": "The summary request failed\ndetails"},
        ]
        client = await bridge(turns={"hello": {"events": events}})
        session_id = await client.new_session(work)

        await client.turn(session_id, "hello")

        updates = client.of_kind("compaction_update")
        assert [update["status"] for update in updates] == [
            "in_progress",
            "cancelled",
            "in_progress",
            "failed",
        ]
        assert updates[0]["compactionId"] == updates[1]["compactionId"]
        assert updates[2]["compactionId"] != updates[0]["compactionId"]
        assert "summary" not in updates[1]
        assert updates[3]["summary"] == [{"type": "text", "text": "The summary request failed"}]


STORED = [
    {"role": "user", "content": "Fix the bug\nplease"},
    {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Look first."},
            {"type": "text", "text": "On it."},
            {"type": "toolCall", "id": "t1", "name": "bash", "arguments": {"command": "make test"}},
            {
                "type": "toolCall",
                "id": "t2",
                "name": "write",
                "arguments": {"path": "notes.md", "content": "hi"},
            },
            {"type": "toolCall", "id": "t3", "name": "read", "arguments": {"path": "a.py", "offset": 10}},
        ],
        "usage": {"totalTokens": 900},
    },
    {
        "role": "toolResult",
        "toolCallId": "t1",
        "isError": True,
        "content": [{"type": "text", "text": "1 failed\n\nCommand exited with code 2"}],
    },
    {
        "role": "toolResult",
        "toolCallId": "t2",
        "isError": False,
        "content": [{"type": "text", "text": "Wrote notes.md"}],
    },
    {"role": "bashExecution", "command": "git status", "output": "clean\n", "exitCode": 0},
    {"role": "compactionSummary", "summary": "Earlier: the bug was found."},
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "And this?"},
            {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"},
        ],
    },
]


class TestLoad:
    async def test_a_stored_session_starts_from_its_file_and_replays(self, bridge, work, pi_home):
        path = _write_session(pi_home / "sessions", str(work), "stored-1", _user("Fix the bug"))
        client = await bridge(session_id="stored-1", messages=STORED)

        result = await client.result("session/load", {"sessionId": "stored-1", "cwd": str(work)})
        await client.update("available_commands_update")

        # The id is the one the chat asked for: the result does not repeat it.
        assert result == {"configOptions": [MODEL_OPTION, THINKING_OPTION]}
        launch = client.pi_log()[0]
        assert launch["argv"] == ["--mode", "rpc", "--session", str(path)]
        assert client.pi_commands("get_messages") == [{"type": "get_messages"}]
        updates = client.updates[:-1]
        shell_id, write_id, read_id = (u["toolCallId"] for u in updates[3:6])
        ran_id = updates[9]["toolCallId"]
        compaction_id = updates[10]["compactionId"]
        assert len({shell_id, write_id, read_id, ran_id}) == 4
        assert updates == [
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "Fix the bug\nplease"},
                "messageId": "u1",
            },
            _text("agent_thought_chunk", "Look first.", "m2.0"),
            _text("agent_message_chunk", "On it.", "m2.1"),
            {
                "sessionUpdate": "tool_call",
                "toolCallId": shell_id,
                "title": "make test",
                "kind": "execute",
                "status": "pending",
                "rawInput": {"command": "make test"},
                "locations": [],
            },
            {
                "sessionUpdate": "tool_call",
                "toolCallId": write_id,
                "title": "Write notes.md",
                "kind": "edit",
                "status": "pending",
                "rawInput": {"path": "notes.md", "content": "hi"},
                "locations": [{"path": os.path.join(str(work), "notes.md")}],
            },
            {
                "sessionUpdate": "tool_call",
                "toolCallId": read_id,
                "title": "Read a.py",
                "kind": "read",
                "status": "pending",
                "rawInput": {"path": "a.py", "offset": 10},
                "locations": [{"path": os.path.join(str(work), "a.py"), "line": 10}],
            },
            {"sessionUpdate": "usage_update", "used": 900, "size": 200000},
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": shell_id,
                "status": "failed",
                "_meta": {
                    "terminal_output": {"data": "1 failed"},
                    "terminal_exit": {"exit_code": 2},
                },
            },
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": write_id,
                "status": "completed",
                "content": [
                    {
                        "type": "diff",
                        "path": os.path.join(str(work), "notes.md"),
                        "oldText": None,
                        "newText": "hi",
                    }
                ],
            },
            # A command the user ran with ``!`` in Pi's terminal UI.
            {
                "sessionUpdate": "tool_call",
                "toolCallId": ran_id,
                "title": "git status",
                "kind": "execute",
                "status": "completed",
                "rawInput": {"command": "git status"},
                "_meta": {
                    "terminal_output": {"data": "clean\n"},
                    "terminal_exit": {"exit_code": 0},
                },
            },
            {
                "sessionUpdate": "compaction_update",
                "compactionId": compaction_id,
                "status": "completed",
                "summary": [{"type": "text", "text": "Earlier: the bug was found."}],
            },
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "And this?"},
                "messageId": "u7",
            },
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "image", "mimeType": "image/jpeg"},
                "messageId": "u7",
            },
            # A call with no stored result was cut off.
            {"sessionUpdate": "tool_call_update", "toolCallId": read_id, "status": "failed"},
            {"sessionUpdate": "session_info_update", "title": "Fix the bug"},
        ]

    async def test_a_stored_session_keeps_its_title_after_a_new_message(self, bridge, work, pi_home):
        _write_session(pi_home / "sessions", str(work), "stored-1")
        client = await bridge(
            session_id="stored-1",
            session_name="Login work",
            messages=[{"role": "user", "content": "Fix the bug"}],
            turns={"hello": {"events": _says("Hi")}},
        )

        await client.result("session/load", {"sessionId": "stored-1", "cwd": str(work)})
        await client.turn("stored-1", "hello again")

        assert client.of_kind("session_info_update") == [
            {"sessionUpdate": "session_info_update", "title": "Login work"}
        ]

    async def test_a_stored_session_with_no_message_takes_its_title_from_the_first_one(
        self, bridge, work, pi_home
    ):
        _write_session(pi_home / "sessions", str(work), "stored-1")
        client = await bridge(session_id="stored-1", turns={"hello": {"events": _says("Hi")}})

        await client.result("session/load", {"sessionId": "stored-1", "cwd": str(work)})
        response = await client.turn("stored-1", "hello")

        assert response["result"] == {"stopReason": "end_turn"}
        assert client.of_kind("session_info_update") == [
            {"sessionUpdate": "session_info_update", "title": "hello"}
        ]

    async def test_a_session_of_another_folder_loads_by_its_file(self, bridge, work, pi_home, tmp_path):
        """By id, Pi would ask on its terminal whether to fork the session."""
        path = _write_session(pi_home / "sessions", str(tmp_path / "elsewhere"), "stored-1")
        client = await bridge(session_id="stored-1")

        await client.result("session/load", {"sessionId": "stored-1", "cwd": str(work)})

        launch = client.pi_log()[0]
        assert launch["argv"] == ["--mode", "rpc", "--session", str(path)]
        assert os.path.realpath(launch["cwd"]) == os.path.realpath(work)

    async def test_the_session_dir_of_pi_settings_is_where_it_looks(self, bridge, work, pi_home, tmp_path):
        custom = tmp_path / "custom-sessions"
        custom.mkdir()
        (pi_home / "settings.json").write_text(json.dumps({"sessionDir": str(custom)}))
        path = custom / "2026_stored-1.jsonl"
        path.write_text(_session_lines("stored-1", str(work)))
        client = await bridge(session_id="stored-1")

        await client.result("session/load", {"sessionId": "stored-1", "cwd": str(work)})

        assert client.pi_log()[0]["argv"] == ["--mode", "rpc", "--session", str(path)]

    async def test_an_id_pi_has_no_file_for_starts_again_under_that_id(self, bridge, work, pi_home):
        """Pi stores a session with its first message: a restart before it
        finds no file."""
        _write_session(pi_home / "sessions", str(work), "stored-1")
        client = await bridge(session_id="stored-2")

        result = await client.result("session/load", {"sessionId": "stored-2", "cwd": str(work)})

        assert "sessionId" not in result
        assert client.pi_log()[0]["argv"] == ["--mode", "rpc", "--session-id", "stored-2"]

    @pytest.mark.parametrize(
        "session_id",
        ["-x", "a b", "a/b", "x" * 201, "abc:def", "abc.", "_abc"],
        ids=["flag", "space", "path", "long", "colon", "ends-with-dot", "starts-with-underscore"],
    )
    async def test_an_id_that_is_no_session_id_is_never_passed_to_pi(self, bridge, work, session_id):
        client = await bridge()

        error = await client.error("session/load", {"sessionId": session_id, "cwd": str(work)})

        assert error == {"code": -32002, "message": "Pi has no stored session with this id"}
        assert client.pi_log() == []

    @pytest.mark.parametrize("session_id", [None, "", 5], ids=["missing", "empty", "number"])
    async def test_a_load_without_an_id_is_refused(self, bridge, work, session_id):
        client = await bridge()

        error = await client.error("session/load", {"sessionId": session_id, "cwd": str(work)})

        assert error == {"code": -32602, "message": "sessionId is required"}


class TestList:
    async def test_the_sessions_of_a_folder_newest_first(self, bridge, work, pi_home, tmp_path):
        root = pi_home / "sessions"
        _write_session(root, str(work), "old", _user("Old work"), mtime=1767323045)
        _write_session(root, str(work), "new", _user("New work"), _named("Named"), mtime=1767323105)
        _write_session(root, str(tmp_path / "elsewhere"), "other", _user("Other"), mtime=1767323200)
        client = await bridge()

        result = await client.result("session/list", {"cwd": str(work)})

        assert result == {
            "sessions": [
                {
                    "sessionId": "new",
                    "cwd": str(work),
                    "title": "Named",
                    "updatedAt": "2026-01-02T03:05:05Z",
                },
                {
                    "sessionId": "old",
                    "cwd": str(work),
                    "title": "Old work",
                    "updatedAt": "2026-01-02T03:04:05Z",
                },
            ]
        }
        # Listing reads Pi's files: no Pi process starts.
        assert client.pi_log() == []

    async def test_without_a_folder_every_session_lists(self, bridge, work, pi_home, tmp_path):
        root = pi_home / "sessions"
        _write_session(root, str(work), "mine", mtime=1000)
        _write_session(root, str(tmp_path / "elsewhere"), "other", mtime=2000)
        client = await bridge()

        result = await client.result("session/list", {})

        assert [row["sessionId"] for row in result["sessions"]] == ["other", "mine"]

    async def test_a_long_list_comes_in_pages(self, bridge, work, pi_home):
        root = pi_home / "sessions"
        for index in range(pi_bridge.PAGE_SIZE + 2):
            _write_session(root, str(work), f"s{index:02d}", mtime=1000 + index)
        client = await bridge()

        first = await client.result("session/list", {"cwd": str(work)})
        second = await client.result(
            "session/list", {"cwd": str(work), "cursor": first["nextCursor"]}
        )

        assert [row["sessionId"] for row in first["sessions"]] == [
            f"s{index:02d}" for index in range(51, 1, -1)
        ]
        assert first["nextCursor"] == "50"
        assert second == {
            "sessions": [
                {"sessionId": "s01", "cwd": str(work), "title": None, "updatedAt": "1970-01-01T00:16:41Z"},
                {"sessionId": "s00", "cwd": str(work), "title": None, "updatedAt": "1970-01-01T00:16:40Z"},
            ]
        }

    async def test_a_list_that_fills_one_page_exactly_has_no_next_page(self, bridge, work, pi_home):
        root = pi_home / "sessions"
        for index in range(pi_bridge.PAGE_SIZE):
            _write_session(root, str(work), f"s{index:02d}", mtime=1000 + index)
        client = await bridge()

        result = await client.result("session/list", {"cwd": str(work)})

        assert len(result["sessions"]) == pi_bridge.PAGE_SIZE
        assert "nextCursor" not in result

    async def test_a_cursor_that_is_no_number_starts_at_the_top(self, bridge, work, pi_home):
        _write_session(pi_home / "sessions", str(work), "only")
        client = await bridge()

        result = await client.result("session/list", {"cwd": str(work), "cursor": "abc"})

        assert [row["sessionId"] for row in result["sessions"]] == ["only"]


def _dialog(method: str, **fields: Any) -> dict[str, Any]:
    return {"type": "extension_ui_request", "id": "d1", "method": method, **fields}


def _asks(dialog: dict[str, Any], hold: bool = True) -> dict[str, Any]:
    return {"turns": {"ask": {"events": [AGENT_START, dialog], "hold": hold}}}


def _selected(option_id: str) -> dict[str, Any]:
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


class TestDialogs:
    async def test_a_confirmation_is_an_approval_card_with_yes_first(self, bridge, work):
        dialog = _dialog("confirm", title="Delete the branch?", message="It has 3 commits.\nNo undo.")
        client = await bridge(**_asks(dialog))
        session_id = await client.new_session(work)
        client.prompt(session_id, "ask")

        request = await client.request()

        assert request["method"] == "session/request_permission"
        assert request["params"] == {
            "sessionId": session_id,
            "toolCall": {
                "title": "Delete the branch?",
                "kind": "other",
                "content": [
                    {
                        "type": "content",
                        "content": {"type": "text", "text": "It has 3 commits.\nNo undo."},
                    }
                ],
            },
            "options": [
                {"optionId": "o0", "name": "Yes", "kind": "allow_once"},
                {"optionId": "o1", "name": "No", "kind": "allow_always"},
            ],
        }

    @pytest.mark.parametrize(
        ("answer", "sent"),
        [
            ({"result": _selected("o0")}, {"confirmed": True}),
            ({"result": _selected("o1")}, {"confirmed": False}),
            ({"result": _selected("o7")}, {"cancelled": True}),
            ({"result": {"outcome": {"outcome": "cancelled"}}}, {"cancelled": True}),
            ({"error": {"code": -32603, "message": "The chat closed"}}, {"cancelled": True}),
        ],
        ids=["yes", "no", "unknown-option", "dismissed", "error"],
    )
    async def test_the_answer_to_a_confirmation_goes_back_to_pi(self, bridge, work, answer, sent):
        client = await bridge(**_asks(_dialog("confirm", title="Delete the branch?")))
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "ask")
        request = await client.request()

        client.send({"id": request["id"], **answer})

        assert (await client.response(running))["result"] == {"stopReason": "end_turn"}
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", **sent}
        ]
        assert client.pi_log()[-1]["id"] == "d1"

    async def test_a_short_choice_is_a_button_each(self, bridge, work):
        dialog = _dialog("select", title="Which branch?", options=["main", "dev", "", 5])
        client = await bridge(**_asks(dialog))
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "ask")

        request = await client.request()
        client.send({"id": request["id"], "result": _selected("o1")})
        await client.response(running)

        assert request["method"] == "session/request_permission"
        assert request["params"]["toolCall"] == {"title": "Which branch?", "kind": "other"}
        assert [(o["optionId"], o["name"]) for o in request["params"]["options"]] == [
            ("o0", "main"),
            ("o1", "dev"),
        ]
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", "value": "dev"}
        ]

    @pytest.mark.parametrize(
        "options",
        [["a", "b", "c", "d", "e"], ["short", "x" * 41]],
        ids=["many-options", "a-long-label"],
    )
    async def test_a_choice_too_big_for_buttons_is_a_form(self, bridge, work, options):
        client = await bridge(**_asks(_dialog("select", title="Which file?", options=options)))
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "ask")

        request = await client.request()
        client.send(
            {"id": request["id"], "result": {"action": "accept", "content": {"value": options[1]}}}
        )
        await client.response(running)

        assert request["method"] == "elicitation/create"
        assert request["params"] == {
            "sessionId": session_id,
            "mode": "form",
            "message": "Which file?",
            "requestedSchema": {
                "type": "object",
                "properties": {"value": {"type": "string", "title": "Choice", "enum": options}},
                "required": ["value"],
            },
        }
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", "value": options[1]}
        ]

    async def test_a_choice_with_no_option_is_cancelled_without_asking(self, bridge, work):
        client = await bridge(**_asks(_dialog("select", title="Which branch?", options=[])))
        session_id = await client.new_session(work)

        response = await client.turn(session_id, "ask")

        assert response["result"] == {"stopReason": "end_turn"}
        assert client.requests == []
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", "cancelled": True}
        ]

    @pytest.mark.parametrize("method", ["input", "editor"])
    async def test_a_text_question_is_a_form_with_its_hint_and_start_text(self, bridge, work, method):
        dialog = _dialog(method, title="Branch name", placeholder="feature/...", prefill="main")
        client = await bridge(**_asks(dialog))
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "ask")

        request = await client.request()
        client.send(
            {"id": request["id"], "result": {"action": "accept", "content": {"value": "feature/x"}}}
        )
        await client.response(running)

        assert request["method"] == "elicitation/create"
        assert request["params"]["message"] == "Branch name"
        assert request["params"]["requestedSchema"]["properties"] == {
            "value": {
                "type": "string",
                "title": "Answer",
                "description": "feature/...",
                "default": "main",
            }
        }
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", "value": "feature/x"}
        ]

    @pytest.mark.parametrize(
        "result",
        [{"action": "decline"}, {"action": "cancel"}, {"action": "accept", "content": {"value": 5}}],
        ids=["declined", "cancelled", "no-text"],
    )
    async def test_a_form_that_gives_no_text_cancels_the_dialog(self, bridge, work, result):
        client = await bridge(**_asks(_dialog("input")))
        session_id = await client.new_session(work)
        running = client.prompt(session_id, "ask")

        request = await client.request()
        client.send({"id": request["id"], "result": result})
        await client.response(running)

        # A dialog without a title still needs a text.
        assert request["params"]["message"] == "Pi has a question"
        assert request["params"]["requestedSchema"]["properties"] == {
            "value": {"type": "string", "title": "Answer"}
        }
        assert client.pi_commands("extension_ui_response") == [
            {"type": "extension_ui_response", "cancelled": True}
        ]

    @pytest.mark.parametrize(
        ("notify_type", "severity"),
        [("warning", "warning"), ("success", "info")],
        ids=["known", "unknown"],
    )
    async def test_a_notification_is_a_notice(self, bridge, work, notify_type, severity):
        dialog = _dialog("notify", message="Saved the plan", notifyType=notify_type)
        client = await bridge(**_asks(dialog, hold=False))
        session_id = await client.new_session(work)

        await client.turn(session_id, "ask")

        assert client.of_kind("notice") == [
            {
                "sessionUpdate": "notice",
                "severity": severity,
                "title": "Saved the plan",
                "description": "",
            }
        ]
        assert client.requests == []
