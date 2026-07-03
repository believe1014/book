"""WebSocket 即時協作路由整合測試（見 app/routers/ws.py + app/services/ws_manager.py）。

端點：/ws/chapters/{chapter_id}?token=<JWT>（token 帶在 query string，非 header
也非 subprotocol）。認證失敗以 close code 4401 關閉；無權限（章節/書不存在、
非該書成員、章節已軟刪）以 4403 關閉（spec §5.10）。

連線成功後，伺服器依序主動推送兩則訊息：
1. {"type": "presence", "users": [...]}（broadcast_presence，含自己）
2. {"type": "lock_changed", "lock_owner": ...}（僅推給自己，目前鎖狀態）

之後支援的 client -> server 訊息：
- {"type": "ping"} -> refresh 持有中的鎖（FR-45）並回 {"type": "pong"}
- {"type": "cursor", "position": ...} -> relay 給房間內其他人（不含自己），
  訊息夾帶 {"type": "cursor", "user": {"user_id", "name"}, "position": ...}

斷線時（finally 區塊）：釋放此使用者持有的鎖（若有）並廣播
lock_changed(None) + 最新 presence 給房間內其餘連線。

實測發現的重要現況（見下方對應測試與報告）：
- ws.py 內以 `await room_manager.broadcast(...)` 觸發的廣播（presence 更新、
  cursor relay、以及斷線時的 lock 釋放廣播）確實會送達其他連線中的 client，
  因為這些呼叫發生在原本就是 async 的 WebSocket handler 內，與 TestClient
  共用同一個事件迴圈（portal）。
- content.py 的 patch_content / acquire_lock / release_lock 是「同步」
  （def，非 async def）的 HTTP 端點，FastAPI 會將其丟到 worker thread 執行，
  該 thread 內沒有執行中的事件迴圈。舊實作的 `_broadcast_safe` 以
  `asyncio.get_event_loop()` 取迴圈會拋 RuntimeError 並被靜默吞掉，導致經由
  HTTP PATCH /content、POST /lock、DELETE /lock、POST /versions/{ver}/restore
  觸發的 content_updated / lock_changed 廣播從不送達 WS client（原 known-bug）。
- Q1 修復：RoomManager 於 lifespan 記錄主事件迴圈（set_loop），`_broadcast_safe`
  改委派 broadcast_threadsafe，以 `asyncio.run_coroutine_threadsafe` 將廣播排入
  主迴圈（fire-and-forget）。以下 `test_http_triggered_*_reaches_ws_client`
  即斷言「修復後」行為：REST 端點觸發後，已連線 WS client 在逾時內收到廣播。
"""
import json
import queue as _queue

import pytest
from starlette.testclient import WebSocketDisconnect

from app.services.locks import lock_manager


@pytest.fixture(autouse=True)
def _reset_locks():
    lock_manager._locks.clear()
    yield
    lock_manager._locks.clear()


# ---------- 輔助 ----------


def _create_book(client, owner, title="測試書"):
    r = client.post("/api/books", json={"title": title}, headers=owner["headers"])
    assert r.status_code == 200, r.text
    return r.json()["data"]["book"]["id"]


def _invite(client, owner, book_id, user_factory, email, role):
    member = user_factory(email=email)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": email, "role": role},
        headers=owner["headers"],
    )
    assert r.status_code == 200, r.text
    return member


def _create_chapter(client, headers, book_id, title="第一章"):
    r = client.post(
        f"/api/books/{book_id}/chapters", json={"title": title}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["chapter"]


def _ws_url(chapter_id: int, token: str) -> str:
    return f"/ws/chapters/{chapter_id}?token={token}"


def _recv(ws, timeout: float = 3.0):
    """帶逾時的接收，避免測試因協定不符而永久阻塞。

    回傳 None 代表逾時內未收到任何訊息（用來斷言「不會送達」的現況）。
    直接讀取底層 _send_queue 是因為 starlette 的 WebSocketTestSession.receive()
    沒有提供逾時參數，會無限阻塞。
    """
    try:
        message = ws._send_queue.get(timeout=timeout)
    except _queue.Empty:
        return None
    if isinstance(message, BaseException):
        raise message
    if message["type"] == "websocket.close":
        return {"__closed__": message.get("code")}
    return json.loads(message["text"])


def _recv_until(ws, target_type: str, timeout: float = 3.0):
    """在總逾時內持續接收，回傳第一則 type == target_type 的訊息。

    跳過其間夾雜的 presence 等前置/無關訊息；逾時內未收到目標型別則回 None。
    用於驗證「經 REST 觸發的廣播確實送達 WS client」（Q1）。
    """
    import time as _time

    deadline = _time.monotonic() + timeout
    while True:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            return None
        msg = _recv(ws, timeout=remaining)
        if msg is None:
            return None
        if isinstance(msg, dict) and msg.get("type") == target_type:
            return msg


# ---------- 認證 / 權限：連線階段 ----------


def test_ws_connect_without_token_closes_4401(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/chapters/{ch['id']}"):
            pass
    assert exc.value.code == 4401


def test_ws_connect_with_garbage_token_closes_4401(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(ch["id"], "not-a-real-jwt")):
            pass
    assert exc.value.code == 4401


def test_ws_connect_nonexistent_chapter_closes_4403(client, auth):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(999999, auth["token"])):
            pass
    assert exc.value.code == 4403


def test_ws_connect_not_book_member_closes_4403(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="ws-stranger@test.com")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(ch["id"], stranger["token"])):
            pass
    assert exc.value.code == 4403


def test_ws_connect_cross_book_isolation_closes_4403(client, auth, user_factory):
    """跨書隔離：另一本書的成員（非本書成員）連線本書章節一律 4403。"""
    book_a = _create_book(client, auth, "書A")
    ch_a = _create_chapter(client, auth["headers"], book_a, "A書章節")

    other_owner = user_factory(email="ws-other-owner@test.com")
    book_b = _create_book(client, other_owner, "書B")
    b_editor = _invite(client, other_owner, book_b, user_factory, "ws-b-editor@test.com", "editor")

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(ch_a["id"], b_editor["token"])):
            pass
    assert exc.value.code == 4403


def test_ws_connect_deleted_chapter_closes_4403(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.delete(f"/api/chapters/{ch['id']}", headers=auth["headers"])
    assert r.status_code == 200, r.text
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(ch["id"], auth["token"])):
            pass
    assert exc.value.code == 4403


@pytest.mark.parametrize("role", ["owner", "editor", "reviewer", "viewer"])
def test_ws_connect_allowed_for_any_member_role(client, auth, user_factory, role):
    """ws.py 僅檢查 get_membership 是否存在，不限制角色 —— viewer/reviewer
    也能連線觀看（無 EDIT_ROLES 限制），符合「即時協作旁觀」的合理需求。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    if role == "owner":
        member = auth
    else:
        member = _invite(client, auth, book_id, user_factory, f"ws-role-{role}@test.com", role)

    with client.websocket_connect(_ws_url(ch["id"], member["token"])) as ws:
        presence = _recv(ws)
        lock_state = _recv(ws)
        assert presence["type"] == "presence"
        assert lock_state == {"type": "lock_changed", "lock_owner": None}


# ---------- 連線成功後的初始推送順序 ----------


def test_ws_initial_messages_are_presence_then_lock_state(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as ws:
        m1 = _recv(ws)
        m2 = _recv(ws)
        assert m1 == {
            "type": "presence",
            "users": [{"user_id": auth["user"]["id"], "name": auth["user"]["name"]}],
        }
        assert m2 == {"type": "lock_changed", "lock_owner": None}


def test_ws_initial_lock_state_reflects_existing_lock(client, auth, user_factory):
    """連線前已有人持鎖（透過 HTTP 端點取得）：新連線的初始 lock_changed 應
    反映目前持有者，而非一律 None。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "ws-lockstate-editor@test.com", "editor")

    r = client.post(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
    assert r.status_code == 200, r.text

    with client.websocket_connect(_ws_url(ch["id"], editor["token"])) as ws:
        _presence = _recv(ws)
        lock_state = _recv(ws)
        assert lock_state == {"type": "lock_changed", "lock_owner": auth["user"]["id"]}


# ---------- Presence：多連線 ----------


def test_ws_presence_updates_when_second_client_joins(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "ws-presence-editor@test.com", "editor")

    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as wsA:
        pA1 = _recv(wsA)
        _recv(wsA)  # lock_changed
        assert len(pA1["users"]) == 1

        with client.websocket_connect(_ws_url(ch["id"], editor["token"])) as wsB:
            pB1 = _recv(wsB)
            _recv(wsB)  # lock_changed
            assert {u["user_id"] for u in pB1["users"]} == {
                auth["user"]["id"], editor["user"]["id"],
            }

            # A 原本連線的 client 應收到 presence 更新廣播（B 加入後）
            pA2 = _recv(wsA, timeout=3)
            assert pA2 is not None, "A 未在逾時內收到 B 加入後的 presence 廣播"
            assert pA2["type"] == "presence"
            assert {u["user_id"] for u in pA2["users"]} == {
                auth["user"]["id"], editor["user"]["id"],
            }

        # B 離線後，A 應收到只剩自己的 presence 廣播
        pA3 = _recv(wsA, timeout=3)
        assert pA3 is not None, "A 未在逾時內收到 B 離線後的 presence 廣播"
        assert pA3["type"] == "presence"
        assert [u["user_id"] for u in pA3["users"]] == [auth["user"]["id"]]


def test_ws_presence_dedupes_same_user_multiple_connections(client, auth):
    """RoomManager.presence() 以 user_id 為 key 去重：同一使用者開兩條連線
    只會在 presence 列表出現一次（記錄現況；語意上代表「兩個分頁」仍算一人）。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)

    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as ws1:
        _recv(ws1)
        _recv(ws1)
        with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as ws2:
            p2 = _recv(ws2)
            _recv(ws2)
            assert len(p2["users"]) == 1
            assert p2["users"][0]["user_id"] == auth["user"]["id"]


# ---------- Cursor relay ----------


def test_ws_cursor_relayed_to_others_excluding_sender(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "ws-cursor-editor@test.com", "editor")

    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as wsA:
        _recv(wsA)
        _recv(wsA)
        with client.websocket_connect(_ws_url(ch["id"], editor["token"])) as wsB:
            _recv(wsB)
            _recv(wsB)
            _recv(wsA, timeout=3)  # A 收到 B 加入的 presence 廣播，先消化掉

            wsA.send_json({"type": "cursor", "position": {"line": 3, "ch": 7}})

            cursor_msg = _recv(wsB, timeout=3)
            assert cursor_msg is not None, "B 未在逾時內收到 A 的 cursor relay"
            assert cursor_msg == {
                "type": "cursor",
                "user": {"user_id": auth["user"]["id"], "name": auth["user"]["name"]},
                "position": {"line": 3, "ch": 7},
            }

            # A 送出 ping，若下一則訊息是 pong（而非自己的 cursor 回音），
            # 代表 broadcast(exclude=自己的 websocket) 正確排除了寄件者本身。
            wsA.send_json({"type": "ping"})
            reply = _recv(wsA, timeout=3)
            assert reply == {"type": "pong"}


# ---------- Ping / pong 與鎖 refresh ----------


def test_ws_ping_returns_pong(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as ws:
        _recv(ws)
        _recv(ws)
        ws.send_json({"type": "ping"})
        reply = _recv(ws, timeout=3)
        assert reply == {"type": "pong"}


def test_ws_ping_refreshes_own_held_lock(client, auth):
    """ping 訊息會呼叫 lock_manager.refresh(chapter_id, user_id)（FR-45），
    若此使用者持有鎖，其 expires_at 應被延後。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.post(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
    assert r.status_code == 200, r.text
    expires_before = lock_manager._locks[ch["id"]]["expires_at"]

    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as ws:
        _recv(ws)
        _recv(ws)
        # 人為把過期時間往前調，讓「refresh 後應延後」的差異可被觀察到
        lock_manager._locks[ch["id"]]["expires_at"] = expires_before.replace(
            microsecond=0
        )
        adjusted_before = lock_manager._locks[ch["id"]]["expires_at"]

        ws.send_json({"type": "ping"})
        reply = _recv(ws, timeout=3)
        assert reply == {"type": "pong"}

        # 必須在 with 區塊內讀取：離開區塊會斷線並觸發鎖釋放（非同步），與此讀取
        # 形成 race → 間歇性 KeyError（line 341）。斷線前讀取才穩定。
        expires_after = lock_manager._locks[ch["id"]]["expires_at"]
        assert expires_after > adjusted_before


# ---------- 斷線：釋放鎖並廣播 ----------


def test_ws_disconnect_releases_lock_and_broadcasts(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "ws-disconnect-editor@test.com", "editor")

    r = client.post(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
    assert r.status_code == 200, r.text

    with client.websocket_connect(_ws_url(ch["id"], editor["token"])) as wsB:
        _recv(wsB)  # presence
        lock_state = _recv(wsB)
        assert lock_state == {"type": "lock_changed", "lock_owner": auth["user"]["id"]}

        with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as wsA:
            _recv(wsA)
            _recv(wsA)
            _recv(wsB, timeout=3)  # B 收到 A 加入的 presence 廣播

            # wsA 離開 with 區塊 -> 斷線 -> finally 釋放鎖並廣播
        lock_changed_msg = _recv(wsB, timeout=3)
        assert lock_changed_msg is not None, "B 未在逾時內收到斷線後的 lock_changed 廣播"
        assert lock_changed_msg == {"type": "lock_changed", "lock_owner": None}

        presence_msg = _recv(wsB, timeout=3)
        assert presence_msg is not None, "B 未在逾時內收到斷線後的 presence 廣播"
        assert presence_msg["type"] == "presence"
        assert [u["user_id"] for u in presence_msg["users"]] == [editor["user"]["id"]]

    # 鎖已釋放，其他人現在可透過 HTTP 端點取得
    r2 = client.post(f"/api/chapters/{ch['id']}/lock", headers=editor["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["lock_owner"] == editor["user"]["id"]


def test_ws_disconnect_does_not_release_lock_held_by_someone_else(client, auth, user_factory):
    """A 斷線時只釋放「A 自己持有」的鎖；B 持有的鎖不受影響。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "ws-disconnect-other@test.com", "editor")

    r = client.post(f"/api/chapters/{ch['id']}/lock", headers=editor["headers"])
    assert r.status_code == 200, r.text  # editor 持鎖

    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as wsA:
        _recv(wsA)
        _recv(wsA)
    # A 斷線後，鎖仍應由 editor 持有（A 本來就沒有鎖，不受影響）
    holder = lock_manager.holder(ch["id"])
    assert holder == editor["user"]["id"]


# ---------- Q1 修復：REST 端點觸發的廣播確實送達 WS client ----------


def test_http_triggered_content_updated_broadcast_reaches_ws_client(client, auth):
    """Q1 修復驗收：PATCH /chapters/{id}/content 是同步（def）端點，經
    room_manager.broadcast_threadsafe（run_coroutine_threadsafe 排入主迴圈）
    後，已連線的 WS client 應在逾時內收到 content_updated 廣播。
    修復前：`_broadcast_safe` 內 `asyncio.get_event_loop()` 在 worker thread
    無執行中迴圈而拋 RuntimeError 被吞掉，訊息從不送達。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)

    with client.websocket_connect(_ws_url(ch["id"], auth["token"])) as ws:
        _recv(ws)  # presence
        _recv(ws)  # lock_changed

        doc = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}],
        }
        r = client.patch(
            f"/api/chapters/{ch['id']}/content",
            json={"content_json": doc, "base_version": 1},
            headers=auth["headers"],
        )
        assert r.status_code == 200, r.text

        msg = _recv_until(ws, "content_updated", timeout=3)
        assert msg is not None, "WS client 未在逾時內收到 content_updated 廣播"
        assert msg["type"] == "content_updated"
        assert msg["version"] == 2  # 初始 version 1 -> PATCH 後 2


def test_http_triggered_lock_changed_broadcast_reaches_ws_client(
    client, auth, user_factory
):
    """Q1 修復驗收：另一使用者經 REST POST /chapters/{id}/lock 取得鎖後，
    已連線的 WS client 應在逾時內收到 lock_changed 廣播（lock_owner 為取得者）。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "ws-httplock-editor@test.com", "editor")

    with client.websocket_connect(_ws_url(ch["id"], editor["token"])) as ws:
        _recv(ws)  # presence
        _recv(ws)  # lock_changed（初始 None）

        r = client.post(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
        assert r.status_code == 200, r.text

        msg = _recv_until(ws, "lock_changed", timeout=3)
        assert msg is not None, "WS client 未在逾時內收到 lock_changed 廣播"
        assert msg == {"type": "lock_changed", "lock_owner": auth["user"]["id"]}
