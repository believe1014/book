"""In-memory WebSocket room manager (spec §5.10, FR-50/51).

Rooms are keyed by chapter id. Each connection tracks the user. Broadcast
helpers send presence/cursor/content_updated/lock_changed events.
"""
import asyncio
from typing import Any

from fastapi import WebSocket


class Connection:
    def __init__(self, ws: WebSocket, user_id: int, name: str):
        self.ws = ws
        self.user_id = user_id
        self.name = name


class RoomManager:
    def __init__(self):
        self._rooms: dict[int, list[Connection]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, chapter_id: int, conn: Connection):
        async with self._lock:
            self._rooms.setdefault(chapter_id, []).append(conn)

    async def disconnect(self, chapter_id: int, conn: Connection):
        async with self._lock:
            room = self._rooms.get(chapter_id, [])
            if conn in room:
                room.remove(conn)
            if not room and chapter_id in self._rooms:
                del self._rooms[chapter_id]

    def presence(self, chapter_id: int) -> list[dict]:
        seen = {}
        for c in self._rooms.get(chapter_id, []):
            seen[c.user_id] = {"user_id": c.user_id, "name": c.name}
        return list(seen.values())

    async def broadcast(self, chapter_id: int, message: dict[str, Any], exclude: WebSocket | None = None):
        room = list(self._rooms.get(chapter_id, []))
        for conn in room:
            if exclude is not None and conn.ws is exclude:
                continue
            try:
                await conn.ws.send_json(message)
            except Exception:
                pass  # drop failed sends; disconnect handler cleans up

    async def broadcast_presence(self, chapter_id: int):
        await self.broadcast(chapter_id, {"type": "presence", "users": self.presence(chapter_id)})


room_manager = RoomManager()
