"""Comments 路由整合測試（章節評論串、單層回覆、解決狀態、跨書隔離）。

涵蓋（見 app/routers/comments.py + app/deps.py）：
- 發表評論：owner/editor/reviewer 可發表（COMMENT_ROLES），viewer 禁止（403）
- 內容驗證：body 與 image_url 皆空 → 400
- 單層回覆：可回覆頂層評論；回覆的評論不屬於本章節 → 400；回覆之回覆（雙層）→ 400
- 讀取評論：回傳巢狀 threads（頂層 + replies）與 unresolved 計數
- 編輯評論：僅作者本人可編輯，他人（含 owner）→ 403
- 刪除評論：作者本人或 book owner 可刪除，其餘 403；刪除頂層會 cascade 軟刪回覆
- 解決/取消解決：COMMENT_ROLES 可標記，viewer 403；僅能標記頂層（回覆標記 → 400）
- 非成員一律 404（不洩漏存在）；跨書隔離
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


def _create_chapter(client, headers, book_id, title="第一章"):
    r = client.post(
        f"/api/books/{book_id}/chapters", json={"title": title}, headers=headers
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["chapter"]


def _post_comment(client, headers, chapter_id, body="不錯的內容", parent_id=None, image_url=None):
    payload = {"body": body}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if image_url is not None:
        payload["image_url"] = image_url
    return client.post(
        f"/api/chapters/{chapter_id}/comments", json=payload, headers=headers
    )


# ---------- 發表評論：權限矩陣 ----------


@pytest.mark.parametrize("role", ["editor", "reviewer"])
def test_create_comment_allowed_roles(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    member = _invite(client, auth, book_id, user_factory, f"comment-{role}@test.com", role)
    r = _post_comment(client, member["headers"], ch["id"], body="這段寫得好")
    assert r.status_code == 200, r.text
    comment = r.json()["data"]["comment"]
    assert comment["body"] == "這段寫得好"
    assert comment["author_id"] == member["user"]["id"]
    assert comment["parent_id"] is None
    assert comment["resolved"] is False


def test_create_comment_owner_allowed(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = _post_comment(client, auth["headers"], ch["id"])
    assert r.status_code == 200, r.text


def test_create_comment_viewer_forbidden(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    viewer = _invite(client, auth, book_id, user_factory, "comment-viewer@test.com", "viewer")
    r = _post_comment(client, viewer["headers"], ch["id"])
    assert r.status_code == 403, r.text


def test_create_comment_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="comment-stranger@test.com")
    r = _post_comment(client, stranger["headers"], ch["id"])
    assert r.status_code == 404, r.text


def test_create_comment_requires_body_or_image(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = _post_comment(client, auth["headers"], ch["id"], body="")
    assert r.status_code == 400, r.text


def test_create_comment_image_only_allowed(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = _post_comment(client, auth["headers"], ch["id"], body="", image_url="/storage/1/x.png")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["comment"]["image_url"] == "/storage/1/x.png"


# ---------- 單層回覆 ----------


def test_reply_to_top_level_comment(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "reply-reviewer@test.com", "reviewer")
    top = _post_comment(client, auth["headers"], ch["id"], body="頂層評論").json()["data"]["comment"]
    r = _post_comment(client, reviewer["headers"], ch["id"], body="回覆內容", parent_id=top["id"])
    assert r.status_code == 200, r.text
    reply = r.json()["data"]["comment"]
    assert reply["parent_id"] == top["id"]


def test_reply_to_reply_rejected(client, auth):
    """僅支援單層回覆：對一則回覆再回覆 → 400。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    top = _post_comment(client, auth["headers"], ch["id"], body="頂層").json()["data"]["comment"]
    reply = _post_comment(
        client, auth["headers"], ch["id"], body="第一層回覆", parent_id=top["id"]
    ).json()["data"]["comment"]
    r = _post_comment(client, auth["headers"], ch["id"], body="第二層回覆", parent_id=reply["id"])
    assert r.status_code == 400, r.text


def test_reply_to_comment_from_other_chapter_rejected(client, auth):
    book_id = _create_book(client, auth)
    ch1 = _create_chapter(client, auth["headers"], book_id, "章節一")
    ch2 = _create_chapter(client, auth["headers"], book_id, "章節二")
    top = _post_comment(client, auth["headers"], ch1["id"], body="章節一的評論").json()["data"]["comment"]
    r = _post_comment(client, auth["headers"], ch2["id"], body="跨章節回覆", parent_id=top["id"])
    assert r.status_code == 400, r.text


def test_reply_to_nonexistent_parent_404(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = _post_comment(client, auth["headers"], ch["id"], body="回覆不存在的評論", parent_id=999999)
    assert r.status_code == 404, r.text


# ---------- 讀取評論串 ----------


def test_list_comments_nested_threads_and_unresolved_count(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "list-reviewer@test.com", "reviewer")
    top1 = _post_comment(client, auth["headers"], ch["id"], body="第一則").json()["data"]["comment"]
    _post_comment(client, reviewer["headers"], ch["id"], body="回覆第一則", parent_id=top1["id"])
    top2 = _post_comment(client, auth["headers"], ch["id"], body="第二則").json()["data"]["comment"]

    r = client.get(f"/api/chapters/{ch['id']}/comments", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 2
    assert data["unresolved"] == 2
    threads = {t["id"]: t for t in data["comments"]}
    assert len(threads[top1["id"]]["replies"]) == 1
    assert threads[top1["id"]]["replies"][0]["author_name"] == reviewer["user"]["name"]
    assert threads[top2["id"]]["replies"] == []


def test_list_comments_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="list-stranger@test.com")
    r = client.get(f"/api/chapters/{ch['id']}/comments", headers=stranger["headers"])
    assert r.status_code == 404, r.text


def test_list_comments_viewer_can_read(client, auth, user_factory):
    """viewer 不能發表評論，但可以讀取（GET 無角色限制，只需是成員）。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    viewer = _invite(client, auth, book_id, user_factory, "view-only@test.com", "viewer")
    _post_comment(client, auth["headers"], ch["id"], body="給大家看")
    r = client.get(f"/api/chapters/{ch['id']}/comments", headers=viewer["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 1


# ---------- 編輯評論 ----------


def test_update_comment_author_only(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "edit-reviewer@test.com", "reviewer")
    c = _post_comment(client, reviewer["headers"], ch["id"], body="原始內容").json()["data"]["comment"]

    # owner（非作者本人）嘗試編輯 → 403
    r = client.patch(
        f"/api/comments/{c['id']}", json={"body": "owner 想改"}, headers=auth["headers"]
    )
    assert r.status_code == 403, r.text

    # 作者本人可編輯
    r2 = client.patch(
        f"/api/comments/{c['id']}", json={"body": "作者修改後"}, headers=reviewer["headers"]
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["comment"]["body"] == "作者修改後"


def test_update_comment_clearing_body_and_image_rejected(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    c = _post_comment(client, auth["headers"], ch["id"], body="有內容").json()["data"]["comment"]
    r = client.patch(
        f"/api/comments/{c['id']}", json={"body": ""}, headers=auth["headers"]
    )
    assert r.status_code == 400, r.text


def test_update_nonexistent_comment_404(client, auth):
    r = client.patch(
        "/api/comments/999999", json={"body": "改不存在的"}, headers=auth["headers"]
    )
    assert r.status_code == 404, r.text


# ---------- 刪除評論（含 cascade 軟刪回覆）----------


def test_delete_comment_author_or_owner(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "del-reviewer@test.com", "reviewer")
    editor = _invite(client, auth, book_id, user_factory, "del-editor@test.com", "editor")
    c = _post_comment(client, reviewer["headers"], ch["id"], body="待刪除").json()["data"]["comment"]

    # 非作者、非 owner（editor）嘗試刪除 → 403
    r = client.delete(f"/api/comments/{c['id']}", headers=editor["headers"])
    assert r.status_code == 403, r.text

    # book owner 可刪除他人的評論
    r2 = client.delete(f"/api/comments/{c['id']}", headers=auth["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["deleted"] is True

    # 刪除後從清單消失
    r3 = client.get(f"/api/chapters/{ch['id']}/comments", headers=auth["headers"])
    assert r3.json()["data"]["total"] == 0


def test_delete_own_comment_allowed(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "del-self@test.com", "reviewer")
    c = _post_comment(client, reviewer["headers"], ch["id"], body="自己刪自己的").json()["data"]["comment"]
    r = client.delete(f"/api/comments/{c['id']}", headers=reviewer["headers"])
    assert r.status_code == 200, r.text


def test_delete_top_level_cascades_soft_delete_to_replies(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "cascade-reviewer@test.com", "reviewer")
    top = _post_comment(client, auth["headers"], ch["id"], body="頂層將被刪").json()["data"]["comment"]
    reply = _post_comment(
        client, reviewer["headers"], ch["id"], body="回覆", parent_id=top["id"]
    ).json()["data"]["comment"]

    r = client.delete(f"/api/comments/{top['id']}", headers=auth["headers"])
    assert r.status_code == 200, r.text

    r2 = client.get(f"/api/chapters/{ch['id']}/comments", headers=auth["headers"])
    assert r2.json()["data"]["total"] == 0  # 頂層與回覆都已軟刪，清單皆不可見

    # 回覆本身也無法再被編輯（已視為不存在）
    r3 = client.patch(
        f"/api/comments/{reply['id']}", json={"body": "改已刪除的回覆"}, headers=reviewer["headers"]
    )
    assert r3.status_code == 404, r3.text


def test_delete_nonexistent_comment_404(client, auth):
    r = client.delete("/api/comments/999999", headers=auth["headers"])
    assert r.status_code == 404, r.text


# ---------- 解決 / 取消解決 ----------


def test_resolve_comment_by_comment_role(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    reviewer = _invite(client, auth, book_id, user_factory, "resolve-reviewer@test.com", "reviewer")
    top = _post_comment(client, auth["headers"], ch["id"], body="待解決").json()["data"]["comment"]

    r = client.post(f"/api/comments/{top['id']}/resolve", headers=reviewer["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]["comment"]
    assert data["resolved"] is True
    assert data["resolved_by"] == reviewer["user"]["id"]

    r2 = client.get(f"/api/chapters/{ch['id']}/comments", headers=auth["headers"])
    assert r2.json()["data"]["unresolved"] == 0

    # 取消解決
    r3 = client.delete(f"/api/comments/{top['id']}/resolve", headers=auth["headers"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["comment"]["resolved"] is False
    assert r3.json()["data"]["comment"]["resolved_by"] is None


def test_resolve_comment_viewer_forbidden(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    viewer = _invite(client, auth, book_id, user_factory, "resolve-viewer@test.com", "viewer")
    top = _post_comment(client, auth["headers"], ch["id"], body="待解決2").json()["data"]["comment"]
    r = client.post(f"/api/comments/{top['id']}/resolve", headers=viewer["headers"])
    assert r.status_code == 403, r.text


def test_resolve_reply_rejected(client, auth):
    """只能標記頂層評論為已解決，標記回覆 → 400。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    top = _post_comment(client, auth["headers"], ch["id"], body="頂層").json()["data"]["comment"]
    reply = _post_comment(
        client, auth["headers"], ch["id"], body="回覆", parent_id=top["id"]
    ).json()["data"]["comment"]
    r = client.post(f"/api/comments/{reply['id']}/resolve", headers=auth["headers"])
    assert r.status_code == 400, r.text


def test_resolve_nonexistent_comment_404(client, auth):
    r = client.post("/api/comments/999999/resolve", headers=auth["headers"])
    assert r.status_code == 404, r.text


# ---------- 跨書隔離 ----------


def test_cross_book_isolation_comments_404(client, auth, user_factory):
    book_a = _create_book(client, auth, "評論書A")
    ch_a = _create_chapter(client, auth["headers"], book_a, "A書章節")
    top_a = _post_comment(client, auth["headers"], ch_a["id"], body="A書的評論").json()["data"]["comment"]

    other_owner = user_factory(email="comment-other-owner@test.com")
    book_b = _create_book(client, other_owner, "評論書B")
    b_reviewer = _invite(client, other_owner, book_b, user_factory, "comment-b-reviewer@test.com", "reviewer")

    r = client.get(f"/api/chapters/{ch_a['id']}/comments", headers=b_reviewer["headers"])
    assert r.status_code == 404, r.text

    r2 = _post_comment(client, b_reviewer["headers"], ch_a["id"], body="跨書留言")
    assert r2.status_code == 404, r2.text

    # 跨書使用者也不能操作 A 書評論本身（patch/delete/resolve 走 comment_id，
    # 但仍需透過 resolve_chapter_book 驗證成員關係）
    r3 = client.patch(
        f"/api/comments/{top_a['id']}", json={"body": "跨書亂改"}, headers=b_reviewer["headers"]
    )
    assert r3.status_code == 404, r3.text

    r4 = client.delete(f"/api/comments/{top_a['id']}", headers=b_reviewer["headers"])
    assert r4.status_code == 404, r4.text

    r5 = client.post(f"/api/comments/{top_a['id']}/resolve", headers=b_reviewer["headers"])
    assert r5.status_code == 404, r5.text
