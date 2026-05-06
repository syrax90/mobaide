"""Tests for ``mobaide.agent.app._resolve_env_file``.

Covers the two-step resolution: install-prefix path first, then a
developer-checkout fallback adjacent to the package source. Both must
miss before the function returns ``None`` and pydantic-settings is left
to read process env vars only.
"""
from __future__ import annotations

from pathlib import Path

from mobaide.agent import app as agent_app
from mobaide.agent.app import _resolve_env_file


def _make_fake_package_root(tmp_path: Path) -> Path:
    """Build a fake ``mobaide_agent/mobaide/agent/app.py`` inside ``tmp_path``.

    Returns the path of the fake ``app.py`` whose ``parents[2]`` is the
    fake package root (the directory where the dev fallback expects to
    find ``.env``).
    """
    fake_app = tmp_path / "fake_pkg" / "mobaide" / "agent" / "app.py"
    fake_app.parent.mkdir(parents=True, exist_ok=True)
    fake_app.write_text("# fake module for test", encoding="utf-8")
    return fake_app


class TestResolveEnvFile:
    def test_install_prefix_hit_returns_prefix_env(self, tmp_path, monkeypatch):
        prefix = tmp_path / "install_prefix"
        env_path = prefix / "mobaide_agent" / ".env"
        env_path.parent.mkdir(parents=True)
        env_path.write_text("JWT_SECRET_KEY=from-install\n", encoding="utf-8")

        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))

        result = _resolve_env_file()

        assert result == env_path

    def test_dev_fallback_hit_when_install_prefix_missing(
        self, tmp_path, monkeypatch
    ):
        empty_prefix = tmp_path / "empty_prefix"
        empty_prefix.mkdir()
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(empty_prefix))

        fake_app = _make_fake_package_root(tmp_path)
        dev_env = fake_app.parents[2] / ".env"
        dev_env.write_text("JWT_SECRET_KEY=from-dev\n", encoding="utf-8")
        monkeypatch.setattr(agent_app, "__file__", str(fake_app))

        result = _resolve_env_file()

        assert result == dev_env

    def test_install_prefix_takes_priority_over_dev_fallback(
        self, tmp_path, monkeypatch
    ):
        prefix = tmp_path / "install_prefix"
        prefix_env = prefix / "mobaide_agent" / ".env"
        prefix_env.parent.mkdir(parents=True)
        prefix_env.write_text("JWT_SECRET_KEY=prod\n", encoding="utf-8")
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(prefix))

        fake_app = _make_fake_package_root(tmp_path)
        dev_env = fake_app.parents[2] / ".env"
        dev_env.write_text("JWT_SECRET_KEY=dev\n", encoding="utf-8")
        monkeypatch.setattr(agent_app, "__file__", str(fake_app))

        result = _resolve_env_file()

        assert result == prefix_env

    def test_both_missing_returns_none(self, tmp_path, monkeypatch):
        empty_prefix = tmp_path / "empty_prefix"
        empty_prefix.mkdir()
        monkeypatch.setenv("MOBAIDE_AGENT_PREFIX", str(empty_prefix))

        fake_app = _make_fake_package_root(tmp_path)
        monkeypatch.setattr(agent_app, "__file__", str(fake_app))

        result = _resolve_env_file()

        assert result is None
