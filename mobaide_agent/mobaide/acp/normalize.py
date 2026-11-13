"""Pure conversions from ACP payloads to the chat wire model.

Everything a client renders goes through here, so this module is where the
size bounds live: text parts, diffs, inputs and lists are all capped. The
functions never raise on malformed agent data; unknown shapes degrade to
"nothing to show".
"""
from __future__ import annotations

import difflib
import posixpath
import re
import shlex
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

import orjson

TEXT_PART_MAX = 32 * 1024
TEXT_TAIL_KEEP = 8 * 1024
ITEM_TEXT_MAX = 256 * 1024
DIFF_INPUT_MAX = 2 * 1024 * 1024
DIFF_OUTPUT_MAX = 48 * 1024
DIFF_LINES_MAX = 1200
# difflib is quadratic in the worst case; above this many line pairs
# (~15 ms) only the counts are computed.
DIFF_PAIRS_MAX = 1_000_000
INPUT_JSON_MAX = 2 * 1024
SUBTITLE_MAX = 300
INLINE_IMAGE_MAX = 200 * 1024
TOOL_PARTS_MAX = 8
COMMANDS_MAX = 500
COMMAND_DESC_MAX = 300
PLAN_MAX = 100
CONFIG_OPTIONS_MAX = 40
CONFIG_VALUES_MAX = 200
FORM_FIELDS_MAX = 20
FORM_OPTIONS_MAX = 50
PERMISSION_OPTIONS_MAX = 8
# Command output kept per tool call, and shown while it still runs.
CONSOLE_KEEP_MAX = 128 * 1024
CONSOLE_LIVE_MAX = 8 * 1024
# Longest command that is parsed as shell words to tidy a title: the parse
# costs the square of the length of one long word.
SHELL_PARSE_MAX = 8 * 1024

TOOL_KINDS = frozenset(
    {"read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other"}
)
TOOL_STATUSES = frozenset({"pending", "in_progress", "completed", "failed"})
TERMINAL_TOOL_STATUSES = frozenset({"completed", "failed", "cancelled"})
PERMISSION_KINDS = frozenset({"allow_once", "allow_always", "reject_once", "reject_always"})
PLAN_STATUSES = frozenset({"pending", "in_progress", "completed"})
PLAN_PRIORITIES = frozenset({"high", "medium", "low"})

MODE_CONFIG_ID = "__mode"

# rawInput keys that name what a tool works on, by preference. Shared by
# every ACP agent: these are the common spellings across agents.
_SUBTITLE_KEYS = (
    "command",
    "cmd",
    "file_path",
    "filePath",
    "path",
    "notebook_path",
    "pattern",
    "query",
    "url",
    "description",
    "prompt",
    # Antigravity's one-line summary; its title already names the command.
    "toolSummary",
)


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def cap_text(text: str, limit: int = TEXT_PART_MAX) -> tuple[str, bool]:
    """Keep the head and the tail of ``text`` when it is over ``limit`` chars."""
    if len(text) <= limit:
        return text, False
    tail = min(TEXT_TAIL_KEEP, limit // 4)
    head = limit - tail
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n… {omitted:,} characters not shown …\n\n{text[-tail:]}",
        True,
    )


def _one_line(text: str, limit: int = SUBTITLE_MAX) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def compact_json(value: Any, limit: int = INPUT_JSON_MAX) -> str | None:
    if value is None or value == {} or value == []:
        return None
    try:
        text = orjson.dumps(value, option=orjson.OPT_INDENT_2).decode("utf-8")
    except TypeError:
        return None
    capped, _ = cap_text(text, limit)
    return capped


_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash"})
_SYSTEM_BIN_DIRS = frozenset({"/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"})


def unwrap_shell(command: str) -> str:
    """``/bin/zsh -lc "<cmd>"`` → ``<cmd>``: the wrapper some agents report
    around every command says nothing to the reader.

    Only a system shell is dropped: ``./bash -c "git status"`` runs a file
    from the project, and an approval card must show that.
    """
    if len(command) > SHELL_PARSE_MAX:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) != 3 or parts[1] not in ("-c", "-lc", "-ic") or not parts[2].strip():
        return command
    shell = parts[0]
    system = shell in _SHELLS or (
        posixpath.dirname(shell) in _SYSTEM_BIN_DIRS and posixpath.basename(shell) in _SHELLS
    )
    return parts[2] if system else command


def _unquote_title(title: str) -> str:
    """A title that is one quoted shell word, as some agents send a command."""
    if len(title) < 2 or title[0] != title[-1] or title[0] not in "\"'":
        return title
    if len(title) > SHELL_PARSE_MAX:
        return title
    try:
        parts = shlex.split(title)
    except ValueError:
        return title
    return parts[0] if len(parts) == 1 and parts[0].strip() else title


# A Markdown code span on one line.
_CODE_SPAN = re.compile(r"`([^`\n]+)`")
# A backtick that no backslash stands before.
_BARE_MARK = re.compile(r"(?<!\\)`")


# An absolute path, POSIX or Windows, with a name after its last separator.
_ABSOLUTE_PATH = re.compile(r"(?:/|[A-Za-z]:[\\/]|\\\\).*[\\/]([^\\/]+)[\\/]*\Z", re.DOTALL)
# rawInput keys that hold the one file a tool works on.
_PATH_KEYS = ("file_path", "filePath", "path", "notebook_path")


def _item_paths(item: Mapping[str, Any], raw_input: Any) -> frozenset[str]:
    """The files a tool call names outside its title: its locations and the
    path of its input. The card shows them in full."""
    paths = {loc["path"] for loc in item["locations"]}
    if isinstance(raw_input, Mapping):
        paths.update(v for key in _PATH_KEYS if isinstance(v := raw_input.get(key), str))
    return frozenset(paths)


def _without_code_marks(title: str) -> str:
    """A Markdown title as the plain text the clients show: each code span
    (``Edit `app.py```) without its marks."""
    if "`" not in title:
        return title
    return _CODE_SPAN.sub(r"\1", title)


def _with_short_paths(title: str, paths: frozenset[str]) -> str:
    """``title`` with each of ``paths`` (the tool call's own files, when
    absolute) as its last name only: a phone cuts a long title before the
    file name, and the card holds the whole path. Any other path stays
    whole."""
    for path in sorted(paths, key=len, reverse=True):
        match = _ABSOLUTE_PATH.match(path)
        if match is None or path not in title:
            continue
        # Not the start of a longer path.
        title = re.sub(re.escape(path) + r"(?![\w./\\-])", lambda _: match.group(1), title)
    return title


def _markdown_title(title: str, kind: str, paths: frozenset[str] = frozenset()) -> str:
    """A title that an agent writes in Markdown (Cursor), as plain text.

    Cursor writes a command as one code span around the whole title, with
    a backslash before each backtick of the command. Only that exact form is
    undone, so an approval card shows the command as it runs, or the title
    as it came."""
    if kind != "execute":
        return _with_short_paths(_without_code_marks(title), paths)
    if len(title) < 3 or title[0] != "`" or title[-1] != "`":
        return title
    body = title[1:-1]
    if _BARE_MARK.search(body) is not None:
        return title
    return body.replace("\\`", "`")


def subtitle_from_input(raw_input: Any) -> str | None:
    if isinstance(raw_input, str):
        return _one_line(raw_input) if raw_input.strip() else None
    if not isinstance(raw_input, Mapping):
        return None
    for key in _SUBTITLE_KEYS:
        value = raw_input.get(key)
        if isinstance(value, str) and value.strip():
            if key in ("command", "cmd"):
                value = unwrap_shell(value)
            return _one_line(value)
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return _one_line(" ".join(value))
    return None


def unified_diff(path: str, old_text: str | None, new_text: str) -> dict[str, Any]:
    """A bounded unified diff part for one file change."""
    part: dict[str, Any] = {
        "type": "diff",
        "path": path,
        "new_file": old_text is None,
        "diff": "",
        "added": 0,
        "removed": 0,
        "truncated": False,
    }
    old = old_text or ""
    if len(old) + len(new_text) > DIFF_INPUT_MAX:
        part["truncated"] = True
        part["added"] = new_text.count("\n") + (1 if new_text else 0)
        part["removed"] = old.count("\n") + (1 if old else 0)
        return part
    old_lines = old.splitlines()
    new_lines = new_text.splitlines()
    if len(old_lines) * len(new_lines) > DIFF_PAIRS_MAX:
        old_count, new_count = Counter(old_lines), Counter(new_lines)
        part["truncated"] = True
        part["added"] = sum((new_count - old_count).values())
        part["removed"] = sum((old_count - new_count).values())
        return part
    out: list[str] = []
    size = 0
    added = removed = 0
    diff = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
    for index, line in enumerate(diff):
        # Only the first two lines are the file header; a removed "-- x" or
        # an added "++x" line also starts with three marks.
        if index < 2:
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if part["truncated"]:
            continue
        if len(out) >= DIFF_LINES_MAX or size + len(line) + 1 > DIFF_OUTPUT_MAX:
            part["truncated"] = True
            continue
        out.append(line)
        size += len(line) + 1
    part["diff"] = "\n".join(out)
    part["added"] = added
    part["removed"] = removed
    return part


# The file lines of a unified diff, less their first mark.
_OLD_FILE_LINE = re.compile(r"\A-- (?:a/[^\n]*|/dev/null)(?:\n|\Z)")
_NEW_FILE_LINE = re.compile(r"\A\+\+ (?:b/[^\n]*|/dev/null)(?:\n|\Z)")


def _without_file_lines(old_text: str | None, new_text: str) -> tuple[str | None, str]:
    """A diff's two texts without the ``--- a/…`` and ``+++ b/…`` lines.

    Cursor builds the texts of a new file from a unified diff and keeps those
    two lines in them, each less its first mark. They are dropped only when
    both texts start with one, and an old side of ``/dev/null`` alone is a
    new file."""
    if old_text is None:
        return old_text, new_text
    old_match = _OLD_FILE_LINE.match(old_text)
    new_match = _NEW_FILE_LINE.match(new_text)
    if old_match is None or new_match is None:
        return old_text, new_text
    old_rest = old_text[old_match.end():]
    new_file = not old_rest and old_match.group(0).startswith("-- /dev/null")
    return (None if new_file else old_rest), new_text[new_match.end():]


def content_block_part(block: Any) -> dict[str, Any] | None:
    """One ACP ``ContentBlock`` as a chat content part."""
    if not isinstance(block, Mapping):
        return None
    kind = block.get("type")
    if kind == "text":
        text = _str(block.get("text")) or ""
        if not text:
            return None
        capped, truncated = cap_text(text)
        part: dict[str, Any] = {"type": "text", "text": capped}
        if truncated:
            part["truncated"] = True
        return part
    if kind == "image":
        mime = _str(block.get("mimeType")) or "image/png"
        data = _str(block.get("data"))
        part = {"type": "image", "mime": mime}
        if data and len(data) <= INLINE_IMAGE_MAX:
            part["data"] = data
        return part
    if kind == "resource_link":
        uri = _str(block.get("uri")) or ""
        return {
            "type": "link",
            "uri": uri,
            "name": _str(block.get("title")) or _str(block.get("name")) or uri,
        }
    if kind == "resource":
        resource = block.get("resource")
        if not isinstance(resource, Mapping):
            return None
        uri = _str(resource.get("uri")) or ""
        text = _str(resource.get("text"))
        part = {"type": "resource", "uri": uri, "name": uri.rsplit("/", 1)[-1] or uri}
        if text:
            part["text"], truncated = cap_text(text)
            if truncated:
                part["truncated"] = True
        return part
    return None


def tool_content_parts(content: Any) -> list[dict[str, Any]]:
    """ACP ``ToolCallContent[]`` as chat content parts (bounded)."""
    if not isinstance(content, list):
        return []
    parts: list[dict[str, Any]] = []
    for entry in content:
        if len(parts) >= TOOL_PARTS_MAX:
            break
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("type")
        if kind == "content":
            part = content_block_part(entry.get("content"))
        elif kind == "diff":
            path = _str(entry.get("path")) or ""
            new_text = _str(entry.get("newText"))
            if new_text is None:
                continue
            old_text = _str(entry.get("oldText"))
            if not new_text and old_text and old_text == path:
                # Cursor reports a deleted file as its own path: no content.
                continue
            old_text, new_text = _without_file_lines(old_text, new_text)
            part = unified_diff(path, old_text, new_text)
        elif kind == "terminal":
            term_id = _str(entry.get("terminalId"))
            part = {"type": "terminal", "terminal_id": term_id} if term_id else None
        else:
            part = None
        if part is not None:
            parts.append(part)
    return parts


def locations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for loc in value[:20]:
        if not isinstance(loc, Mapping):
            continue
        path = _str(loc.get("path"))
        if not path:
            continue
        entry: dict[str, Any] = {"path": path}
        line = loc.get("line")
        if isinstance(line, int) and line > 0:
            entry["line"] = line
        out.append(entry)
    return out


def new_tool_item(tool_call_id: str) -> dict[str, Any]:
    return {
        "id": tool_call_id,
        "type": "tool",
        "title": "Tool call",
        "kind": "other",
        "status": "pending",
        "subtitle": None,
        "locations": [],
        "content": [],
        "input": None,
    }


def merge_tool_update(
    item: dict[str, Any], update: Mapping[str, Any], *, markdown_titles: bool = False
) -> None:
    """Apply a ``tool_call`` or ``tool_call_update`` payload to ``item``.

    Omitted fields keep their value; ``content`` and ``locations`` replace.
    ``markdown_titles`` is for an agent that writes its titles in Markdown.
    """
    kind = update.get("kind")
    if isinstance(kind, str):
        item["kind"] = kind if kind in TOOL_KINDS else "other"
    status = update.get("status")
    if isinstance(status, str) and status in TOOL_STATUSES:
        item["status"] = status
    if "rawInput" in update and update.get("rawInput") is not None:
        raw_input = update.get("rawInput")
        subtitle = subtitle_from_input(raw_input)
        if subtitle:
            item["subtitle"] = subtitle
        item["input"] = compact_json(raw_input)
    if "locations" in update and update.get("locations") is not None:
        locs = locations(update.get("locations"))
        if locs or isinstance(update.get("locations"), list):
            item["locations"] = locs
    # After the kind, the input and the locations: a Markdown title is read
    # with them.
    title = _str(update.get("title"))
    if title:
        title = title.strip()
        if markdown_titles:
            title = _markdown_title(
                title, item["kind"], _item_paths(item, update.get("rawInput"))
            )
        item["title"] = _one_line(_unquote_title(unwrap_shell(title)), 200)
    if "content" in update and update.get("content") is not None:
        item["content"] = tool_content_parts(update.get("content"))
        if item["kind"] == "execute":
            item["content"] = command_result_parts(item["content"])
    raw_output = update.get("rawOutput")
    shell = _shell_result(raw_output) if item["kind"] == "execute" else None
    if shell is not None and update.get("content") is None:
        # The output takes the place of what stood there while the command
        # waited (why it needed an approval).
        rendered = console_part(shell[0], live=False, exit_code=shell[1])
        item["content"] = [rendered] if rendered is not None else []
    raw_output = _raw_error_text(raw_output)
    if (
        item.get("status") == "failed"
        and isinstance(raw_output, str)
        and raw_output.strip()
        and not any(p.get("type") == "text" for p in item["content"])
    ):
        text, truncated = cap_text(raw_output)
        part: dict[str, Any] = {"type": "text", "text": text}
        if truncated:
            part["truncated"] = True
        item["content"] = [*item["content"], part][:TOOL_PARTS_MAX]
    if not item.get("subtitle") and item["locations"]:
        item["subtitle"] = _one_line(item["locations"][0]["path"])


def _shell_result(raw_output: Any) -> tuple[str, int] | None:
    """``(output, exit code)`` of a finished command that an agent reports as
    ``rawOutput: {exitCode, stdout, stderr}`` (Cursor); None for any other
    ``rawOutput``."""
    if not isinstance(raw_output, Mapping):
        return None
    code = raw_output.get("exitCode")
    stdout = raw_output.get("stdout")
    stderr = raw_output.get("stderr")
    if isinstance(code, bool) or not isinstance(code, int):
        return None
    if not isinstance(stdout, str) and not isinstance(stderr, str):
        return None
    # Cut before the join: the output is rendered once more after this.
    texts = [
        cap_text(text, CONSOLE_KEEP_MAX)[0].rstrip("\n")
        for text in (stdout, stderr)
        if isinstance(text, str) and text
    ]
    return "\n".join(texts), code


def _raw_error_text(raw_output: Any) -> Any:
    """The error text of a ``rawOutput``: the string itself, or the ``error``
    string of a mapping (Cursor). Any other value is returned as it is."""
    if isinstance(raw_output, Mapping) and isinstance(raw_output.get("error"), str):
        return raw_output["error"]
    return raw_output


def config_options(raw: Any) -> list[dict[str, Any]]:
    """ACP ``configOptions`` as flat, typed chat config options."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for opt in raw[:CONFIG_OPTIONS_MAX]:
        if not isinstance(opt, Mapping):
            continue
        opt_id = _str(opt.get("id"))
        name = _str(opt.get("name"))
        kind = opt.get("type")
        if not opt_id or not name or kind not in ("select", "boolean"):
            continue
        entry: dict[str, Any] = {
            "id": opt_id,
            "name": name,
            "description": _str(opt.get("description")),
            "category": _str(opt.get("category")),
            "type": kind,
        }
        current = opt.get("currentValue")
        if kind == "boolean":
            if not isinstance(current, bool):
                continue
            entry["value"] = current
            entry["options"] = []
        else:
            values = _select_values(opt.get("options"))
            if not values or not isinstance(current, str):
                continue
            entry["value"] = current
            entry["options"] = values
        out.append(entry)
    return out


def _select_values(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    values: list[dict[str, Any]] = []

    def add(value: Any, group: str | None) -> None:
        if not isinstance(value, Mapping) or len(values) >= CONFIG_VALUES_MAX:
            return
        val = _str(value.get("value"))
        if val is None:
            return
        values.append(
            {
                "value": val,
                "name": _str(value.get("name")) or val,
                "description": _str(value.get("description")),
                "group": group,
            }
        )

    for entry in raw:
        if isinstance(entry, Mapping) and isinstance(entry.get("options"), list):
            group = _str(entry.get("name")) or _str(entry.get("group"))
            for value in entry["options"]:
                add(value, group)
        else:
            add(entry, None)
    return values


def modes_as_config(raw: Any) -> dict[str, Any] | None:
    """Legacy ``modes`` state as one ``select`` option, for agents that send
    no config options."""
    if not isinstance(raw, Mapping):
        return None
    current = _str(raw.get("currentModeId"))
    available = raw.get("availableModes")
    if current is None or not isinstance(available, list):
        return None
    values = [
        {
            "value": mode_id,
            "name": _str(mode.get("name")) or mode_id,
            "description": _str(mode.get("description")),
            "group": None,
        }
        for mode in available
        if isinstance(mode, Mapping) and (mode_id := _str(mode.get("id")))
    ]
    if not values:
        return None
    return {
        "id": MODE_CONFIG_ID,
        "name": "Mode",
        "description": None,
        "category": "mode",
        "type": "select",
        "value": current,
        "options": values,
    }


def commands(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cmd in raw:
        if len(out) >= COMMANDS_MAX:
            break
        if not isinstance(cmd, Mapping):
            continue
        name = _str(cmd.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        hint = None
        cmd_input = cmd.get("input")
        if isinstance(cmd_input, Mapping):
            hint = _str(cmd_input.get("hint"))
        description = _str(cmd.get("description")) or ""
        out.append(
            {
                "name": name.lstrip("/"),
                "description": _one_line(description, COMMAND_DESC_MAX),
                "hint": hint,
            }
        )
    return out


def plan_entries(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw[:PLAN_MAX]:
        if not isinstance(entry, Mapping):
            continue
        content = _str(entry.get("content"))
        if not content:
            continue
        status = entry.get("status")
        priority = entry.get("priority")
        out.append(
            {
                "content": _one_line(content, 500),
                "status": status if status in PLAN_STATUSES else "pending",
                "priority": priority if priority in PLAN_PRIORITIES else "medium",
            }
        )
    return out


def usage(update: Mapping[str, Any]) -> dict[str, Any] | None:
    used = update.get("used")
    size = update.get("size")
    if not isinstance(used, int) or not isinstance(size, int):
        return None
    result: dict[str, Any] = {"used": used, "size": size, "cost": None}
    cost = update.get("cost")
    if isinstance(cost, Mapping):
        amount = cost.get("amount")
        currency = _str(cost.get("currency"))
        if isinstance(amount, int | float) and currency:
            result["cost"] = {"amount": float(amount), "currency": currency}
    return result


def permission_options(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for opt in raw[:PERMISSION_OPTIONS_MAX]:
        if not isinstance(opt, Mapping):
            continue
        opt_id = _str(opt.get("optionId"))
        if not opt_id:
            continue
        kind = opt.get("kind")
        out.append(
            {
                "id": opt_id,
                "name": _one_line(_str(opt.get("name")) or opt_id, 120),
                "kind": kind if kind in PERMISSION_KINDS else "allow_once",
            }
        )
    return out


def _enum_options(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for key in ("oneOf", "anyOf"):
        raw = schema.get(key)
        if isinstance(raw, list):
            for opt in raw[:FORM_OPTIONS_MAX]:
                if isinstance(opt, Mapping) and isinstance(opt.get("const"), str):
                    options.append(
                        {
                            "value": opt["const"],
                            "label": _str(opt.get("title")) or opt["const"],
                            "description": _str(opt.get("description")),
                        }
                    )
            if options:
                return options
    raw_enum = schema.get("enum")
    if isinstance(raw_enum, list):
        names = schema.get("enumNames")
        for idx, value in enumerate(raw_enum[:FORM_OPTIONS_MAX]):
            if not isinstance(value, str):
                continue
            label = value
            if isinstance(names, list) and idx < len(names) and isinstance(names[idx], str):
                label = names[idx]
            options.append({"value": value, "label": label, "description": None})
    return options


def form_fields(schema: Any) -> list[dict[str, Any]] | None:
    """A flat elicitation ``requestedSchema`` as typed form fields.

    Returns ``None`` when the schema has no usable field.
    """
    if not isinstance(schema, Mapping):
        return None
    props = schema.get("properties")
    if not isinstance(props, Mapping):
        return None
    required = schema.get("required")
    required_set = {r for r in required if isinstance(r, str)} if isinstance(required, list) else set()
    fields: list[dict[str, Any]] = []
    for key, prop in props.items():
        if len(fields) >= FORM_FIELDS_MAX:
            break
        if not isinstance(key, str) or not isinstance(prop, Mapping):
            continue
        prop_type = prop.get("type")
        field: dict[str, Any] = {
            "key": key,
            "title": _str(prop.get("title")) or key,
            "description": _str(prop.get("description")),
            "required": key in required_set,
            "options": [],
            "default": None,
        }
        default = prop.get("default")
        if prop_type == "array":
            items = prop.get("items")
            options = _enum_options(items) if isinstance(items, Mapping) else []
            if not options:
                continue
            field["type"] = "multiselect"
            field["options"] = options
            if isinstance(default, list):
                field["default"] = [d for d in default if isinstance(d, str)]
        elif prop_type == "boolean":
            field["type"] = "boolean"
            if isinstance(default, bool):
                field["default"] = default
        elif prop_type in ("number", "integer"):
            field["type"] = prop_type
            if isinstance(default, int | float) and not isinstance(default, bool):
                field["default"] = default
        else:
            options = _enum_options(prop)
            if options:
                field["type"] = "select"
                field["options"] = options
            else:
                field["type"] = "text"
            if isinstance(default, str):
                field["default"] = default
        fields.append(field)
    return fields or None


def coerce_form_values(
    fields: Iterable[Mapping[str, Any]], values: Any
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate client form values against the normalized fields.

    Returns ``(content, None)`` or ``(None, error)``.
    """
    if not isinstance(values, Mapping):
        values = {}
    content: dict[str, Any] = {}
    for field in fields:
        key = field["key"]
        ftype = field["type"]
        value = values.get(key)
        allowed = {opt["value"] for opt in field.get("options") or []}
        if value is None or value == "" or value == []:
            if field.get("required"):
                return None, f"“{field['title']}” is required"
            continue
        if ftype == "select":
            if not isinstance(value, str) or value not in allowed:
                return None, f"Choose a value for “{field['title']}”"
        elif ftype == "multiselect":
            if not isinstance(value, list) or not all(
                isinstance(v, str) and v in allowed for v in value
            ):
                return None, f"Choose values for “{field['title']}”"
        elif ftype == "boolean":
            if not isinstance(value, bool):
                return None, f"“{field['title']}” must be on or off"
        elif ftype in ("number", "integer"):
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None, f"“{field['title']}” must be a number"
            if ftype == "integer":
                if float(value) != int(value):
                    return None, f"“{field['title']}” must be a whole number"
                value = int(value)
        else:
            if not isinstance(value, str):
                return None, f"“{field['title']}” must be text"
            value = value[:10_000]
        content[key] = value
    return content, None


_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[ -/]+[0-~]|[@-Z\\-_])")


def console_text(raw: str) -> str:
    """Terminal output as plain text: no escape sequences, and a carriage
    return overwrites its line, as on screen (progress bars)."""
    text = _ANSI.sub("", raw).replace("\r\n", "\n")
    if "\r" in text:
        text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))
    return text


def console_part(output: str, *, live: bool, exit_code: int | None) -> dict[str, Any] | None:
    """Command output as a fenced ``console`` text part.

    While the command runs only the tail is shown, like a terminal; once it
    ends, the head and the tail.
    """
    text = console_text(output).rstrip("\n")
    truncated = False
    if live and len(text) > CONSOLE_LIVE_MAX:
        text = "…\n" + text[-CONSOLE_LIVE_MAX:]
        truncated = True
    elif not live:
        text, truncated = cap_text(text)
    if not text and exit_code in (None, 0):
        return None
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    body = f"{fence}console\n{text}\n{fence}" if text else ""
    if exit_code not in (None, 0):
        body = f"{body}\nExit code {exit_code}".lstrip("\n")
    part: dict[str, Any] = {"type": "text", "text": body}
    if truncated:
        part["truncated"] = True
    return part


# Antigravity reports a finished command as one text part.
_COMMAND_RESULT = re.compile(
    r"\ACommand exited with code (-?\d+)\.\n\n(?:Command output:\n(.*)|No output from the command\.\s*)\Z",
    re.DOTALL,
)


# GitHub Copilot ends the output of a finished command with this line.
_SHELL_EXIT = re.compile(r"(?:\r?\n)?<shellId: \d{1,9} completed with exit code (-?\d{1,9})>\s*\Z")
# That line is the end of the text: only this much of the tail is searched.
_SHELL_EXIT_TAIL = 128


def _command_result(text: str) -> tuple[str, int] | None:
    """``(output, exit code)`` of a finished command, from the text an agent
    reports it as; None for any other text."""
    match = _COMMAND_RESULT.match(text)
    if match is not None:
        return match.group(2) or "", int(match.group(1))
    match = _SHELL_EXIT.search(text, max(0, len(text) - _SHELL_EXIT_TAIL))
    if match is not None:
        return text[: match.start()], int(match.group(1))
    return None


def command_result_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each text that reports a finished command (Antigravity's ``Command
    exited with code N.\n\nCommand output:\n…``, Copilot's output with a last
    ``<shellId: N completed with exit code M>`` line) as the ``console`` part
    other agents' command output gets (dropped when the command printed
    nothing and succeeded)."""
    out: list[dict[str, Any]] = []
    for part in parts:
        text = part.get("text") if part.get("type") == "text" else None
        result = _command_result(text) if isinstance(text, str) else None
        if result is None:
            out.append(part)
            continue
        rendered = console_part(result[0], live=False, exit_code=result[1])
        if rendered is not None:
            if part.get("truncated"):
                rendered["truncated"] = True
            out.append(rendered)
    return out


def terminal_meta(update: Mapping[str, Any]) -> tuple[str | None, bool, int | None, bool]:
    """``(text, replaces, exit_code, exited)`` from the terminal ``_meta`` of
    a tool call update (``terminal_output`` replaces the output so far,
    ``terminal_output_delta`` adds to it)."""
    meta = update.get("_meta")
    if not isinstance(meta, Mapping):
        return None, False, None, False
    text: str | None = None
    replaces = False
    full = meta.get("terminal_output")
    delta = meta.get("terminal_output_delta")
    if isinstance(full, Mapping) and isinstance(full.get("data"), str):
        text, replaces = full["data"], True
    elif isinstance(delta, Mapping) and isinstance(delta.get("data"), str):
        text = delta["data"]
    exit_info = meta.get("terminal_exit")
    exited = isinstance(exit_info, Mapping)
    code = exit_info.get("exit_code") if exited else None  # type: ignore[union-attr]
    return text, replaces, code if isinstance(code, int) and not isinstance(code, bool) else None, exited


def thread_busy(update: Mapping[str, Any]) -> bool | None:
    """Whether the agent reports its session busy (Codex sends its thread
    status in ``session_info_update._meta``); None when it says nothing."""
    meta = update.get("_meta")
    codex = meta.get("codex") if isinstance(meta, Mapping) else None
    status = codex.get("threadStatus") if isinstance(codex, Mapping) else None
    kind = status.get("type") if isinstance(status, Mapping) else None
    if kind == "active":
        return True
    if kind in ("idle", "notLoaded", "systemError"):
        return False
    return None
