"""Tests for the session request/response Pydantic models.

Validates that the API contract between the Android client and the Python
agent round-trips correctly for the ``skip_permissions`` field added to
``ClaudeOptionsModel``.
"""

from __future__ import annotations

import pytest

from mobaide.agent.sessions.models import ClaudeOptionsModel
from mobaide.core.claude_launcher import ClaudeOptions


def test_skip_permissions_defaults_to_true():
    """An empty options object must default skip_permissions to True — the
    mobile client cannot mediate approval dialogs, so permissions are
    skipped unless the user explicitly opts out."""
    opts = ClaudeOptionsModel()
    assert opts.skip_permissions is True


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


def test_options_model_omits_skip_permissions_uses_default():
    """JSON body that does not mention skip_permissions at all must still
    deserialize with the True default intact."""
    payload = {"plan_mode": False, "no_memory": True}
    opts = ClaudeOptionsModel.model_validate(payload)
    assert opts.skip_permissions is True
    assert opts.no_memory is True


# ClaudeOptionsModel.to_options — API-DTO → domain-dataclass conversion.
#
# This method is the single source of truth for DTO→dataclass translation
# used by every relay and REST handler. A drift between ``ClaudeOptionsModel``
# fields and ``ClaudeOptions`` fields — or a silently-dropped field — would
# reintroduce the exact latent bug that let the relay ``POST /api/sessions``
# handler forget ``claude_options`` entirely. The round-trip test locks all
# nine fields down field-by-field so any future addition can't silently slip.


class TestToOptions:
    """``ClaudeOptionsModel.to_options()`` must return a real ``ClaudeOptions``
    dataclass carrying every field verbatim from the model."""

    def test_round_trip_all_fields_populated(self):
        """A fully-populated model round-trips every field into the frozen
        dataclass, returns an ``isinstance(ClaudeOptions, ...)``, and each
        value equals the source."""
        model = ClaudeOptionsModel(
            plan_mode=True,
            no_memory=True,
            skip_permissions=False,
            initial_prompt="hello world",
            worktree=True,
            branch_name="feature/abc",
            identifier="CLA-77",
            title="Some title",
            max_effort=False,
        )

        opts = model.to_options()

        assert isinstance(opts, ClaudeOptions)
        assert opts.plan_mode is True
        assert opts.no_memory is True
        assert opts.skip_permissions is False
        assert opts.initial_prompt == "hello world"
        assert opts.worktree is True
        assert opts.branch_name == "feature/abc"
        assert opts.identifier == "CLA-77"
        assert opts.title == "Some title"
        assert opts.max_effort is False

    def test_round_trip_defaults(self):
        """A bare model (all defaults) must round-trip to the dataclass
        defaults — proving the conversion path does not silently override
        falsy values with other falsy values (e.g. ``None`` vs ``False``)."""
        model = ClaudeOptionsModel()

        opts = model.to_options()

        assert isinstance(opts, ClaudeOptions)
        assert opts.plan_mode is False
        assert opts.no_memory is False
        assert opts.skip_permissions is True
        assert opts.initial_prompt is None
        assert opts.worktree is False
        assert opts.branch_name is None
        assert opts.identifier is None
        assert opts.title is None
        assert opts.max_effort is True

    def test_returned_dataclass_is_frozen(self):
        """``ClaudeOptions`` is ``@dataclass(frozen=True)``; mutating the
        result of ``to_options()`` must raise. This is a guard against a
        future refactor that accidentally drops the frozen decorator and
        reopens the door to mutable sharing between handlers."""
        import dataclasses

        opts = ClaudeOptionsModel(plan_mode=True).to_options()

        with pytest.raises(dataclasses.FrozenInstanceError):
            opts.plan_mode = False  # type: ignore[misc]

    def test_model_mutation_after_conversion_does_not_affect_dataclass(self):
        """Because ``to_options()`` builds a frozen dataclass from
        ``model_dump()``, mutating the source model afterwards must not
        bleed through into the previously-returned ``ClaudeOptions``."""
        model = ClaudeOptionsModel(plan_mode=False, no_memory=False)
        opts = model.to_options()

        model.plan_mode = True
        model.no_memory = True

        assert opts.plan_mode is False
        assert opts.no_memory is False


class TestSessionToWireDictKind:
    """``session_to_wire_dict`` must serialise the ``kind`` field through
    ``info.kind.value`` so SHELL and AUTO_SCAN sessions don't silently wire
    as ``"claude"``. The existing ``TestProxySessionsWireShape`` tests only
    exercise the default (CLAUDE) — a regression that hard-codes the value
    to ``"claude"`` instead of ``info.kind.value`` would slip past them.
    """

    @pytest.mark.parametrize(
        ("kind_enum", "expected_value"),
        [
            ("CLAUDE", "claude"),
            ("CODEX", "codex"),
            ("AUTO_SCAN", "auto_scan"),
            ("SHELL", "shell"),
        ],
    )
    def test_kind_round_trips_via_enum_value(self, kind_enum, expected_value):
        """Every member of ``SessionKind`` must wire as its ``.value`` string,
        and every value must round-trip cleanly through ``SessionKind(...)``
        so the Android client can deserialise without an extra mapping table.
        """
        from datetime import datetime, timezone

        from mobaide.core.models import (
            SessionInfo,
            SessionKind,
            SessionState,
            session_to_wire_dict,
        )

        info = SessionInfo(
            session_id="sid",
            state=SessionState.RUNNING,
            created_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
            pid=42,
            command=["bash"],
            cwd="/work",
            kind=SessionKind[kind_enum],
        )

        wire = session_to_wire_dict(info)

        assert wire["kind"] == expected_value
        # Round-trip: the wire value must reconstruct the same enum member,
        # so a future field rename here doesn't silently desync producer
        # and consumer.
        assert SessionKind(wire["kind"]) is SessionKind[kind_enum]

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

    def test_defaults_are_none_on_session_info(self):
        info = _attention_info()
        assert info.attention is None
        assert info.exit_reason is None

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


class TestSessionEventAttentionField:
    """``SessionEvent`` gained an optional ``attention`` carrying the trigger
    reason; ``None`` marks a clear, not a notify trigger.
    """

    def test_attention_defaults_to_none(self):
        from mobaide.core.models import SessionEvent

        ev = SessionEvent(event_type="output", session_id="s1")
        assert ev.attention is None

    def test_attention_carries_reason_when_set(self):
        from mobaide.core.models import SessionEvent

        ev = SessionEvent(
            event_type="attention", session_id="s1", attention="turn_finished",
        )
        assert ev.attention == "turn_finished"


class TestCodexToOptions:
    """``CodexOptionsModel.to_options()`` must return a real ``CodexOptions``
    dataclass carrying every field verbatim — the same drift guard as the
    Claude DTO so a future field addition can't silently slip on the Codex path.
    """

    def test_round_trip_all_fields_populated(self):
        from mobaide.agent.sessions.models import CodexOptionsModel
        from mobaide.core.codex_launcher import CodexOptions

        model = CodexOptionsModel(
            model="gpt-5.4-codex",
            sandbox="danger-full-access",
            approval="on-request",
            reasoning_effort="low",
            full_auto=True,
            initial_prompt="ship it",
        )
        opts = model.to_options()

        assert isinstance(opts, CodexOptions)
        assert opts.model == "gpt-5.4-codex"
        assert opts.sandbox == "danger-full-access"
        assert opts.approval == "on-request"
        assert opts.reasoning_effort == "low"
        assert opts.full_auto is True
        assert opts.initial_prompt == "ship it"

    def test_round_trip_defaults_match_v1_preset(self):
        from mobaide.agent.sessions.models import CodexOptionsModel
        from mobaide.core.codex_launcher import CodexOptions

        opts = CodexOptionsModel().to_options()

        assert isinstance(opts, CodexOptions)
        assert opts.model is None
        assert opts.sandbox == "workspace-write"
        assert opts.approval == "never"
        assert opts.reasoning_effort == "high"
        assert opts.full_auto is False
        assert opts.initial_prompt is None
        assert opts.worktree is False
        assert opts.branch_name is None

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

        for bad in ({"sandbox": "bogus"}, {"approval": "always"},
                    {"reasoning_effort": "ultra"}):
            with pytest.raises(pydantic.ValidationError):
                CodexOptionsModel.model_validate(bad)


class TestResolveSpawnArgs:
    """``resolve_spawn_args`` is the single normaliser the REST + relay create
    paths share: it picks the kind, applies the shell security guard, and
    guarantees exactly one options object is set for a managed session.
    """

    _SHELL = ["/bin/sh"]

    def _call(self, cli=None, claude=None, codex=None, cmd=None, env=None):
        from mobaide.core.models import resolve_spawn_args

        return resolve_spawn_args(cli, claude, codex, cmd, env, self._SHELL)

    def test_no_cli_no_options_is_shell_with_clean_env(self):
        from mobaide.core.models import SessionKind

        kind, command, env, claude, codex = self._call(
            cmd=["rm", "-rf"], env={"EVIL": "1"},
        )
        # Security guard: client command/env hints are dropped for shell.
        assert kind is SessionKind.SHELL
        assert command == self._SHELL
        assert env is None
        assert claude is None and codex is None

    def test_claude_options_only_infers_claude(self):
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.models import SessionKind

        kind, command, env, claude, codex = self._call(
            claude=ClaudeOptions(), cmd=["claude"], env={"X": "1"},
        )
        assert kind is SessionKind.CLAUDE
        assert command == ["claude"] and env == {"X": "1"}
        assert isinstance(claude, ClaudeOptions) and codex is None

    def test_codex_options_only_infers_codex(self):
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex = self._call(codex=CodexOptions())
        assert kind is SessionKind.CODEX
        assert isinstance(codex, CodexOptions) and claude is None

    def test_explicit_cli_codex_defaults_options_when_omitted(self):
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex = self._call(cli="codex")
        assert kind is SessionKind.CODEX
        assert isinstance(codex, CodexOptions) and claude is None

    def test_explicit_cli_claude_defaults_options_when_omitted(self):
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex = self._call(cli="claude")
        assert kind is SessionKind.CLAUDE
        assert isinstance(claude, ClaudeOptions) and codex is None

    def test_explicit_cli_codex_clears_stray_claude_options(self):
        from mobaide.core.claude_launcher import ClaudeOptions
        from mobaide.core.codex_launcher import CodexOptions
        from mobaide.core.models import SessionKind

        kind, _command, _env, claude, codex = self._call(
            cli="codex", claude=ClaudeOptions(), codex=CodexOptions(),
        )
        # Exactly one options object survives, matching the chosen kind.
        assert kind is SessionKind.CODEX
        assert codex is not None and claude is None
