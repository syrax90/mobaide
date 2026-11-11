"""Tests for ``mobaide.__main__.main`` Windows event-loop policy guard.

The agent's pairing flow performs ``await ws.recv()`` against the central
server's pairing WebSocket. Python 3.12 defaults to
``WindowsProactorEventLoopPolicy`` on Windows, and python-websockets has
documented TLS-recv hangs / silent recv data-loss under Proactor:

* https://github.com/python-websockets/websockets/issues/1118
* https://vstinner.github.io/asyncio-proactor-wsarecv-cancellation-data-loss.html

The recently-added top-of-``main()`` guard forces
``WindowsSelectorEventLoopPolicy`` on ``sys.platform == "win32"`` so the
agent's WS path on Windows matches Linux behaviour. These tests pin the
contract that:

* The policy IS set on Windows.
* The policy is NOT touched on POSIX (Linux/macOS).

We avoid invoking real uvicorn / asyncio bootstrap by passing
``--version`` as argv, which argparse handles by writing to stdout and
raising ``SystemExit(0)`` before any subcommand dispatch runs.
"""
from __future__ import annotations

import pytest

import mobaide.__main__ as _main


class _StubSelectorPolicy:
    """Sentinel returned by the stubbed
    ``asyncio.WindowsSelectorEventLoopPolicy`` constructor.

    Letting the production code call the real
    ``asyncio.WindowsSelectorEventLoopPolicy()`` is unsafe on the project's
    Linux CI runner: the symbol is missing on POSIX builds of CPython
    (``hasattr(asyncio, "WindowsSelectorEventLoopPolicy")`` is False on
    Linux), so a test that monkeypatches ``sys.platform = "win32"`` would
    crash with ``AttributeError`` inside the production line we're trying
    to pin. We stub the class with a sentinel so the test exercises the
    branch on every platform.
    """


def _install_policy_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Wire recorders around ``asyncio.set_event_loop_policy`` and
    ``asyncio.WindowsSelectorEventLoopPolicy`` on the
    ``mobaide.__main__`` module's ``asyncio`` reference.

    Returns a recorder dict carrying:

    * ``set_calls`` — list of policy instances passed to
      ``set_event_loop_policy``.
    * ``policy_ctor_calls`` — count of times the stubbed
      ``WindowsSelectorEventLoopPolicy`` constructor was invoked.
    """
    recorder: dict = {"set_calls": [], "policy_ctor_calls": 0}

    def _record_set_policy(policy) -> None:
        recorder["set_calls"].append(policy)

    def _policy_ctor() -> _StubSelectorPolicy:
        recorder["policy_ctor_calls"] += 1
        return _StubSelectorPolicy()

    monkeypatch.setattr(
        _main.asyncio, "set_event_loop_policy", _record_set_policy,
    )
    monkeypatch.setattr(
        _main.asyncio,
        "WindowsSelectorEventLoopPolicy",
        _policy_ctor,
        raising=False,
    )
    return recorder


class TestMainEventLoopPolicy:
    def test_main_sets_selector_event_loop_policy_on_windows(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Windows entry point MUST install WindowsSelectorEventLoopPolicy
        # before any subcommand dispatch (uvicorn.run / asyncio.run) so
        # python-websockets' Proactor recv hang cannot strand the pairing
        # flow.
        monkeypatch.setattr(_main.sys, "platform", "win32")
        recorder = _install_policy_recorder(monkeypatch)

        # ``--version`` makes argparse print __version__ and raise
        # SystemExit(0) BEFORE any subcommand handler is dispatched. The
        # policy guard runs at the very top of ``main()``, so it fires
        # before the SystemExit. Catching SystemExit keeps the test
        # focused on the policy contract without invoking uvicorn.
        with pytest.raises(SystemExit) as excinfo:
            _main.main(argv=["--version"])
        assert excinfo.value.code == 0

        # Exactly one ``set_event_loop_policy`` call, carrying the
        # stubbed-policy sentinel — proves the production code took the
        # Windows branch and constructed the SelectorEventLoopPolicy.
        assert len(recorder["set_calls"]) == 1, (
            f"expected exactly one set_event_loop_policy call on Windows; "
            f"got {len(recorder['set_calls'])}: {recorder['set_calls']!r}"
        )
        assert isinstance(recorder["set_calls"][0], _StubSelectorPolicy), (
            "set_event_loop_policy was called with something other than "
            "the WindowsSelectorEventLoopPolicy instance — the guard "
            "must construct that exact policy class, not a generic "
            "default"
        )
        # The stubbed constructor was invoked exactly once. A regression
        # that called ``set_event_loop_policy(None)`` (or any other
        # accidental no-op) would pass the first assertion (an instance
        # check) but trip this one.
        assert recorder["policy_ctor_calls"] == 1, (
            f"expected WindowsSelectorEventLoopPolicy() to be invoked "
            f"exactly once on the Windows branch; got "
            f"{recorder['policy_ctor_calls']}"
        )

    def test_main_does_not_touch_event_loop_policy_on_posix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # On non-Windows hosts the Proactor bug does not apply; the
        # native SelectorEventLoop is already the default on Linux and
        # macOS. The guard MUST NOT call ``set_event_loop_policy`` on
        # those platforms — touching the policy unnecessarily would
        # interfere with any host-level policy a wrapper script may
        # have installed before mobaide-agent was invoked.
        monkeypatch.setattr(_main.sys, "platform", "linux")
        recorder = _install_policy_recorder(monkeypatch)

        with pytest.raises(SystemExit) as excinfo:
            _main.main(argv=["--version"])
        assert excinfo.value.code == 0

        assert recorder["set_calls"] == [], (
            f"set_event_loop_policy must not be called on POSIX; got "
            f"{len(recorder['set_calls'])} calls: "
            f"{recorder['set_calls']!r}"
        )
        assert recorder["policy_ctor_calls"] == 0, (
            f"WindowsSelectorEventLoopPolicy must not be constructed on "
            f"POSIX; got {recorder['policy_ctor_calls']} calls"
        )


class _StubApp:
    """Sentinel returned by the stubbed ``create_app``.

    Carries an ``app.state.config`` shape with ``host`` / ``port``
    attributes — the production ``_run_agent`` reads these directly to
    pass to ``uvicorn.Config``. We do NOT construct a
    real ``FastAPI`` or ``AgentConfig`` here: the test is about the
    event-loop / Runner contract, not the app configuration, and
    importing the real ``create_app`` would drag in the full agent
    bootstrap (logging, env file parsing, store creation).
    """

    class _State:
        class _Cfg:
            host = "127.0.0.1"
            port = 9988

        config = _Cfg()

    state = _State()


class _FakeConfig:
    """Fake ``uvicorn.Config`` constructor recorder.

    Captures the positional ``app`` and keyword args so the test can
    assert on the host/port that ``_run_agent`` threaded through. The
    instance is consumed by ``_FakeServer``; production code never
    introspects the Config object once Server has it.
    """

    instances: list["_FakeConfig"] = []

    def __init__(self, app, *, host, port, **extra) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.extra = extra
        _FakeConfig.instances.append(self)


class _FakeServer:
    """Fake ``uvicorn.Server`` whose ``serve()`` returns an awaitable.

    The production ``_run_agent`` does ``runner.run(server.serve())`` — it
    relies on ``serve()`` returning a coroutine object. We return a
    sentinel coroutine here so the recorder downstream can assert on
    object identity. The coroutine is closed in ``_FakeRunner.run`` to
    avoid pytest's ``RuntimeWarning: coroutine 'serve' was never
    awaited``.
    """

    instances: list["_FakeServer"] = []

    def __init__(self, config: _FakeConfig) -> None:
        self.config = config
        self.started = True
        self.should_exit = False
        self.run_calls = 0
        _FakeServer.instances.append(self)

    def run(self) -> None:
        self.run_calls += 1

    def serve(self):
        async def _serve_coro() -> None:
            # Returned but never awaited; _FakeRunner.run closes it.
            return None

        return _serve_coro()


class _FakeRunner:
    """Fake ``asyncio.Runner`` recorder.

    Production code does:

        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())

    We record the ``loop_factory`` kwarg passed at construction (the
    smoking gun for the SelectorEventLoop fix) and the coroutine
    handed to ``runner.run``. The recorded coroutine is closed
    immediately so it doesn't leak as an un-awaited warning.
    """

    instances: list["_FakeRunner"] = []

    def __init__(self, *, loop_factory=None, **extra) -> None:
        self.loop_factory = loop_factory
        self.extra = extra
        self.runs: list[object] = []
        _FakeRunner.instances.append(self)

    def __enter__(self) -> "_FakeRunner":
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def run(self, coro):
        self.runs.append(coro)
        # Close the coroutine to suppress the "never awaited" warning;
        # the production contract is "Runner awaits server.serve() to
        # completion", but the test only needs to prove the coroutine
        # was *handed* to the Runner, not that it ran. Closing here
        # keeps the test deterministic on every Python version.
        if hasattr(coro, "close"):
            coro.close()


def _install_run_agent_recorders(
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    """Wire fake ``uvicorn.Config`` / ``Server`` / ``run`` and
    ``asyncio.Runner`` onto the ``_main`` module.

    Returns a recorder dict carrying:

    * ``stub_app`` — the ``_StubApp`` instance the fake ``create_app``
      returns (so tests can assert on object identity into
      ``uvicorn.Config``).
    * ``uvicorn_run_calls`` — list of ``(args, kwargs)`` for every
      ``uvicorn.run`` invocation. MUST stay empty on the Windows
      branch (Windows uses the Runner path, not ``uvicorn.run``).
    """
    _FakeConfig.instances = []
    _FakeServer.instances = []
    _FakeRunner.instances = []
    recorder: dict = {
        "stub_app": _StubApp(),
        "uvicorn_run_calls": [],
    }

    def _fake_create_app():
        return recorder["stub_app"]

    # ``_run_agent`` does ``from mobaide.agent.app import create_app``
    # inside the function body, so patch the symbol on the
    # ``mobaide.agent.app`` module rather than on ``_main``.
    import mobaide.agent.app as _app
    monkeypatch.setattr(_app, "create_app", _fake_create_app)

    monkeypatch.setattr(_main.uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(_main.uvicorn, "Server", _FakeServer)

    def _fake_uvicorn_run(*args, **kwargs):
        recorder["uvicorn_run_calls"].append((args, kwargs))

    monkeypatch.setattr(_main.uvicorn, "run", _fake_uvicorn_run)
    monkeypatch.setattr(_main.asyncio, "Runner", _FakeRunner, raising=False)
    return recorder


class TestRunAgentEventLoopRunner:
    """Pin the ``_run_agent`` dual-path Runner contract.

    ``main()`` installs ``WindowsSelectorEventLoopPolicy`` globally, but
    uvicorn 0.36+ bypasses ``asyncio.get_event_loop_policy()`` in
    single-process mode by constructing its loop via
    ``asyncio.Runner(loop_factory=Config.get_loop_factory())`` — which
    on Windows hands back the bare ``ProactorEventLoop`` constructor.
    The global policy is therefore NOT honoured for the ``run``
    subcommand path. ``_run_agent`` works around this by dropping down
    to ``uvicorn.Server.serve()`` and running it on an
    ``asyncio.Runner`` we explicitly construct with
    ``loop_factory=asyncio.SelectorEventLoop``. POSIX drives the same
    ``uvicorn.Server`` through ``Server.run()``.

    These tests pin both branches at the call-shape level. They do NOT
    invoke real uvicorn (we'd need a free port + signal handling) —
    instead they record what ``_run_agent`` *would have* called and
    verify the dispatch contract. A regression that reverts to
    ``uvicorn.run`` on Windows (the original Proactor-strands-recv bug)
    would fail the Windows test; a regression that forces the Runner
    path on POSIX, or that goes back to ``uvicorn.run`` (whose private
    Server ``request_restart`` cannot stop), would fail the POSIX test.
    """

    def test_run_agent_uses_selector_event_loop_runner_on_windows(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_main.sys, "platform", "win32")
        recorder = _install_run_agent_recorders(monkeypatch)

        _main._run_agent()

        # ``uvicorn.Config(app, host=..., port=...)`` MUST have been
        # called exactly once with the stub app and its host/port.
        # Without this, a regression that builds a Config with a
        # different app object (e.g. forgets to thread ``create_app()``
        # through) would slip past the Runner-path assertion below.
        assert len(_FakeConfig.instances) == 1, (
            f"expected exactly one uvicorn.Config construction; got "
            f"{len(_FakeConfig.instances)}"
        )
        cfg = _FakeConfig.instances[0]
        assert cfg.app is recorder["stub_app"], (
            "uvicorn.Config must receive the result of create_app() as "
            "the app argument — not a re-created or substituted app"
        )
        assert cfg.host == "127.0.0.1", (
            f"uvicorn.Config.host must come from app.state.config.host; "
            f"got {cfg.host!r}"
        )
        assert cfg.port == 9988, (
            f"uvicorn.Config.port must come from app.state.config.port; "
            f"got {cfg.port!r}"
        )

        # ``uvicorn.Server(config)`` MUST have been constructed off the
        # Config — the bypass-uvicorn-run path goes through Server.serve.
        assert len(_FakeServer.instances) == 1, (
            f"expected exactly one uvicorn.Server construction; got "
            f"{len(_FakeServer.instances)}"
        )
        server = _FakeServer.instances[0]
        assert server.config is cfg

        # The smoking-gun assertion: ``asyncio.Runner`` was constructed
        # with ``loop_factory=asyncio.SelectorEventLoop``. A regression
        # that forgets the loop_factory (or passes a different factory
        # — e.g. ProactorEventLoop, which would re-introduce the
        # websockets recv hang) is exactly what this test catches.
        assert len(_FakeRunner.instances) == 1, (
            f"expected exactly one asyncio.Runner construction on the "
            f"Windows branch; got {len(_FakeRunner.instances)}"
        )
        runner = _FakeRunner.instances[0]
        assert runner.loop_factory is _main.asyncio.SelectorEventLoop, (
            f"Runner.loop_factory must be asyncio.SelectorEventLoop "
            f"(the policy-bypassing override that reaches WS recv on "
            f"Windows); got {runner.loop_factory!r}"
        )

        # The coroutine handed to ``runner.run`` MUST have come from
        # ``server.serve()`` — proves the Runner is actually driving
        # uvicorn rather than being constructed and then discarded.
        # We check at the coroutine-name level since ``server.serve()``
        # returns a fresh coroutine object on each call (object-identity
        # would require pre-capturing the coroutine before invoking
        # _run_agent, which we cannot do without re-calling serve()).
        assert len(runner.runs) == 1, (
            f"expected exactly one Runner.run call; got {len(runner.runs)}"
        )
        run_arg = runner.runs[0]
        assert hasattr(run_arg, "__class__") and (
            run_arg.__class__.__name__ == "coroutine"
            or "coro" in run_arg.__class__.__name__.lower()
        ), (
            f"Runner.run must receive the coroutine returned by "
            f"server.serve(); got {run_arg!r} (type={type(run_arg).__name__})"
        )

        # Critically, ``uvicorn.run`` was NOT called on Windows. The
        # whole point of the Windows event-loop fix is to bypass ``uvicorn.run`` so
        # uvicorn's policy-bypassing Runner construction cannot reach
        # the agent's WS recv. A regression that reintroduces the
        # ``uvicorn.run(app, ...)`` call on Windows would re-strand
        # pairing — this is the contract that prevents it.
        assert recorder["uvicorn_run_calls"] == [], (
            f"uvicorn.run must NOT be called on Windows — the Runner "
            f"override is the whole point of the Windows event-loop fix; got "
            f"{recorder['uvicorn_run_calls']!r}"
        )

    def test_run_agent_uses_server_run_on_posix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(_main.sys, "platform", "linux")
        recorder = _install_run_agent_recorders(monkeypatch)

        _main._run_agent()

        assert len(_FakeConfig.instances) == 1, (
            f"uvicorn.Config must still be constructed on POSIX; got "
            f"{len(_FakeConfig.instances)} constructions"
        )
        cfg = _FakeConfig.instances[0]
        assert cfg.app is recorder["stub_app"]
        assert (cfg.host, cfg.port) == ("127.0.0.1", 9988)

        # POSIX drives the same ``uvicorn.Server`` instance through
        # ``Server.run`` (uvicorn's own loop + signal handling), so
        # ``request_restart`` can stop it. ``uvicorn.run`` would build a
        # private Server that nothing else can reach.
        assert len(_FakeServer.instances) == 1
        server = _FakeServer.instances[0]
        assert server.config is cfg
        assert server.run_calls == 1
        assert recorder["uvicorn_run_calls"] == [], (
            f"uvicorn.run must NOT be called on POSIX; got "
            f"{recorder['uvicorn_run_calls']!r}"
        )

        # The smoking-gun negative assertion: ``asyncio.Runner`` was NOT
        # constructed on POSIX. The Runner override exists solely for
        # the uvicorn-0.36+-on-Windows policy bypass; touching Runner
        # on POSIX would interfere with uvicorn's signal-aware
        # supervision and is unnecessary because POSIX defaults to
        # SelectorEventLoop already.
        assert _FakeRunner.instances == [], (
            f"asyncio.Runner must NOT be constructed on POSIX; got "
            f"{len(_FakeRunner.instances)} constructions: "
            f"{_FakeRunner.instances!r}"
        )
