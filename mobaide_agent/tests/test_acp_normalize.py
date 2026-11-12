"""Pure-function tests for the ACP → chat wire conversions: text and diff
caps, content parts, tool merges, config options, plans, usage, permission
options, elicitation forms, terminal output, Antigravity's command results
and Codex thread status."""
from __future__ import annotations

import pytest

from mobaide.acp import normalize as nz


class TestCapText:
    def test_long_text_keeps_head_and_tail(self):
        text = "H" * 600 + "M" * 300 + "T" * 100
        capped, truncated = nz.cap_text(text, 400)

        assert truncated is True
        assert capped.startswith("H" * 300)
        assert capped.endswith("T" * 100)
        assert "600 characters not shown" in capped


class TestSubtitleFromInput:


    def test_collapses_whitespace_and_caps_length(self):
        long_cmd = "echo   one\n two " + "x" * 400
        subtitle = nz.subtitle_from_input({"command": long_cmd})

        assert subtitle.startswith("echo one two ")
        assert len(subtitle) == nz.SUBTITLE_MAX
        assert subtitle.endswith("…")

    @pytest.mark.parametrize("raw", [None, {"other": "x"}, {"command": "  "}])
    def test_nothing_to_show(self, raw):
        assert nz.subtitle_from_input(raw) is None

    @pytest.mark.parametrize(
        ("raw", "subtitle"),
        [
            ({"cmd": "bash -c 'ls -la'"}, "ls -la"),
            ({"file_path": "/bin/zsh -lc x"}, "/bin/zsh -lc x"),
        ],
        ids=["cmd", "not-a-command-key"],
    )
    def test_the_shell_wrapper_around_a_command_is_dropped(self, raw, subtitle):
        assert nz.subtitle_from_input(raw) == subtitle

    def test_antigravity_tool_summary_when_nothing_more_specific(self):
        # agy names the command in the title; its arguments use its own keys.
        raw = {"CommandLine": "pytest -q", "WaitMsBeforeAsync": 5000, "toolSummary": "Run the tests"}

        assert nz.subtitle_from_input(raw) == "Run the tests"
        assert nz.subtitle_from_input({**raw, "path": "/p/a.py"}) == "/p/a.py"


class TestUnwrapShell:
    @pytest.mark.parametrize(
        ("command", "unwrapped"),
        [
            ('/bin/zsh -lc "git status"', "git status"),
            ("/usr/bin/sh -ic 'make'", "make"),
            ("python -c 'print(1)'", "python -c 'print(1)'"),
            ("bash -c 'make' extra", "bash -c 'make' extra"),
            ('bash -lc "unterminated', 'bash -lc "unterminated'),
            ('./bash -lc "x"', './bash -lc "x"'),
        ],
        ids=[
            "zsh-login",
            "interactive-sh",
            "not-a-shell",
            "extra-argument",
            "unbalanced-quote",
            "project-relative-shell",
        ],
    )
    def test_unwrap(self, command, unwrapped):
        assert nz.unwrap_shell(command) == unwrapped


class TestUnifiedDiff:
    def test_new_file_counts_every_line_as_added(self):
        part = nz.unified_diff("/p/new.py", None, "a\nb\nc\n")

        assert part["new_file"] is True
        assert part["added"] == 3
        assert part["removed"] == 0
        assert not part["truncated"]

    def test_change_has_hunks_without_file_headers(self):
        part = nz.unified_diff("/p/app.py", "a\nb\nc\n", "a\nB\nc\nd\n")

        assert part["new_file"] is False
        assert (part["added"], part["removed"]) == (2, 1)
        lines = part["diff"].splitlines()
        assert lines[0].startswith("@@")
        assert "-b" in lines and "+B" in lines and "+d" in lines
        assert not any(line.startswith(("---", "+++")) for line in lines)

    def test_removed_sql_comment_is_kept_and_counted(self):
        """A removed ``-- note`` line reads ``--- note`` in the diff, the same
        three marks as the old-file header."""
        part = nz.unified_diff(
            "/p/q.sql", "select 1;\n-- old note\nselect 2;\n", "select 1;\nselect 2;\n"
        )

        assert (part["added"], part["removed"]) == (0, 1)
        lines = part["diff"].splitlines()
        assert lines[0].startswith("@@")
        assert "--- old note" in lines

    def test_long_diff_is_cut_but_counts_stay_exact(self):
        new_text = "".join(f"line {i}\n" for i in range(nz.DIFF_LINES_MAX + 50))
        part = nz.unified_diff("/p/big.txt", "", new_text)

        assert part["truncated"] is True
        assert len(part["diff"].splitlines()) == nz.DIFF_LINES_MAX
        assert part["added"] == nz.DIFF_LINES_MAX + 50

    def test_oversized_input_skips_the_diff(self, monkeypatch):
        monkeypatch.setattr(nz, "DIFF_INPUT_MAX", 20)
        part = nz.unified_diff("/p/huge.txt", "old", "one\ntwo\nthree\nfour")

        assert part["truncated"] is True
        assert part["diff"] == ""
        assert part["added"] == 4
        assert part["removed"] == 1

    def test_too_many_line_pairs_skip_the_diff_but_count_lines(self, monkeypatch):
        """difflib is quadratic in the worst case: above the pair budget the
        counts come from line multisets."""
        monkeypatch.setattr(nz, "DIFF_PAIRS_MAX", 11)

        part = nz.unified_diff("/p/wide.txt", "a\nb\nc\nc\n", "c\nd\nd\n")

        assert part["truncated"] is True
        assert part["diff"] == ""
        assert (part["added"], part["removed"]) == (2, 3)


class TestContentBlockPart:
    def test_long_text_is_marked_truncated(self):
        part = nz.content_block_part({"type": "text", "text": "x" * (nz.TEXT_PART_MAX + 1)})

        assert part["type"] == "text"
        assert part["truncated"] is True


    def test_large_image_drops_its_data(self):
        part = nz.content_block_part(
            {"type": "image", "data": "A" * (nz.INLINE_IMAGE_MAX + 1)}
        )
        assert part == {"type": "image", "mime": "image/png"}


    @pytest.mark.parametrize("block", [{"type": "audio"}, {"type": "resource"}])
    def test_unknown_shapes_are_dropped(self, block):
        assert nz.content_block_part(block) is None


class TestToolContentParts:
    def test_maps_each_entry_kind(self):
        parts = nz.tool_content_parts(
            [
                {"type": "content", "content": {"type": "text", "text": "out"}},
                {"type": "diff", "path": "/a.py", "oldText": "a\n", "newText": "b\n"},
                {"type": "diff", "path": "/no-new-text.py", "oldText": "a\n"},
                {"type": "terminal", "terminalId": "t-1"},
                {"type": "unknown"},
            ]
        )

        assert [p["type"] for p in parts] == ["text", "diff", "terminal"]
        assert parts[1]["path"] == "/a.py"
        assert parts[2] == {"type": "terminal", "terminal_id": "t-1"}

    def test_caps_the_part_count(self):
        entries = [{"type": "content", "content": {"type": "text", "text": f"{i}"}} for i in range(20)]
        assert len(nz.tool_content_parts(entries)) == nz.TOOL_PARTS_MAX


class TestMergeToolUpdate:
    def test_first_update_fills_the_item(self):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(
            item,
            {
                "title": "Run tests",
                "kind": "execute",
                "status": "in_progress",
                "rawInput": {"command": "pytest -q"},
                "locations": [{"path": "/p/a.py", "line": 3}, {"path": ""}, {"line": 4}],
            },
        )

        assert item["title"] == "Run tests"
        assert item["kind"] == "execute"
        assert item["status"] == "in_progress"
        assert item["subtitle"] == "pytest -q"
        assert item["locations"] == [{"path": "/p/a.py", "line": 3}]
        assert '"command": "pytest -q"' in item["input"]

    def test_omitted_fields_keep_their_value(self):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(item, {"title": "Read a.py", "kind": "read", "status": "pending"})
        nz.merge_tool_update(item, {"status": "completed"})

        assert (item["title"], item["kind"], item["status"]) == ("Read a.py", "read", "completed")

    def test_unknown_kind_becomes_other_and_bad_status_is_ignored(self):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(item, {"kind": "teleport", "status": "exploded"})

        assert item["kind"] == "other"
        assert item["status"] == "pending"

    def test_failed_tool_shows_its_raw_output(self):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(item, {"status": "failed", "rawOutput": "permission denied"})

        assert item["content"] == [{"type": "text", "text": "permission denied"}]

    def test_failed_tool_with_text_content_keeps_only_that_content(self):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(
            item,
            {
                "status": "failed",
                "rawOutput": "raw",
                "content": [{"type": "content", "content": {"type": "text", "text": "nice error"}}],
            },
        )

        assert item["content"] == [{"type": "text", "text": "nice error"}]

    @pytest.mark.parametrize(
        ("title", "shown"),
        [
            ('/bin/zsh -lc "pytest -q"', "pytest -q"),
            ('"pytest -q"', "pytest -q"),
            ('"a" and "b"', '"a" and "b"'),
        ],
        ids=["shell-wrapper", "double-quoted", "two-words"],
    )
    def test_a_command_title_is_shown_without_its_wrapper_or_quotes(self, title, shown):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(item, {"title": title})

        assert item["title"] == shown

    def test_an_antigravity_command_result_becomes_console_output(self):
        item = nz.new_tool_item("call-1")
        body = "Command exited with code 0.\n\nCommand output:\n....\r\n4 passed in 0.12s\r\n"
        nz.merge_tool_update(item, {"kind": "execute", "title": "pytest -q", "status": "in_progress"})
        nz.merge_tool_update(
            item,
            {"status": "completed", "content": [{"type": "content", "content": {"type": "text", "text": body}}]},
        )

        assert item["content"] == [{"type": "text", "text": "```console\n....\n4 passed in 0.12s\n```"}]

    def test_the_same_text_from_a_tool_that_is_not_a_command_stays_text(self):
        item = nz.new_tool_item("call-1")
        body = "Command exited with code 0.\n\nCommand output:\nok"
        nz.merge_tool_update(
            item,
            {"kind": "read", "content": [{"type": "content", "content": {"type": "text", "text": body}}]},
        )

        assert item["content"] == [{"type": "text", "text": body}]


class TestConfigOptions:
    def test_select_and_boolean_options(self):
        options = nz.config_options(
            [
                {
                    "id": "model",
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": "smart",
                    "options": [{"value": "fast", "name": "Fast"}, {"value": "smart"}],
                },
                {"id": "fast", "name": "Fast mode", "type": "boolean", "currentValue": False},
            ]
        )

        assert [o["id"] for o in options] == ["model", "fast"]
        assert options[0]["value"] == "smart"
        assert options[0]["options"][1] == {
            "value": "smart",
            "name": "smart",
            "description": None,
            "group": None,
        }
        assert options[1]["value"] is False
        assert options[1]["options"] == []

    def test_grouped_values_are_flattened_with_their_group(self):
        options = nz.config_options(
            [
                {
                    "id": "model",
                    "name": "Model",
                    "type": "select",
                    "currentValue": "a",
                    "options": [
                        {"name": "Anthropic", "options": [{"value": "a"}, {"value": "b"}]},
                        {"value": "c"},
                    ],
                }
            ]
        )

        assert [(v["value"], v["group"]) for v in options[0]["options"]] == [
            ("a", "Anthropic"),
            ("b", "Anthropic"),
            ("c", None),
        ]

    @pytest.mark.parametrize(
        "raw",
        [
            {"id": "x", "name": "X", "type": "text", "currentValue": "a"},
            {"id": "x", "name": "X", "type": "select", "currentValue": 1, "options": [{"value": "a"}]},
            {"name": "X", "type": "boolean", "currentValue": True},
        ],
        ids=["unknown-type", "select-bad-current", "no-id"],
    )
    def test_unusable_options_are_skipped(self, raw):
        assert nz.config_options([raw]) == []


class TestModesAsConfig:
    def test_legacy_modes_become_one_mode_select(self):
        option = nz.modes_as_config(
            {
                "currentModeId": "ask",
                "availableModes": [{"id": "ask", "name": "Ask"}, {"id": "code"}, {"name": "no id"}],
            }
        )

        assert option["id"] == nz.MODE_CONFIG_ID
        assert option["category"] == "mode"
        assert option["value"] == "ask"
        assert [(v["value"], v["name"]) for v in option["options"]] == [("ask", "Ask"), ("code", "code")]

    @pytest.mark.parametrize(
        "raw",
        [None, {"currentModeId": "a", "availableModes": []}],
    )
    def test_no_usable_modes(self, raw):
        assert nz.modes_as_config(raw) is None


class TestCommands:
    def test_strips_slash_dedupes_and_reads_the_hint(self):
        cmds = nz.commands(
            [
                {"name": "/review", "description": "Review", "input": {"hint": "focus"}},
                {"name": "/review", "description": "Again"},
                {"name": "test"},
                {"description": "no name"},
            ]
        )

        assert cmds == [
            {"name": "review", "description": "Review", "hint": "focus"},
            {"name": "test", "description": "", "hint": None},
        ]


class TestPlanEntries:
    def test_unknown_status_and_priority_get_defaults(self):
        entries = nz.plan_entries(
            [
                {"content": "Read", "status": "completed", "priority": "high"},
                {"content": "Write", "status": "blocked", "priority": "urgent"},
                {"content": ""},
            ]
        )

        assert entries == [
            {"content": "Read", "status": "completed", "priority": "high"},
            {"content": "Write", "status": "pending", "priority": "medium"},
        ]


class TestUsage:
    def test_reads_used_size_and_cost(self):
        assert nz.usage({"used": 10, "size": 100, "cost": {"amount": 1, "currency": "USD"}}) == {
            "used": 10,
            "size": 100,
            "cost": {"amount": 1.0, "currency": "USD"},
        }


class TestPermissionOptions:
    def test_unknown_kind_defaults_to_allow_once_and_ids_are_required(self):
        options = nz.permission_options(
            [
                {"optionId": "a", "name": "Allow", "kind": "allow_always"},
                {"optionId": "b", "kind": "maybe"},
                {"name": "no id"},
            ]
        )

        assert options == [
            {"id": "a", "name": "Allow", "kind": "allow_always"},
            {"id": "b", "name": "b", "kind": "allow_once"},
        ]

    def test_caps_the_option_count(self):
        raw = [{"optionId": f"o{i}", "kind": "allow_once"} for i in range(20)]
        assert len(nz.permission_options(raw)) == nz.PERMISSION_OPTIONS_MAX


QUESTION_SCHEMA = {
    "type": "object",
    "properties": {
        "framework": {
            "type": "string",
            "title": "Framework",
            "oneOf": [{"const": "react", "title": "React"}, {"const": "vue"}],
        },
        "extras": {
            "type": "array",
            "title": "Extras",
            "items": {"anyOf": [{"const": "lint", "title": "Linting"}, {"const": "tests"}]},
            "default": ["lint", 3],
        },
        "size": {"type": "string", "enum": ["s", "l"], "enumNames": ["Small", "Large"]},
        "name": {"type": "string", "title": "Name", "default": "app"},
        "strict": {"type": "boolean", "default": True},
        "count": {"type": "integer", "default": 2},
        "ratio": {"type": "number"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["framework", "name"],
}


class TestFormFields:
    def test_maps_each_property_type(self):
        fields = {f["key"]: f for f in nz.form_fields(QUESTION_SCHEMA)}

        assert fields["framework"]["type"] == "select"
        assert fields["framework"]["required"] is True
        assert [o["label"] for o in fields["framework"]["options"]] == ["React", "vue"]
        assert fields["extras"]["type"] == "multiselect"
        assert fields["extras"]["default"] == ["lint"]
        assert [(o["value"], o["label"]) for o in fields["size"]["options"]] == [
            ("s", "Small"),
            ("l", "Large"),
        ]
        assert (fields["name"]["type"], fields["name"]["default"]) == ("text", "app")
        assert (fields["strict"]["type"], fields["strict"]["default"]) == ("boolean", True)
        assert (fields["count"]["type"], fields["count"]["default"]) == ("integer", 2)
        assert fields["ratio"]["type"] == "number"
        assert fields["ratio"]["title"] == "ratio"

    @pytest.mark.parametrize(
        "schema",
        [None, {"properties": {"tags": {"type": "array", "items": {}}}}],
    )
    def test_no_usable_field(self, schema):
        assert nz.form_fields(schema) is None


class TestCoerceFormValues:
    @pytest.fixture
    def fields(self):
        return nz.form_fields(QUESTION_SCHEMA)

    def test_valid_values_pass_and_empty_optionals_are_left_out(self, fields):
        content, error = nz.coerce_form_values(
            fields,
            {
                "framework": "react",
                "extras": ["lint", "tests"],
                "name": "web",
                "strict": False,
                "count": 3.0,
                "ratio": "",
                "size": None,
            },
        )

        assert error is None
        assert content == {
            "framework": "react",
            "extras": ["lint", "tests"],
            "name": "web",
            "strict": False,
            "count": 3,
        }
        assert isinstance(content["count"], int)

    def test_missing_required_field(self, fields):
        content, error = nz.coerce_form_values(fields, {"name": "web"})
        assert content is None
        assert error == "“Framework” is required"

    @pytest.mark.parametrize(
        ("values", "error"),
        [
            ({"framework": "svelte"}, "Choose a value for “Framework”"),
            ({"extras": ["lint", "docs"]}, "Choose values for “Extras”"),
            ({"count": True}, "“count” must be a number"),
            ({"count": 2.5}, "“count” must be a whole number"),
        ],
        ids=["select", "multiselect", "bool-as-number", "fraction"],
    )
    def test_invalid_values(self, fields, values, error):
        base = {"framework": "react", "name": "web"}
        content, got = nz.coerce_form_values(fields, {**base, **values})
        assert content is None
        assert got == error

    def test_long_text_is_cut(self, fields):
        content, error = nz.coerce_form_values(fields, {"framework": "vue", "name": "n" * 20_000})
        assert error is None
        assert len(content["name"]) == 10_000


class TestConsoleText:
    def test_color_and_title_sequences_are_removed(self):
        raw = "\x1b[32mok\x1b[0m \x1b]0;window title\x07done\x1b[K"

        assert nz.console_text(raw) == "ok done"

    def test_a_charset_designation_is_removed(self):
        """``tput sgr0`` on an xterm-like TERM prints ``ESC ( B ESC [ m``."""
        raw = "\x1b[1mbold\x1b(B\x1b[m plain"

        assert nz.console_text(raw) == "bold plain"

    def test_a_carriage_return_overwrites_its_line(self):
        assert nz.console_text("10%\r50%\r100%\r\nnext\r\n") == "100%\nnext\n"


class TestConsolePart:
    def test_running_output_shows_only_its_tail(self):
        output = "old\n" * 10 + "y" * nz.CONSOLE_LIVE_MAX

        part = nz.console_part(output, live=True, exit_code=None)

        assert part["truncated"] is True
        assert part["text"] == f"```console\n…\n{'y' * nz.CONSOLE_LIVE_MAX}\n```"

    def test_finished_output_keeps_its_head_and_tail(self):
        output = "H" * nz.TEXT_PART_MAX + "T" * 100

        part = nz.console_part(output, live=False, exit_code=0)

        assert part["truncated"] is True
        assert part["text"].startswith("```console\nHHH")
        assert part["text"].endswith("T" * 100 + "\n```")
        assert "characters not shown" in part["text"]

    def test_a_failed_command_shows_its_exit_code(self):
        part = nz.console_part("boom\n", live=False, exit_code=2)

        assert part == {"type": "text", "text": "```console\nboom\n```\nExit code 2"}

    @pytest.mark.parametrize(
        ("output", "exit_code", "expected"),
        [
            ("", None, None),
            ("\n\n", 0, None),
            ("", 1, {"type": "text", "text": "Exit code 1"}),
        ],
        ids=["nothing-yet", "silent-success", "silent-failure"],
    )
    def test_no_output(self, output, exit_code, expected):
        assert nz.console_part(output, live=False, exit_code=exit_code) == expected

    def test_backticks_in_the_output_get_a_longer_fence(self):
        part = nz.console_part("```py\nx\n```", live=False, exit_code=None)

        assert part["text"] == "````console\n```py\nx\n```\n````"


def _agy_result(code: int, output: str | None) -> dict:
    tail = "No output from the command." if output is None else f"Command output:\n{output}"
    return {"type": "text", "text": f"Command exited with code {code}.\n\n{tail}"}


def _copilot_result(code: int, output: str, shell_id: int = 0) -> dict:
    """The text of a finished Copilot command: its output, then one last line."""
    return {"type": "text", "text": f"{output}<shellId: {shell_id} completed with exit code {code}>"}


class TestCommandResultParts:
    @pytest.mark.parametrize(
        ("part", "expected"),
        [
            (
                _agy_result(0, "\x1b[32mok\x1b[0m\r\n10%\r100%\r\n"),
                [{"type": "text", "text": "```console\nok\n100%\n```"}],
            ),
            (_agy_result(2, "boom\n"), [{"type": "text", "text": "```console\nboom\n```\nExit code 2"}]),
            (_agy_result(0, None), []),
            (_agy_result(1, None), [{"type": "text", "text": "Exit code 1"}]),
        ],
        ids=["output", "failed", "silent-success", "silent-failure"],
    )
    def test_a_command_result(self, part, expected):
        assert nz.command_result_parts([part]) == expected

    def test_a_shortened_result_stays_marked(self):
        part = {**_agy_result(0, "head … tail"), "truncated": True}

        assert nz.command_result_parts([part]) == [
            {"type": "text", "text": "```console\nhead … tail\n```", "truncated": True}
        ]

    @pytest.mark.parametrize(
        "part",
        [
            {"type": "text", "text": "Ran the tests: Command exited with code 0.\n\nCommand output:\nok"},
            {"type": "text", "text": "Command exited with code 0."},
        ],
        ids=["text-around-it", "no-output-section"],
    )
    def test_other_parts_pass_through(self, part):
        assert nz.command_result_parts([part]) == [part]

    @pytest.mark.parametrize(
        ("part", "expected"),
        [
            (
                _copilot_result(0, "....\n4 passed in 0.12s\n"),
                [{"type": "text", "text": "```console\n....\n4 passed in 0.12s\n```"}],
            ),
            (
                _copilot_result(2, "boom\n", shell_id=14),
                [{"type": "text", "text": "```console\nboom\n```\nExit code 2"}],
            ),
            (_copilot_result(0, ""), []),
            (_copilot_result(1, ""), [{"type": "text", "text": "Exit code 1"}]),
            (
                _copilot_result(0, "first\r\nsecond\r\n"),
                [{"type": "text", "text": "```console\nfirst\nsecond\n```"}],
            ),
            (
                _copilot_result(3, "".join(f"line {n}\n" for n in range(40))),
                [
                    {
                        "type": "text",
                        "text": "```console\n" + "".join(f"line {n}\n" for n in range(40)) + "```\nExit code 3",
                    }
                ],
            ),
        ],
        ids=[
            "output",
            "failed",
            "silent-success",
            "silent-failure",
            "crlf-output",
            "output-longer-than-the-searched-tail",
        ],
    )
    def test_a_copilot_command_result(self, part, expected):
        assert nz.command_result_parts([part]) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "....\n4 passed in 0.12s\n",
            "<shellId: 0 completed with exit code 0>\nThe tests pass.",
            "<shellId: 0 still running>",
            "ok\n<shellId: 0 completed with exit code 1234567890>",
            "ok\n<shellId: 1234567890 completed with exit code 0>",
            "ok\n<shellId: 0 completed with exit code 0>" + "\n" * 200,
        ],
        ids=[
            "output-so-far",
            "text-after-the-line",
            "another-shell-line",
            "ten-digit-exit-code",
            "ten-digit-shell-id",
            "exit-line-before-the-searched-tail",
        ],
    )
    def test_copilot_text_without_a_last_exit_line_passes_through(self, text):
        part = {"type": "text", "text": text}

        assert nz.command_result_parts([part]) == [part]


class TestTerminalMeta:
    @pytest.mark.parametrize(
        ("update", "expected"),
        [
            ({}, (None, False, None, False)),
            ({"_meta": {"terminal_output": {"data": "all"}}}, ("all", True, None, False)),
            ({"_meta": {"terminal_output_delta": {"data": "more"}}}, ("more", False, None, False)),
            (
                {"_meta": {"terminal_output_delta": {"data": "end\n"}, "terminal_exit": {"exit_code": 3}}},
                ("end\n", False, 3, True),
            ),
            ({"_meta": {"terminal_exit": {"exit_code": None, "signal": "SIGKILL"}}}, (None, False, None, True)),
        ],
        ids=[
            "no-meta",
            "full-output",
            "delta",
            "delta-and-exit",
            "killed-by-signal",
        ],
    )
    def test_reads_output_and_exit(self, update, expected):
        assert nz.terminal_meta(update) == expected


class TestThreadBusy:
    @pytest.mark.parametrize(
        ("status", "busy"),
        [
            ({"type": "active", "activeFlags": []}, True),
            ({"type": "idle"}, False),
            ({"type": "somethingNew"}, None),
            ("active", None),
        ],
    )
    def test_codex_thread_status(self, status, busy):
        update = {"sessionUpdate": "session_info_update", "_meta": {"codex": {"threadStatus": status}}}

        assert nz.thread_busy(update) is busy
