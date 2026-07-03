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
        # 主事件迴圈參照（WS 連線所在）。同步端點在 worker thread 內無執行中
        # 迴圈，需透過此參照以 run_coroutine_threadsafe 排入廣播（Q1）。
        self._loop = None

    def set_loop(self, loop):
        """記錄主事件迴圈；於 app lifespan 啟動時呼叫（main.py）。"""
        self._loop = loop

    def broadcast_threadsafe(self, chapter_id: int, message: dict[str, Any]):
        """從同步端點（threadpool worker thread，無執行中迴圈）安全觸發廣播。

        fire-and-forget：將 broadcast() 排入主迴圈，不呼叫 .result() 阻塞。
        無 loop（例如尚未啟動或非伺服器情境）時為安全 no-op。
        """
        loop = self._loop
        if loop is None:
            return
        try:
            import asyncio as _asyncio
            _asyncio.run_coroutine_threadsafe(
                self.broadcast(chapter_id, message), loop
            )
        except Exception:
            pass  # 廣播為盡力而為，不可讓其錯誤影響請求主流程

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
