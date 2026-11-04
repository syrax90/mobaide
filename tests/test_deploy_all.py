"""Pins the step ordering, SSH cooldown and health fallback of deploy/prod/deploy_all.py."""
from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import deploy_all  # noqa: E402

MANIFEST = {"central_server": "0.2.20", "mobaide_agent": "0.3.21", "web": "0.1.16", "android": "3.6.15"}


def _args(**kw: object) -> argparse.Namespace:
    base = {"ssh_cooldown": 300, "server_verify_timeout": 1, "yes": True}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[str] = []
    waits: list[float] = []
    exit_codes: dict[str, int] = {}
    clock = {"t": 0.0}

    def fake_run(cmd: list[str], cwd: object = None) -> types.SimpleNamespace:
        script = Path(cmd[1]).name
        calls.append(script)
        clock["t"] += 60  # every step takes a minute
        return types.SimpleNamespace(returncode=exit_codes.get(script, 0))

    monkeypatch.setattr(deploy_all.subprocess, "run", fake_run)
    monkeypatch.setattr(deploy_all.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(deploy_all, "wait_cooldown", lambda s: waits.append(s))
    monkeypatch.setattr(deploy_all, "state_path", lambda: tmp_path / "state.json")
    return types.SimpleNamespace(calls=calls, waits=waits, exit_codes=exit_codes)


def test_runs_in_fixed_order_and_cools_down_only_before_web(harness) -> None:
    results = deploy_all.run_plan(list(deploy_all.COMPONENTS), MANIFEST, _args())

    assert harness.calls == ["deploy_central.py", "release_agent.py", "deploy_web.py", "release_android.py"]
    # The agent step (60 s) already ran inside the 300 s window.
    assert harness.waits == [240]
    assert all(o.startswith("shipped") for _, o in results)
    assert deploy_all.load_state()["web"]["version"] == "0.1.16"


def test_failure_stops_the_remaining_steps(harness) -> None:
    harness.exit_codes["deploy_central.py"] = 1

    results = deploy_all.run_plan(list(deploy_all.COMPONENTS), MANIFEST, _args())

    assert harness.calls == ["deploy_central.py"]
    assert [o for _, o in results] == ["FAILED (exit 1)", "not run", "not run", "not run"]


def test_unconfirmed_health_is_accepted_when_version_is_live(harness, monkeypatch) -> None:
    harness.exit_codes["deploy_central.py"] = deploy_all.EXIT_HEALTH_UNCONFIRMED
    monkeypatch.setattr(deploy_all, "live_server_version", lambda: "0.2.20")

    plan = [c for c in deploy_all.COMPONENTS if c.key in ("server", "web")]
    results = deploy_all.run_plan(plan, MANIFEST, _args())

    assert harness.calls == ["deploy_central.py", "deploy_web.py"]
    assert harness.waits == [300]
    assert [o for _, o in results] == ["shipped 0.2.20", "shipped 0.1.16"]


def test_unconfirmed_health_with_old_version_fails(harness, monkeypatch) -> None:
    harness.exit_codes["deploy_central.py"] = deploy_all.EXIT_HEALTH_UNCONFIRMED
    monkeypatch.setattr(deploy_all, "live_server_version", lambda: "0.2.19")
    monkeypatch.setattr(deploy_all.time, "sleep", lambda s: None)
    ticks = iter(range(0, 10_000, 1))
    monkeypatch.setattr(deploy_all.time, "monotonic", lambda: next(ticks))

    results = deploy_all.run_plan(list(deploy_all.COMPONENTS[:1]), MANIFEST, _args())

    assert results == [("central_server", f"FAILED (exit {deploy_all.EXIT_HEALTH_UNCONFIRMED})")]


@pytest.mark.parametrize(
    ("live", "target", "outdated"),
    [("0.2.19", "0.2.20", True), ("0.2.20", "0.2.20", False), ("0.10.0", "0.9.9", False), (None, "1.0.0", None)],
)
def test_status_outdated(live: str | None, target: str, outdated: bool | None) -> None:
    assert deploy_all.Status(target, live, "x").outdated is outdated


def test_parse_only_rejects_unknown() -> None:
    assert deploy_all.parse_only("web, server") == {"web", "server"}
    with pytest.raises(argparse.ArgumentTypeError):
        deploy_all.parse_only("ios")
