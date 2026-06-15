"""WebSocket route (spec §5.10): /ws/chapters/{id}?token=<JWT>.

Handles presence, cursor relay, lock state. Close codes 4401 (auth) / 4403
(no permission) per spec §5.10.
"""
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from ..auth import decode_token
from ..database import engine
from ..deps import get_membership
from ..models import Chapter, User
from ..services.locks import lock_info_public, lock_manager
from ..services.ws_manager import Connection, room_manager

router = APIRouter()


@router.websocket("/ws/chapters/{chapter_id}")
async def chapter_ws(websocket: WebSocket, chapter_id: int, token: str = Query(default="")):
    # Auth (spec §5.10: 4401 on auth failure)
    user_id = decode_token(token)
    if user_id is None:
        await websocket.close(code=4401)
        return

    with Session(engine) as session:
        user = session.get(User, user_id)
        chapter = session.get(Chapter, chapter_id)
        if user is None or chapter is None or chapter.deleted_at is not None:
            await websocket.close(code=4403)
            return
        membership = get_membership(session, chapter.book_id, user_id)
        if membership is None:  # no permission (spec §5.10: 4403)
            await websocket.close(code=4403)
            return
        user_name = user.name

    await websocket.accept()
    conn = Connection(websocket, user_id, user_name)
    await room_manager.connect(chapter_id, conn)

    # Send initial presence + current lock state
    await room_manager.broadcast_presence(chapter_id)
    lock = lock_manager.get(chapter_id)
    await websocket.send_json({"type": "lock_changed",
                               "lock_owner": lock["user_id"] if lock else None})

    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "ping":
                # Refresh lock if this user holds it (idle timer, FR-45)
                lock_manager.refresh(chapter_id, user_id)
                await websocket.send_json({"type": "pong"})
            elif mtype == "cursor":
                # Relay cursor to others (spec FR-51)
                await room_manager.broadcast(
                    chapter_id,
                    {"type": "cursor", "user": {"user_id": user_id, "name": user_name},
                     "position": msg.get("position")},
                    exclude=websocket,
                )
    except WebSocketDisconnect:
        pass
    finally:
        await room_manager.disconnect(chapter_id, conn)
        # Release lock on disconnect (spec FR-45)
        if lock_manager.release(chapter_id, user_id):
            await room_manager.broadcast(
                chapter_id, {"type": "lock_changed", "lock_owner": None}
            )
        await room_manager.broadcast_presence(chapter_id)
