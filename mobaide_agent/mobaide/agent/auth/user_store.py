from __future__ import annotations

import json
import logging
from pathlib import Path

import bcrypt

logger = logging.getLogger(__name__)


class UserStore:
    """JSON-file backed user store with bcrypt password hashing."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._users: dict[str, dict] = {}

    def load(self) -> None:
        """Load users from the JSON file. Creates an empty file if missing."""
        if not self._path.exists():
            self._path.write_text("{}")
            logger.info("Created empty user store at %s", self._path)
        raw = self._path.read_text()
        self._users = json.loads(raw) if raw.strip() else {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(self._users, indent=2))

    def user_exists(self, username: str) -> bool:
        return username in self._users

    def verify(self, username: str, password: str) -> bool:
        """Verify username/password against bcrypt hash. Returns False if user not found."""
        user = self._users.get(username)
        if not user:
            return False
        stored_hash = user["password_hash"].encode()
        return bcrypt.checkpw(password.encode(), stored_hash)

    def add_user(self, username: str, password: str) -> None:
        """Add a user with bcrypt-hashed password. Overwrites if exists."""
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        self._users[username] = {"password_hash": hashed}
        self._save()
        logger.info("Added user '%s'", username)
