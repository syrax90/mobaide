"""Read / resolve / take / restore of one conflicted file against real temp
repositories: the shape the resolver shows, the guards that keep the write
route on a path git itself reports unmerged (and off symlinks), the file's
own line-ending and BOM convention surviving a resolution, the side picked
from the index stages, the precondition that keeps ``checkout --merge`` off
files it would silently overwrite, and the ``git.changed`` echo."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import subprocess
import sys

import pytest

from mobaide.agent.git import changes_conflicts, changes_mutations
from mobaide.agent.git.changes_conflicts import (
    get_session_conflict,
    resolve_session_conflict,
    restore_session_conflict,
    take_session_conflict,
)
from mobaide.agent.git.changes_jobs import job_registry
from mobaide.agent.git.changes_models import (
    EDIT_BYTE_LIMIT,
    GitConflictResolveRequest,
    GitConflictRestoreRequest,
    GitConflictTakeRequest,
)
from mobaide.agent.git.changes_service import (
    GitBadRequestError,
    GitCommandError,
    GitRefusedError,
    GitTimeoutError,
    get_session_git_status,
)
from mobaide.core.git_exec import CmdResult, reset_caches

from .git_repo_support import (
    GIT_BIN,
    commit_all,
    finish_merge_as_ours,
    make_conflict_branches,
    make_modify_delete_branches,
    make_submodule_conflict,
    norm_path,
    posix_only,
    repo,  # noqa: F401 - pytest fixture
    requires_git,
    run_git_sync,
    start_failing_am_after_a_merge_as_ours,
    stop_rebase_on_exec_after_a_pick_as_ours,
    write_file,
)


@pytest.fixture(autouse=True)
def _fresh_caches():
    reset_caches()
    yield
    reset_caches()


@pytest.fixture(autouse=True)
async def _fresh_registry():
    job_registry.reset()
    yield
    await job_registry.shutdown()
    job_registry.reset()


class FakeEventBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def publish(self, event_type: str, payload: dict) -> None:
        self.published.append((event_type, payload))


def _changed(toplevel: str, project_name: str = "proj") -> tuple[str, dict]:
    return (
        "git.changed",
        {
            "project_name": project_name,
            "toplevel": norm_path(toplevel),
            "common_dir": norm_path(toplevel) + "/.git",
        },
    )


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = os.path.devnull
    env["GIT_CONFIG_SYSTEM"] = os.path.devnull
    env["GIT_EDITOR"] = "true"
    return env


def _git_out(repo_dir: str, *args: str) -> str:
    return subprocess.run(
        [GIT_BIN, *args], cwd=repo_dir, check=True, env=_git_env(), capture_output=True
    ).stdout.decode().strip()


def _read(repo_dir: str, rel: str) -> bytes:
    with open(os.path.join(repo_dir, *rel.split("/")), "rb") as fh:
        return fh.read()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unmerged(repo_dir: str) -> list[str]:
    out = _git_out(repo_dir, "diff", "--name-only", "--diff-filter=U")
    return out.splitlines()


def _staged_blob(repo_dir: str, rel: str) -> bytes:
    return subprocess.run(
        [GIT_BIN, "cat-file", "blob", f":0:{rel}"],
        cwd=repo_dir, check=True, env=_git_env(), capture_output=True,
    ).stdout


def _stop_rebase(repo_dir: str) -> None:
    """``other`` replayed onto ``main`` stops on line 2 of ``keep.txt``:
    stage 2 (ours) is ``main``'s ``MAIN``, stage 3 (theirs) the replayed
    commit's ``OTHER``."""
    make_conflict_branches(repo_dir)
    run_git_sync(repo_dir, "checkout", "-q", "other")
    run_git_sync(repo_dir, "rebase", "--no-autostash", "main", check=False)


def _stop_merge(repo_dir: str) -> None:
    make_conflict_branches(repo_dir)
    run_git_sync(repo_dir, "merge", "other", check=False)


def _stop_cherry_pick(repo_dir: str) -> None:
    make_conflict_branches(repo_dir)
    run_git_sync(repo_dir, "cherry-pick", "other", check=False)


def _stop_revert(repo_dir: str) -> None:
    """Reverting ``main``'s older rewrite of line 2 of ``keep.txt`` stops on
    the newer one."""
    write_file(repo_dir, "keep.txt", b"one\nFIRST\nthree\n")
    commit_all(repo_dir, "first rewrite")
    write_file(repo_dir, "keep.txt", b"one\nSECOND\nthree\n")
    commit_all(repo_dir, "second rewrite")
    run_git_sync(repo_dir, "revert", "--no-edit", "HEAD~1", check=False)


def _finish_merge_as_ours_then_bisect(repo_dir: str) -> None:
    """A merge resolved as "ours" and committed, unrelated work on top, then a
    bisect: the merge's resolve-undo record outlives all of it, and its stage
    2 is still ``HEAD``'s blob of ``keep.txt``."""
    finish_merge_as_ours(repo_dir)
    for name in ("later.txt", "latest.txt"):
        write_file(repo_dir, name, b"unrelated\n")
        commit_all(repo_dir, f"adds {name}")
    run_git_sync(repo_dir, "bisect", "start", "HEAD")


_LF_CONFLICT = b"one\n<<<<<<< HEAD\nMAIN\n=======\nOTHER\n>>>>>>> other\n"
_CRLF_CONFLICT = _LF_CONFLICT.replace(b"\n", b"\r\n")


def _stop_merge_at(repo_dir: str, rel: str) -> None:
    """A merge stopped on ``rel`` instead of ``keep.txt``."""
    write_file(repo_dir, rel, b"one\ntwo\nthree\n")
    commit_all(repo_dir, "adds the file")
    run_git_sync(repo_dir, "checkout", "-q", "-b", "other")
    write_file(repo_dir, rel, b"one\nOTHER\nthree\n")
    commit_all(repo_dir, "other side")
    run_git_sync(repo_dir, "checkout", "-q", "main")
    write_file(repo_dir, rel, b"one\nMAIN\nthree\n")
    commit_all(repo_dir, "main side")
    run_git_sync(repo_dir, "merge", "other", check=False)


def _stop_modify_delete_at(repo_dir: str, rel: str, *, ours_deletes: bool) -> None:
    """A merge stopped on ``rel`` because one side deleted it and the other
    rewrote line 2: ``DU`` when ``main`` is the side that deleted, else ``UD``."""
    write_file(repo_dir, rel, b"one\ntwo\nthree\n")
    commit_all(repo_dir, "adds the file")
    full = os.path.join(repo_dir, *rel.split("/"))
    run_git_sync(repo_dir, "checkout", "-q", "-b", "other")
    if ours_deletes:
        write_file(repo_dir, rel, b"one\nOTHER\nthree\n")
    else:
        os.remove(full)
    commit_all(repo_dir, "other side")
    run_git_sync(repo_dir, "checkout", "-q", "main")
    if ours_deletes:
        os.remove(full)
    else:
        write_file(repo_dir, rel, b"one\nMAIN\nthree\n")
    commit_all(repo_dir, "main side")
    run_git_sync(repo_dir, "merge", "other", check=False)


def _stop_rename_rename(repo_dir: str) -> None:
    """Both sides renamed ``keep.txt``, each to its own name: the merge leaves
    ``keep.txt`` with a base stage only (``DD``), ``ours.txt`` with "ours"
    only (``AU``) and ``theirs.txt`` with "theirs" only (``UA``)."""
    run_git_sync(repo_dir, "checkout", "-q", "-b", "other")
    run_git_sync(repo_dir, "mv", "keep.txt", "theirs.txt")
    commit_all(repo_dir, "other renames")
    run_git_sync(repo_dir, "checkout", "-q", "main")
    run_git_sync(repo_dir, "mv", "keep.txt", "ours.txt")
    commit_all(repo_dir, "main renames")
    run_git_sync(repo_dir, "merge", "other", check=False)


def _swap_parent_for_a_symlink(repo_dir: str, parent: str, outside) -> None:
    """``parent`` becomes a symlink to ``outside``, a directory beyond the
    repository holding its own ``a.txt``."""
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"secret\n")
    full = os.path.join(repo_dir, parent)
    if os.path.lexists(full):
        os.rename(full, full + ".real")
    os.symlink(str(outside), full)


def _untracked(repo_dir: str) -> list[str]:
    out = _git_out(repo_dir, "ls-files", "--others", "--exclude-standard")
    return out.splitlines()


def _plant_gitlink_conflict(repo_dir: str, rel: str) -> None:
    """The index of a submodule both sides moved: gitlink entries at stages
    1-3 of ``rel`` and a directory in the worktree."""
    oids = {stage: f"{stage}" * 40 for stage in (1, 2, 3)}
    lines = f"0 {'0' * 40}\t{rel}\n" + "".join(
        f"160000 {oid} {stage}\t{rel}\n" for stage, oid in oids.items()
    )
    subprocess.run(
        [GIT_BIN, "update-index", "--index-info"],
        cwd=repo_dir, check=True, env=_git_env(), input=lines.encode(),
    )
    os.makedirs(os.path.join(repo_dir, rel), exist_ok=True)


async def _status(cwd: str, etag: str | None = None):
    return await get_session_git_status(session_id="s1", cwd=cwd, etag=etag)


async def _get(cwd, path="keep.txt"):
    return await get_session_conflict(cwd=cwd, path=path)


async def _resolve(
    cwd, content, *, path="keep.txt", token=None, mark_resolved=True,
    project_name="proj", event_bus=None,
):
    if token is None:
        token = _sha(_read(cwd, path))
    return await resolve_session_conflict(
        cwd=cwd,
        request=GitConflictResolveRequest(
            path=path, token=token, content=content, mark_resolved=mark_resolved
        ),
        project_name=project_name,
        event_bus=event_bus,
    )


async def _take(cwd, side, *, path="keep.txt", project_name="proj", event_bus=None):
    return await take_session_conflict(
        cwd=cwd,
        request=GitConflictTakeRequest(path=path, side=side),
        project_name=project_name,
        event_bus=event_bus,
    )


async def _restore(
    cwd, *, path="keep.txt", style="merge", token=None, project_name="proj",
    event_bus=None,
):
    return await restore_session_conflict(
        cwd=cwd,
        request=GitConflictRestoreRequest(path=path, style=style, token=token),
        project_name=project_name,
        event_bus=event_bus,
    )


@requires_git
class TestGetConflict:
    async def test_a_rebase_stop_names_both_sides_and_carries_the_file(self, repo):
        _stop_rebase(repo)
        raw = _read(repo, "keep.txt")
        short = _git_out(repo, "rev-parse", "--short", "other")

        resp = await _get(repo)

        assert resp.model_dump() == {
            "path": "keep.txt",
            "code": "UU",
            "state": "rebasing",
            "kind": "text",
            "content": raw.decode(),
            "token": _sha(raw),
            "markers": 1,
            "has_base": False,
            "ours": {"label": "main", "present": True},
            "theirs": {"label": f"{short} other side", "present": True},
            "byte_limit": 256 * 1024,
        }
        assert "<<<<<<< " in resp.content and "\nMAIN\n=======\nOTHER\n" in resp.content

    async def test_a_merge_labels_the_branch_and_the_merged_commit(self, repo):
        _stop_merge(repo)
        short = _git_out(repo, "rev-parse", "--short", "other")

        resp = await _get(repo)

        assert (resp.state, resp.code) == ("merging", "UU")
        assert resp.ours.model_dump() == {"label": "main", "present": True}
        assert resp.theirs.model_dump() == {"label": f"{short} other side", "present": True}

    async def test_a_diff3_file_reports_its_base_section(self, repo):
        run_git_sync(repo, "config", "merge.conflictStyle", "diff3")
        _stop_merge(repo)

        resp = await _get(repo)

        assert (resp.markers, resp.has_base) == (1, True)
        assert "\n||||||| " in resp.content

    async def test_markers_count_every_conflict_region_of_the_file(self, repo):
        _stop_merge(repo)
        write_file(
            repo, "keep.txt",
            b"<<<<<<< a\nx\n=======\ny\n>>>>>>> b\nmid\n<<<<<<<\nz\n=======\n>>>>>>> b\n"
            b"<<<<<<<x not a marker\n",
        )

        resp = await _get(repo)

        assert resp.markers == 2

    async def test_crlf_and_a_bom_are_stripped_from_the_text_but_not_the_token(self, repo):
        _stop_merge(repo)
        raw = b"\xef\xbb\xbfone\r\n<<<<<<< HEAD\r\nMAIN\r\n=======\r\nOTHER\r\n>>>>>>> other\r\n"
        write_file(repo, "keep.txt", raw)

        resp = await _get(repo)

        assert resp.content == "one\n<<<<<<< HEAD\nMAIN\n=======\nOTHER\n>>>>>>> other\n"
        assert resp.token == _sha(raw)
        assert resp.markers == 1

    @pytest.mark.parametrize(
        ("ours_deletes", "code", "present", "content"),
        [
            (True, "DU", (False, True), "one\nOTHER\nthree\n"),
            (False, "UD", (True, False), "one\nMAIN\nthree\n"),
        ],
    )
    async def test_a_deleted_side_is_absent_and_the_surviving_text_is_editable(
        self, repo, ours_deletes, code, present, content
    ):
        make_modify_delete_branches(repo, ours_deletes=ours_deletes)
        run_git_sync(repo, "merge", "other", check=False)

        resp = await _get(repo)

        assert (resp.code, resp.kind, resp.content, resp.markers) == (code, "text", content, 0)
        assert (resp.ours.present, resp.theirs.present) == present

    @pytest.mark.parametrize(
        ("data", "kind"),
        [
            (b"one\n\0two\n", "binary"),
            (b"caf\xe9\n", "encoding"),
            (b"x" * (EDIT_BYTE_LIMIT + 1), "too_large"),
        ],
    )
    async def test_a_file_the_editor_cannot_show_travels_without_content(
        self, repo, data, kind
    ):
        _stop_merge(repo)
        write_file(repo, "keep.txt", data)

        resp = await _get(repo)

        assert (resp.kind, resp.content, resp.token, resp.markers) == (kind, None, None, 0)
        assert (resp.code, resp.ours.present, resp.theirs.present) == ("UU", True, True)

    async def test_a_file_of_exactly_the_limit_is_still_text(self, repo):
        _stop_merge(repo)
        write_file(repo, "keep.txt", b"x" * EDIT_BYTE_LIMIT)

        resp = await _get(repo)

        assert (resp.kind, len(resp.content)) == ("text", EDIT_BYTE_LIMIT)

    async def test_a_file_gone_from_the_worktree_is_missing(self, repo):
        _stop_merge(repo)
        os.remove(os.path.join(repo, "keep.txt"))

        resp = await _get(repo)

        assert (resp.kind, resp.content, resp.token) == ("missing", None, None)

    async def test_a_directory_in_the_files_place_is_unsupported(self, repo):
        _stop_merge(repo)
        os.remove(os.path.join(repo, "keep.txt"))
        write_file(repo, "keep.txt/inner.txt", b"i\n")

        resp = await _get(repo)

        assert (resp.kind, resp.content) == ("unsupported", None)

    @posix_only
    async def test_a_symlink_left_in_the_worktree_is_never_followed(self, repo, tmp_path):
        _stop_merge(repo)
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret\n")
        os.remove(os.path.join(repo, "keep.txt"))
        os.symlink(str(outside), os.path.join(repo, "keep.txt"))

        resp = await _get(repo)

        assert (resp.kind, resp.content, resp.token) == ("symlink", None, None)

    @posix_only
    async def test_a_conflict_between_two_symlinks_is_a_symlink_kind(self, repo):
        run_git_sync(repo, "checkout", "-q", "-b", "other")
        os.symlink("README.md", os.path.join(repo, "link"))
        commit_all(repo, "other link")
        run_git_sync(repo, "checkout", "-q", "main")
        os.symlink("keep.txt", os.path.join(repo, "link"))
        commit_all(repo, "main link")
        run_git_sync(repo, "merge", "other", check=False)

        resp = await _get(repo, "link")

        assert (resp.code, resp.kind, resp.content) == ("AA", "symlink", None)

    async def test_a_submodule_conflict_is_reported_without_reading_anything(self, repo):
        _plant_gitlink_conflict(repo, "vendor")

        resp = await _get(repo, "vendor")

        assert resp.model_dump() == {
            "path": "vendor",
            "code": "UU",
            "state": "clean",
            "kind": "submodule",
            "content": None,
            "token": None,
            "markers": 0,
            "has_base": False,
            "ours": {"label": None, "present": True},
            "theirs": {"label": None, "present": True},
            "byte_limit": 256 * 1024,
        }

    async def test_a_path_that_is_not_in_conflict_is_refused(self, repo):
        _stop_merge(repo)

        with pytest.raises(GitRefusedError) as info:
            await _get(repo, "README.md")

        assert (info.value.code, info.value.message) == ("not_conflicted", "README.md")

    async def test_the_directory_of_a_conflicted_file_is_not_itself_in_conflict(self, repo):
        # ``ls-files -u -- src`` lists ``src/a.txt``: only the exact path counts.
        _stop_merge_at(repo, "src/a.txt")

        with pytest.raises(GitRefusedError) as info:
            await _get(repo, "src")

        assert (info.value.code, info.value.message) == ("not_conflicted", "src")
        assert (await _get(repo, "src/a.txt")).kind == "text"

    @pytest.mark.parametrize(
        "path", [None, "", "../keep.txt", "/etc/passwd", ".git/config", "sub/.GIT/x", ".git"]
    )
    async def test_an_escaping_or_git_dir_path_is_bad_before_the_repo_is_resolved(
        self, tmp_path, path
    ):
        # A vanished cwd would be cwd_missing: the path is refused first.
        with pytest.raises(GitBadRequestError) as info:
            await _get(str(tmp_path / "gone"), path)

        assert info.value.code == "bad_path"

    async def test_outside_a_repo_is_a_bad_request_with_the_reason(self, tmp_path):
        with pytest.raises(GitBadRequestError) as info:
            await _get(str(tmp_path))

        assert info.value.code == "not_a_repo"

    async def test_an_index_listing_that_times_out_is_a_timeout(self, repo, monkeypatch):
        _stop_merge(repo)
        real_run_git = changes_mutations.run_git

        async def hung_listing(args, **kwargs):
            if args[:2] == ["ls-files", "-u"]:
                return CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_mutations, "run_git", hung_listing)

        with pytest.raises(GitTimeoutError):
            await _get(repo)


@requires_git
class TestResolve:
    async def test_apply_writes_the_text_marks_the_path_resolved_and_publishes(self, repo):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        bus = FakeEventBus()

        resp = await _resolve(repo, "one\nRESOLVED\nthree\n", token=token, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert _staged_blob(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert _unmerged(repo) == []
        assert sorted(os.listdir(repo)) == [".git", "README.md", "keep.txt"]
        assert bus.published == [_changed(repo)]

    async def test_an_applied_resolution_lets_the_rebase_continue(self, repo):
        _stop_rebase(repo)

        await _resolve(repo, "one\nRESOLVED\nthree\n")
        run_git_sync(repo, "-c", "core.editor=true", "rebase", "--continue")

        assert (await _status(repo)).repo.state == "clean"
        assert _git_out(repo, "show", "HEAD:keep.txt") == "one\nRESOLVED\nthree"

    async def test_a_draft_is_written_with_its_markers_and_stays_in_conflict(self, repo):
        _stop_rebase(repo)
        bus = FakeEventBus()
        draft = "one\n<<<<<<< main\nhalf done\n=======\nOTHER\n>>>>>>> x\nthree\n"

        resp = await _resolve(repo, draft, mark_resolved=False, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == draft.encode()
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == [_changed(repo)]

    @pytest.mark.parametrize(
        "content",
        [
            "one\n<<<<<<< main\nMAIN\nthree\n",
            "one\n>>>>>>> b2ee9b2 other side\nthree\n",
            "one\r\n<<<<<<<\r\nthree\r\n",
            "<<<<<<<<< longer\n",
        ],
    )
    async def test_a_resolution_still_holding_a_marker_line_is_refused(self, repo, content):
        _stop_rebase(repo)
        before = _read(repo, "keep.txt")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, content, event_bus=bus)

        assert (info.value.code, info.value.message) == ("conflict_markers", "keep.txt")
        assert _read(repo, "keep.txt") == before
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == []

    @pytest.mark.parametrize(
        "content", ["one\n=======\nthree\n", "a <<<<<<< b\n", "<<<<<<<x\n", "<<<<<< six\n"]
    )
    async def test_lines_that_only_resemble_a_marker_are_a_valid_resolution(
        self, repo, content
    ):
        _stop_rebase(repo)

        await _resolve(repo, content)

        assert _read(repo, "keep.txt") == content.encode()
        assert _unmerged(repo) == []

    async def test_a_file_that_moved_on_since_it_was_read_is_refused_untouched(self, repo):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        write_file(repo, "keep.txt", b"one\nCLAUDE EDITED THIS\nthree\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "one\nRESOLVED\nthree\n", token=token, event_bus=bus)

        assert (info.value.code, info.value.message) == ("file_changed", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nCLAUDE EDITED THIS\nthree\n"
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == []

    async def test_a_file_deleted_since_it_was_read_is_refused(self, repo):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        os.remove(os.path.join(repo, "keep.txt"))

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "one\nRESOLVED\nthree\n", token=token)

        assert info.value.code == "file_changed"
        assert not os.path.lexists(os.path.join(repo, "keep.txt"))

    async def test_a_tracked_file_that_is_not_in_conflict_is_never_written(self, repo):
        _stop_rebase(repo)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "pwned\n", path="README.md", event_bus=bus)

        assert (info.value.code, info.value.message) == ("not_conflicted", "README.md")
        assert _read(repo, "README.md") == b"hello\n"
        assert bus.published == []

    @pytest.mark.parametrize("path", [".git/config", "sub/.Git/hooks/pre-commit", "../x"])
    async def test_a_git_dir_or_escaping_path_is_bad_before_anything_runs(
        self, tmp_path, path
    ):
        with pytest.raises(GitBadRequestError) as info:
            await _resolve(str(tmp_path / "gone"), "x\n", path=path, token="t")

        assert info.value.code == "bad_path"

    @pytest.mark.parametrize(
        ("content", "code"),
        [
            ("x" * (EDIT_BYTE_LIMIT + 1), "too_large"),
            # under the limit in characters, over it in UTF-8 bytes
            ("ж" * (EDIT_BYTE_LIMIT // 2 + 1), "too_large"),
            ("one\0two\n", "bad_content"),
            ("lone \ud800 surrogate\n", "bad_content"),
        ],
    )
    async def test_text_the_file_cannot_hold_is_a_bad_request_before_the_repo_is_resolved(
        self, tmp_path, content, code
    ):
        with pytest.raises(GitBadRequestError) as info:
            await _resolve(str(tmp_path / "gone"), content, token="t")

        assert info.value.code == code

    async def test_text_of_exactly_the_limit_is_written(self, repo):
        _stop_rebase(repo)
        content = "ж" * (EDIT_BYTE_LIMIT // 2)

        await _resolve(repo, content)

        assert len(_read(repo, "keep.txt")) == EDIT_BYTE_LIMIT
        assert _unmerged(repo) == []

    async def test_a_crlf_file_with_a_bom_keeps_both_when_lf_text_is_applied(self, repo):
        _stop_merge(repo)
        write_file(
            repo, "keep.txt",
            b"\xef\xbb\xbfone\r\n<<<<<<< HEAD\r\nMAIN\r\n=======\r\nOTHER\r\n>>>>>>> other\r\n",
        )

        await _resolve(repo, "one\nRESOLVED\nthree\n")

        assert _read(repo, "keep.txt") == b"\xef\xbb\xbfone\r\nRESOLVED\r\nthree\r\n"
        assert _staged_blob(repo, "keep.txt") == b"\xef\xbb\xbfone\r\nRESOLVED\r\nthree\r\n"

    async def test_crlf_sent_by_a_client_is_not_doubled_in_a_crlf_file(self, repo):
        _stop_merge(repo)
        write_file(repo, "keep.txt", b"one\r\n<<<<<<< HEAD\r\nMAIN\r\n=======\r\n>>>>>>> o\r\n")

        await _resolve(repo, "one\r\nRESOLVED\r\n")

        assert _read(repo, "keep.txt") == b"one\r\nRESOLVED\r\n"

    async def test_an_lf_file_stays_lf(self, repo):
        _stop_merge(repo)

        await _resolve(repo, "one\r\nRESOLVED\r\nthree\r\n")

        assert _read(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"

    @pytest.mark.parametrize("mark_resolved", [True, False])
    @pytest.mark.parametrize(
        ("original", "content"),
        [
            # 134 bytes as sent, 201 once every line ending is CRLF again
            (_CRLF_CONFLICT, "x\n" * 66 + "abc"),
            # 198 bytes as sent, 201 behind the file's BOM
            (b"\xef\xbb\xbf" + _LF_CONFLICT, "x" * 198),
        ],
        ids=["crlf", "bom"],
    )
    async def test_text_that_outgrows_the_limit_in_the_files_own_encoding_is_refused_unwritten(
        self, repo, monkeypatch, original, content, mark_resolved
    ):
        monkeypatch.setattr(changes_conflicts, "EDIT_BYTE_LIMIT", 200)
        _stop_merge(repo)
        write_file(repo, "keep.txt", original)
        listing = sorted(os.listdir(repo))
        bus = FakeEventBus()

        with pytest.raises(GitBadRequestError) as info:
            await _resolve(repo, content, mark_resolved=mark_resolved, event_bus=bus)

        assert info.value.code == "too_large"
        assert _read(repo, "keep.txt") == original
        assert sorted(os.listdir(repo)) == listing
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == []
        assert (await _get(repo)).kind == "text"

    @pytest.mark.parametrize("mark_resolved", [True, False])
    async def test_text_of_exactly_the_limit_in_the_files_own_encoding_is_written(
        self, repo, monkeypatch, mark_resolved
    ):
        monkeypatch.setattr(changes_conflicts, "EDIT_BYTE_LIMIT", 200)
        _stop_merge(repo)
        write_file(repo, "keep.txt", _CRLF_CONFLICT)

        await _resolve(repo, "x\n" * 66 + "ab", mark_resolved=mark_resolved)

        assert _read(repo, "keep.txt") == b"x\r\n" * 66 + b"ab"
        assert len(_read(repo, "keep.txt")) == 200
        assert _unmerged(repo) == ([] if mark_resolved else ["keep.txt"])

    @posix_only
    async def test_the_files_permission_bits_survive_the_replace(self, repo):
        _stop_merge(repo)
        os.chmod(os.path.join(repo, "keep.txt"), 0o750)

        await _resolve(repo, "one\nRESOLVED\nthree\n")

        mode = os.stat(os.path.join(repo, "keep.txt")).st_mode
        assert stat.S_IMODE(mode) == 0o750

    @posix_only
    async def test_a_symlink_in_the_files_place_is_refused_and_its_target_untouched(
        self, repo, tmp_path
    ):
        _stop_merge(repo)
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret\n")
        os.remove(os.path.join(repo, "keep.txt"))
        os.symlink(str(outside), os.path.join(repo, "keep.txt"))

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "pwned\n", token=_sha(b"secret\n"))

        assert info.value.code == "file_changed"
        assert outside.read_bytes() == b"secret\n"
        assert os.path.islink(os.path.join(repo, "keep.txt"))

    @posix_only
    async def test_a_symlinked_parent_directory_is_refused_and_its_target_untouched(
        self, repo, tmp_path
    ):
        _stop_merge_at(repo, "src/a.txt")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "a.txt").write_bytes(b"secret\n")
        os.rename(os.path.join(repo, "src"), os.path.join(repo, "src.real"))
        os.symlink(str(outside), os.path.join(repo, "src"))

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "pwned\n", path="src/a.txt", token=_sha(b"secret\n"))

        assert info.value.code == "file_changed"
        assert (outside / "a.txt").read_bytes() == b"secret\n"
        assert sorted(os.listdir(outside)) == ["a.txt"]

    @posix_only
    async def test_a_conflict_between_two_symlinks_is_unsupported(self, repo):
        run_git_sync(repo, "checkout", "-q", "-b", "other")
        os.symlink("README.md", os.path.join(repo, "link"))
        commit_all(repo, "other link")
        run_git_sync(repo, "checkout", "-q", "main")
        os.symlink("keep.txt", os.path.join(repo, "link"))
        commit_all(repo, "main link")
        run_git_sync(repo, "merge", "other", check=False)

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "x\n", path="link", token="t")

        assert (info.value.code, info.value.message) == ("unsupported", "link")
        assert _read(repo, "keep.txt") == b"one\ntwo\nthree\n"

    async def test_a_failed_add_after_the_write_still_publishes(self, repo):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        write_file(repo, ".git/index.lock", b"")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "one\nRESOLVED\nthree\n", token=token, event_bus=bus)

        assert info.value.code == "index_locked"
        assert _read(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert bus.published == [_changed(repo)]

    async def test_a_retry_under_the_token_from_before_its_own_write_finishes_the_add(
        self, repo
    ):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        write_file(repo, ".git/index.lock", b"")
        with pytest.raises(GitRefusedError):
            await _resolve(repo, "one\nRESOLVED\nthree\n", token=token)
        os.remove(os.path.join(repo, ".git", "index.lock"))
        bus = FakeEventBus()

        resp = await _resolve(repo, "one\nRESOLVED\nthree\n", token=token, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert _staged_blob(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert _unmerged(repo) == []
        assert sorted(os.listdir(repo)) == [".git", "README.md", "keep.txt"]
        assert bus.published == [_changed(repo)]

    async def test_a_draft_sent_again_under_its_first_token_is_accepted(self, repo):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        draft = "one\n<<<<<<< main\nhalf done\n=======\nOTHER\n>>>>>>> x\nthree\n"
        await _resolve(repo, draft, token=token, mark_resolved=False)

        resp = await _resolve(repo, draft, token=token, mark_resolved=False)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == draft.encode()
        assert _unmerged(repo) == ["keep.txt"]

    async def test_a_retry_with_other_text_than_its_own_write_is_refused_untouched(
        self, repo
    ):
        _stop_rebase(repo)
        token = (await _get(repo)).token
        write_file(repo, ".git/index.lock", b"")
        with pytest.raises(GitRefusedError):
            await _resolve(repo, "one\nRESOLVED\nthree\n", token=token)
        os.remove(os.path.join(repo, ".git", "index.lock"))
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "one\nSECOND THOUGHTS\nthree\n", token=token, event_bus=bus)

        assert (info.value.code, info.value.message) == ("file_changed", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nRESOLVED\nthree\n"
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == []

    async def test_a_retry_of_lf_text_matches_the_crlf_bytes_its_first_write_left(self, repo):
        _stop_merge(repo)
        write_file(
            repo, "keep.txt",
            b"\xef\xbb\xbfone\r\n<<<<<<< HEAD\r\nMAIN\r\n=======\r\nOTHER\r\n>>>>>>> other\r\n",
        )
        token = (await _get(repo)).token
        write_file(repo, ".git/index.lock", b"")
        with pytest.raises(GitRefusedError):
            await _resolve(repo, "one\nRESOLVED\nthree\n", token=token)
        os.remove(os.path.join(repo, ".git", "index.lock"))

        resp = await _resolve(repo, "one\nRESOLVED\nthree\n", token=token)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == b"\xef\xbb\xbfone\r\nRESOLVED\r\nthree\r\n"
        assert _staged_blob(repo, "keep.txt") == b"\xef\xbb\xbfone\r\nRESOLVED\r\nthree\r\n"
        assert _unmerged(repo) == []

    async def test_outside_a_repo_is_refused(self, tmp_path):
        with pytest.raises(GitRefusedError) as info:
            await _resolve(str(tmp_path), "x\n", token="t")

        assert info.value.code == "not_a_repo"


@requires_git
class TestTake:
    @pytest.mark.parametrize(
        ("side", "content"),
        [("ours", b"one\nMAIN\nthree\n"), ("theirs", b"one\nOTHER\nthree\n")],
    )
    async def test_a_present_side_replaces_the_file_and_resolves_it(
        self, repo, side, content
    ):
        _stop_rebase(repo)
        bus = FakeEventBus()

        resp = await _take(repo, side, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == content
        assert _staged_blob(repo, "keep.txt") == content
        assert _unmerged(repo) == []
        assert bus.published == [_changed(repo)]

    @pytest.mark.parametrize(
        ("ours_deletes", "side"), [(True, "ours"), (False, "theirs")]
    )
    async def test_the_side_that_deleted_the_file_resolves_by_removing_it(
        self, repo, ours_deletes, side
    ):
        make_modify_delete_branches(repo, ours_deletes=ours_deletes)
        run_git_sync(repo, "merge", "other", check=False)
        bus = FakeEventBus()

        resp = await _take(repo, side, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert not os.path.lexists(os.path.join(repo, "keep.txt"))
        assert _git_out(repo, "ls-files", "--", "keep.txt") == ""
        assert _unmerged(repo) == []
        assert bus.published == [_changed(repo)]

    @pytest.mark.parametrize(
        ("ours_deletes", "side", "content"),
        [
            (True, "theirs", b"one\nOTHER\nthree\n"),
            (False, "ours", b"one\nMAIN\nthree\n"),
        ],
    )
    async def test_the_side_that_kept_the_file_resolves_by_keeping_it(
        self, repo, ours_deletes, side, content
    ):
        make_modify_delete_branches(repo, ours_deletes=ours_deletes)
        run_git_sync(repo, "merge", "other", check=False)

        await _take(repo, side)

        assert _read(repo, "keep.txt") == content
        assert _staged_blob(repo, "keep.txt") == content
        assert _unmerged(repo) == []

    @pytest.mark.parametrize(
        ("ours_deletes", "side"), [(True, "ours"), (False, "theirs")]
    )
    async def test_a_nested_file_is_removed_leaving_nothing_untracked_and_a_way_back(
        self, repo, ours_deletes, side
    ):
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=ours_deletes)
        bus = FakeEventBus()

        resp = await _take(repo, side, path="src/a.txt", event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert not os.path.lexists(os.path.join(repo, "src", "a.txt"))
        assert _git_out(repo, "ls-files", "--", "src/a.txt") == ""
        assert _unmerged(repo) == []
        assert _untracked(repo) == []
        assert "\tsrc/a.txt" in _git_out(repo, "ls-files", "--resolve-undo")
        assert bus.published == [_changed(repo)]

    @pytest.mark.parametrize("gone", ["file", "directory"])
    async def test_a_file_already_gone_from_the_worktree_still_resolves_by_removal(
        self, repo, gone
    ):
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=False)
        os.remove(os.path.join(repo, "src", "a.txt"))
        if gone == "directory":
            os.rmdir(os.path.join(repo, "src"))
        bus = FakeEventBus()

        resp = await _take(repo, "theirs", path="src/a.txt", event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _git_out(repo, "ls-files", "--", "src/a.txt") == ""
        assert _unmerged(repo) == []
        assert bus.published == [_changed(repo)]

    @pytest.mark.parametrize(
        ("path", "side", "left"),
        [
            ("keep.txt", "ours", ["ours.txt", "theirs.txt"]),
            ("keep.txt", "theirs", ["ours.txt", "theirs.txt"]),
            ("ours.txt", "theirs", ["theirs.txt"]),
            ("theirs.txt", "ours", ["ours.txt"]),
        ],
    )
    async def test_a_path_the_side_never_had_resolves_by_removal(
        self, repo, path, side, left
    ):
        _stop_rename_rename(repo)
        others = sorted({"keep.txt", "ours.txt", "theirs.txt"} - {path})

        resp = await _take(repo, side, path=path)

        assert resp.model_dump() == {"ok": True}
        assert _git_out(repo, "ls-files", "--", path) == ""
        assert _unmerged(repo) == others
        assert sorted(os.listdir(repo)) == [".git", "README.md", *left]

    @posix_only
    @pytest.mark.parametrize("ours_deletes", [True, False])
    @pytest.mark.parametrize("side", ["ours", "theirs"])
    async def test_a_symlinked_parent_directory_is_refused_and_its_target_untouched(
        self, repo, tmp_path, ours_deletes, side
    ):
        # ``git rm`` unlinks by path, through the symlink and out of the repo.
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=ours_deletes)
        outside = tmp_path / "outside"
        _swap_parent_for_a_symlink(repo, "src", outside)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, side, path="src/a.txt", event_bus=bus)

        assert (info.value.code, info.value.message) == ("unsupported", "src/a.txt")
        assert (outside / "a.txt").read_bytes() == b"secret\n"
        assert sorted(os.listdir(outside)) == ["a.txt"]
        assert os.path.islink(os.path.join(repo, "src"))
        assert _unmerged(repo) == ["src/a.txt"]
        assert bus.published == []

    @pytest.mark.parametrize("side", ["ours", "theirs"])
    async def test_a_file_in_a_parent_directorys_place_is_refused_and_kept(
        self, repo, side
    ):
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=False)
        os.remove(os.path.join(repo, "src", "a.txt"))
        os.rmdir(os.path.join(repo, "src"))
        write_file(repo, "src", b"not a directory\n")

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, side, path="src/a.txt")

        assert (info.value.code, info.value.message) == ("unsupported", "src/a.txt")
        assert _read(repo, "src") == b"not a directory\n"
        assert _unmerged(repo) == ["src/a.txt"]

    async def test_a_file_that_cannot_be_unlinked_is_busy_and_stays_in_conflict(
        self, repo, monkeypatch
    ):
        make_modify_delete_branches(repo, ours_deletes=True)
        run_git_sync(repo, "merge", "other", check=False)

        def held_open(_self, _name):
            raise PermissionError(13, "The process cannot access the file")

        monkeypatch.setattr(changes_conflicts._Target, "unlink", held_open)
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, "ours", event_bus=bus)

        assert (info.value.code, info.value.message) == ("file_busy", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nOTHER\nthree\n"
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == [_changed(repo)]

    async def test_a_file_the_filesystem_will_not_remove_is_a_git_error_and_stays_in_conflict(
        self, repo, monkeypatch
    ):
        make_modify_delete_branches(repo, ours_deletes=True)
        run_git_sync(repo, "merge", "other", check=False)

        def read_only(_self, _name):
            raise OSError(errno.EROFS, "Read-only file system")

        monkeypatch.setattr(changes_conflicts._Target, "unlink", read_only)
        bus = FakeEventBus()

        with pytest.raises(GitCommandError) as info:
            await _take(repo, "ours", event_bus=bus)

        assert info.value.message == "could not remove keep.txt: Read-only file system"
        assert _read(repo, "keep.txt") == b"one\nOTHER\nthree\n"
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == [_changed(repo)]

    @posix_only
    @pytest.mark.parametrize("mode", [0o311, 0o000, 0o200])
    @pytest.mark.parametrize("side", ["ours", "theirs"])
    async def test_a_file_in_a_directory_the_agent_cannot_open_is_refused_and_kept(
        self, repo, side, mode
    ):
        # Mode 0o311 lets the path resolve but not the directory open; without
        # the search bit (0o000, 0o200) even ``lstat`` of the file is denied.
        # Either way the no-follow walk gives up on a file that is still there.
        if os.geteuid() == 0:
            pytest.skip("root opens directories of any mode")
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=True)
        parent = os.path.join(repo, "src")
        os.chmod(parent, mode)
        bus = FakeEventBus()

        try:
            with pytest.raises(GitRefusedError) as info:
                await _take(repo, side, path="src/a.txt", event_bus=bus)
        finally:
            # the file cannot be read back while its parent is unsearchable
            os.chmod(parent, 0o755)

        assert (info.value.code, info.value.message) == ("unsupported", "src/a.txt")
        assert _read(repo, "src/a.txt") == b"one\nOTHER\nthree\n"
        assert _unmerged(repo) == ["src/a.txt"]
        assert _untracked(repo) == []
        assert bus.published == []

    async def test_a_removal_the_walk_cannot_reach_never_leaves_the_file_behind_silently(
        self, repo, monkeypatch
    ):
        # The parent swapped after the guard looked: nothing to unlink through,
        # yet the file is there by path.
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=True)

        @contextlib.contextmanager
        def refused(_toplevel, _rel):
            yield None

        monkeypatch.setattr(changes_conflicts, "_open_target", refused)

        with pytest.raises(GitRefusedError) as info:
            changes_conflicts._remove_worktree_file(repo, "src/a.txt")

        assert (info.value.code, info.value.message) == ("unsupported", "src/a.txt")
        assert _read(repo, "src/a.txt") == b"one\nOTHER\nthree\n"

    async def test_a_submodule_is_unsupported_and_left_in_conflict(self, repo):
        _plant_gitlink_conflict(repo, "vendor")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, "ours", path="vendor", event_bus=bus)

        assert (info.value.code, info.value.message) == ("unsupported", "vendor")
        assert _unmerged(repo) == ["vendor"]
        assert bus.published == []

    async def test_a_directory_in_the_files_place_is_unsupported_and_kept(self, repo):
        _stop_merge(repo)
        os.remove(os.path.join(repo, "keep.txt"))
        write_file(repo, "keep.txt/inner.txt", b"i\n")

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, "theirs")

        assert info.value.code == "unsupported"
        assert _read(repo, "keep.txt/inner.txt") == b"i\n"
        assert _unmerged(repo) == ["keep.txt"]

    async def test_a_path_that_is_not_in_conflict_is_refused_untouched(self, repo):
        _stop_merge(repo)
        write_file(repo, "README.md", b"unsaved work\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, "theirs", path="README.md", event_bus=bus)

        assert (info.value.code, info.value.message) == ("not_conflicted", "README.md")
        assert _read(repo, "README.md") == b"unsaved work\n"
        assert bus.published == []

    async def test_a_git_dir_path_is_bad(self, tmp_path):
        with pytest.raises(GitBadRequestError) as info:
            await _take(str(tmp_path / "gone"), "ours", path=".git/HEAD")

        assert info.value.code == "bad_path"


@requires_git
class TestRestore:
    async def test_a_resolved_path_goes_back_into_conflict_with_the_sides_named(self, repo):
        _stop_rebase(repo)
        short = _git_out(repo, "rev-parse", "--short", "other")
        await _resolve(repo, "one\nRESOLVED\nthree\n")
        bus = FakeEventBus()

        resp = await _restore(repo, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == (
            f"one\n<<<<<<< main\nMAIN\n=======\nOTHER\n>>>>>>> {short} other side\nthree\n"
        ).encode()
        assert _unmerged(repo) == ["keep.txt"]
        status = await _status(repo)
        assert [(e.group, e.path) for e in status.entries] == [("conflicts", "keep.txt")]
        assert status.resolved_paths == []
        assert bus.published == [_changed(repo)]

    async def test_diff3_brings_the_original_lines_whatever_the_configured_style(self, repo):
        _stop_rebase(repo)
        await _resolve(repo, "one\nRESOLVED\nthree\n")

        await _restore(repo, style="diff3")

        assert b"\n||||||| base\ntwo\n=======\n" in _read(repo, "keep.txt")
        assert (await _get(repo)).has_base is True

    async def test_merge_style_drops_the_original_lines_a_diff3_config_would_add(self, repo):
        run_git_sync(repo, "config", "merge.conflictStyle", "diff3")
        _stop_rebase(repo)

        await _restore(repo, style="merge")

        assert b"|||||||" not in _read(repo, "keep.txt")
        assert (await _get(repo)).has_base is False

    async def test_starting_over_discards_a_draft_of_a_file_still_in_conflict(self, repo):
        _stop_merge(repo)
        await _resolve(repo, "half done\n", mark_resolved=False)
        token = _sha(b"half done\n")

        await _restore(repo, token=token)

        restored = _read(repo, "keep.txt")
        assert restored.startswith(b"one\n<<<<<<< main\nMAIN\n=======\nOTHER\n>>>>>>> ")
        assert _unmerged(repo) == ["keep.txt"]

    @pytest.mark.parametrize("seam", ["operation_state", "_side_labels", "_relabel_markers"])
    async def test_a_failure_naming_the_sides_leaves_the_rebuilt_conflict_a_success(
        self, repo, monkeypatch, seam
    ):
        _stop_rebase(repo)
        await _resolve(repo, "half done\n", mark_resolved=False)

        def boom(*_args, **_kwargs):
            raise RuntimeError("labels unavailable")

        monkeypatch.setattr(changes_conflicts, seam, boom)
        bus = FakeEventBus()

        resp = await _restore(repo, event_bus=bus)

        assert resp.model_dump() == {"ok": True}
        assert _read(repo, "keep.txt") == (
            b"one\n<<<<<<< ours\nMAIN\n=======\nOTHER\n>>>>>>> theirs\nthree\n"
        )
        assert _unmerged(repo) == ["keep.txt"]
        assert bus.published == [_changed(repo)]

    async def test_a_checkout_that_times_out_is_a_timeout_and_still_publishes(
        self, repo, monkeypatch
    ):
        _stop_merge(repo)
        real_run_git = changes_mutations.run_git

        async def hung_checkout(args, **kwargs):
            if args[0] == "checkout":
                return CmdResult(rc=-9, stdout=b"", stderr=b"", timed_out=True)
            return await real_run_git(args, **kwargs)

        monkeypatch.setattr(changes_mutations, "run_git", hung_checkout)
        bus = FakeEventBus()

        with pytest.raises(GitTimeoutError):
            await _restore(repo, event_bus=bus)

        assert bus.published == [_changed(repo)]

    async def test_a_token_from_before_someone_elses_edit_is_refused_untouched(self, repo):
        _stop_merge(repo)
        token = (await _get(repo)).token
        write_file(repo, "keep.txt", b"one\nCLAUDE FIXED IT\nthree\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, token=token, event_bus=bus)

        assert (info.value.code, info.value.message) == ("file_changed", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nCLAUDE FIXED IT\nthree\n"
        assert bus.published == []

    async def test_a_modified_file_that_was_never_in_conflict_keeps_its_edits(self, repo):
        # ``checkout --merge`` on such a path overwrites it from the index.
        _stop_rebase(repo)
        write_file(repo, "README.md", b"unsaved work\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, path="README.md", event_bus=bus)

        assert (info.value.code, info.value.message) == ("not_restorable", "README.md")
        assert _read(repo, "README.md") == b"unsaved work\n"
        assert bus.published == []

    async def test_a_record_left_by_a_finished_merge_restores_nothing(self, repo):
        # Resolved as "ours", so the record's stage 2 is still HEAD's blob:
        # only the finished operation tells this record from a live one.
        _stop_merge(repo)
        await _take(repo, "ours")
        run_git_sync(repo, "commit", "-q", "--no-edit")
        assert "keep.txt" in _git_out(repo, "ls-files", "--resolve-undo")
        write_file(repo, "keep.txt", b"one\nNEW WORK\nthree\n")

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo)

        assert info.value.code == "not_restorable"
        assert _read(repo, "keep.txt") == b"one\nNEW WORK\nthree\n"
        assert _unmerged(repo) == []

    async def test_a_record_left_by_a_finished_merge_restores_nothing_during_a_bisect(
        self, repo
    ):
        # The repository is not clean, and the record's stage 2 is still HEAD's
        # blob: only the kind of operation keeps ``checkout --merge`` off the file.
        _finish_merge_as_ours_then_bisect(repo)
        write_file(repo, "keep.txt", b"one\nNEW WORK\nthree\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, event_bus=bus)

        status = await _status(repo)
        assert "keep.txt" in _git_out(repo, "ls-files", "--resolve-undo")
        assert (status.repo.state, status.resolved_paths) == ("bisecting", [])
        assert (info.value.code, info.value.message) == ("not_restorable", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nNEW WORK\nthree\n"
        assert _unmerged(repo) == []
        assert bus.published == []

    async def test_a_record_left_by_a_finished_merge_restores_nothing_during_a_git_am(
        self, repo, tmp_path
    ):
        # ``rebasing`` is a state that conflicts, yet a ``git am`` session never
        # rewrote the index: the record is the finished merge's, and its stage 2
        # is still HEAD's blob.
        start_failing_am_after_a_merge_as_ours(repo, str(tmp_path / "patches"))
        write_file(repo, "keep.txt", b"one\nNEW WORK\nthree\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, event_bus=bus)

        status = await _status(repo)
        assert os.path.exists(os.path.join(repo, ".git", "rebase-apply", "applying"))
        assert "keep.txt" in _git_out(repo, "ls-files", "--resolve-undo")
        assert (status.repo.state, status.repo.rebase) == ("rebasing", None)
        assert status.resolved_paths == []
        assert (info.value.code, info.value.message) == ("not_restorable", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nNEW WORK\nthree\n"
        assert _unmerged(repo) == []
        assert bus.published == []

    async def test_a_record_of_a_pick_already_committed_restores_nothing_at_an_exec_stop(
        self, repo
    ):
        # The rebase is still running, but on a failed ``exec``: no pick is
        # stopped on (no REBASE_HEAD), and the record is the committed pick's,
        # its stage 2 still HEAD's blob.
        stop_rebase_on_exec_after_a_pick_as_ours(repo)
        write_file(repo, "keep.txt", b"one\nNEW WORK\nthree\n")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, event_bus=bus)

        status = await _status(repo)
        rebase_head = run_git_sync(
            repo, "rev-parse", "-q", "--verify", "REBASE_HEAD", check=False
        )
        assert rebase_head.returncode == 1
        assert "keep.txt" in _git_out(repo, "ls-files", "--resolve-undo")
        assert (status.repo.state, status.repo.rebase.stopped_on) == ("rebasing", None)
        assert status.resolved_paths == []
        assert (info.value.code, info.value.message) == ("not_restorable", "keep.txt")
        assert _read(repo, "keep.txt") == b"one\nNEW WORK\nthree\n"
        assert _unmerged(repo) == []
        assert bus.published == []

    @pytest.mark.parametrize(
        ("state", "stop"),
        [
            ("merging", _stop_merge),
            ("rebasing", _stop_rebase),
            ("cherry_picking", _stop_cherry_pick),
            ("reverting", _stop_revert),
        ],
    )
    async def test_a_path_resolved_in_any_operation_that_conflicts_is_listed_and_rebuilt(
        self, repo, state, stop
    ):
        stop(repo)
        await _resolve(repo, "one\nRESOLVED\nthree\n")
        listed = await _status(repo)

        await _restore(repo)

        assert (listed.repo.state, listed.resolved_paths) == (state, ["keep.txt"])
        assert _read(repo, "keep.txt").count(b"\n<<<<<<< ") == 1
        assert _unmerged(repo) == ["keep.txt"]
        assert (await _status(repo)).resolved_paths == []

    async def test_a_conflict_with_a_deleted_side_cannot_be_rebuilt(self, repo):
        make_modify_delete_branches(repo, ours_deletes=True)
        run_git_sync(repo, "merge", "other", check=False)
        write_file(repo, "keep.txt", b"edited\n")

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo)

        assert info.value.code == "not_restorable"
        assert _read(repo, "keep.txt") == b"edited\n"

    async def test_a_submodule_conflict_cannot_be_rebuilt(self, repo):
        _plant_gitlink_conflict(repo, "vendor")

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, path="vendor")

        assert info.value.code == "not_restorable"

    async def test_a_resolved_submodule_conflict_cannot_be_rebuilt(self, repo, tmp_path):
        # Resolved as "ours", the record's stage 2 is HEAD's gitlink: only its
        # mode keeps ``checkout --merge`` off an object that is no blob.
        make_submodule_conflict(repo, str(tmp_path))
        run_git_sync(repo, "add", "sub")
        staged = _git_out(repo, "ls-files", "--stage", "--", "sub")
        bus = FakeEventBus()

        with pytest.raises(GitRefusedError) as info:
            await _restore(repo, path="sub", event_bus=bus)

        assert "160000" in _git_out(repo, "ls-files", "--resolve-undo")
        assert (info.value.code, info.value.message) == ("not_restorable", "sub")
        assert _git_out(repo, "ls-files", "--stage", "--", "sub") == staged
        assert _unmerged(repo) == []
        assert bus.published == []

    async def test_a_git_dir_path_is_bad(self, tmp_path):
        with pytest.raises(GitBadRequestError) as info:
            await _restore(str(tmp_path / "gone"), path="a/.git/index")

        assert info.value.code == "bad_path"


class TestUnwalkable:
    """What is left at a path the no-follow walk could not reach."""

    def test_a_file_that_is_there_is_unwalkable(self, tmp_path):
        top = str(tmp_path)
        write_file(top, "src/a.txt", b"one\n")

        assert changes_conflicts._unwalkable(top, "src/a.txt") is True

    def test_a_missing_file_is_nothing_left_behind(self, tmp_path):
        top = str(tmp_path)
        write_file(top, "src/other.txt", b"one\n")

        assert changes_conflicts._unwalkable(top, "src/a.txt") is False

    def test_a_missing_parent_directory_is_nothing_left_behind(self, tmp_path):
        assert changes_conflicts._unwalkable(str(tmp_path), "src/deep/a.txt") is False

    def test_a_file_in_a_parent_directorys_place_is_nothing_left_behind(self, tmp_path):
        top = str(tmp_path)
        write_file(top, "src", b"not a directory\n")

        assert changes_conflicts._unwalkable(top, "src/a.txt") is False

    def test_a_path_the_agent_may_not_look_at_is_never_read_as_missing(
        self, tmp_path, monkeypatch
    ):
        # What ``lstat`` answers under a parent without search permission:
        # ``os.path.lexists`` turns that into "nothing there".
        top = str(tmp_path)
        write_file(top, "src/a.txt", b"one\n")

        def denied(path, *_args, **_kwargs):
            raise PermissionError(errno.EACCES, "Permission denied", path)

        monkeypatch.setattr(changes_conflicts.os, "lstat", denied)

        assert changes_conflicts._unwalkable(top, "src/a.txt") is True


class TestDirFdGate:
    def test_the_descriptor_walk_is_on_everywhere_but_windows(self):
        # ``os.replace`` is never in ``os.supports_dir_fd``: a gate asking for
        # it closes on every platform and nothing else notices.
        assert changes_conflicts._DIR_FD is (sys.platform != "win32")


@requires_git
class TestCheckedPathFallback:
    """The branch Windows takes, forced on whatever platform runs the suite."""

    @pytest.fixture(autouse=True)
    def _no_dir_fd(self, monkeypatch):
        monkeypatch.setattr(changes_conflicts, "_DIR_FD", False)

    async def test_a_nested_file_is_read_resolved_and_rebuilt(self, repo):
        _stop_merge_at(repo, "src/deep/a.txt")

        shown = await _get(repo, "src/deep/a.txt")
        await _resolve(
            repo, "one\nRESOLVED\nthree\n", path="src/deep/a.txt", token=shown.token
        )
        resolved = _read(repo, "src/deep/a.txt")
        await _restore(repo, path="src/deep/a.txt")

        assert (shown.kind, shown.markers) == ("text", 1)
        assert resolved == b"one\nRESOLVED\nthree\n"
        assert _read(repo, "src/deep/a.txt").startswith(
            b"one\n<<<<<<< main\nMAIN\n=======\nOTHER\n>>>>>>> "
        )
        assert sorted(os.listdir(os.path.join(repo, "src", "deep"))) == ["a.txt"]

    async def test_a_file_whose_directory_is_gone_is_missing(self, repo):
        _stop_merge_at(repo, "src/a.txt")
        os.remove(os.path.join(repo, "src", "a.txt"))
        os.rmdir(os.path.join(repo, "src"))

        resp = await _get(repo, "src/a.txt")

        assert (resp.kind, resp.content) == ("missing", None)

    @posix_only
    async def test_a_symlink_in_the_files_place_is_refused_and_its_target_untouched(
        self, repo, tmp_path
    ):
        _stop_merge(repo)
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret\n")
        os.remove(os.path.join(repo, "keep.txt"))
        os.symlink(str(outside), os.path.join(repo, "keep.txt"))

        shown = await _get(repo)
        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "pwned\n", token=_sha(b"secret\n"))

        assert (shown.kind, shown.content) == ("symlink", None)
        assert info.value.code == "file_changed"
        assert outside.read_bytes() == b"secret\n"
        assert os.path.islink(os.path.join(repo, "keep.txt"))

    @posix_only
    async def test_a_symlinked_parent_directory_is_refused_and_its_target_untouched(
        self, repo, tmp_path
    ):
        _stop_merge_at(repo, "src/a.txt")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "a.txt").write_bytes(b"secret\n")
        os.rename(os.path.join(repo, "src"), os.path.join(repo, "src.real"))
        os.symlink(str(outside), os.path.join(repo, "src"))

        shown = await _get(repo, "src/a.txt")
        with pytest.raises(GitRefusedError) as info:
            await _resolve(repo, "pwned\n", path="src/a.txt", token=_sha(b"secret\n"))

        assert (shown.kind, shown.content) == ("missing", None)
        assert info.value.code == "file_changed"
        assert (outside / "a.txt").read_bytes() == b"secret\n"
        assert sorted(os.listdir(outside)) == ["a.txt"]

    @pytest.mark.parametrize("gone", ["nothing", "file", "directory"])
    async def test_the_side_that_deleted_a_nested_file_resolves_by_removing_it(
        self, repo, gone
    ):
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=True)
        if gone != "nothing":
            os.remove(os.path.join(repo, "src", "a.txt"))
        if gone == "directory":
            os.rmdir(os.path.join(repo, "src"))

        resp = await _take(repo, "ours", path="src/a.txt")

        assert resp.model_dump() == {"ok": True}
        assert not os.path.lexists(os.path.join(repo, "src", "a.txt"))
        assert _git_out(repo, "ls-files", "--", "src/a.txt") == ""
        assert _unmerged(repo) == []
        assert _untracked(repo) == []

    @posix_only
    @pytest.mark.parametrize("side", ["ours", "theirs"])
    async def test_a_take_through_a_symlinked_parent_is_refused_and_its_target_untouched(
        self, repo, tmp_path, side
    ):
        _stop_modify_delete_at(repo, "src/a.txt", ours_deletes=True)
        outside = tmp_path / "outside"
        _swap_parent_for_a_symlink(repo, "src", outside)

        with pytest.raises(GitRefusedError) as info:
            await _take(repo, side, path="src/a.txt")

        assert (info.value.code, info.value.message) == ("unsupported", "src/a.txt")
        assert (outside / "a.txt").read_bytes() == b"secret\n"
        assert sorted(os.listdir(outside)) == ["a.txt"]
        assert _unmerged(repo) == ["src/a.txt"]
