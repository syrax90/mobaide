"""Unit tests for ``mobaide.agent.auth.user_store.UserStore``.

These are internals tests for the JSON+bcrypt filesystem user store. HTTP
route coverage lives in ``tests/test_auth.py`` — do NOT duplicate it here.
"""
from __future__ import annotations

import json
from pathlib import Path

import bcrypt
import pytest

from mobaide.agent.auth.user_store import UserStore


# ---------------------------------------------------------------------------
# Speed up bcrypt for the whole module: default rounds=12 (~300ms each) is
# painful across many tests. Rounds=4 is still a real bcrypt hash but ~100x
# faster. We patch the ``bcrypt`` module that ``user_store`` imported — since
# ``user_store`` does ``import bcrypt`` and then calls ``bcrypt.gensalt()``,
# patching the global module object is enough.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    real_gensalt = bcrypt.gensalt

    def _fake_gensalt(*args, **kwargs):
        # Swallow any caller-provided rounds/prefix/etc. — we always use fast
        # rounds here because the production defaults are too slow for tests.
        del args, kwargs
        return real_gensalt(rounds=4)

    monkeypatch.setattr(bcrypt, "gensalt", _fake_gensalt)
    yield


_ = _fast_bcrypt  # satisfy "not accessed" pyright hint


class TestLoad:
    def test_creates_empty_file_when_missing(self, tmp_path):
        path = tmp_path / "users.json"
        assert not path.exists()

        store = UserStore(str(path))
        store.load()

        assert path.exists()
        # Created file should be valid JSON and parse to an empty dict.
        assert json.loads(path.read_text()) == {}
        assert store.user_exists("anyone") is False

    def test_empty_file_yields_no_users(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text("")

        store = UserStore(str(path))
        store.load()

        assert store.user_exists("anyone") is False
        assert store.verify("anyone", "pw") is False

    def test_whitespace_only_file_yields_no_users(self, tmp_path):
        path = tmp_path / "users.json"
        path.write_text("   \n\t  \n")

        store = UserStore(str(path))
        store.load()

        assert store.user_exists("anyone") is False

    def test_pre_populated_file_is_read(self, tmp_path):
        path = tmp_path / "users.json"
        hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
        path.write_text(json.dumps({"preexisting": {"password_hash": hashed}}))

        store = UserStore(str(path))
        store.load()

        assert store.user_exists("preexisting") is True
        assert store.verify("preexisting", "secret") is True
        assert store.verify("preexisting", "wrong") is False


class TestAddAndVerify:
    def test_add_then_verify_happy_path(self, tmp_path):
        store = UserStore(str(tmp_path / "users.json"))
        store.load()

        store.add_user("alice", "correct-horse-battery-staple")

        assert store.user_exists("alice") is True
        assert store.verify("alice", "correct-horse-battery-staple") is True

    def test_verify_wrong_password_returns_false(self, tmp_path):
        store = UserStore(str(tmp_path / "users.json"))
        store.load()
        store.add_user("alice", "pw1")

        assert store.verify("alice", "pw2") is False

    def test_verify_missing_user_returns_false(self, tmp_path):
        store = UserStore(str(tmp_path / "users.json"))
        store.load()

        # No user added; must not raise.
        assert store.verify("ghost", "any") is False

    def test_user_exists_true_and_false(self, tmp_path):
        store = UserStore(str(tmp_path / "users.json"))
        store.load()
        store.add_user("alice", "pw")

        assert store.user_exists("alice") is True
        assert store.user_exists("bob") is False

    def test_add_user_overwrites_existing_entry(self, tmp_path):
        store = UserStore(str(tmp_path / "users.json"))
        store.load()

        store.add_user("alice", "pw1")
        store.add_user("alice", "pw2")

        assert store.verify("alice", "pw1") is False
        assert store.verify("alice", "pw2") is True


class TestPersistence:
    def test_add_user_persists_to_disk(self, tmp_path):
        path = tmp_path / "users.json"
        store = UserStore(str(path))
        store.load()

        store.add_user("alice", "plaintext-pw")

        on_disk = json.loads(path.read_text())
        assert "alice" in on_disk
        assert "password_hash" in on_disk["alice"]
        # bcrypt hashes always start with "$2" (e.g. $2a$, $2b$, $2y$).
        assert on_disk["alice"]["password_hash"].startswith("$2")

    def test_persisted_hash_is_not_plaintext(self, tmp_path):
        path = tmp_path / "users.json"
        store = UserStore(str(path))
        store.load()

        store.add_user("alice", "plaintext-pw")

        on_disk = json.loads(path.read_text())
        assert on_disk["alice"]["password_hash"] != "plaintext-pw"
        # And the hash must round-trip via verify.
        reloaded = UserStore(str(path))
        reloaded.load()
        assert reloaded.verify("alice", "plaintext-pw") is True


class TestLoadOmittedFootgun:
    """Document the subtle ``add_user`` without ``load`` overwrite behavior.

    ``UserStore.__init__`` starts with ``self._users = {}`` and ``add_user``
    does NOT call ``load`` itself. A caller that constructs a fresh UserStore
    on top of a pre-populated file and immediately calls ``add_user`` will
    clobber the existing file with just the newly added user. These tests
    pin that behavior so any future "auto-load" refactor shows up loudly.
    """

    def test_add_user_without_load_overwrites_existing_file(self, tmp_path: Path):
        path = tmp_path / "users.json"
        # Pre-populate directly on disk.
        existing_hash = bcrypt.hashpw(b"old-pw", bcrypt.gensalt()).decode()
        path.write_text(json.dumps({"preexisting": {"password_hash": existing_hash}}))

        # Construct a NEW store pointing at the same file. Intentionally skip load().
        store = UserStore(str(path))
        store.add_user("newcomer", "new-pw")

        on_disk = json.loads(path.read_text())
        # The previously-stored user is GONE.
        assert "preexisting" not in on_disk
        assert list(on_disk.keys()) == ["newcomer"]

    def test_add_user_after_load_preserves_existing_file(self, tmp_path: Path):
        path = tmp_path / "users.json"
        existing_hash = bcrypt.hashpw(b"old-pw", bcrypt.gensalt()).decode()
        path.write_text(json.dumps({"preexisting": {"password_hash": existing_hash}}))

        store = UserStore(str(path))
        store.load()
        store.add_user("newcomer", "new-pw")

        on_disk = json.loads(path.read_text())
        assert "preexisting" in on_disk
        assert "newcomer" in on_disk
        # And both verify.
        assert store.verify("preexisting", "old-pw") is True
        assert store.verify("newcomer", "new-pw") is True
