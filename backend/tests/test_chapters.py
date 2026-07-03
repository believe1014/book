"""Chapters 路由整合測試（章節樹 CRUD、重排序、權限矩陣、跨書隔離）。

涵蓋（見 app/routers/chapters.py + app/deps.py）：
- 建立章節（含 parent_id 子章節、order_index 自動遞增、最多兩層限制）
- 列出章節樹（兩層排序）
- 修改標題/狀態（含非法 status 400）
- 軟刪除（含頂層章節 cascade 軟刪子章節）
- 重排序（含三層限制 400、非本書章節 400）
- 權限矩陣：owner/editor 可寫，reviewer/viewer 403，非成員 404
- 跨書隔離：A 書成員操作 B 書章節一律 404（resolve_chapter_book 以章節所屬書籍
  的成員關係判斷，與呼叫時所帶的 book_id 無關）
"""
import pytest


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


def _create_chapter(client, headers, book_id, title="第一章", parent_id=None):
    r = client.post(
        f"/api/books/{book_id}/chapters",
        json={"title": title, "parent_id": parent_id},
        headers=headers,
    )
    return r


# ---------- 建立章節 ----------


def test_create_top_level_chapter(client, auth):
    book_id = _create_book(client, auth)
    r = _create_chapter(client, auth["headers"], book_id, "楔子")
    assert r.status_code == 200, r.text
    chapter = r.json()["data"]["chapter"]
    assert chapter["title"] == "楔子"
    assert chapter["parent_id"] is None
    assert chapter["order_index"] == 0
    assert chapter["status"] == "not_started"
    assert chapter["book_id"] == book_id


def test_create_second_top_level_chapter_increments_order_index(client, auth):
    book_id = _create_book(client, auth)
    _create_chapter(client, auth["headers"], book_id, "第一章")
    r = _create_chapter(client, auth["headers"], book_id, "第二章")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["chapter"]["order_index"] == 1


def test_create_child_chapter_under_parent(client, auth):
    book_id = _create_book(client, auth)
    parent = _create_chapter(client, auth["headers"], book_id, "第一章").json()["data"]["chapter"]
    r = _create_chapter(client, auth["headers"], book_id, "第一節", parent_id=parent["id"])
    assert r.status_code == 200, r.text
    child = r.json()["data"]["chapter"]
    assert child["parent_id"] == parent["id"]
    assert child["order_index"] == 0  # 子層 order_index 從 0 開始，與父層獨立計數

    r2 = _create_chapter(client, auth["headers"], book_id, "第二節", parent_id=parent["id"])
    assert r2.json()["data"]["chapter"]["order_index"] == 1


def test_create_chapter_nonexistent_parent_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = _create_chapter(client, auth["headers"], book_id, "孤兒節", parent_id=999999)
    assert r.status_code == 400, r.text


def test_create_chapter_third_level_rejected(client, auth):
    book_id = _create_book(client, auth)
    parent = _create_chapter(client, auth["headers"], book_id, "第一章").json()["data"]["chapter"]
    child = _create_chapter(
        client, auth["headers"], book_id, "第一節", parent_id=parent["id"]
    ).json()["data"]["chapter"]
    r = _create_chapter(client, auth["headers"], book_id, "第一小節", parent_id=child["id"])
    assert r.status_code == 400, r.text  # 最多支援兩層結構


def test_create_chapter_blank_title_rejected(client, auth):
    """Q2：create_chapter 與 books.py::create_book 一致，strip 後為空的
    標題應回 400（不再被接受為空字串）。"""
    book_id = _create_book(client, auth)
    r = _create_chapter(client, auth["headers"], book_id, "   ")
    assert r.status_code == 400, r.text


def test_create_chapter_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="stranger-ch@test.com")
    r = _create_chapter(client, stranger["headers"], book_id, "偷加章節")
    assert r.status_code == 404, r.text


# ---------- 列出章節樹 ----------


def test_list_chapters_builds_two_level_tree_sorted_by_order(client, auth):
    book_id = _create_book(client, auth)
    top1 = _create_chapter(client, auth["headers"], book_id, "第一章").json()["data"]["chapter"]
    top2 = _create_chapter(client, auth["headers"], book_id, "第二章").json()["data"]["chapter"]
    _create_chapter(client, auth["headers"], book_id, "1-2節", parent_id=top1["id"])
    _create_chapter(client, auth["headers"], book_id, "1-1節", parent_id=top1["id"])

    r = client.get(f"/api/books/{book_id}/chapters", headers=auth["headers"])
    assert r.status_code == 200, r.text
    tree = r.json()["data"]["chapters"]
    assert [c["title"] for c in tree] == ["第一章", "第二章"]
    assert tree[0]["id"] == top1["id"]
    assert tree[1]["id"] == top2["id"]
    # 子章節依 order_index 排序（先建立的 1-2節 order_index=0 排前面）
    assert [c["title"] for c in tree[0]["children"]] == ["1-2節", "1-1節"]
    assert tree[1]["children"] == []


def test_list_chapters_excludes_soft_deleted(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id, "待刪章節").json()["data"]["chapter"]
    client.delete(f"/api/chapters/{ch['id']}", headers=auth["headers"])
    r = client.get(f"/api/books/{book_id}/chapters", headers=auth["headers"])
    assert r.json()["data"]["chapters"] == []


def test_list_chapters_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="stranger-list@test.com")
    r = client.get(f"/api/books/{book_id}/chapters", headers=stranger["headers"])
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_list_chapters_any_member_role_can_view(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    _create_chapter(client, auth["headers"], book_id, "章節")
    member = _invite(client, auth, book_id, user_factory, f"view-list-{role}@test.com", role)
    r = client.get(f"/api/books/{book_id}/chapters", headers=member["headers"])
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["chapters"]) == 1


# ---------- 修改章節 ----------


def test_update_chapter_title_and_status(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id, "初稿").json()["data"]["chapter"]
    r = client.patch(
        f"/api/chapters/{ch['id']}",
        json={"title": "定稿", "status": "writing"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    updated = r.json()["data"]["chapter"]
    assert updated["title"] == "定稿"
    assert updated["status"] == "writing"


def test_update_chapter_invalid_status_rejected(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    r = client.patch(
        f"/api/chapters/{ch['id']}", json={"status": "not-a-status"}, headers=auth["headers"]
    )
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_update_chapter_reviewer_viewer_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    member = _invite(client, auth, book_id, user_factory, f"upd-{role}@test.com", role)
    r = client.patch(
        f"/api/chapters/{ch['id']}", json={"title": "改標題"}, headers=member["headers"]
    )
    assert r.status_code == 403, r.text


def test_update_chapter_editor_allowed(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    editor = _invite(client, auth, book_id, user_factory, "editor-upd@test.com", "editor")
    r = client.patch(
        f"/api/chapters/{ch['id']}", json={"title": "編輯改的標題"}, headers=editor["headers"]
    )
    assert r.status_code == 200, r.text


def test_update_chapter_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    stranger = user_factory(email="stranger-upd@test.com")
    r = client.patch(
        f"/api/chapters/{ch['id']}", json={"title": "偷改"}, headers=stranger["headers"]
    )
    assert r.status_code == 404, r.text


# ---------- 刪除章節（軟刪 + cascade）----------


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_delete_chapter_reviewer_viewer_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    member = _invite(client, auth, book_id, user_factory, f"del-{role}@test.com", role)
    r = client.delete(f"/api/chapters/{ch['id']}", headers=member["headers"])
    assert r.status_code == 403, r.text


def test_delete_top_level_chapter_cascades_to_children(client, auth):
    book_id = _create_book(client, auth)
    parent = _create_chapter(client, auth["headers"], book_id, "父章節").json()["data"]["chapter"]
    child = _create_chapter(
        client, auth["headers"], book_id, "子章節", parent_id=parent["id"]
    ).json()["data"]["chapter"]

    r = client.delete(f"/api/chapters/{parent['id']}", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["success"] is True

    tree = client.get(f"/api/books/{book_id}/chapters", headers=auth["headers"]).json()["data"]["chapters"]
    assert tree == []

    # 子章節本身也被 cascade 軟刪：後續修改應視為不存在 (404)
    r2 = client.patch(
        f"/api/chapters/{child['id']}", json={"title": "改已刪的子章節"}, headers=auth["headers"]
    )
    assert r2.status_code == 404, r2.text


def test_delete_chapter_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    stranger = user_factory(email="stranger-del@test.com")
    r = client.delete(f"/api/chapters/{ch['id']}", headers=stranger["headers"])
    assert r.status_code == 404, r.text


# ---------- 重排序 ----------


def test_reorder_chapters_success(client, auth):
    book_id = _create_book(client, auth)
    top1 = _create_chapter(client, auth["headers"], book_id, "A").json()["data"]["chapter"]
    top2 = _create_chapter(client, auth["headers"], book_id, "B").json()["data"]["chapter"]

    r = client.patch(
        f"/api/books/{book_id}/chapters/reorder",
        json=[
            {"id": top1["id"], "parent_id": None, "order_index": 1},
            {"id": top2["id"], "parent_id": None, "order_index": 0},
        ],
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["success"] is True

    tree = client.get(f"/api/books/{book_id}/chapters", headers=auth["headers"]).json()["data"]["chapters"]
    assert [c["title"] for c in tree] == ["B", "A"]


def test_reorder_to_third_level_rejected(client, auth):
    book_id = _create_book(client, auth)
    top = _create_chapter(client, auth["headers"], book_id, "頂層").json()["data"]["chapter"]
    child = _create_chapter(
        client, auth["headers"], book_id, "子章節", parent_id=top["id"]
    ).json()["data"]["chapter"]
    grandchild = _create_chapter(
        client, auth["headers"], book_id, "孫章節", parent_id=top["id"]
    ).json()["data"]["chapter"]

    # 嘗試把 grandchild 移到 child 底下 → 三層結構，應 400
    r = client.patch(
        f"/api/books/{book_id}/chapters/reorder",
        json=[{"id": grandchild["id"], "parent_id": child["id"], "order_index": 0}],
        headers=auth["headers"],
    )
    assert r.status_code == 400, r.text


def test_reorder_chapter_not_in_book_rejected(client, auth, user_factory):
    book_a = _create_book(client, auth, "書A")
    book_b = _create_book(client, auth, "書B")
    ch_b = _create_chapter(client, auth["headers"], book_b, "B書的章節").json()["data"]["chapter"]

    r = client.patch(
        f"/api/books/{book_a}/chapters/reorder",
        json=[{"id": ch_b["id"], "parent_id": None, "order_index": 0}],
        headers=auth["headers"],
    )
    assert r.status_code == 400, r.text  # 章節不存在或不屬於此書


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_reorder_reviewer_viewer_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    top = _create_chapter(client, auth["headers"], book_id, "A").json()["data"]["chapter"]
    member = _invite(client, auth, book_id, user_factory, f"reorder-{role}@test.com", role)
    r = client.patch(
        f"/api/books/{book_id}/chapters/reorder",
        json=[{"id": top["id"], "parent_id": None, "order_index": 0}],
        headers=member["headers"],
    )
    assert r.status_code == 403, r.text


def test_reorder_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    top = _create_chapter(client, auth["headers"], book_id, "A").json()["data"]["chapter"]
    stranger = user_factory(email="stranger-reorder@test.com")
    r = client.patch(
        f"/api/books/{book_id}/chapters/reorder",
        json=[{"id": top["id"], "parent_id": None, "order_index": 0}],
        headers=stranger["headers"],
    )
    assert r.status_code == 404, r.text


# ---------- 跨書隔離 ----------


def test_cross_book_isolation_update_returns_404(client, auth, user_factory):
    """A 書 owner 建立章節；B 書的成員（即使角色是 editor）不可修改 A 書章節。"""
    book_a = _create_book(client, auth, "書A")
    ch_a = _create_chapter(client, auth["headers"], book_a, "A書章節").json()["data"]["chapter"]

    other_owner = user_factory(email="other-owner@test.com")
    book_b = _create_book(client, other_owner, "書B")
    b_editor = _invite(client, other_owner, book_b, user_factory, "b-editor@test.com", "editor")

    r = client.patch(
        f"/api/chapters/{ch_a['id']}", json={"title": "跨書亂改"}, headers=b_editor["headers"]
    )
    assert r.status_code == 404, r.text

    r2 = client.delete(f"/api/chapters/{ch_a['id']}", headers=b_editor["headers"])
    assert r2.status_code == 404, r2.text


def test_cross_book_isolation_list_via_wrong_book_id(client, auth):
    """章節樹是以 book_id 路徑參數過濾，跨書 id 查詢應只回自己書的章節（此處驗證
    不會把 A 書章節錯放進 B 書的樹）。"""
    book_a = _create_book(client, auth, "書A")
    book_b = _create_book(client, auth, "書B")
    _create_chapter(client, auth["headers"], book_a, "A書章節")

    tree_b = client.get(f"/api/books/{book_b}/chapters", headers=auth["headers"]).json()["data"]["chapters"]
    assert tree_b == []


# ---------- 章節統計 ----------


def test_chapter_stats_no_content_written_yet(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    r = client.get(f"/api/chapters/{ch['id']}/stats", headers=auth["headers"])
    assert r.status_code == 200, r.text
    stats = r.json()["data"]
    assert stats["word_count"] == 0
    assert stats["paragraph_count"] == 0


def test_chapter_stats_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id).json()["data"]["chapter"]
    stranger = user_factory(email="stranger-stats@test.com")
    r = client.get(f"/api/chapters/{ch['id']}/stats", headers=stranger["headers"])
    assert r.status_code == 404, r.text
