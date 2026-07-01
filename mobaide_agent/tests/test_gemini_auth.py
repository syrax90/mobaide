from __future__ import annotations

import json
import os
from pathlib import Path

from mobaide.core.gemini_auth import gemini_auth_present, gemini_home


# gemini_home


def test_gemini_home_uses_env_override(tmp_path: Path):
    assert gemini_home({"GEMINI_HOME": str(tmp_path)}) == str(tmp_path)


def test_gemini_home_defaults_to_user_dot_gemini():
    expected = os.path.join(os.path.expanduser("~"), ".gemini")
    assert gemini_home({}) == expected


# gemini_auth_present — env credentials


def test_auth_present_true_when_gemini_api_key(tmp_path: Path):
    env = {"GEMINI_HOME": str(tmp_path), "GEMINI_API_KEY": "key"}
    assert gemini_auth_present(env) is True


def test_auth_present_true_when_google_api_key(tmp_path: Path):
    env = {"GEMINI_HOME": str(tmp_path), "GOOGLE_API_KEY": "key"}
    assert gemini_auth_present(env) is True


def test_auth_present_false_for_empty_api_key(tmp_path: Path):
    env = {"GEMINI_HOME": str(tmp_path), "GEMINI_API_KEY": ""}
    assert gemini_auth_present(env) is False


def test_auth_present_true_when_application_credentials_file_exists(tmp_path: Path):
    creds = tmp_path / "sa.json"
    creds.write_text("{}", encoding="utf-8")
    env = {
        "GEMINI_HOME": str(tmp_path / "empty"),
        "GOOGLE_APPLICATION_CREDENTIALS": str(creds),
    }
    assert gemini_auth_present(env) is True


def test_auth_present_false_when_application_credentials_file_missing(tmp_path: Path):
    env = {
        "GEMINI_HOME": str(tmp_path),
        "GOOGLE_APPLICATION_CREDENTIALS": str(tmp_path / "nope.json"),
    }
    assert gemini_auth_present(env) is False


# gemini_auth_present — filesystem credentials under ~/.gemini


def test_auth_present_true_when_cached_oauth_creds(tmp_path: Path):
    (tmp_path / "oauth_creds.json").write_text("{}", encoding="utf-8")
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is True


def test_auth_present_true_when_settings_flat_selected_auth_type(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"selectedAuthType": "oauth-personal"}), encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is True


def test_auth_present_true_when_settings_nested_selected_type(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"security": {"auth": {"selectedType": "gemini-api-key"}}}),
        encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is True


def test_auth_present_false_when_settings_selected_auth_empty(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"selectedAuthType": ""}), encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is False


def test_auth_present_false_when_settings_has_no_auth_type(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"theme": "dark"}), encoding="utf-8",
    )
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is False


def test_auth_present_false_when_settings_malformed(tmp_path: Path):
    (tmp_path / "settings.json").write_text("{ not json", encoding="utf-8")
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is False


def test_auth_present_false_when_nothing_present(tmp_path: Path):
    """Empty home, no env credential — the deterministic unauthenticated case."""
    assert gemini_auth_present({"GEMINI_HOME": str(tmp_path)}) is False
