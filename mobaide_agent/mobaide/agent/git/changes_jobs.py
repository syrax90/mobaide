"""Session-scoped git network jobs — fetch, pull, push — shared by the HTTP
router and the relay dispatcher.

A job outlives the request that started it: the route answers with the
job's first snapshot and the work runs in a background task that publishes
``git.job.progress`` (throttled, full snapshots) and exactly one
``git.job.done`` on the agent events channel, then ``git.changed``. One job
per repository toplevel; a write arriving meanwhile is refused with
``job_running`` rather than queued behind the job's 120 s budget.

Concurrency: the tracker and the publisher are touched only from the event
loop — the runner's stderr reader thread hands chunks over with
``call_soon_threadsafe``. Cancellation kills the whole git process tree
through the runner's :class:`ProcessHandle`; a job still waiting for its
repository lock notices the flag at its next checkpoint.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from mobaide.core.git_exec import ProcessHandle, run_git

from .changes_models import (
    BYTE_LIMIT,
    JOB_LINE_LIMIT,
    JOB_MESSAGE_LIMIT,
    GitJobOp,
    GitJobResponse,
    GitPullMode,
)
from .changes_service import (
    _QUICK_TIMEOUT,
    _SMALL_OUTPUT,
    GitNotFoundError,
    GitRefusedError,
    GitTimeoutError,
    ResolvedRepo,
    _check_result,
    _lines,
    _lock_for,
    _mutable_repo,
    _text,
)
from .service import _publish_git_changed

logger = logging.getLogger(__name__)

JOB_TIMEOUT = 120.0
MIN_PROGRESS_INTERVAL = 0.25
FINISHED_TTL = 600.0
MAX_FINISHED = 100
_TAIL_LINES = 50
# stderr is streamed, not capped, so a hook or sideband message that never
# ends its line must not grow the pending tail without bound
_PARTIAL_CAP = 4096
_CANCEL_SETTLE = 3.0
_SHUTDOWN_TIMEOUT = 10.0
# Credential helpers and ssh-agent keep working; everything that would wait
# on a person at the host is switched off, so a job can only succeed or
# fail fast — never hang on a prompt nobody can see from the phone.
_JOB_ENV = {
    "GIT_ASKPASS": "",
    "SSH_ASKPASS_REQUIRE": "never",
    "GCM_INTERACTIVE": "never",
}

_REMOTE_PREFIX = "remote: "
_PROGRESS_RE = re.compile(
    r"^(?P<phase>[A-Za-z][A-Za-z ]*?):\s+(?P<pct>\d{1,3})% \(\d+/\d+\)"
)
_COUNT_RE = re.compile(r"^(?P<phase>[A-Za-z][A-Za-z ]*?): \d+(?P<done>, done\.)?$")
_REBASE_RE = re.compile(r"^Rebasing \((?P<cur>\d+)/(?P<tot>\d+)\)")
_LINE_SPLIT_RE = re.compile(rb"\r\n|\r|\n")
_ERROR_LINE_RE = re.compile(r"^(fatal|error|ERROR|CONFLICT|remote: (error|fatal))\b|^\s*! ")

# First match wins; the push-side "failed to push some refs" without a
# rejection line is a local hook saying no (git prints nothing else for it).
_FAILURE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "auth_required",
        (
            "could not read Username",
            "could not read Password",
            "terminal prompts disabled",
            "Authentication failed",
            "Permission denied (publickey",
            "Permission denied, please try again",
            "Host key verification failed",
            "Invalid username or",
            "Permission to ",
            "returned error: 401",
            "returned error: 403",
        ),
    ),
    (
        "network",
        (
            "Could not resolve host",
            "unable to access",
            "Connection refused",
            "Connection timed out",
            "Operation timed out",
            "Network is unreachable",
            "Could not connect",
            "ssh: connect to host",
            "The remote end hung up unexpectedly",
        ),
    ),
    (
        "remote_not_found",
        (
            "Repository not found",
            "Could not read from remote repository",
            "does not appear to be a git repository",
        ),
    ),
    # A push the remote refused for being behind gets the same code as the
    # ff-only pull that could not advance: the remedy is the same pull.
    ("not_fast_forward", ("Not possible to fast-forward", "(fetch first)", "(non-fast-forward)")),
    ("conflicts", ("CONFLICT (", "Automatic merge failed", "could not apply")),
    ("dirty_worktree", ("would be overwritten by", "You have unstaged changes", "Your index contains uncommitted")),
    ("unmerged", ("unmerged files", "Pulling is not possible")),
    (
        "operation_in_progress",
        ("rebase in progress", "MERGE_HEAD exists", "not concluded your merge", "is in progress"),
    ),
    ("no_upstream", ("has no upstream branch", "no tracking information", "no upstream configured")),
    ("no_remote", ("No configured push destination", "No remote repository specified")),
    ("detached_head", ("not currently on a branch",)),
    ("no_head", ("src refspec", "does not have any commits yet")),
    ("index_locked", ("index.lock",)),
    ("hook_failed", ("hook declined", "pre-push hook")),
    ("rejected", ("[rejected]", "[remote rejected]", "(stale info)")),
    ("hook_failed", ("failed to push some refs",)),
)
_SUCCESS_FALLBACK = {"fetch": "Fetched", "pull": "Pulled", "push": "Pushed"}


class ProgressTracker:
    """Turns git's ``--progress`` stderr into ``phase``/``percent``/``line``
    plus a tail of the ordinary lines for the failure classifier and the
    success summary — the runner's retained stderr is capped and progress
    output alone can exceed the cap."""

    def __init__(self) -> None:
        self.phase: str | None = None
        self.percent: int | None = None
        self.line: str | None = None
        self.tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self._partial = b""

    def feed(self, chunk: bytes) -> bool:
        """Consume a stderr chunk; ``True`` when the visible state changed."""
        before = (self.phase, self.percent, self.line)
        parts = _LINE_SPLIT_RE.split(self._partial + chunk)
        self._partial = parts.pop()[:_PARTIAL_CAP]
        for raw in parts:
            self._consume(raw)
        # git rewrites a progress line in place, so the unterminated tail is
        # usually the freshest counter — show it, but never file it in the
        # tail until its terminator proves it complete
        pending = self._display_text(self._partial)
        if pending:
            self._apply_progress(pending)
        return (self.phase, self.percent, self.line) != before

    def flush(self) -> None:
        """Promote a trailing unterminated line once the process is gone."""
        if self._partial:
            self._consume(self._partial)
            self._partial = b""

    @staticmethod
    def _display_text(raw: bytes) -> str:
        text = _text(raw).rstrip()
        if text.startswith(_REMOTE_PREFIX):
            text = text[len(_REMOTE_PREFIX):]
        return text

    def _apply_progress(self, text: str) -> bool | None:
        """``True``/``False`` when ``text`` is a progress line (with or
        without a percentage), ``None`` when it is ordinary output."""
        match = _PROGRESS_RE.match(text)
        if match is not None:
            self.phase = match.group("phase")
            self.percent = min(100, int(match.group("pct")))
            self.line = text[:JOB_LINE_LIMIT]
            return True
        match = _REBASE_RE.match(text)
        if match is not None:
            total = max(1, int(match.group("tot")))
            self.phase = "Rebasing"
            self.percent = min(100, int(match.group("cur")) * 100 // total)
            self.line = text[:JOB_LINE_LIMIT]
            return True
        match = _COUNT_RE.match(text)
        if match is not None:
            self.phase = match.group("phase")
            self.percent = 100 if match.group("done") else None
            self.line = text[:JOB_LINE_LIMIT]
            return False
        return None

    def _consume(self, raw: bytes) -> None:
        text = self._display_text(raw)
        if not text:
            return
        if self._apply_progress(text) is None:
            self.tail.append(_text(raw).rstrip()[:JOB_MESSAGE_LIMIT])
            self.line = text[:JOB_LINE_LIMIT]


def _squash(line: str) -> str:
    return " ".join(line.split())


def classify_job_failure(
    op: GitJobOp,
    *,
    err_tail: Sequence[str],
    out_tail: Sequence[str],
) -> tuple[str, str]:
    """Map a failed job's output to ``(error_code, message)``; the message
    is git's own explanation (its error lines, else the last line)."""
    haystack = "\n".join([*out_tail, *err_tail])
    code = "git_error"
    for candidate, needles in _FAILURE_RULES:
        if any(needle in haystack for needle in needles):
            code = candidate
            break
    explained = [
        _squash(line)
        for line in [*out_tail, *err_tail]
        if _ERROR_LINE_RE.match(line)
    ]
    if not explained:
        plain = [
            _squash(line)
            for line in [*err_tail, *out_tail]
            if line.strip() and not line.startswith("hint:")
        ]
        explained = plain[-1:]
    message = "\n".join(explained) or f"git {op} failed"
    return code, message[:JOB_MESSAGE_LIMIT]


def summarise_success(
    op: GitJobOp, *, err_tail: Sequence[str], out_tail: Sequence[str]
) -> str:
    """One line for the result notice: the ref update for fetch/push, the
    merge outcome for pull, a per-op word when git said nothing."""
    if op == "pull":
        candidates = [line for line in out_tail if line.strip()] or [
            line for line in err_tail if line.strip() and not line.startswith("hint:")
        ]
        return _squash(candidates[-1])[:JOB_MESSAGE_LIMIT] if candidates else _SUCCESS_FALLBACK[op]
    refs = [line for line in err_tail if " -> " in line]
    if refs:
        return _squash(refs[-1])[:JOB_MESSAGE_LIMIT]
    for line in reversed(err_tail):
        if "up-to-date" in line or "up to date" in line:
            return _squash(line)
    return _SUCCESS_FALLBACK[op] if op == "push" else "Already up to date"


class _JobFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message[:JOB_MESSAGE_LIMIT]


@dataclass
class GitJob:
    job_id: str
    session_id: str
    op: GitJobOp
    repo: ResolvedRepo
    project_name: str
    event_bus: Any | None
    pull_mode: GitPullMode = "ff_only"
    force: bool = False
    handle: ProcessHandle = field(default_factory=ProcessHandle)
    tracker: ProgressTracker = field(default_factory=ProgressTracker)
    status: str = "running"
    ok: bool | None = None
    error_code: str | None = None
    message: str | None = None
    task: asyncio.Task[None] | None = None
    finished_at: float | None = None
    _timer: asyncio.TimerHandle | None = None
    _pending: bool = False
    _done_published: bool = False

    @property
    def running(self) -> bool:
        return self.status == "running"

    def snapshot(self) -> GitJobResponse:
        return GitJobResponse(
            job_id=self.job_id,
            session_id=self.session_id,
            op=self.op,
            status="running" if self.running else "done",
            phase=self.tracker.phase,
            percent=self.tracker.percent,
            line=self.tracker.line,
            ok=self.ok,
            error_code=self.error_code,
            message=self.message,
        )

    def stderr_callback(self, loop: asyncio.AbstractEventLoop) -> Callable[[bytes], None]:
        def on_chunk(chunk: bytes) -> None:
            try:
                loop.call_soon_threadsafe(self.feed, chunk)
            except RuntimeError:
                pass

        return on_chunk

    def feed(self, chunk: bytes) -> None:
        if self.tracker.feed(chunk):
            self.touch()

    def checkpoint(self) -> None:
        if self.handle.cancelled:
            raise _JobFailure("cancelled", "cancelled")

    # Throttle: a change publishes at once when no cooldown is running,
    # otherwise the cooldown's tick publishes the latest state — at most one
    # frame per MIN_PROGRESS_INTERVAL, the newest counters always winning.
    def touch(self) -> None:
        if not self.running:
            return
        if self._timer is None:
            self._publish("git.job.progress")
            self._arm()
        else:
            self._pending = True

    def _arm(self) -> None:
        loop = asyncio.get_running_loop()
        self._timer = loop.call_later(MIN_PROGRESS_INTERVAL, self._tick)

    def _tick(self) -> None:
        self._timer = None
        if self._pending and self.running:
            self._pending = False
            self._publish("git.job.progress")
            self._arm()

    def finish(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._pending = False
        if self._done_published:
            return
        self._done_published = True
        self._publish("git.job.done")
        _publish_git_changed(
            self.event_bus,
            self.project_name,
            toplevel=self.repo.toplevel,
            common_dir=self.repo.common_dir,
        )

    def _publish(self, event_type: str) -> None:
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(event_type, self.snapshot().model_dump(mode="json"))
        except Exception:
            logger.exception("git.job.publish_failed")


class GitJobRegistry:
    """All jobs the agent knows: the running one per toplevel plus finished
    ones kept for ``FINISHED_TTL`` so a client can reconcile after a missed
    ``git.job.done``."""

    def __init__(self) -> None:
        self._jobs: dict[str, GitJob] = {}
        self._active: dict[str, str] = {}

    def reset(self) -> None:
        self._jobs.clear()
        self._active.clear()

    def active_job_id(self, toplevel: str) -> str | None:
        return self._active.get(os.path.normcase(toplevel))

    async def start(
        self,
        op: GitJobOp,
        *,
        cwd: str | None,
        session_id: str,
        project_name: str | None,
        event_bus: Any | None,
        pull_mode: GitPullMode = "ff_only",
        force: bool = False,
    ) -> GitJobResponse:
        repo = await _mutable_repo(cwd)
        key = os.path.normcase(repo.toplevel)
        active = self._active.get(key)
        if active is not None:
            raise GitRefusedError("job_running", active)
        self._prune()
        job = GitJob(
            job_id=secrets.token_hex(16),
            session_id=session_id,
            op=op,
            repo=repo,
            project_name=project_name or "",
            event_bus=event_bus,
            pull_mode=pull_mode,
            force=force,
        )
        self._jobs[job.job_id] = job
        self._active[key] = job.job_id
        job.touch()
        # A fresh context keeps the request's log fields (request id, path)
        # out of every line the job logs after the response went out.
        job.task = asyncio.create_task(
            self._run(job, key),
            name=f"git-job[{op}:{job.job_id[:8]}]",
            context=contextvars.Context(),
        )
        return job.snapshot()

    def get(self, job_id: str) -> GitJobResponse:
        return self._lookup(job_id).snapshot()

    async def cancel(self, job_id: str) -> GitJobResponse:
        job = self._lookup(job_id)
        if job.running:
            await asyncio.to_thread(job.handle.cancel)
            if job.task is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(job.task), _CANCEL_SETTLE)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
        return job.snapshot()

    async def shutdown(self) -> None:
        running = [job for job in self._jobs.values() if job.running]
        for job in running:
            await asyncio.to_thread(job.handle.cancel)
        tasks = [job.task for job in running if job.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=_SHUTDOWN_TIMEOUT)

    def _lookup(self, job_id: str) -> GitJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise GitNotFoundError("job_not_found")
        return job

    def _prune(self) -> None:
        now = time.monotonic()
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda job: job.finished_at or 0.0,
        )
        stale = [job for job in finished if now - (job.finished_at or 0.0) > FINISHED_TTL]
        overflow = max(0, len(finished) - len(stale) - MAX_FINISHED)
        for job in stale + [job for job in finished if job not in stale][:overflow]:
            self._jobs.pop(job.job_id, None)

    async def _run(self, job: GitJob, key: str) -> None:
        try:
            async with _lock_for(job.repo.toplevel):
                await self._execute(job)
        except _JobFailure as exc:
            job.ok, job.error_code, job.message = False, exc.code, exc.message
        except GitTimeoutError:
            # A pre-step past _QUICK_TIMEOUT is the same outcome as the network
            # command past JOB_TIMEOUT, only sooner.
            job.ok, job.error_code = False, "git_timeout"
            job.message = f"git {job.op} preflight exceeded {_QUICK_TIMEOUT:.0f} s"
        except asyncio.CancelledError:
            job.ok, job.error_code, job.message = False, "cancelled", "agent shutting down"
        except Exception as exc:
            logger.exception("git.job.failed op=%s", job.op)
            job.ok, job.error_code = False, "git_error"
            job.message = (str(exc) or f"git {job.op} failed")[:JOB_MESSAGE_LIMIT]
        finally:
            job.tracker.flush()
            job.status = "done"
            job.finished_at = time.monotonic()
            if self._active.get(key) == job.job_id:
                del self._active[key]
            job.finish()

    async def _execute(self, job: GitJob) -> None:
        toplevel = job.repo.toplevel
        job.checkpoint()
        branch: str | None = None
        if job.op != "fetch":
            branch = await self._current_branch(toplevel)
            if branch is None:
                raise _JobFailure("detached_head", "HEAD is detached")
            if not await self._has_head(toplevel):
                raise _JobFailure("no_head", "the branch has no commits yet")
        remotes = await self._remotes(toplevel)
        if not remotes:
            raise _JobFailure("no_remote", "no remote is configured")
        # Remote and branch names come from the repository, so they sit after
        # the end-of-options marker: a branch named ``--mirror`` must never
        # become a flag.
        if job.op == "fetch":
            remote = await self._remote_for(toplevel, "upstream", remotes)
            argv = ["fetch", "--progress", "--prune", "--", remote]
        elif job.op == "pull":
            upstream_error = await self._upstream_error(toplevel)
            if upstream_error is not None:
                raise _JobFailure("no_upstream", upstream_error)
            modes = {
                "ff_only": ["--ff-only"],
                "rebase": ["--rebase"],
                "merge": ["--no-rebase", "--no-edit"],
            }
            argv = ["pull", "--progress", "--no-stat", *modes[job.pull_mode]]
        else:
            argv = ["push", "--progress"]
            if job.force:
                argv.append("--force-with-lease")
            if await self._upstream_error(toplevel) is not None:
                configured = await self._configured_upstream(toplevel, str(branch), remotes)
                if configured is None:
                    remote = await self._remote_for(toplevel, "push", remotes)
                    argv += ["--set-upstream", "--", remote, _short_branch(str(branch))]
                else:
                    # The upstream is configured but its tracking ref is gone
                    # (the remote branch was deleted, then pruned): recreate
                    # that branch, as the Sync row promises, rather than
                    # publish a new one and rewrite branch.<name>.merge.
                    remote, merge_ref = configured
                    argv += ["--", remote, f"{branch}:{merge_ref}"]
        job.checkpoint()
        result = await run_git(
            argv,
            cwd=toplevel,
            timeout=JOB_TIMEOUT,
            max_bytes=BYTE_LIMIT,
            env_extra=_JOB_ENV,
            handle=job.handle,
            on_stderr=job.stderr_callback(asyncio.get_running_loop()),
        )
        job.tracker.flush()
        # A cancel that lands after git already exited killed nothing, and the
        # remote or worktree may have moved; a killed process never exits 0
        # (killpg gives a negative rc, taskkill 1), so rc 0 means git finished
        # and the real outcome is reported.
        if job.handle.cancelled and result.rc != 0:
            raise _JobFailure("cancelled", "cancelled")
        if result.timed_out:
            raise _JobFailure("git_timeout", f"git {job.op} exceeded {JOB_TIMEOUT:.0f} s")
        out_tail = _lines(result.stdout)[-_TAIL_LINES:]
        err_tail = list(job.tracker.tail)
        if result.rc != 0:
            code, message = classify_job_failure(job.op, err_tail=err_tail, out_tail=out_tail)
            raise _JobFailure(code, message)
        job.ok = True
        job.message = summarise_success(job.op, err_tail=err_tail, out_tail=out_tail)

    async def _quick(self, toplevel: str, args: Sequence[str]) -> tuple[int, str, str]:
        result = await run_git(
            list(args), cwd=toplevel, timeout=_QUICK_TIMEOUT, max_bytes=_SMALL_OUTPUT
        )
        _check_result(result)
        return result.rc, _text(result.stdout), _text(result.stderr)

    async def _current_branch(self, toplevel: str) -> str | None:
        """The full ref HEAD points at (``refs/heads/<name>``), ``None`` when
        detached. Never ``--short``: a tag of the same name shortens it to
        ``heads/<name>``, which no ref lookup or refspec would resolve."""
        rc, out, err = await self._quick(toplevel, ["symbolic-ref", "-q", "HEAD"])
        if rc == 0:
            return out.strip("\n") or None
        if rc == 1:
            return None
        raise _JobFailure("git_error", err.strip() or "git symbolic-ref failed")

    async def _has_head(self, toplevel: str) -> bool:
        rc, _, _ = await self._quick(toplevel, ["rev-parse", "-q", "--verify", "HEAD"])
        return rc == 0

    async def _remotes(self, toplevel: str) -> list[str]:
        rc, out, err = await self._quick(toplevel, ["remote"])
        if rc != 0:
            raise _JobFailure("git_error", err.strip() or "git remote failed")
        return [line for line in out.split("\n") if line]

    async def _upstream_error(self, toplevel: str) -> str | None:
        """``None`` when ``@{upstream}`` resolves, else git's reason."""
        rc, _, err = await self._quick(
            toplevel, ["rev-parse", "-q", "--abbrev-ref", "@{upstream}"]
        )
        if rc == 0:
            return None
        return _squash(err) or "the branch has no upstream"

    async def _configured_upstream(
        self, toplevel: str, ref: str, remotes: Sequence[str]
    ) -> tuple[str, str] | None:
        """``(remote, merge ref)`` from ``branch.<name>.remote`` / ``.merge``
        when both are set and the remote is listed — resolved from the
        configuration alone, so unlike ``@{upstream}`` it survives a pruned
        remote-tracking ref."""
        rc, out, _ = await self._quick(
            toplevel,
            [
                "for-each-ref",
                "--format=%(upstream:remotename)%0a%(upstream:remoteref)",
                ref,
            ],
        )
        if rc != 0:
            return None
        remote, _, merge_ref = out.strip("\n").partition("\n")
        if remote in remotes and merge_ref.startswith("refs/"):
            return remote, merge_ref
        return None

    async def _remote_for(self, toplevel: str, kind: str, remotes: Sequence[str]) -> str:
        """The remote git itself would use (``%(upstream:remotename)`` /
        ``%(push:remotename)`` honour ``branch.<name>.remote``,
        ``branch.<name>.pushRemote`` and ``remote.pushDefault``), validated
        against the configured remotes; ``origin``, then the only remote,
        then the first listed one otherwise."""
        preferred = ""
        rc, out, _ = await self._quick(toplevel, ["symbolic-ref", "-q", "HEAD"])
        ref = out.strip()
        if rc == 0 and ref:
            rc, out, _ = await self._quick(
                toplevel, ["for-each-ref", f"--format=%({kind}:remotename)", ref]
            )
            preferred = out.strip() if rc == 0 else ""
        if preferred in remotes:
            return preferred
        if "origin" in remotes:
            return "origin"
        return remotes[0]


def _short_branch(ref: str) -> str:
    return ref.removeprefix("refs/heads/")


job_registry = GitJobRegistry()
