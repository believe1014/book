"""Content 路由整合測試（章節內容讀寫、版本、樂觀鎖、軟鎖、跨書隔離）。

涵蓋（見 app/routers/content.py + app/services/locks.py + app/services/wordcount.py）：
- 讀取初始內容（content_json={}、version=1、word_count=0、can_edit）
- 寫入 content_json：word_count 依 CJK/拉丁字詞規則重新計算、version 遞增、
  同時寫入 ContentVersion 快照
- 版本衝突（base_version 與目前 version 不符 → 409）
- 軟鎖（423）：patch 會自動搶鎖；鎖被他人持有時寫入被拒；鎖可手動取得/釋放
- 權限矩陣：owner/editor 可寫；reviewer/viewer 只能讀（can_edit=False），寫入 403
- 版本列表 / 取單一版本 / 還原版本（還原＝新增一筆版本，不覆寫歷史）
- 跨書隔離：非該書成員一律 404

注意：lock_manager 是模組層級的全域單例（記憶體內狀態，非 DB），不會隨著
conftest 的 `_reset_db`（drop_all/create_all）重建而清空。因為每個測試的
DB 都從空 schema 重新自增 id，不同測試裡的章節/使用者可能重複拿到相同的
id，若不清空鎖狀態，前一個測試殘留的鎖可能誤判到本次測試的章節上。因此本檔
另外用 autouse fixture 在每個測試前後清空 lock_manager，這只是測試隔離手段，
不影響 production 行為。
"""
import pytest

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


def _doc(text: str) -> dict:
    """一段簡單的 ProseMirror/TipTap 風格文件，包一段文字。"""
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        ],
    }


# ---------- 讀取初始內容 ----------


def test_get_content_initial_state(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["content_json"] == {}
    assert data["version"] == 1
    assert data["word_count"] == 0
    assert data["lock"] is None
    assert data["can_edit"] is True  # owner


@pytest.mark.parametrize("role,can_edit", [
    ("editor", True), ("reviewer", False), ("viewer", False),
])
def test_get_content_can_edit_reflects_role(client, auth, user_factory, role, can_edit):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    member = _invite(client, auth, book_id, user_factory, f"canedit-{role}@test.com", role)
    r = client.get(f"/api/chapters/{ch['id']}/content", headers=member["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["can_edit"] == can_edit


def test_get_content_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="stranger-getcontent@test.com")
    r = client.get(f"/api/chapters/{ch['id']}/content", headers=stranger["headers"])
    assert r.status_code == 404, r.text


# ---------- 寫入內容：word_count / version ----------


def test_patch_content_updates_word_count_and_version(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("hello world"), "base_version": 1},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["version"] == 2
    assert data["word_count"] == 2  # "hello" + "world" 兩個拉丁字詞

    # 再次讀取確認已持久化
    r2 = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    d2 = r2.json()["data"]
    assert d2["version"] == 2
    assert d2["word_count"] == 2
    assert d2["content_json"]["content"][0]["content"][0]["text"] == "hello world"


def test_patch_content_counts_cjk_characters(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("你好世界"), "base_version": 1},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["word_count"] == 4  # 4 個中文字，逐字計數


def test_patch_content_version_increments_on_repeated_edits_by_same_editor(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r1 = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("第一版"), "base_version": 1},
        headers=auth["headers"],
    )
    assert r1.json()["data"]["version"] == 2
    r2 = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("第二版內容"), "base_version": 2},
        headers=auth["headers"],
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["version"] == 3
    assert r2.json()["data"]["word_count"] == 5


# ---------- 版本衝突（樂觀鎖）----------


def test_patch_content_stale_base_version_conflicts(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("第一版"), "base_version": 1},
        headers=auth["headers"],
    )
    # 用過時的 base_version=1 再次寫入（目前實際 version 已是 2）
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("衝突版本"), "base_version": 1},
        headers=auth["headers"],
    )
    assert r.status_code == 409, r.text

    # 確認內容未被覆蓋
    r2 = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    assert r2.json()["data"]["version"] == 2
    assert r2.json()["data"]["content_json"]["content"][0]["content"][0]["text"] == "第一版"


# ---------- 權限矩陣 ----------


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_patch_content_reviewer_viewer_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    member = _invite(client, auth, book_id, user_factory, f"patch-{role}@test.com", role)
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("我沒有權限"), "base_version": 1},
        headers=member["headers"],
    )
    assert r.status_code == 403, r.text


def test_patch_content_editor_allowed(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "editor-patch@test.com", "editor")
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("編輯寫的內容"), "base_version": 1},
        headers=editor["headers"],
    )
    assert r.status_code == 200, r.text


def test_patch_content_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="stranger-patchcontent@test.com")
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("偷寫"), "base_version": 1},
        headers=stranger["headers"],
    )
    assert r.status_code == 404, r.text


# ---------- 軟鎖 ----------


def test_patch_content_blocked_when_locked_by_another_editor(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-editor@test.com", "editor")

    # owner 先手動搶鎖
    r_lock = client.post(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
    assert r_lock.status_code == 200, r_lock.text

    # 另一位 editor 嘗試寫入 → 423（鎖被他人持有）
    r = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("搶著寫"), "base_version": 1},
        headers=editor["headers"],
    )
    assert r.status_code == 423, r.text

    # 鎖的持有者本人可以正常寫入
    r2 = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("持有者寫入"), "base_version": 1},
        headers=auth["headers"],
    )
    assert r2.status_code == 200, r2.text


def test_patch_content_auto_acquires_lock_for_same_editor(client, auth):
    """第一次 patch 會自動搶鎖；同一位使用者接續 patch 不受自己的鎖阻擋。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r1 = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("A"), "base_version": 1},
        headers=auth["headers"],
    )
    assert r1.status_code == 200, r1.text
    r_content = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    assert r_content.json()["data"]["lock"]["lock_owner"] == auth["user"]["id"]

    r2 = client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("AB"), "base_version": 2},
        headers=auth["headers"],
    )
    assert r2.status_code == 200, r2.text


def test_lock_acquire_and_release_endpoints(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-endpoint@test.com", "editor")

    r = client.post(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["lock_owner"] == auth["user"]["id"]

    # 他人搶鎖失敗
    r2 = client.post(f"/api/chapters/{ch['id']}/lock", headers=editor["headers"])
    assert r2.status_code == 423, r2.text

    # owner 釋放鎖
    r3 = client.delete(f"/api/chapters/{ch['id']}/lock", headers=auth["headers"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["success"] is True

    # 鎖釋放後他人可搶到
    r4 = client.post(f"/api/chapters/{ch['id']}/lock", headers=editor["headers"])
    assert r4.status_code == 200, r4.text
    assert r4.json()["data"]["lock_owner"] == editor["user"]["id"]


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_lock_acquire_reviewer_viewer_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    member = _invite(client, auth, book_id, user_factory, f"lockacq-{role}@test.com", role)
    r = client.post(f"/api/chapters/{ch['id']}/lock", headers=member["headers"])
    assert r.status_code == 403, r.text


# ---------- 版本列表 / 取單一版本 / 還原 ----------


def test_list_versions_after_multiple_edits(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("v2內容"), "base_version": 1},
        headers=auth["headers"],
    )
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("v3內容"), "base_version": 2},
        headers=auth["headers"],
    )
    r = client.get(f"/api/chapters/{ch['id']}/versions", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 2
    versions = [item["version"] for item in data["items"]]
    assert versions == [3, 2]  # 新到舊排序
    assert data["items"][0]["editor_name"] == auth["user"]["name"]


def test_get_single_version_content(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("初版內容"), "base_version": 1},
        headers=auth["headers"],
    )
    r = client.get(f"/api/chapters/{ch['id']}/versions/2", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["version"] == 2
    assert data["content_json"]["content"][0]["content"][0]["text"] == "初版內容"
    assert data["editor"]["id"] == auth["user"]["id"]


def test_get_nonexistent_version_404(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.get(f"/api/chapters/{ch['id']}/versions/999", headers=auth["headers"])
    assert r.status_code == 404, r.text


def test_restore_version_creates_new_version_not_overwrite(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("舊內容"), "base_version": 1},
        headers=auth["headers"],
    )  # -> version 2
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("新內容"), "base_version": 2},
        headers=auth["headers"],
    )  # -> version 3

    r = client.post(f"/api/chapters/{ch['id']}/versions/2/restore", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["version"] == 4  # 還原＝新增一筆版本，不是回到 2

    r2 = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    d2 = r2.json()["data"]
    assert d2["version"] == 4
    assert d2["content_json"]["content"][0]["content"][0]["text"] == "舊內容"

    # 歷史版本 2、3 仍然存在（沒有被覆蓋），還原新增一筆 version=4，共 3 筆快照
    # （初始 version=1 建立時不會寫入 ContentVersion 快照，只有 patch/restore
    # 才會產生快照，因此不是 4 筆）。
    r3 = client.get(f"/api/chapters/{ch['id']}/versions", headers=auth["headers"])
    assert r3.json()["data"]["total"] == 3
    assert sorted(item["version"] for item in r3.json()["data"]["items"]) == [2, 3, 4]


def test_restore_version_reviewer_forbidden(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    client.patch(
        f"/api/chapters/{ch['id']}/content",
        json={"content_json": _doc("舊內容"), "base_version": 1},
        headers=auth["headers"],
    )
    reviewer = _invite(client, auth, book_id, user_factory, "restore-reviewer@test.com", "reviewer")
    r = client.post(f"/api/chapters/{ch['id']}/versions/2/restore", headers=reviewer["headers"])
    assert r.status_code == 403, r.text


def test_restore_nonexistent_version_404(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.post(f"/api/chapters/{ch['id']}/versions/999/restore", headers=auth["headers"])
    assert r.status_code == 404, r.text


# ---------- 跨書隔離 ----------


def test_cross_book_isolation_content_404(client, auth, user_factory):
    book_a = _create_book(client, auth, "書A")
    ch_a = _create_chapter(client, auth["headers"], book_a, "A書章節")

    other_owner = user_factory(email="content-other-owner@test.com")
    book_b = _create_book(client, other_owner, "書B")
    b_editor = _invite(client, other_owner, book_b, user_factory, "content-b-editor@test.com", "editor")

    r = client.get(f"/api/chapters/{ch_a['id']}/content", headers=b_editor["headers"])
    assert r.status_code == 404, r.text

    r2 = client.patch(
        f"/api/chapters/{ch_a['id']}/content",
        json={"content_json": _doc("跨書亂寫"), "base_version": 1},
        headers=b_editor["headers"],
    )
    assert r2.status_code == 404, r2.text

    r3 = client.get(f"/api/chapters/{ch_a['id']}/versions", headers=b_editor["headers"])
    assert r3.status_code == 404, r3.text
