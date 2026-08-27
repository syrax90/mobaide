"""Pure-function tests for the session git Changes service: git output line
splitting, porcelain v2 and unified-diff parsers, request validation and the
HTTP error map."""
from __future__ import annotations

import pytest

from mobaide.agent.git.changes_models import LINE_LIMIT
from mobaide.agent.git.changes_service import (
    GitBadRequestError,
    GitCommandError,
    GitTimeoutError,
    _lines,
    clamp_context,
    http_error_for,
    parse_porcelain_v2,
    parse_unified_diff,
    validate_repo_path,
)
from mobaide.core.git_exec import GitCwdMissingError, GitUnavailableError

_OID = "42a400abc7d33cc7694fddc94fcd5a8f9a120be3"
_HASHES = "100644 100644 100644 " + "a" * 40 + " " + "b" * 40


def _z(*records: str) -> bytes:
    return b"".join(r.encode("utf-8") + b"\0" for r in records)


def _entry_view(entries):
    return [
        (e.group, e.path, e.code, e.index_status, e.worktree_status,
         e.orig_path, e.rename_score, e.submodule)
        for e in entries
    ]


class TestLines:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (b"a\x0bb\nc\rd\n", ["a\x0bb", "c\rd"]),
            ("nel\u0085b\nls\u2028b\n".encode("utf-8"), ["nel\u0085b", "ls\u2028b"]),
            (b"", []),
            (b"x", ["x"]),
            (b"a\n\n", ["a", ""]),
        ],
        ids=["vt-and-cr", "nel-and-ls", "empty", "no-terminator", "one-blank-kept"],
    )
    def test_splits_on_newline_only(self, data, expected):
        assert _lines(data) == expected


class TestParsePorcelainV2:
    def test_branch_headers(self):
        parsed = parse_porcelain_v2(_z(
            f"# branch.oid {_OID}",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -1",
        ))

        assert (parsed.oid, parsed.branch, parsed.upstream) == (_OID, "main", "origin/main")
        assert (parsed.ahead, parsed.behind) == (2, 1)
        assert (parsed.detached, parsed.initial) == (False, False)

    def test_initial_and_detached_markers(self):
        parsed = parse_porcelain_v2(_z(
            "# branch.oid (initial)", "# branch.head (detached)",
            "# branch.upstream origin/main",
        ))

        assert parsed.initial is True and parsed.oid is None
        assert parsed.detached is True and parsed.branch is None
        assert (parsed.ahead, parsed.behind) == (None, None)

    def test_mm_yields_staged_and_unstaged_entries(self):
        parsed = parse_porcelain_v2(_z(f"1 MM N... {_HASHES} a b/c [d].txt"))

        assert _entry_view(parsed.entries) == [
            ("staged", "a b/c [d].txt", "M", "M", "M", None, None, False),
            ("unstaged", "a b/c [d].txt", "M", "M", "M", None, None, False),
        ]

    def test_rename_record_consumes_origin_token(self):
        parsed = parse_porcelain_v2(_z(
            f"2 RM N... {_HASHES} R90 new name.txt", "old name.txt",
            "? after.txt",
        ))

        assert _entry_view(parsed.entries) == [
            ("staged", "new name.txt", "R", "R", "M", "old name.txt", 90, False),
            ("unstaged", "new name.txt", "M", "R", "M", None, None, False),
            ("untracked", "after.txt", "?", "?", "?", None, None, False),
        ]

    def test_conflict_and_submodule_records(self):
        hashes = "100644 100644 100644 100644 " + " ".join(c * 40 for c in "abc")
        parsed = parse_porcelain_v2(_z(
            f"u UU N... {hashes} clash.txt",
            f"1 A. SC.. {_HASHES} sub",
            "? 日本 [x].txt",
        ))

        assert _entry_view(parsed.entries) == [
            ("conflicts", "clash.txt", "UU", "U", "U", None, None, False),
            ("staged", "sub", "A", "A", ".", None, None, True),
            ("untracked", "日本 [x].txt", "?", "?", "?", None, None, False),
        ]

    def test_truncated_stream_drops_partial_tail(self):
        whole = _z("? a.txt", f"2 R. N... {_HASHES} R100 b.txt", "a-old.txt")

        cut_in_path = parse_porcelain_v2(whole[:-len(b"a-old.txt\0") - 3], truncated=True)
        cut_before_origin = parse_porcelain_v2(whole[:-len(b"a-old.txt\0")], truncated=True)

        assert [e.path for e in cut_in_path.entries] == ["a.txt"]
        assert [e.path for e in cut_before_origin.entries] == ["a.txt"]

    def test_short_records_are_skipped(self):
        parsed = parse_porcelain_v2(_z("1 M.", "2 R.", "u UU", "? ok.txt"))

        assert [e.path for e in parsed.entries] == ["ok.txt"]


class TestParseUnifiedDiff:
    def test_numbers_lines_and_reads_modes(self):
        parsed = parse_unified_diff(
            b"diff --git a/f.txt b/f.txt\n"
            b"index 1111111..2222222 100644\n"
            b"--- a/f.txt\n+++ b/f.txt\n"
            b"@@ -1,3 +1,3 @@ def heading():\n one\n-two\n+TWO\n three\n"
        )

        assert parsed.saw_header and parsed.change() == "modified"
        assert (parsed.old_mode, parsed.new_mode) == ("100644", "100644")
        [hunk] = parsed.hunks
        assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (1, 3, 1, 3)
        assert hunk.heading == "def heading():"
        assert [(l.t, l.old, l.new, l.text) for l in hunk.lines] == [
            ("ctx", 1, 1, "one"), ("del", 2, None, "two"),
            ("add", None, 2, "TWO"), ("ctx", 3, 3, "three"),
        ]
        assert parsed.total_lines == 4

    def test_no_newline_marker_flags_previous_line(self):
        parsed = parse_unified_diff(
            b"diff --git a/f b/f\nnew file mode 100644\nindex 0000000..1111111\n"
            b"--- /dev/null\n+++ b/f\n@@ -0,0 +1 @@\n+last\n\\ No newline at end of file\n"
        )

        assert parsed.change() == "added" and parsed.new_mode == "100644"
        [hunk] = parsed.hunks
        assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (0, 0, 1, 1)
        assert [(l.text, l.no_newline) for l in hunk.lines] == [("last", True)]

    def test_type_change_merges_delete_and_add_blocks(self):
        parsed = parse_unified_diff(
            b"diff --git a/f b/f\ndeleted file mode 100644\nindex 1111111..0000000\n"
            b"--- a/f\n+++ /dev/null\n@@ -1 +0,0 @@\n-text\n"
            b"diff --git a/f b/f\nnew file mode 120000\nindex 0000000..2222222\n"
            b"--- /dev/null\n+++ b/f\n@@ -0,0 +1 @@\n+target\n"
        )

        assert parsed.change() == "type_changed"
        assert (parsed.old_mode, parsed.new_mode) == ("100644", "120000")
        assert [len(h.lines) for h in parsed.hunks] == [1, 1]

    def test_mode_lines_distinguish_chmod_from_type_change(self):
        chmod = parse_unified_diff(
            b"diff --git a/f b/f\nold mode 100644\nnew mode 100755\n"
        )
        typed = parse_unified_diff(
            b"diff --git a/f b/f\nold mode 100644\nnew mode 120000\n"
        )

        assert chmod.change() == "modified"
        assert typed.change() == "type_changed"

    def test_binary_submodule_rename_and_copy_markers(self):
        binary = parse_unified_diff(
            b"diff --git a/b.bin b/b.bin\nnew file mode 100644\n"
            b"Binary files /dev/null and b/b.bin differ\n"
        )
        submodule = parse_unified_diff(
            b"diff --git a/sub b/sub\nnew file mode 160000\nindex 0000000..1111111\n"
            b"--- /dev/null\n+++ b/sub\n@@ -0,0 +1 @@\n+Subproject commit " + b"a" * 40 + b"\n"
        )
        renamed = parse_unified_diff(
            b"diff --git a/old b/new\nsimilarity index 100%\nrename from old\nrename to new\n"
        )
        copied = parse_unified_diff(
            b"diff --git a/old b/new\nsimilarity index 100%\ncopy from old\ncopy to new\n"
        )

        assert binary.binary is True and binary.hunks == []
        assert submodule.submodule is True and submodule.change() == "added"
        assert renamed.change() == "renamed" and renamed.hunks == []
        assert copied.change() == "copied"

    def test_combined_diff_is_refused(self):
        with pytest.raises(GitCommandError):
            parse_unified_diff(b"diff --cc f.txt\nindex 1,2..3\n")

    def test_unrecognised_hunk_header_is_refused(self):
        with pytest.raises(GitCommandError):
            parse_unified_diff(b"diff --git a/f b/f\n@@ garbage @@\n")

    def test_line_limit_counts_but_stops_collecting(self):
        body = b"".join(b"+line %d\n" % i for i in range(LINE_LIMIT + 2))
        parsed = parse_unified_diff(
            b"diff --git a/f b/f\n--- a/f\n+++ b/f\n"
            b"@@ -0,0 +1,%d @@\n" % (LINE_LIMIT + 2) + body
        )

        assert parsed.truncated is True
        assert parsed.total_lines == LINE_LIMIT + 2
        assert len(parsed.hunks[0].lines) == LINE_LIMIT

    def test_truncated_input_drops_partial_last_line(self):
        parsed = parse_unified_diff(
            b"diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n one\n+tw",
            truncated=True,
        )

        assert parsed.truncated is True
        assert [l.text for l in parsed.hunks[0].lines] == ["one"]


class TestValidateRepoPath:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a.txt", "a.txt"),
            ("dir/./b.txt", "dir/b.txt"),
            ("dir//c.txt", "dir/c.txt"),
            ("dir/../x.txt", "x.txt"),
            ("a b/日本 [x].txt", "a b/日本 [x].txt"),
        ],
    )
    def test_accepts_normalised_relative_paths(self, raw, expected):
        assert validate_repo_path(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [None, "", "/abs.txt", "../up.txt", "a/../../b.txt", ".", "..",
         "C:/x.txt", "c:x.txt", "a\0b"],
    )
    def test_rejects_escapes_and_absolutes(self, raw):
        with pytest.raises(GitBadRequestError) as info:
            validate_repo_path(raw)
        assert info.value.code == "bad_path"


class TestClampContext:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("3", 3), (7, 7), (None, 3), ("abc", 3), ("-5", 0), ("0", 0),
         ("50", 50), ("99", 50)],
    )
    def test_clamps_to_range_with_default(self, raw, expected):
        assert clamp_context(raw) == expected


class TestHttpErrorFor:
    def test_maps_each_service_error(self):
        assert http_error_for(GitUnavailableError("x")) == (
            503, {"detail": {"code": "git_unavailable"}})
        assert http_error_for(GitCwdMissingError("/gone")) == (
            400, {"detail": {"code": "cwd_missing"}})
        assert http_error_for(GitTimeoutError()) == (
            504, {"detail": {"code": "git_timeout"}})
        assert http_error_for(GitBadRequestError("bad_scope")) == (
            400, {"detail": {"code": "bad_scope"}})
        assert http_error_for(GitCommandError("boom")) == (
            500, {"detail": {"code": "git_error", "message": "boom"}})

    def test_command_error_message_is_capped(self):
        mapped = http_error_for(GitCommandError("x" * 600))

        assert mapped is not None
        assert len(mapped[1]["detail"]["message"]) == 500

    def test_unrelated_exception_is_not_mapped(self):
        assert http_error_for(ValueError("nope")) is None
