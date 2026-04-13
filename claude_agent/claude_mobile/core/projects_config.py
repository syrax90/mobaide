from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProjectConfig:
    """Per-project configuration shared by mobile sessions and Linear auto-scan.

    Mirrors the schema used by ``claude_auto_executor.ps1`` ``$PROJECTS``:
    every project has a working directory and may optionally provide a
    Linear API key, a conda environment to activate, and default Claude
    flags applied when launching a session for that project.
    """

    name: str
    path: str
    linear_api_key: str | None = None
    conda_env: str | None = None
    default_flags: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectsConfig:
    """Wrapper around the configured project map.

    The ``PROJECTS`` env var is JSON of one of the forms::

        {"My Project": {"path": "C:/...", "linear_api_key": "lin_..."}}
        {"My Project": {"path": "C:/...", "conda_env": "ml", "default_flags": {...}}}

    Legacy string values (``{"Name": "C:/..."}``) are skipped — they
    carry no Linear key and were never used by the agent.
    """

    projects: dict[str, ProjectConfig]

    # -- Construction --

    @classmethod
    def from_env(cls) -> ProjectsConfig | None:
        raw = os.getenv("PROJECTS")
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"PROJECTS environment variable contains invalid JSON: {exc}"
            ) from exc
        return cls.from_dict(parsed)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProjectsConfig | None:
        projects: dict[str, ProjectConfig] = {}
        for name, value in data.items():
            if isinstance(value, str):
                # Legacy form — no API key, skip.
                continue
            if not isinstance(value, dict):
                continue
            path = value.get("path")
            if not isinstance(path, str):
                continue
            api_key = value.get("linear_api_key")
            conda_env = value.get("conda_env")
            default_flags = value.get("default_flags") or {}
            projects[name] = ProjectConfig(
                name=name,
                path=path,
                linear_api_key=api_key if isinstance(api_key, str) else None,
                conda_env=conda_env if isinstance(conda_env, str) else None,
                default_flags=default_flags if isinstance(default_flags, dict) else {},
            )
        if not projects:
            return None
        return cls(projects=projects)

    # -- Lookup --

    def get(self, name: str) -> ProjectConfig | None:
        return self.projects.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.projects

    def list_public(self) -> list[dict[str, object]]:
        """Return a list of project descriptors safe to send to clients.

        Linear API keys are NEVER included.
        """
        return [
            {
                "name": p.name,
                "path": p.path,
                "has_linear_key": p.linear_api_key is not None,
                "is_git": (Path(p.path) / ".git").exists(),
                "default_flags": dict(p.default_flags),
            }
            for p in self.projects.values()
        ]
