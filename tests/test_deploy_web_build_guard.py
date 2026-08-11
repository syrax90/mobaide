"""Pins the ``web/node_modules`` preflight guard in ``deploy/prod/deploy_web.py``.

The guard has to run *before* npm is spawned, otherwise the actionable message
is buried under vite's output and the operator still sees the 127.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "deploy" / "prod"))

import deploy_web  # noqa: E402


def _fake_npm(monkeypatch: pytest.MonkeyPatch, run: object) -> None:
    monkeypatch.setattr(
        deploy_web, "shutil", types.SimpleNamespace(which=lambda tool: "/bin/npm")
    )
    monkeypatch.setattr(deploy_web, "subprocess", types.SimpleNamespace(run=run))


class TestBuildWebNodeModulesGuard:
    def test_missing_node_modules_fails_before_npm_is_spawned(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def never_run(*args: object, **kwargs: object) -> None:
            raise AssertionError("npm run build must not be spawned")

        monkeypatch.setattr(deploy_web, "WEB_DIR", tmp_path)
        _fake_npm(monkeypatch, never_run)

        with pytest.raises(deploy_web.DeployError) as excinfo:
            deploy_web.build_web(skip_build=False)

        message = str(excinfo.value)
        assert "node_modules" in message
        assert "npm ci" in message

    def test_installed_dependencies_let_the_build_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The positive control: the guard must gate on the directory, not
        refuse unconditionally.
        """
        web_dir = tmp_path / "web"
        (web_dir / "node_modules").mkdir(parents=True)
        dist_dir = web_dir / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<html></html>", encoding="utf-8")
        calls: list[tuple[list[str], Path]] = []

        def record(cmd: list[str], cwd: Path) -> types.SimpleNamespace:
            calls.append((cmd, cwd))
            return types.SimpleNamespace(returncode=0)

        monkeypatch.setattr(deploy_web, "WEB_DIR", web_dir)
        monkeypatch.setattr(deploy_web, "DIST_DIR", dist_dir)
        _fake_npm(monkeypatch, record)

        deploy_web.build_web(skip_build=False)

        assert calls == [(["/bin/npm", "run", "build"], web_dir)]
