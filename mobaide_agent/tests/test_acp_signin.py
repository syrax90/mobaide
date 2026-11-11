"""The ``sign_in`` request of a chat session (``mobaide/acp/signin.py``): the
terminal text helpers, then ``SignInFlow`` driving a login command that the
test plays through ``LoginPty`` (its output, what the user typed into it, its
exit code) or a key login through ``submit_secret``."""
from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any

import pytest

from mobaide.acp import signin
from mobaide.acp.signin import (
    LoginProcess,
    SignInError,
    SignInFlow,
    SignInMethod,
    find_url,
    last_lines,
    terminal_text,
)

pytestmark = pytest.mark.timeout(30)

URL = "https://example.com/sign-in?state=abc"
PROMPT = "Paste code here if prompted > "


class TestTerminalText:
    def test_an_osc_8_hyperlink_keeps_only_its_visible_text(self):
        raw = f"Visit: \x1b]8;;{URL}\x07{URL}\x1b]8;;\x07 now"

        assert terminal_text(raw) == f"Visit: {URL} now"

    def test_colors_go_and_cursor_moves_to_another_line_break_the_line(self):
        raw = "\x1b[1;32mDone\x1b[0m\x1b[2;1HNext\x1b[K"

        assert terminal_text(raw) == "Done\nNext"

    def test_carriage_returns_become_line_breaks(self):
        assert terminal_text("one\r\ntwo\rthree\x07") == "one\ntwo\nthree"

    def test_a_character_set_switch_after_an_address_goes_too(self):
        text = terminal_text("visit https://example.com/a\x1b(B\x1b[m now\n")

        assert text == "visit https://example.com/a now\n"
        assert find_url(text) == "https://example.com/a"


class TestFindUrl:
    def test_the_first_complete_address(self):
        text = f"Open {URL}\nor https://example.com/other\n"

        assert find_url(text) == URL

    def test_an_address_at_the_end_of_the_output_may_be_cut(self):
        assert find_url(f"Open {URL}") is None

    def test_closing_punctuation_is_not_part_of_the_address(self):
        assert find_url(f"(see {URL}).\n") == URL

    def test_plain_http_is_never_taken(self):
        assert find_url("Open http://example.com/sign-in\n") is None


class TestLastLines:
    def test_keeps_the_last_lines_without_blanks(self):
        assert last_lines("a\n\nb\n  c  \nd\n", count=2) == "c\nd"

    def test_drops_the_prompt_the_echo_and_the_address(self):
        text = f"Visit {URL}\n{PROMPT}abc\nabc\nInvalid code.\n{PROMPT}"

        result = last_lines(text, prompt=PROMPT.strip(), typed="abc", url=URL)

        assert result == "Invalid code."

    def test_long_lines_are_cut(self):
        assert len(last_lines("x" * 500)) == signin.ERROR_LINE_MAX


class LoginPty:
    """A login command the test plays: ``say`` is its output, ``exit`` ends
    it with a code. What the user submits arrives in ``typed``."""

    def __init__(self, spawn_error: Exception | None = None) -> None:
        self.spawn_error = spawn_error
        self.spawned: list[tuple[list[str], str | None, int, int]] = []
        self.typed: list[bytes] = []
        self.terminated = False
        self._code: int | None = None
        self._exited = threading.Event()
        self._output: queue.Queue[bytes] = queue.Queue()

    def say(self, text: str) -> None:
        self._output.put(text.encode("utf-8"))

    def exit(self, code: int, *, eof: bool = True) -> None:
        """Without ``eof`` the PTY stays open (a browser the login started
        inherited it), so a read never ends."""
        self._code = code
        self._exited.set()
        if eof:
            self._output.put(b"")

    def spawn(self, cmd, cwd, env, rows, cols) -> None:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append((cmd, cwd, rows, cols))

    def read(self, size: int = 4096) -> bytes:
        return self._output.get(timeout=20)

    def write(self, data: bytes) -> None:
        self.typed.append(data)

    def resize(self, rows: int, cols: int) -> None:
        return None

    def is_alive(self) -> bool:
        return self._code is None and not self.terminated

    def get_pid(self) -> int | None:
        return None

    def wait_exit(self, timeout: float) -> int | None:
        self._exited.wait(timeout)
        return self._code

    def terminate(self, force: bool = False) -> None:
        self.terminated = True
        if self._code is None:
            self._code = -15
        self._exited.set()
        self._output.put(b"")

    def close(self) -> None:
        return None


def _paste_method(method_id: str = "claude_ai", name: str = "Claude subscription") -> SignInMethod:
    return SignInMethod(
        method_id,
        name,
        "Pro or Max plan",
        "Paste the code from the page.",
        argv=["claude", "auth", "login"],
        cwd="/home/me",
        reads_code=True,
    )


def _device_method() -> SignInMethod:
    return SignInMethod(
        "chatgpt",
        "ChatGPT account",
        "Plus or Pro plan",
        "Enter the code below on the page.",
        argv=["codex", "login", "--device-auth"],
        user_code_pattern=r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b",
    )


class FakeKeyLogin:
    """``submit_secret``: answers each value from ``answers`` in turn."""

    def __init__(self, *answers: tuple[bool, str] | Exception) -> None:
        self.answers = list(answers)
        self.values: list[str] = []

    async def __call__(self, value: str) -> tuple[bool, str]:
        self.values.append(value)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _key_method(login: FakeKeyLogin) -> SignInMethod:
    return SignInMethod(
        "api_key",
        "OpenAI API key",
        "API usage billing",
        "Paste an API key.",
        input_label="OpenAI API key",
        submit_secret=login,
    )


class Harness:
    def __init__(self, methods: list[SignInMethod], pty: LoginPty | None = None) -> None:
        self.pty = pty or LoginPty()
        self.changes = 0
        self.signed_in = 0
        self.flow = SignInFlow(
            methods,
            "Claude Code",
            request_id="sign-in",
            on_change=self._changed,
            on_signed_in=self._done,
            backend_factory=lambda: self.pty,
        )

    @property
    def public(self) -> dict[str, Any] | None:
        return self.flow.public

    def _changed(self) -> None:
        self.changes += 1

    def _done(self) -> None:
        self.signed_in += 1

    async def until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                raise AssertionError(f"timed out; public={self.public!r}")
            await asyncio.sleep(0.01)

    async def at_prompt(self) -> None:
        """Starts the paste login and waits for its sign-in page and prompt."""
        await self.flow.start("claude_ai")
        self.pty.say(f"If the browser didn't open, visit: \x1b]8;;{URL}\x07{URL}\x1b]8;;\x07\r\n")
        self.pty.say(PROMPT)
        await self.until(lambda: self.public is not None and self.public["url"] is not None)


@pytest.fixture
def fast_settle(monkeypatch):
    monkeypatch.setattr(signin, "SUBMIT_SETTLE", 0.05)


class TestChoose:
    def test_offer_lists_the_methods(self):
        h = Harness([_paste_method(), _paste_method("console", "Anthropic Console")])

        h.flow.offer()

        assert h.public == {
            "id": "sign-in",
            "kind": "sign_in",
            "tool_call_id": None,
            "title": "Sign in to Claude Code",
            "text": "Claude Code is not signed in on your PC. Sign in here to continue.",
            "stage": "choose",
            "methods": [
                {"id": "claude_ai", "name": "Claude subscription", "description": "Pro or Max plan"},
                {"id": "console", "name": "Anthropic Console", "description": "Pro or Max plan"},
            ],
            "url": None,
            "user_code": None,
            "input": None,
            "busy": False,
            "error": None,
        }
        assert h.changes == 1

    def test_without_methods_nothing_is_offered(self):
        h = Harness([])

        h.flow.offer()

        assert h.flow.available is False
        assert h.public is None
        assert h.changes == 0

    async def test_open_with_several_methods_lets_the_user_pick(self):
        h = Harness([_paste_method(), _paste_method("console", "Anthropic Console")])

        await h.flow.open()

        assert h.public is not None and h.public["stage"] == "choose"
        assert h.pty.spawned == []

    async def test_open_with_one_method_starts_it(self):
        h = Harness([_paste_method()])

        await h.flow.open()

        assert h.public is not None
        # The clients show their own status line until the page is known.
        assert (h.public["stage"], h.public["busy"], h.public["text"]) == ("waiting", True, "")
        assert h.pty.spawned == [
            (["claude", "auth", "login"], "/home/me", signin.PTY_ROWS, signin.PTY_COLS)
        ]
        h.flow.close()

    async def test_an_offer_while_a_login_runs_keeps_it(self):
        h = Harness([_paste_method()])
        await h.at_prompt()

        h.flow.offer(error="Signed out again")

        assert h.public is not None and h.public["stage"] == "waiting"
        assert h.public["error"] is None
        h.flow.close()

    def test_withdraw_drops_an_offer_nobody_acted_on(self):
        h = Harness([_paste_method()])
        h.flow.offer()

        h.flow.withdraw()

        assert h.public is None
        assert h.changes == 2

    def test_withdraw_without_a_request_changes_nothing(self):
        h = Harness([_paste_method()])

        h.flow.withdraw()

        assert h.public is None
        assert h.changes == 0

    async def test_withdraw_keeps_a_running_login(self):
        h = Harness([_paste_method()])
        await h.at_prompt()

        h.flow.withdraw()

        assert h.public is not None and h.public["stage"] == "waiting"
        assert h.public["url"] == URL
        assert h.pty.terminated is False
        h.flow.close()

    @pytest.mark.parametrize("method_id", ["nope", None, 3])
    async def test_an_unknown_method_is_refused(self, method_id):
        h = Harness([_paste_method()])

        with pytest.raises(SignInError, match="Unknown sign-in method"):
            await h.flow.start(method_id)

    async def test_a_command_that_cannot_start_goes_back_to_the_methods(self):
        h = Harness([_paste_method()], LoginPty(spawn_error=OSError("no such file")))

        await h.flow.start("claude_ai")

        assert h.public is not None and h.public["stage"] == "choose"
        assert h.public["error"] == "The sign-in could not start: no such file"


class TestPastedCode:
    async def test_the_sign_in_page_opens_the_code_field(self):
        h = Harness([_paste_method()])

        await h.at_prompt()

        assert h.public is not None
        assert h.public["url"] == URL
        assert h.public["text"] == "Paste the code from the page."
        assert h.public["input"] == {"label": "Code from the sign-in page", "secret": False}
        assert h.public["busy"] is False
        assert h.public["user_code"] is None
        h.flow.close()

    async def test_an_address_still_being_printed_is_not_shown(self):
        h = Harness([_paste_method()])
        await h.flow.start("claude_ai")

        h.pty.say("visit: https://example.com/sign-in?sta")
        await asyncio.sleep(0.1)
        assert h.public is not None and h.public["url"] is None

        h.pty.say("te=abc\r\n")
        await h.until(lambda: h.public is not None and h.public["url"] is not None)
        assert h.public["url"] == URL
        h.flow.close()

    async def test_submit_types_the_code_into_the_login(self):
        h = Harness([_paste_method()])
        await h.at_prompt()

        await h.flow.submit("  abc-123  ")

        assert h.pty.typed == [b"abc-123\r"]
        assert h.public is not None and h.public["busy"] is True
        h.flow.close()

    async def test_a_wrong_code_shows_the_answer_and_asks_again(self, fast_settle):
        h = Harness([_paste_method()])
        await h.at_prompt()

        await h.flow.submit("bad")
        h.pty.say(f"bad\r\nInvalid code. Check the code and try again.\r\n{PROMPT}")
        await h.until(lambda: h.public is not None and h.public["busy"] is False)

        assert h.public["error"] == "Invalid code. Check the code and try again."
        assert h.public["stage"] == "waiting"
        assert h.public["input"] is not None
        h.flow.close()

    async def test_the_echo_of_the_code_alone_is_no_answer(self, fast_settle):
        h = Harness([_paste_method()])
        await h.at_prompt()

        await h.flow.submit("abc")
        h.pty.say("abc\r\n")
        await asyncio.sleep(0.3)

        assert h.public is not None
        assert (h.public["busy"], h.public["error"]) == (True, None)
        h.flow.close()

    async def test_the_next_code_clears_the_last_error(self, fast_settle):
        h = Harness([_paste_method()])
        await h.at_prompt()
        await h.flow.submit("bad")
        h.pty.say(f"bad\r\nInvalid code.\r\n{PROMPT}")
        await h.until(lambda: h.public is not None and h.public["busy"] is False)

        await h.flow.submit("good")

        assert h.public is not None
        assert (h.public["busy"], h.public["error"]) == (True, None)
        assert h.pty.typed == [b"bad\r", b"good\r"]
        h.flow.close()

    async def test_exit_code_zero_signs_in(self):
        h = Harness([_paste_method()])
        await h.at_prompt()
        await h.flow.submit("good")

        h.pty.say("good\r\nLogin successful.\r\n")
        h.pty.exit(0)
        await h.until(lambda: h.signed_in == 1)

        assert h.public is None
        assert h.flow.running is False

    async def test_a_failed_login_goes_back_to_the_methods_with_its_last_lines(self):
        h = Harness([_paste_method()])
        await h.at_prompt()
        await h.flow.submit("fail")

        h.pty.say("fail\r\nLogin failed: the sign-in server refused the code.\r\n")
        h.pty.exit(1)
        await h.until(lambda: h.public is not None and h.public["stage"] == "choose")

        assert h.public["error"] == (
            "The sign-in did not finish.\nLogin failed: the sign-in server refused the code."
        )
        assert (h.public["url"], h.public["input"]) == (None, None)
        assert h.signed_in == 0

    async def test_a_login_that_runs_too_long_is_stopped(self, monkeypatch):
        monkeypatch.setattr(signin, "LOGIN_TIMEOUT", 0.05)
        h = Harness([_paste_method()])
        await h.flow.start("claude_ai")

        await h.until(lambda: h.public is not None and h.public["stage"] == "choose")

        assert h.public["error"] == "The sign-in took too long, so it was stopped. Try again."
        assert h.pty.terminated is True

    async def test_cancel_stops_the_login_and_drops_the_request(self):
        h = Harness([_paste_method()])
        await h.at_prompt()

        h.flow.cancel()
        h.pty.exit(0)
        await asyncio.sleep(0.1)

        assert h.public is None
        assert h.pty.terminated is True
        assert h.signed_in == 0


class TestOneTimeCode:
    async def test_the_page_shows_once_its_code_is_known(self):
        h = Harness([_device_method()])
        await h.flow.start("chatgpt")

        h.pty.say(f"1. Open this link in your browser and sign in\r\n   {URL}\r\n")
        await asyncio.sleep(0.1)
        assert h.public is not None and h.public["url"] is None

        h.pty.say("2. Enter this one-time code (expires in 15 minutes)\r\n   ABCD-12345\r\n")
        await h.until(lambda: h.public is not None and h.public["url"] is not None)

        assert h.public["url"] == URL
        assert h.public["user_code"] == "ABCD-12345"
        assert h.public["text"] == "Enter the code below on the page."
        assert h.public["input"] is None
        assert h.public["busy"] is True
        h.flow.close()

    async def test_nothing_can_be_submitted_to_it(self):
        h = Harness([_device_method()])
        await h.flow.start("chatgpt")
        h.pty.say(f"{URL}\r\nABCD-12345\r\n")
        await h.until(lambda: h.public is not None and h.public["url"] is not None)

        with pytest.raises(SignInError, match="Nothing to submit"):
            await h.flow.submit("ABCD-12345")
        h.flow.close()


class TestSubmitChecks:
    async def test_nothing_to_submit_before_a_method_runs(self):
        h = Harness([_paste_method()])
        h.flow.offer()

        with pytest.raises(SignInError, match="Nothing to submit"):
            await h.flow.submit("abc")

    @pytest.mark.parametrize("value", ["", "   ", None, 42])
    async def test_an_empty_code_is_refused(self, value):
        h = Harness([_paste_method()])
        await h.at_prompt()

        with pytest.raises(SignInError, match="Enter the code first"):
            await h.flow.submit(value)
        assert h.pty.typed == []
        h.flow.close()

    async def test_a_code_over_the_cap_is_refused(self):
        h = Harness([_paste_method()])
        await h.at_prompt()

        with pytest.raises(SignInError, match="The code is too long"):
            await h.flow.submit("x" * (signin.INPUT_MAX + 1))
        h.flow.close()

    @pytest.mark.parametrize("value", ["abc\ndef", "abc\rdef", "ab\x03cd", "ab\tcd", "ab\x7fcd"])
    async def test_a_code_with_control_characters_never_reaches_the_login(self, value):
        h = Harness([_paste_method()])
        await h.at_prompt()

        with pytest.raises(SignInError, match="The code can only be one line of plain text"):
            await h.flow.submit(value)
        assert h.pty.typed == []
        assert h.public is not None and h.public["busy"] is False
        h.flow.close()

    async def test_a_key_with_a_line_break_never_reaches_the_key_login(self):
        login = FakeKeyLogin()
        h = Harness([_key_method(login)])
        await h.flow.start("api_key")

        with pytest.raises(SignInError, match="The code can only be one line of plain text"):
            await h.flow.submit("sk-one\nsk-two")
        assert login.values == []

    async def test_a_second_code_while_the_first_is_checked_is_refused(self):
        h = Harness([_paste_method()])
        await h.at_prompt()
        await h.flow.submit("first")

        with pytest.raises(SignInError, match="still checking the last code"):
            await h.flow.submit("second")
        assert h.pty.typed == [b"first\r"]
        h.flow.close()


class TestKeyLogin:
    async def test_start_asks_for_the_key_without_a_command(self):
        h = Harness([_key_method(FakeKeyLogin())])

        await h.flow.start("api_key")

        assert h.public is not None
        assert h.public["stage"] == "waiting"
        assert h.public["text"] == "Paste an API key."
        assert h.public["input"] == {"label": "OpenAI API key", "secret": True}
        assert h.public["busy"] is False
        assert h.pty.spawned == []

    async def test_an_accepted_key_signs_in(self):
        login = FakeKeyLogin((True, ""))
        h = Harness([_key_method(login)])
        await h.flow.start("api_key")

        await h.flow.submit(" sk-test ")
        await h.until(lambda: h.signed_in == 1)

        assert login.values == ["sk-test"]
        assert h.public is None

    async def test_a_refused_key_shows_why_and_can_be_tried_again(self):
        login = FakeKeyLogin((False, "Incorrect API key provided."), (True, ""))
        h = Harness([_key_method(login)])
        await h.flow.start("api_key")

        await h.flow.submit("sk-bad")
        await h.until(lambda: h.public is not None and h.public["busy"] is False)
        assert h.public["error"] == "Incorrect API key provided."

        await h.flow.submit("sk-good")
        await h.until(lambda: h.signed_in == 1)
        assert login.values == ["sk-bad", "sk-good"]

    async def test_a_refusal_without_detail_has_a_default_text(self):
        h = Harness([_key_method(FakeKeyLogin((False, "")))])
        await h.flow.start("api_key")

        await h.flow.submit("sk-bad")
        await h.until(lambda: h.public is not None and h.public["busy"] is False)

        assert h.public["error"] == "The sign-in failed. Check the value and try again."

    async def test_a_login_that_raises_shows_the_error(self):
        h = Harness([_key_method(FakeKeyLogin(RuntimeError("codex is not installed")))])
        await h.flow.start("api_key")

        await h.flow.submit("sk-test")
        await h.until(lambda: h.public is not None and h.public["busy"] is False)

        assert h.public["error"] == "codex is not installed"
        assert h.signed_in == 0


class SpawnRacePty(LoginPty):
    """``stop`` lands while the command spawns: a terminate before the spawn
    finished finds no process to end."""

    def __init__(self) -> None:
        super().__init__()
        self.on_spawn: Callable[[], None] = lambda: None
        self.early_terminate = threading.Event()

    def spawn(self, cmd, cwd, env, rows, cols) -> None:
        self.on_spawn()
        super().spawn(cmd, cwd, env, rows, cols)

    def terminate(self, force: bool = False) -> None:
        if not self.spawned:
            self.early_terminate.set()
            return
        super().terminate(force)


class SlowStopPty(LoginPty):
    """A terminate that takes a while, like ConPTY's between its signals."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def terminate(self, force: bool = False) -> None:
        self.release.wait(5)
        super().terminate(force)


class TestLoginProcess:
    @staticmethod
    def _process(pty: LoginPty, exits: list[int | None] | None = None) -> LoginProcess:
        return LoginProcess(
            _paste_method(),
            loop=asyncio.get_running_loop(),
            on_output=lambda chunk: None,
            on_exit=(exits if exits is not None else []).append,
            backend_factory=lambda: pty,
        )

    @staticmethod
    async def _until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                raise AssertionError("timed out")
            await asyncio.sleep(0.01)

    async def test_an_exit_without_the_end_of_the_output_still_signs_in(self):
        h = Harness([_paste_method()])
        await h.at_prompt()
        await h.flow.submit("good")

        h.pty.say("good\r\nLogin successful.\r\n")
        h.pty.exit(0, eof=False)
        await h.until(lambda: h.signed_in == 1)

        assert h.public is None

    async def test_the_exit_is_delivered_once(self):
        pty = LoginPty()
        exits: list[int | None] = []
        proc = self._process(pty, exits)
        proc.start()

        pty.exit(1, eof=False)
        await self._until(lambda: exits == [1])
        # The output ends later (the browser that held the PTY quits).
        pty.say("")
        await asyncio.sleep(0.3)

        assert exits == [1]
        # The leader is already reaped: signalling its pid could hit another process.
        assert pty.terminated is False

    async def test_a_stop_while_the_command_spawns_ends_it(self):
        pty = SpawnRacePty()
        proc = self._process(pty)

        def stop_meanwhile() -> None:
            proc.stop()
            assert pty.early_terminate.wait(5)

        pty.on_spawn = stop_meanwhile
        proc.start()

        assert pty.spawned
        assert pty.terminated is True

    async def test_stop_leaves_the_event_loop_free(self):
        pty = SlowStopPty()
        exits: list[int | None] = []
        proc = self._process(pty, exits)
        proc.start()

        proc.stop()
        assert pty.terminated is False

        pty.release.set()
        await self._until(lambda: pty.terminated)
        await asyncio.sleep(0.1)
        assert exits == []
