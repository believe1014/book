"""Books 路由 + 成員/角色權限整合測試（回歸護欄）。

涵蓋：建書/列書/取書、改書（權限矩陣）、軟刪與還原、成員管理（邀請/角色/移除）、
邀請接受流程，以及非成員 404（不洩漏存在）與各角色的允許/禁止操作矩陣。

角色權限矩陣（實際程式行為，見 app/routers/books.py + app/deps.py）：
- 書籍 CRUD 的「改書 / 刪書 / 還原 / 管理成員」四項操作僅限 owner，
  editor/reviewer/viewer 一律 403（不同於 deps.py 中 EDIT_ROLES 常數對章節內容
  的定義 — 該常數用於章節/內容編輯，不適用於書籍中繼資料本身）。
- 列出成員（GET .../members）任何成員角色皆可存取（僅需是成員，無需 owner）。
- 非成員一律回 404，不論該書是否存在，避免洩漏存在性。
"""
import pytest


# ---------- 建書 / 列書 / 取書 ----------


def test_create_book_creator_becomes_owner(client, auth):
    r = client.post(
        "/api/books", json={"title": "測試書", "description": "desc", "tags": ["a", "b"]},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    book = r.json()["data"]["book"]
    assert book["title"] == "測試書"
    assert book["owner_id"] == auth["user"]["id"]
    assert book["status"] == "draft"
    assert book["tags"] == ["a", "b"]
    assert book["deleted_at"] is None

    # 建立者自動成為 owner：可在 GET 取回並驗證 my_role
    r2 = client.get(f"/api/books/{book['id']}", headers=auth["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["my_role"] == "owner"


def test_create_book_blank_title_rejected(client, auth):
    r = client.post("/api/books", json={"title": "   "}, headers=auth["headers"])
    assert r.status_code == 400, r.text


def test_list_books_only_own_memberships(client, auth, user_factory):
    other = user_factory(email="other@test.com")
    client.post("/api/books", json={"title": "A的書"}, headers=auth["headers"])
    client.post("/api/books", json={"title": "B的書"}, headers=other["headers"])

    r = client.get("/api/books", headers=auth["headers"])
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["title"] == "A的書"


def test_list_books_empty_for_new_user(client, auth):
    r = client.get("/api/books", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"items": [], "total": 0}


def test_get_book_not_member_returns_404(client, auth, user_factory):
    other = user_factory(email="stranger@test.com")
    r = client.post("/api/books", json={"title": "私密書"}, headers=auth["headers"])
    book_id = r.json()["data"]["book"]["id"]

    r2 = client.get(f"/api/books/{book_id}", headers=other["headers"])
    assert r2.status_code == 404, r2.text


def test_get_book_nonexistent_returns_404(client, auth):
    r = client.get("/api/books/999999", headers=auth["headers"])
    assert r.status_code == 404, r.text


def test_get_book_requires_auth(client):
    assert client.get("/api/books/1").status_code == 401


# ---------- 輔助：建立一本書並邀請不同角色成員 ----------


def _create_book(client, owner, title="協作書"):
    r = client.post("/api/books", json={"title": title}, headers=owner["headers"])
    assert r.status_code == 200, r.text
    return r.json()["data"]["book"]["id"]


def _invite_and_get_headers(client, owner, book_id, user_factory, email, role):
    """邀請一位已註冊使用者加入書籍，回傳其 headers。"""
    member = user_factory(email=email)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": email, "role": role},
        headers=owner["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["invitation"]["status"] == "accepted"
    return member


# ---------- 改書：權限矩陣 ----------


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_update_book_non_owner_forbidden(client, auth, user_factory, role):
    """僅 owner 可修改書籍中繼資料；editor/reviewer/viewer 皆 403。"""
    book_id = _create_book(client, auth)
    member = _invite_and_get_headers(
        client, auth, book_id, user_factory, f"{role}@test.com", role
    )
    r = client.patch(
        f"/api/books/{book_id}", json={"title": "改名"}, headers=member["headers"]
    )
    assert r.status_code == 403, r.text


def test_update_book_owner_allowed(client, auth):
    book_id = _create_book(client, auth)
    r = client.patch(
        f"/api/books/{book_id}",
        json={"title": "新標題", "status": "writing", "word_count_goal": 50000},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    book = r.json()["data"]["book"]
    assert book["title"] == "新標題"
    assert book["status"] == "writing"
    assert book["word_count_goal"] == 50000


def test_update_book_invalid_status_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = client.patch(
        f"/api/books/{book_id}", json={"status": "not-a-real-status"}, headers=auth["headers"]
    )
    assert r.status_code == 400, r.text


def test_update_book_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="stranger2@test.com")
    r = client.patch(
        f"/api/books/{book_id}", json={"title": "偷改"}, headers=stranger["headers"]
    )
    assert r.status_code == 404, r.text


# ---------- 軟刪與還原 ----------


def test_delete_book_owner_only(client, auth, user_factory):
    book_id = _create_book(client, auth)
    editor = _invite_and_get_headers(
        client, auth, book_id, user_factory, "editor-del@test.com", "editor"
    )
    r = client.delete(f"/api/books/{book_id}", headers=editor["headers"])
    assert r.status_code == 403, r.text

    r2 = client.delete(f"/api/books/{book_id}", headers=auth["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["success"] is True


def test_deleted_book_not_visible_and_get_returns_404(client, auth):
    book_id = _create_book(client, auth)
    client.delete(f"/api/books/{book_id}", headers=auth["headers"])

    r = client.get(f"/api/books/{book_id}", headers=auth["headers"])
    assert r.status_code == 404, r.text

    r2 = client.get("/api/books", headers=auth["headers"])
    assert r2.json()["data"]["items"] == []


def test_deleted_book_appears_in_trash_with_days_remaining(client, auth):
    book_id = _create_book(client, auth)
    client.delete(f"/api/books/{book_id}", headers=auth["headers"])

    r = client.get("/api/books/trash", headers=auth["headers"])
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == book_id
    assert items[0]["days_remaining"] == 30  # settings.restore_window_days 預設 30


def test_restore_book_owner_only(client, auth, user_factory):
    book_id = _create_book(client, auth)
    editor = _invite_and_get_headers(
        client, auth, book_id, user_factory, "editor-res@test.com", "editor"
    )
    client.delete(f"/api/books/{book_id}", headers=auth["headers"])

    r = client.post(f"/api/books/{book_id}/restore", headers=editor["headers"])
    assert r.status_code == 403, r.text

    r2 = client.post(f"/api/books/{book_id}/restore", headers=auth["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["book"]["deleted_at"] is None


def test_restore_book_not_deleted_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = client.post(f"/api/books/{book_id}/restore", headers=auth["headers"])
    assert r.status_code == 400, r.text


def test_restore_book_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    client.delete(f"/api/books/{book_id}", headers=auth["headers"])
    stranger = user_factory(email="stranger3@test.com")
    r = client.post(f"/api/books/{book_id}/restore", headers=stranger["headers"])
    assert r.status_code == 404, r.text


# ---------- 成員管理：邀請 ----------


def test_invite_registered_user_becomes_member_immediately(client, auth, user_factory):
    book_id = _create_book(client, auth)
    user_factory(email="joinme@test.com")
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "joinme@test.com", "role": "editor"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    inv = r.json()["data"]["invitation"]
    assert inv["status"] == "accepted"
    assert inv["registered"] is True

    r2 = client.get(f"/api/books/{book_id}/members", headers=auth["headers"])
    emails = {m["email"] for m in r2.json()["data"]["members"]}
    assert "joinme@test.com" in emails


def test_invite_unregistered_user_creates_pending_invitation(client, auth):
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "notyet@test.com", "role": "viewer"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    inv = r.json()["data"]["invitation"]
    assert inv["status"] == "pending"
    assert inv["registered"] is False
    assert inv["token"]


def test_invite_self_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": auth["user"]["email"], "role": "editor"},
        headers=auth["headers"],
    )
    assert r.status_code == 400, r.text


def test_invite_already_member_conflicts(client, auth, user_factory):
    book_id = _create_book(client, auth)
    member = _invite_and_get_headers(
        client, auth, book_id, user_factory, "already@test.com", "editor"
    )
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "already@test.com", "role": "viewer"},
        headers=auth["headers"],
    )
    assert r.status_code == 409, r.text


def test_invite_invalid_role_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "x@test.com", "role": "owner"},
        headers=auth["headers"],
    )
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("role", ["editor", "reviewer", "viewer"])
def test_invite_member_non_owner_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    member = _invite_and_get_headers(
        client, auth, book_id, user_factory, f"inviter-{role}@test.com", role
    )
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "newbie@test.com", "role": "viewer"},
        headers=member["headers"],
    )
    assert r.status_code == 403, r.text


# ---------- 成員管理：角色調整 / 移除 ----------


def test_update_member_role_owner_only(client, auth, user_factory):
    book_id = _create_book(client, auth)
    editor = _invite_and_get_headers(
        client, auth, book_id, user_factory, "role-target@test.com", "editor"
    )
    target_id = editor["user"]["id"]

    r = client.patch(
        f"/api/books/{book_id}/members/{target_id}",
        json={"role": "viewer"},
        headers=editor["headers"],  # 非 owner
    )
    assert r.status_code == 403, r.text

    r2 = client.patch(
        f"/api/books/{book_id}/members/{target_id}",
        json={"role": "viewer"},
        headers=auth["headers"],
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["member"]["role"] == "viewer"


def test_update_member_role_owner_cannot_be_demoted(client, auth):
    r = client.get("/api/auth/me", headers=auth["headers"])
    owner_id = r.json()["data"]["user"]["id"]
    book_id = _create_book(client, auth)
    r2 = client.patch(
        f"/api/books/{book_id}/members/{owner_id}",
        json={"role": "viewer"},
        headers=auth["headers"],
    )
    assert r2.status_code == 403, r2.text


def test_update_member_role_nonexistent_member_404(client, auth):
    book_id = _create_book(client, auth)
    r = client.patch(
        f"/api/books/{book_id}/members/999999",
        json={"role": "viewer"},
        headers=auth["headers"],
    )
    assert r.status_code == 404, r.text


def test_remove_member_owner_only(client, auth, user_factory):
    book_id = _create_book(client, auth)
    viewer = _invite_and_get_headers(
        client, auth, book_id, user_factory, "remove-target@test.com", "viewer"
    )
    target_id = viewer["user"]["id"]

    r = client.delete(
        f"/api/books/{book_id}/members/{target_id}", headers=viewer["headers"]
    )
    assert r.status_code == 403, r.text

    r2 = client.delete(
        f"/api/books/{book_id}/members/{target_id}", headers=auth["headers"]
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["success"] is True

    r3 = client.get(f"/api/books/{book_id}/members", headers=auth["headers"])
    emails = {m["email"] for m in r3.json()["data"]["members"]}
    assert "remove-target@test.com" not in emails


def test_remove_member_cannot_remove_owner(client, auth):
    r = client.get("/api/auth/me", headers=auth["headers"])
    owner_id = r.json()["data"]["user"]["id"]
    book_id = _create_book(client, auth)
    r2 = client.delete(f"/api/books/{book_id}/members/{owner_id}", headers=auth["headers"])
    assert r2.status_code == 403, r2.text


def test_list_members_accessible_to_any_member_role(client, auth, user_factory):
    """列成員只需是成員（任何角色皆可），非 owner 專屬。"""
    book_id = _create_book(client, auth)
    viewer = _invite_and_get_headers(
        client, auth, book_id, user_factory, "viewer-list@test.com", "viewer"
    )
    r = client.get(f"/api/books/{book_id}/members", headers=viewer["headers"])
    assert r.status_code == 200, r.text
    emails = {m["email"] for m in r.json()["data"]["members"]}
    assert auth["user"]["email"] in emails
    assert "viewer-list@test.com" in emails


def test_list_members_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="stranger4@test.com")
    r = client.get(f"/api/books/{book_id}/members", headers=stranger["headers"])
    assert r.status_code == 404, r.text


# ---------- 邀請接受流程 ----------
#
# 注意（實測釐清）：/api/auth/register 在註冊時會自動比對 email 找出所有 pending
# 邀請並直接加入成員、將邀請標記為 accepted（app/routers/auth.py 的
# `_auto_accept_invites`，spec FR-21）。也就是說「被邀請信箱本人註冊」這條路徑
# 在註冊當下就已經完成加入，事後再呼叫 POST /api/invitations/accept 用同一
# token 會因為邀請已非 pending 而 404。POST /api/invitations/accept 這支端點
# 實際只在「token 持有者是另一個已註冊並登入中的帳號」時才會走到，見下方測試。


def test_register_with_matching_pending_invite_auto_joins_book(client, user_factory):
    """spec FR-21：註冊信箱與 pending 邀請相符時，自動加入書籍並標記邀請已接受。"""
    owner = user_factory(email="inv-owner@test.com")
    book_id = _create_book(client, owner)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "invitee@test.com", "role": "editor"},
        headers=owner["headers"],
    )
    assert r.json()["data"]["invitation"]["status"] == "pending"

    invitee = user_factory(email="invitee@test.com")
    r2 = client.get(f"/api/books/{book_id}", headers=invitee["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["my_role"] == "editor"


def test_accept_invitation_invalid_token_404(client, auth):
    r = client.post(
        "/api/invitations/accept", json={"token": "not-a-real-token"}, headers=auth["headers"]
    )
    assert r.status_code == 404, r.text


def test_accept_invitation_by_non_invited_user_forbidden(client, auth, user_factory):
    """安全（S1）：POST /invitations/accept 必須驗證登入者 email == 邀請信箱。
    email 不符者取得外流 token 也不能兌換（403），且不會被加入成員。
    """
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "ghost@test.com", "role": "viewer"},  # 從未註冊的信箱
        headers=auth["headers"],
    )
    token = r.json()["data"]["invitation"]["token"]

    redeemer = user_factory(email="redeemer@test.com")  # email 與邀請信箱不同
    r2 = client.post(
        "/api/invitations/accept", json={"token": token}, headers=redeemer["headers"]
    )
    assert r2.status_code == 403, r2.text  # 修復後：拒絕非受邀信箱兌換

    # 未被加入：非成員存取該書應 404（不洩漏存在）。
    r3 = client.get(f"/api/books/{book_id}", headers=redeemer["headers"])
    assert r3.status_code == 404, r3.text


def test_accept_invitation_by_invited_email_succeeds(client, auth, user_factory):
    """受邀信箱本人（大小寫不敏感）可正常接受邀請並成為指定角色成員。"""
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "Invited@test.com", "role": "editor"},
        headers=auth["headers"],
    )
    token = r.json()["data"]["invitation"]["token"]

    invited = user_factory(email="invited@test.com")  # 大小寫不同仍應相符
    r2 = client.post(
        "/api/invitations/accept", json={"token": token}, headers=invited["headers"]
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["book_id"] == book_id

    r3 = client.get(f"/api/books/{book_id}", headers=invited["headers"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["my_role"] == "editor"


def test_accept_invitation_token_consumed_after_register_autojoin(client, auth, user_factory):
    """受邀信箱註冊時會自動接受 pending 邀請（FR-21），token 隨即被消耗（標記
    accepted）；之後再手動兌換同一 token 應回 404（已失效）。

    註：S1 修復後，「已是成員」的 409 分支需 email 相符才可能到達，而註冊自動接受
    會先把邀請標記為 accepted，故正常流程下無法構造「pending 邀請 + 已是成員」；
    本測試改驗證合法且可達的 token 消耗行為。
    """
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/members",
        json={"email": "joiner@test.com", "role": "viewer"},  # 從未註冊的信箱
        headers=auth["headers"],
    )
    token = r.json()["data"]["invitation"]["token"]

    # 受邀信箱本人註冊 → 自動接受 pending 邀請，token 被消耗
    joiner = user_factory(email="joiner@test.com")
    # 已是成員：可存取該書
    assert client.get(f"/api/books/{book_id}", headers=joiner["headers"]).status_code == 200

    # 手動兌換已消耗的 token → 404（已失效）
    r2 = client.post(
        "/api/invitations/accept", json={"token": token}, headers=joiner["headers"]
    )
    assert r2.status_code == 404, r2.text


# ---------- 權限矩陣總覽：對每個角色驗證可做/不可做的操作 ----------


@pytest.mark.parametrize("role,can_view", [
    ("owner", True), ("editor", True), ("reviewer", True), ("viewer", True),
])
def test_permission_matrix_view_allowed_for_all_roles(
    client, auth, user_factory, role, can_view
):
    book_id = _create_book(client, auth)
    if role == "owner":
        member = auth
    else:
        member = _invite_and_get_headers(
            client, auth, book_id, user_factory, f"matrix-view-{role}@test.com", role
        )
    r = client.get(f"/api/books/{book_id}", headers=member["headers"])
    assert (r.status_code == 200) == can_view, r.text
    r2 = client.get(f"/api/books/{book_id}/stats", headers=member["headers"])
    assert (r2.status_code == 200) == can_view, r2.text


@pytest.mark.parametrize("role,can_manage", [
    ("owner", True), ("editor", False), ("reviewer", False), ("viewer", False),
])
def test_permission_matrix_manage_only_owner(
    client, auth, user_factory, role, can_manage
):
    """管理操作（改書中繼資料/刪書/邀請成員/調整角色/移除成員）僅 owner 可執行。"""
    book_id = _create_book(client, auth)
    if role == "owner":
        member = auth
    else:
        member = _invite_and_get_headers(
            client, auth, book_id, user_factory, f"matrix-manage-{role}@test.com", role
        )

    r = client.patch(
        f"/api/books/{book_id}", json={"title": "矩陣改名"}, headers=member["headers"]
    )
    expected = 200 if can_manage else 403
    assert r.status_code == expected, r.text

    r2 = client.post(
        f"/api/books/{book_id}/members",
        json={"email": f"matrix-invitee-{role}@test.com", "role": "viewer"},
        headers=member["headers"],
    )
    assert r2.status_code == expected, r2.text
