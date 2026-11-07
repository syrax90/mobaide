"""Pure-function tests for the ACP → chat wire conversions: text and diff
caps, content parts, tool merges, config options, plans, usage, permission
options and elicitation forms."""
from __future__ import annotations

import pytest

from mobaide.acp import normalize as nz


class TestCapText:
    def test_short_text_is_unchanged(self):
        assert nz.cap_text("hello", 10) == ("hello", False)

    def test_long_text_keeps_head_and_tail(self):
        text = "H" * 600 + "M" * 300 + "T" * 100
        capped, truncated = nz.cap_text(text, 400)

        assert truncated is True
        assert capped.startswith("H" * 300)
        assert capped.endswith("T" * 100)
        assert "600 characters not shown" in capped


class TestSubtitleFromInput:
    def test_prefers_command_over_path(self):
        assert nz.subtitle_from_input({"file_path": "/a.py", "command": "ls -la"}) == "ls -la"

    def test_joins_a_list_of_strings(self):
        assert nz.subtitle_from_input({"command": ["git", "status"]}) == "git status"

    def test_collapses_whitespace_and_caps_length(self):
        long_cmd = "echo   one\n two " + "x" * 400
        subtitle = nz.subtitle_from_input({"command": long_cmd})

        assert subtitle.startswith("echo one two ")
        assert len(subtitle) == nz.SUBTITLE_MAX
        assert subtitle.endswith("…")

    @pytest.mark.parametrize("raw", [None, 42, {"other": "x"}, "   ", {"command": "  "}])
    def test_nothing_to_show(self, raw):
        assert nz.subtitle_from_input(raw) is None


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
    def test_empty_text_is_dropped(self):
        assert nz.content_block_part({"type": "text", "text": ""}) is None

    def test_long_text_is_marked_truncated(self):
        part = nz.content_block_part({"type": "text", "text": "x" * (nz.TEXT_PART_MAX + 1)})

        assert part["type"] == "text"
        assert part["truncated"] is True

    def test_small_image_keeps_its_data(self):
        part = nz.content_block_part({"type": "image", "mimeType": "image/jpeg", "data": "AAAA"})
        assert part == {"type": "image", "mime": "image/jpeg", "data": "AAAA"}

    def test_large_image_drops_its_data(self):
        part = nz.content_block_part(
            {"type": "image", "data": "A" * (nz.INLINE_IMAGE_MAX + 1)}
        )
        assert part == {"type": "image", "mime": "image/png"}

    def test_resource_link_prefers_the_title(self):
        part = nz.content_block_part(
            {"type": "resource_link", "uri": "file:///a/b.txt", "name": "b.txt", "title": "Notes"}
        )
        assert part == {"type": "link", "uri": "file:///a/b.txt", "name": "Notes"}

    def test_resource_is_named_by_the_last_uri_segment(self):
        part = nz.content_block_part(
            {"type": "resource", "resource": {"uri": "file:///a/b.txt", "text": "body"}}
        )
        assert part == {"type": "resource", "uri": "file:///a/b.txt", "name": "b.txt", "text": "body"}

    @pytest.mark.parametrize("block", [None, "text", {"type": "audio"}, {"type": "resource"}])
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

    def test_subtitle_falls_back_to_the_first_location(self):
        item = nz.new_tool_item("call-1")
        nz.merge_tool_update(item, {"locations": [{"path": "/p/b.py"}]})

        assert item["subtitle"] == "/p/b.py"


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
            {"id": "x", "name": "X", "type": "boolean", "currentValue": "yes"},
            {"id": "x", "name": "X", "type": "select", "currentValue": "a", "options": []},
            {"id": "x", "name": "X", "type": "select", "currentValue": 1, "options": [{"value": "a"}]},
            {"name": "X", "type": "boolean", "currentValue": True},
        ],
        ids=["unknown-type", "boolean-not-bool", "select-no-values", "select-bad-current", "no-id"],
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
        [None, {"availableModes": [{"id": "a"}]}, {"currentModeId": "a", "availableModes": []}],
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

    def test_cost_without_currency_is_dropped(self):
        assert nz.usage({"used": 10, "size": 100, "cost": {"amount": 1}})["cost"] is None

    def test_missing_counts_mean_no_usage(self):
        assert nz.usage({"used": 10}) is None


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

    def test_array_without_choices_is_skipped(self):
        keys = [f["key"] for f in nz.form_fields(QUESTION_SCHEMA)]
        assert "tags" not in keys

    @pytest.mark.parametrize(
        "schema",
        [None, {"type": "object"}, {"properties": {"tags": {"type": "array", "items": {}}}}],
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
            ({"strict": "yes"}, "“strict” must be on or off"),
            ({"count": True}, "“count” must be a number"),
            ({"count": 2.5}, "“count” must be a whole number"),
            ({"name": 5}, "“Name” must be text"),
        ],
        ids=["select", "multiselect", "boolean", "bool-as-number", "fraction", "text"],
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

    def test_non_mapping_values_count_as_empty(self, fields):
        content, error = nz.coerce_form_values(fields, "nope")
        assert content is None
        assert error == "“Framework” is required"
