"""Tests for the session request/response Pydantic models.

Validates that the API contract between the Android client and the Python
agent round-trips correctly for the ``skip_permissions`` field added to
``ClaudeOptionsModel``.
"""

from __future__ import annotations

import pytest

from mobaide.agent.sessions.models import ClaudeOptionsModel


def test_skip_permissions_explicit_false_round_trips():
    """When the Android client sends ``skip_permissions: false``, the
    Pydantic model must parse and preserve it."""
    payload = {"skip_permissions": False, "plan_mode": True}
    opts = ClaudeOptionsModel.model_validate(payload)
    assert opts.skip_permissions is False
    assert opts.plan_mode is True
    # Round-trip through dict
    dumped = opts.model_dump()
    assert dumped["skip_permissions"] is False


def test_effort_invalid_value_rejected():
    """``effort`` is Literal-typed, so an out-of-range level is rejected at the
    wire boundary (422) rather than producing a broken ``claude`` argv."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ClaudeOptionsModel.model_validate({"effort": "turbo"})


def test_debug_wire_absence_is_off_and_survives_the_hop_to_claude_options():
    """``to_options()`` is ``ClaudeOptions(**model_dump())``, so a field carried
    by only one of the two would raise ``TypeError`` rather than merely drop
    the flag; this pins the pair together."""
    assert ClaudeOptionsModel.model_validate({}).to_options().debug is False
    assert ClaudeOptionsModel.model_validate({"debug": True}).to_options().debug is True


def test_focus_mode_wire_absence_is_none_and_survives_the_hop_to_claude_options():
    assert ClaudeOptionsModel.model_validate({}).to_options().focus_mode is None
    for value in (True, False):
        opts = ClaudeOptionsModel.model_validate({"focus_mode": value}).to_options()
        assert opts.focus_mode is value


class TestSessionToWireDictKind:
    """``session_to_wire_dict`` must serialise the ``kind`` field through
    ``info.kind.value`` so SHELL and AUTO_SCAN sessions don't silently wire
    as ``"claude"``. The existing ``TestProxySessionsWireShape`` tests only
    exercise the default (CLAUDE) — a regression that hard-codes the value
    to ``"claude"`` instead of ``info.kind.value`` would slip past them.
    """

    def test_kind_field_is_always_present(self):
        """Even when caller omits ``kind`` from ``SessionInfo`` construction,
        the wire dict must carry the key — the Android DTO declares
        ``kind`` without an ``Optional`` and would fail to deserialise a
        missing-field payload.
        """
        from datetime import datetime, timezone

        from mobaide.core.models import SessionInfo, SessionState, session_to_wire_dict

        info = SessionInfo(
            session_id="sid",
            state=SessionState.STARTING,
            created_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
            pid=None,
            command=["claude"],
            cwd=None,
            # kind omitted — should default to CLAUDE per the dataclass default.
        )

        wire = session_to_wire_dict(info)

        assert "kind" in wire
        assert wire["kind"] == "claude"


def _attention_info(attention=None, exit_reason=None):
    from datetime import datetime, timezone

    from mobaide.core.models import SessionInfo, SessionState

    return SessionInfo(
        session_id="sid",
        state=SessionState.RUNNING,
        created_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
        pid=42,
        command=["claude"],
        cwd="/work",
        attention=attention,
        exit_reason=exit_reason,
    )


class TestAttentionWireFields:
    """``attention`` / ``exit_reason`` must round-trip through the
    single wire-shape builder, the Pydantic ``SessionResponse``, and default
    to ``None`` so a producer that omits them still serialises cleanly.
    """

    def test_wire_dict_always_carries_both_keys(self):
        from mobaide.core.models import session_to_wire_dict

        wire = session_to_wire_dict(_attention_info())
        assert "attention" in wire
        assert "exit_reason" in wire
        assert wire["attention"] is None
        assert wire["exit_reason"] is None

    def test_wire_dict_propagates_set_values(self):
        from mobaide.core.models import session_to_wire_dict

        wire = session_to_wire_dict(
            _attention_info(attention="ended", exit_reason="clear"),
        )
        assert wire["attention"] == "ended"
        assert wire["exit_reason"] == "clear"

    def test_session_response_round_trips_from_info(self):
        from mobaide.agent.sessions.models import SessionResponse

        resp = SessionResponse.from_info(
            _attention_info(attention="awaiting_permission"),
        )
        assert resp.attention == "awaiting_permission"
        assert resp.exit_reason is None

    def test_session_response_defaults_when_absent_from_payload(self):
        # The Android DTO and a back-compat producer may omit the fields
        # entirely; the model must still validate with the None defaults.
        from mobaide.agent.sessions.models import SessionResponse

        resp = SessionResponse.model_validate(
            {
                "session_id": "sid",
                "state": "running",
                "created_at": "2026-05-20T00:00:00+00:00",
                "pid": 42,
                "command": ["claude"],
                "cwd": "/work",
            },
        )
        assert resp.attention is None
        assert resp.exit_reason is None


class TestActivityWireField:
    """``activity`` (``working`` / ``idle`` / ``None``) rides the same single
    wire-shape builder and Pydantic response as ``attention``. The key is
    always present so a client can tell "unknown" from a dropped field.
    """

    def test_wire_dict_always_carries_the_key(self):
        from mobaide.core.models import session_to_wire_dict

        wire = session_to_wire_dict(_attention_info())
        assert "activity" in wire
        assert wire["activity"] is None

    def test_wire_dict_and_session_response_propagate_the_value(self):
        from dataclasses import replace

        from mobaide.agent.sessions.models import SessionResponse
        from mobaide.core.models import session_to_wire_dict

        info = replace(_attention_info(), activity="working")

        assert session_to_wire_dict(info)["activity"] == "working"
        assert SessionResponse.from_info(info).activity == "working"

    def test_session_response_defaults_when_absent_from_payload(self):
        # An older agent omits the field; it must read as unknown, not fail.
        from mobaide.agent.sessions.models import SessionResponse

        resp = SessionResponse.model_validate(
            {
                "session_id": "sid",
                "state": "running",
                "created_at": "2026-05-20T00:00:00+00:00",
                "pid": 42,
                "command": ["claude"],
                "cwd": "/work",
            },
        )
        assert resp.activity is None


class TestCodexToOptions:
    """``CodexOptionsModel.to_options()`` must return a real ``CodexOptions``
    dataclass carrying every field verbatim — the same drift guard as the
    Claude DTO so a future field addition can't silently slip on the Codex path.
    """

    def test_worktree_fields_round_trip(self):
        # The worktree pair is the server-side addition: ``_resolve_cwd``
        # reads ``options.worktree`` / ``options.branch_name`` off the dataclass,
        # so both must survive ``model_dump()`` -> ``CodexOptions(**...)`` or the
        # git-worktree path silently never fires.
        from mobaide.agent.sessions.models import CodexOptionsModel
        from mobaide.core.codex_launcher import CodexOptions

        opts = CodexOptionsModel(
            worktree=True, branch_name="feature/x",
        ).to_options()

        assert isinstance(opts, CodexOptions)
        assert opts.worktree is True
        assert opts.branch_name == "feature/x"

    def test_invalid_enum_value_rejected_at_wire_boundary(self):
        import pydantic

        from mobaide.agent.sessions.models import CodexOptionsModel

        for bad in ({"sandbox": "bogus"}, {"approval": "always"}):
            with pytest.raises(pydantic.ValidationError):
                CodexOptionsModel.model_validate(bad)

    def test_reasoning_effort_is_free_form_for_dynamic_catalog(self):
        """``reasoning_effort`` must NOT be a closed Literal: the vocabulary is
        enumerated per-model at runtime (``capabilities.cli.codex.model_details``),
        so a closed set would 422 efforts the agent itself just advertised —
        e.g. ``xhigh``/``max``/``ultra`` on the gpt-5.6 catalog."""
        from mobaide.agent.sessions.models import CodexOptionsModel

        for effort in ("ultra", "xhigh", "max", "minimal"):
            opts = CodexOptionsModel.model_validate(
                {"reasoning_effort": effort}
            ).to_options()
            assert opts.reasoning_effort == effort


class TestAntigravityOptionsModel:
    """``AntigravityOptionsModel`` keeps ``model`` free-form (like the
    Claude/Codex DTOs) — it carries the full display name ``agy models`` prints
    — and rejects the ``skip_permissions``+``sandbox`` pair at the wire boundary
    (skip-permissions auto-approves ``bypassSandbox`` actions)."""

    def test_accepts_full_display_name_verbatim(self):
        from mobaide.agent.sessions.models import AntigravityOptionsModel

        # Spaces and parens included — the launcher forwards ``--model``
        # verbatim as a single argv element, so nothing is normalised here.
        opts = AntigravityOptionsModel(model="Gemini 3.1 Pro (Low)").to_options()
        assert opts.model == "Gemini 3.1 Pro (Low)"

    def test_rejects_skip_permissions_with_sandbox(self):
        import pydantic

        from mobaide.agent.sessions.models import AntigravityOptionsModel

        with pytest.raises(pydantic.ValidationError):
            AntigravityOptionsModel(skip_permissions=True, sandbox=True)

    def test_to_options_converts_add_dirs_list_to_tuple(self):
        # The wire DTO carries ``add_dirs`` as a JSON list; the frozen
        # dataclass wants an immutable tuple — ``to_options`` must convert or
        # ``AntigravityOptions(**data)`` would smuggle a mutable list into a
        # frozen instance.
        from mobaide.agent.sessions.models import AntigravityOptionsModel

        opts = AntigravityOptionsModel(add_dirs=["/a", "/b"]).to_options()
        assert opts.add_dirs == ("/a", "/b")
        assert isinstance(opts.add_dirs, tuple)

    def test_rejects_flag_shaped_add_dirs(self):
        # Guards the mutual-exclusion check: a flag-shaped add_dirs entry could
        # otherwise smuggle ``--dangerously-skip-permissions`` into argv while
        # ``sandbox=True`` passed the pair check.
        import pydantic

        from mobaide.agent.sessions.models import AntigravityOptionsModel

        with pytest.raises(pydantic.ValidationError):
            AntigravityOptionsModel(
                sandbox=True, add_dirs=["--dangerously-skip-permissions"]
            )

    def test_rejects_flag_shaped_model(self):
        import pydantic

        from mobaide.agent.sessions.models import AntigravityOptionsModel

        with pytest.raises(pydantic.ValidationError):
            AntigravityOptionsModel(model="--sandbox")


class TestResolveSpawnArgs:
    """``resolve_spawn_args`` is the single normaliser the REST + relay create
    paths share: it picks the kind, applies the shell security guard, and
    guarantees exactly one options object is set for a managed session.
    """

    _SHELL = ["/bin/sh"]

    def _call(
        self, cli=None, claude=None, codex=None, antigravity=None, cmd=None, env=None,
    ):
        from mobaide.core.models import resolve_spawn_args

        return resolve_spawn_args(
            cli, claude, codex, antigravity, cmd, env, self._SHELL,
        )

    def test_no_cli_no_options_is_shell_with_clean_env(self):
        from mobaide.core.models import SessionKind

        kind, command, env, claude, codex, antigravity = self._call(
            cmd=["rm", "-rf"], env={"EVIL": "1"},
        )
        # Security guard: client command/env hints are dropped for shell.
        assert kind is SessionKind.SHELL
        assert command == self._SHELL
        assert env is None
        assert claude is None and codex is None and antigravity is None

    def test_claude_options_only_infers_claude(self):
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.models import SessionKind

        kind, command, env, claude, codex, antigravity = self._call(
            claude=ClaudeOptions(), cmd=["claude"], env={"X": "1"},
        )
        assert kind is SessionKind.CLAUDE
        assert command == ["claude"] and env == {"X": "1"}
        assert (
            isinstance(claude, ClaudeOptions)
            and codex is None
            and antigravity is None
        )

    def test_codex_options_only_infers_codex(self):
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex, antigravity = self._call(
            codex=CodexOptions(),
        )
        assert kind is SessionKind.CODEX
        assert (
            isinstance(codex, CodexOptions)
            and claude is None
            and antigravity is None
        )

    def test_explicit_cli_codex_defaults_options_when_omitted(self):
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex, antigravity = self._call(cli="codex")
        assert kind is SessionKind.CODEX
        assert (
            isinstance(codex, CodexOptions)
            and claude is None
            and antigravity is None
        )

    def test_explicit_cli_codex_clears_stray_claude_options(self):
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex, antigravity = self._call(
            cli="codex", claude=ClaudeOptions(), codex=CodexOptions(),
        )
        # Exactly one options object survives, matching the chosen kind.
        assert kind is SessionKind.CODEX
        assert codex is not None and claude is None and antigravity is None


CUSTOM_KIND = "custom_0123456789ab"


class TestResolveSpawnArgsCustomKind:
    """The custom-agent-type branch of ``resolve_spawn_args``: argv comes
    exclusively from the relay-injected ``resolved_command`` (client hints
    stay discarded), and a custom ``cli`` without it fails loudly instead
    of degrading to a bare shell."""

    _SHELL = ["/bin/sh"]

    def _call(
        self, cli, resolved_command=None, claude=None, cmd=None, env=None,
    ):
        from mobaide.core.models import resolve_spawn_args

        return resolve_spawn_args(
            cli, claude, None, None, cmd, env, self._SHELL,
            resolved_command=resolved_command,
        )

    def test_resolved_command_builds_login_shell_argv(self):
        from mobaide.core.shell import build_custom_argv

        kind, command, env, claude, codex, antigravity = self._call(
            CUSTOM_KIND,
            resolved_command="aider --model x",
            cmd=["rm", "-rf"],
            env={"EVIL": "1"},
        )

        assert kind == CUSTOM_KIND
        assert isinstance(kind, str)
        # Spawn contract: the stored command rides as ONE argv element
        # through the login shell — never the client's command/env hints.
        assert command == build_custom_argv("aider --model x")
        assert command[-1] == "aider --model x"
        assert env is None
        assert claude is None and codex is None and antigravity is None

    def test_custom_branch_wins_over_stray_options(self):
        from mobaide.core.claude_launcher import ClaudeOptions

        kind, _command, _env, claude, _codex, _antigravity = self._call(
            CUSTOM_KIND,
            resolved_command="echo hi",
            claude=ClaudeOptions(),
        )
        assert kind == CUSTOM_KIND
        assert claude is None

    def test_missing_resolved_command_raises(self):
        from mobaide.core.models import CustomCommandUnresolvedError

        with pytest.raises(CustomCommandUnresolvedError, match="relay"):
            self._call(CUSTOM_KIND)

    @pytest.mark.parametrize(
        "bad_cli",
        [
            "custom_xyz",
            "custom_0123456789a",   # 11 hex chars
            "custom_0123456789abc",  # 13 hex chars
            "custom_0123456789AB",  # uppercase
            "custom_",
        ],
    )
    def test_malformed_custom_cli_raises_even_with_resolved_command(
        self, bad_cli,
    ):
        from mobaide.core.models import CustomCommandUnresolvedError

        with pytest.raises(CustomCommandUnresolvedError):
            self._call(bad_cli, resolved_command="echo hi")


class TestSessionWireDictCustomKind:
    """``session_to_wire_dict`` emits raw-string custom kinds verbatim and
    always carries the additive nullable ``kind_label``."""

    def _info(self, **overrides):
        from datetime import datetime, timezone

        from mobaide.core.models import SessionInfo, SessionState

        defaults = dict(
            session_id="sid",
            state=SessionState.RUNNING,
            created_at=datetime(2026, 7, 27, 0, 0, 0, tzinfo=timezone.utc),
            pid=42,
            command=["/bin/sh"],
            cwd="/work",
        )
        defaults.update(overrides)
        return SessionInfo(**defaults)

    def test_custom_str_kind_and_label_pass_through(self):
        from mobaide.core.models import session_to_wire_dict

        wire = session_to_wire_dict(
            self._info(kind=CUSTOM_KIND, kind_label="My Agent"),
        )
        assert wire["kind"] == CUSTOM_KIND
        assert wire["kind_label"] == "My Agent"

    def test_builtin_kind_still_wires_enum_value_with_null_label(self):
        from mobaide.core.models import SessionKind, session_to_wire_dict

        wire = session_to_wire_dict(self._info(kind=SessionKind.SHELL))
        assert wire["kind"] == "shell"
        assert "kind_label" in wire
        assert wire["kind_label"] is None
