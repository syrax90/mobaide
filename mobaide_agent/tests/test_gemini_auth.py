from __future__ import annotations

import json
import os
from pathlib import Path

from mobaide.core.gemini_auth import (
    gemini_auth_present,
    gemini_home,
    gemini_home_root,
)


def _config_dir(root: Path) -> Path:
    """Create and return ``<root>/.gemini`` — Gemini's config dir under the home root."""
    d = root / ".gemini"
    d.mkdir(parents=True, exist_ok=True)
    return d


# gemini_home_root / gemini_home


def test_gemini_home_root_uses_env_override(tmp_path: Path):
    assert gemini_home_root({"GEMINI_CLI_HOME": str(tmp_path)}) == str(tmp_path)


def test_gemini_home_root_defaults_to_user_home():
    assert gemini_home_root({}) == os.path.expanduser("~")


def test_gemini_home_is_dot_gemini_inside_the_root(tmp_path: Path):
    assert gemini_home({"GEMINI_CLI_HOME": str(tmp_path)}) == str(tmp_path / ".gemini")


def test_gemini_home_defaults_to_user_dot_gemini():
    expected = os.path.join(os.path.expanduser("~"), ".gemini")
    assert gemini_home({}) == expected


# gemini_auth_present — env credentials


def test_auth_present_true_when_gemini_api_key(tmp_path: Path):
    env = {"GEMINI_CLI_HOME": str(tmp_path), "GEMINI_API_KEY": "key"}
    assert gemini_auth_present(env) is True


def test_auth_present_true_when_google_api_key(tmp_path: Path):
    env = {"GEMINI_CLI_HOME": str(tmp_path), "GOOGLE_API_KEY": "key"}
    assert gemini_auth_present(env) is True


def test_auth_present_false_for_empty_api_key(tmp_path: Path):
    env = {"GEMINI_CLI_HOME": str(tmp_path), "GEMINI_API_KEY": ""}
    assert gemini_auth_present(env) is False


def test_auth_present_true_when_application_credentials_file_exists(tmp_path: Path):
    creds = tmp_path / "sa.json"
    creds.write_text("{}", encoding="utf-8")
    env = {
        "GEMINI_CLI_HOME": str(tmp_path / "empty"),
        "GOOGLE_APPLICATION_CREDENTIALS": str(creds),
    }
    assert gemini_auth_present(env) is True


def test_auth_present_false_when_application_credentials_file_missing(tmp_path: Path):
    env = {
        "GEMINI_CLI_HOME": str(tmp_path),
        "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "nope.json"),
    }
    assert gemini_auth_present(env) is False


# gemini_auth_present — filesystem credentials under <root>/.gemini


def test_auth_present_true_when_cached_oauth_creds(tmp_path: Path):
    (_config_dir(tmp_path) / "oauth_creds.json").write_text("{}", encoding="utf-8")
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is True


def test_auth_present_true_when_settings_flat_selected_auth_type(tmp_path: Path):
    (_config_dir(tmp_path) / "settings.json").write_text(
        json.dumps({"selectedAuthType": "oauth-personal"}), encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is True


def test_auth_present_true_when_settings_nested_selected_type(tmp_path: Path):
    (_config_dir(tmp_path) / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}),
        encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is True


def test_auth_present_false_when_settings_selected_auth_empty(tmp_path: Path):
    (_config_dir(tmp_path) / "settings.json").write_text(
        json.dumps({"selectedAuthType": ""}), encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is False


def test_auth_present_false_when_settings_has_no_auth_type(tmp_path: Path):
    (_config_dir(tmp_path) / "settings.json").write_text(
        json.dumps({"theme": "dark"}), encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is False


def test_auth_present_false_when_settings_malformed(tmp_path: Path):
    (_config_dir(tmp_path) / "settings.json").write_text("{ not json", encoding="utf-8")
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is False


def test_auth_present_false_when_nothing_present(tmp_path: Path):
    """Empty home, no env credential, no cwd — the deterministic unauthenticated case."""
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is False


# gemini_auth_present — the .env file Gemini itself auto-loads


def test_auth_present_true_when_project_dotenv_has_key(tmp_path: Path):
    """A ``.env`` at the launch cwd (which the real ``gemini`` auto-loads) authenticates."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("GEMINI_API_KEY=from-dotenv\n", encoding="utf-8")
    env = {"GEMINI_CLI_HOME": str(tmp_path / "empty_home")}
    assert gemini_auth_present(env, cwd=str(project)) is True


def test_auth_present_true_when_project_gemini_dotenv_has_key(tmp_path: Path):
    """``.gemini/.env`` at the cwd is a recognised Gemini dotenv location."""
    project = tmp_path / "proj"
    (project / ".gemini").mkdir(parents=True)
    (project / ".gemini" / ".env").write_text("GOOGLE_API_KEY=k\n", encoding="utf-8")
    env = {"GEMINI_CLI_HOME": str(tmp_path / "empty_home")}
    assert gemini_auth_present(env, cwd=str(project)) is True


def test_auth_present_true_when_dotenv_found_walking_up_to_project_root(tmp_path: Path):
    """The search climbs from a nested cwd up to the repo root's ``.env`` (stops at .git)."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".env").write_text("GEMINI_API_KEY=k\n", encoding="utf-8")
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    env = {"GEMINI_CLI_HOME": str(tmp_path / "empty_home")}
    assert gemini_auth_present(env, cwd=str(nested)) is True


def test_auth_present_true_via_home_gemini_dotenv_fallback(tmp_path: Path):
    """With no cwd dotenv, ``<root>/.gemini/.env`` is the fallback Gemini loads."""
    (_config_dir(tmp_path) / ".env").write_text("GEMINI_API_KEY=k\n", encoding="utf-8")
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is True


def test_auth_present_true_via_root_dotenv_fallback(tmp_path: Path):
    """``<root>/.env`` (home-level) is the last dotenv source Gemini consults."""
    (tmp_path / ".env").write_text("GEMINI_API_KEY=k\n", encoding="utf-8")
    assert gemini_auth_present({"GEMINI_CLI_HOME": str(tmp_path)}) is True


def test_auth_present_false_when_first_dotenv_found_has_no_credential(tmp_path: Path):
    """First ``.env`` found wins (Gemini does not merge); one without a key stays False."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("FOO=bar\nGEMINI_API_KEY=\n", encoding="utf-8")
    env = {"GEMINI_CLI_HOME": str(tmp_path / "empty_home")}
    assert gemini_auth_present(env, cwd=str(project)) is False


def test_auth_present_false_when_dotenv_is_non_utf8(tmp_path: Path):
    """A non-UTF-8 ``.env`` (UTF-16 from PowerShell ``>`` redirection) degrades to
    False (→ clean 409), never raising a UnicodeDecodeError up the spawn path."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_bytes("GEMINI_API_KEY=secret\n".encode("utf-16"))
    env = {"GEMINI_CLI_HOME": str(tmp_path / "empty_home")}
    assert gemini_auth_present(env, cwd=str(project)) is False
