"""Signing in to a chat agent from the chat itself.

ACP agents report a missing or expired login as ``authRequired`` (and
claude-agent-acp also pushes ``_auth/status_update``), but their own sign-in
methods either open a browser on the PC or expect the client to run a login
command in a terminal. A phone has neither, so the PC agent runs the agent's
own login command in a hidden PTY and shows what the user needs in a
``sign_in`` request card: the sign-in page, a one-time code to enter there, or
a field for the code the page shows. Whatever the command prints is parsed
only for the page address and that code; its exit code decides the outcome.
"""
from __future__ import annotations

import asyncio
import codecs
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mobaide.core.pty_backend import PtyBackend, create_pty_backend

logger = logging.getLogger(__name__)

# A device code expires after 15 minutes; a login still running later is stuck.
LOGIN_TIMEOUT = 20 * 60.0
OUTPUT_MAX = 32_000
# Wide, so the sign-in address is never wrapped onto two lines.
PTY_ROWS = 50
PTY_COLS = 2000
# Output that follows a pasted code without ending the login is its answer
# (a wrong code); wait this long for the rest of it.
SUBMIT_SETTLE = 1.5
INPUT_MAX = 4096
ERROR_LINES = 3
ERROR_LINE_MAX = 200

_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*([@-~])")
# Cursor moves to another line (ConPTY draws new lines this way).
_CSI_LINE_MOVES = frozenset("HfEFd")
_ESC = re.compile(r"\x1b[ -/]+[0-~]|\x1b[@-Z\\-_]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# A pasted code is one line of plain text; control bytes would reach the
# login's terminal as signals or extra lines.
_INPUT_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_URL = re.compile(r"https://[^\s<>\"'`\x00-\x1f\x7f]+")


def terminal_text(raw: str) -> str:
    """The text a terminal would show, without escape sequences.

    An OSC 8 hyperlink keeps only its visible text, which carries the same
    address."""
    text = _OSC.sub("", raw)
    text = _CSI.sub(lambda m: "\n" if m.group(1) in _CSI_LINE_MOVES else "", text)
    text = _ESC.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text)


def find_url(text: str) -> str | None:
    """The first complete ``https`` address: one that more output follows,
    so an address cut at the end of a read is never taken."""
    for match in _URL.finditer(text):
        if match.end() < len(text):
            return match.group(0).rstrip(".,;:)]}")
    return None


def last_lines(
    text: str,
    *,
    count: int = ERROR_LINES,
    prompt: str = "",
    typed: str = "",
    url: str = "",
) -> str:
    """The last lines worth showing: not the sign-in address, the prompt
    (with what the user typed after it) or the echo of what they typed."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or (typed and line == typed):
            continue
        if (prompt and line.startswith(prompt)) or (url and url in line):
            continue
        lines.append(line[:ERROR_LINE_MAX])
    return "\n".join(lines[-count:])


@dataclass(frozen=True)
class SignInMethod:
    """One way to sign in, as the request card offers it.

    A terminal login has ``argv``: the command runs in a PTY until it exits.
    A key login has ``submit_secret`` instead: the value the user types goes
    to it, never to a command line.
    """

    id: str
    name: str
    description: str
    # What the user does once the sign-in page is known.
    instructions: str
    argv: list[str] | None = None
    env: dict[str, str] | None = field(default=None, repr=False)
    cwd: str | None = None
    # The command waits for the code that the sign-in page shows.
    reads_code: bool = False
    input_label: str = "Code from the sign-in page"
    # The one-time code the command prints for the user to enter on the page.
    user_code_pattern: str | None = None
    submit_secret: Callable[[str], Awaitable[tuple[bool, str]]] | None = field(
        default=None, compare=False
    )


class SignInError(Exception):
    """A sign-in command that cannot be applied; the text goes to the client."""


class LoginProcess:
    """One login command in a PTY. Output and the exit code reach the event
    loop through ``call_soon_threadsafe``."""

    def __init__(
        self,
        method: SignInMethod,
        *,
        loop: asyncio.AbstractEventLoop,
        on_output: Callable[[str], None],
        on_exit: Callable[[int | None], None],
        backend_factory: Callable[[], PtyBackend] = create_pty_backend,
    ) -> None:
        assert method.argv
        self._method = method
        self._loop = loop
        self._on_output = on_output
        self._on_exit = on_exit
        self._backend = backend_factory()
        self._stopped = False
        self._exit_lock = threading.Lock()
        self._exit_sent = False
        self._read_done = threading.Event()

    def start(self) -> None:
        """Blocking: spawns the command, its reader and its exit waiter."""
        self._backend.spawn(
            list(self._method.argv or []), self._method.cwd, self._method.env, PTY_ROWS, PTY_COLS
        )
        if self._stopped:
            # Stopped while it spawned: nothing else would end it.
            self._backend.terminate(force=True)
        threading.Thread(target=self._read_loop, name="acp-sign-in", daemon=True).start()
        threading.Thread(target=self._wait_loop, name="acp-sign-in-exit", daemon=True).start()

    def write_line(self, text: str) -> None:
        self._backend.write(text.encode("utf-8") + b"\r")

    def stop(self) -> None:
        self._stopped = True
        # Off the event loop: ConPTY's terminate sleeps between its signals.
        try:
            self._loop.run_in_executor(None, self._backend.terminate, True)
        except RuntimeError:
            pass

    def _read_loop(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            try:
                data = self._backend.read(4096)
            except Exception:
                data = b""
            if not data:
                break
            self._deliver(self._on_output, decoder.decode(data))
        self._read_done.set()
        self._finish(self._backend.wait_exit(5.0))
        try:
            self._backend.close()
        except Exception:
            logger.debug("sign_in.close_failed", exc_info=True)

    def _wait_loop(self) -> None:
        """The exit, even when the PTY stays open: a browser the login
        started can inherit it, and then the reader never sees EOF."""
        code: int | None = None
        while not self._stopped and not self._read_done.is_set():
            code = self._backend.wait_exit(1.0)
            if code is not None:
                break
        if code is None:
            return
        # Let the reader drain the last output first.
        if not self._read_done.wait(0.5):
            self._finish(code)

    def _finish(self, code: int | None) -> None:
        with self._exit_lock:
            if self._exit_sent:
                return
            self._exit_sent = True
        if code is None:
            self._backend.terminate(force=True)
        if not self._stopped:
            self._deliver(self._on_exit, code)

    def _deliver(self, callback: Callable[[Any], None], value: Any) -> None:
        try:
            self._loop.call_soon_threadsafe(callback, value)
        except RuntimeError:
            pass


class SignInFlow:
    """The ``sign_in`` request of one chat session.

    ``choose`` lists the methods (and why the last try failed); ``waiting``
    runs one of them. ``public`` is the request as clients see it, or None.
    """

    def __init__(
        self,
        methods: list[SignInMethod],
        agent_title: str,
        *,
        request_id: str,
        on_change: Callable[[], None],
        on_signed_in: Callable[[], None],
        backend_factory: Callable[[], PtyBackend] = create_pty_backend,
    ) -> None:
        self._methods = {m.id: m for m in methods}
        self._agent_title = agent_title
        self._request_id = request_id
        self._on_change = on_change
        self._on_signed_in = on_signed_in
        self._backend_factory = backend_factory
        self.public: dict[str, Any] | None = None
        self._method: SignInMethod | None = None
        self._proc: LoginProcess | None = None
        self._output = ""
        self._submitted_at: int | None = None
        # Where the answer to the last code starts (kept after it settled).
        self._answer_from = 0
        # The last line before the code went in (the prompt), and the code.
        self._prompt = ""
        self._typed = ""
        self._timeout: asyncio.TimerHandle | None = None
        self._settle: asyncio.TimerHandle | None = None
        self._secret_task: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return bool(self._methods)

    @property
    def running(self) -> bool:
        return self.public is not None and self.public["stage"] == "waiting"

    def offer(self, error: str | None = None) -> None:
        """Show the methods, unless a sign-in already runs."""
        if not self._methods or self.running:
            return
        self._show("choose", error=error)

    def withdraw(self) -> None:
        """The agent is signed in after all: drop an offer nobody acted on.
        A running login stays."""
        if self.public is not None and self.public["stage"] == "choose":
            self.public = None
            self._on_change()

    async def open(self) -> None:
        """The user asked to sign in: start the only method, or let them pick."""
        if not self._methods or self.running:
            return
        if len(self._methods) == 1:
            await self.start(next(iter(self._methods)))
        else:
            self._show("choose")

    async def start(self, method_id: Any) -> None:
        method = self._methods.get(method_id) if isinstance(method_id, str) else None
        if method is None:
            raise SignInError("Unknown sign-in method")
        self._stop()
        self._method = method
        if method.submit_secret is not None:
            self._show("waiting", text=method.instructions, input_secret=True)
            return
        # The clients show their own "Starting" status line while busy.
        self._show("waiting", text="", busy=True)
        loop = asyncio.get_running_loop()
        proc = LoginProcess(
            method,
            loop=loop,
            on_output=lambda chunk: self._on_output(proc, chunk),
            on_exit=lambda code: self._on_exit(proc, code),
            backend_factory=self._backend_factory,
        )
        self._proc = proc
        try:
            # The default executor: a login's I/O must never hold up the
            # probe pool that every chat shares.
            await loop.run_in_executor(None, proc.start)
        except Exception as exc:
            logger.warning("sign_in.start_failed method=%s: %s", method.id, exc)
            if self._proc is proc:
                self._proc = None
                self._show("choose", error=f"The sign-in could not start: {exc}")
            return
        if self._proc is not proc:
            # Cancelled while it started.
            proc.stop()
            return
        self._timeout = loop.call_later(LOGIN_TIMEOUT, self._on_timeout, proc)

    async def submit(self, value: Any) -> None:
        method, public = self._method, self.public
        if method is None or public is None or public.get("input") is None:
            raise SignInError("Nothing to submit")
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            raise SignInError("Enter the code first")
        if len(text) > INPUT_MAX:
            raise SignInError("The code is too long")
        if _INPUT_CONTROL.search(text):
            raise SignInError("The code can only be one line of plain text")
        if public.get("busy"):
            raise SignInError("The sign-in is still checking the last code")
        if method.submit_secret is not None:
            self._update(busy=True, error=None)
            task = asyncio.create_task(self._submit_secret(method, text))
            self._secret_task = task
            return
        proc = self._proc
        if proc is None:
            raise SignInError("The sign-in is not running")
        self._prompt = last_lines(terminal_text(self._output), count=1)
        self._typed = text
        self._submitted_at = self._answer_from = len(self._output)
        self._update(busy=True, error=None)
        await asyncio.get_running_loop().run_in_executor(None, proc.write_line, text)

    def cancel(self) -> None:
        self._stop()
        if self.public is not None:
            self.public = None
            self._on_change()

    def close(self) -> None:
        self._stop()
        self.public = None

    def _stop(self) -> None:
        for handle in (self._timeout, self._settle):
            if handle is not None:
                handle.cancel()
        self._timeout = self._settle = None
        if self._secret_task is not None and not self._secret_task.done():
            self._secret_task.cancel()
        self._secret_task = None
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.stop()
        self._method = None
        self._output = ""
        self._submitted_at = None
        self._answer_from = 0
        self._prompt = self._typed = ""

    def _show(
        self,
        stage: str,
        *,
        text: str | None = None,
        error: str | None = None,
        busy: bool = False,
        input_secret: bool = False,
    ) -> None:
        method = self._method
        self.public = {
            "id": self._request_id,
            "kind": "sign_in",
            "tool_call_id": None,
            "title": f"Sign in to {self._agent_title}",
            "text": text
            if text is not None
            else f"{self._agent_title} is not signed in on your PC. Sign in here to continue.",
            "stage": stage,
            "methods": [
                {"id": m.id, "name": m.name, "description": m.description}
                for m in self._methods.values()
            ],
            "url": None,
            "user_code": None,
            "input": (
                {"label": method.input_label, "secret": True}
                if input_secret and method is not None
                else None
            ),
            "busy": busy,
            "error": error,
        }
        self._on_change()

    def _update(self, **changes: Any) -> None:
        if self.public is None:
            return
        changed = {k: v for k, v in changes.items() if self.public.get(k) != v}
        if changed:
            self.public = {**self.public, **changed}
            self._on_change()

    def _on_output(self, proc: LoginProcess, chunk: str) -> None:
        if proc is not self._proc or self._method is None or not chunk:
            return
        before = len(self._output)
        self._output = (self._output + chunk)[-OUTPUT_MAX:]
        # Trimming moved the text: keep the marks on the same output.
        dropped = before + len(chunk) - len(self._output)
        self._answer_from = max(0, self._answer_from - dropped)
        if self._submitted_at is not None:
            self._submitted_at = max(0, self._submitted_at - dropped)
        public = self.public
        if public is None:
            return
        if public["url"] is None:
            text = terminal_text(self._output)
            url = find_url(text)
            if url is None:
                return
            method = self._method
            user_code = None
            if method.user_code_pattern:
                match = re.search(method.user_code_pattern, text)
                user_code = match.group(0) if match else None
                if user_code is None:
                    # The code follows the address; wait for it.
                    return
            self._update(
                url=url,
                user_code=user_code,
                text=method.instructions,
                input={"label": method.input_label, "secret": False} if method.reads_code else None,
                # Nothing to type: the command finishes once the page is done.
                busy=not method.reads_code,
            )
            return
        if self._submitted_at is not None and self._reply(self._output[self._submitted_at:]):
            if self._settle is not None:
                self._settle.cancel()
            self._settle = asyncio.get_running_loop().call_later(
                SUBMIT_SETTLE, self._on_settled, proc
            )

    def _on_settled(self, proc: LoginProcess) -> None:
        """The login answered a pasted code and still runs: it wants another."""
        self._settle = None
        if proc is not self._proc or self._submitted_at is None:
            return
        reply = self._reply(self._output[self._submitted_at:])
        self._submitted_at = None
        self._update(busy=False, error=reply or "That code did not work. Try again.")

    def _on_exit(self, proc: LoginProcess, code: int | None) -> None:
        if proc is not self._proc:
            return
        self._proc = None
        detail = self._reply(self._output[self._answer_from:])
        method = self._method
        self._stop()
        if code == 0:
            self._succeed()
            return
        logger.info(
            "sign_in.failed method=%s code=%s", method.id if method else None, code
        )
        error = "The sign-in did not finish."
        if detail:
            error = f"{error}\n{detail}"
        self._show("choose", error=error)

    def _reply(self, raw: str) -> str:
        url = self.public.get("url") if self.public is not None else None
        return last_lines(terminal_text(raw), prompt=self._prompt, typed=self._typed, url=url or "")

    def _on_timeout(self, proc: LoginProcess) -> None:
        if proc is not self._proc:
            return
        self._stop()
        self._show("choose", error="The sign-in took too long, so it was stopped. Try again.")

    async def _submit_secret(self, method: SignInMethod, value: str) -> None:
        assert method.submit_secret is not None
        try:
            ok, detail = await method.submit_secret(value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("sign_in.secret_failed method=%s: %s", method.id, exc)
            ok, detail = False, str(exc)
        if self._method is not method:
            return
        self._secret_task = None
        if ok:
            self._stop()
            self._succeed()
            return
        self._update(busy=False, error=detail or "The sign-in failed. Check the value and try again.")

    def _succeed(self) -> None:
        self.public = None
        self._on_change()
        self._on_signed_in()
