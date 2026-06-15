"""In-memory chapter soft-lock manager (spec §1.4, FR-44/45).

A single process holds lock state in memory (spec §8.3). Each lock has an owner
user id and an expiry; idle/disconnect releases it (spec FR-45).
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import settings


class LockManager:
    def __init__(self):
        self._locks: dict[int, dict] = {}  # chapter_id -> {user_id, name, expires_at}
        self._lock = threading.Lock()

    def _is_expired(self, entry: dict) -> bool:
        return datetime.now(timezone.utc) >= entry["expires_at"]

    def get(self, chapter_id: int) -> Optional[dict]:
        with self._lock:
            entry = self._locks.get(chapter_id)
            if entry and self._is_expired(entry):
                del self._locks[chapter_id]
                return None
            return entry

    def acquire(self, chapter_id: int, user_id: int, name: str) -> tuple[bool, dict]:
        """Try to acquire/refresh the lock. Returns (success, lock_info)."""
        with self._lock:
            entry = self._locks.get(chapter_id)
            now = datetime.now(timezone.utc)
            if entry and not (now >= entry["expires_at"]) and entry["user_id"] != user_id:
                return False, entry  # held by someone else
            expires = now + timedelta(seconds=settings.lock_idle_seconds)
            new_entry = {"user_id": user_id, "name": name, "expires_at": expires}
            self._locks[chapter_id] = new_entry
            return True, new_entry

    def refresh(self, chapter_id: int, user_id: int) -> bool:
        with self._lock:
            entry = self._locks.get(chapter_id)
            if entry and entry["user_id"] == user_id:
                entry["expires_at"] = datetime.now(timezone.utc) + timedelta(
                    seconds=settings.lock_idle_seconds
                )
                return True
            return False

    def release(self, chapter_id: int, user_id: int) -> bool:
        with self._lock:
            entry = self._locks.get(chapter_id)
            if entry and entry["user_id"] == user_id:
                del self._locks[chapter_id]
                return True
            return False

    def holder(self, chapter_id: int) -> Optional[int]:
        entry = self.get(chapter_id)
        return entry["user_id"] if entry else None


lock_manager = LockManager()


def lock_info_public(entry: Optional[dict]) -> Optional[dict]:
    if not entry:
        return None
    return {
        "lock_owner": entry["user_id"],
        "name": entry["name"],
        "expires_at": entry["expires_at"].isoformat(),
    }
