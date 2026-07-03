"""章節編輯鎖（soft-lock）整合測試（見 app/services/locks.py + app/routers/content.py）。

鎖端點掛在 content 路由下：POST/DELETE /api/chapters/{id}/lock。GET
/api/chapters/{id}/content 會一併回報目前的鎖狀態（lock_owner/name/expires_at）。

涵蓋：
- 取得鎖成功（editor/owner）；他人取鎖被拒 → 423
- 鎖的持有者可重複取鎖（等同 refresh，不受自己鎖阻擋）
- 釋放鎖：持有者釋放後，他人可取得
- 非持有者呼叫釋放：對他人的鎖無效（lock_manager 內部以 user_id 比對），
  Q3 後端點回應 released 反映實際結果（本人持鎖才 True），不再誤導性恆回成功
- 權限矩陣：取鎖僅 EDIT_ROLES（reviewer/viewer 403）；釋放鎖端點本身未限制
  角色（見報告），但因 lock_manager.release() 內部仍以 user_id 比對持有者，
  非持有者呼叫不會真的清除他人的鎖（無安全漏洞，僅回應語意可能誤導）
- idle 逾時釋放：直接操弄 lock_manager._locks 的 expires_at 模擬時間流逝，
  驗證逾時後鎖視為已釋放、可被他人取得（對應 config.lock_idle_seconds=60）
- 不同章節的鎖互相獨立
- 跨書隔離：非成員一律 404
"""
from datetime import datetime, timedelta, timezone

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


def _acquire(client, headers, chapter_id):
    return client.post(f"/api/chapters/{chapter_id}/lock", headers=headers)


def _release(client, headers, chapter_id):
    return client.delete(f"/api/chapters/{chapter_id}/lock", headers=headers)


# ---------- 基本取得 / 衝突 ----------


def test_acquire_lock_success(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = _acquire(client, auth["headers"], ch["id"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["lock_owner"] == auth["user"]["id"]
    assert data["name"] == auth["user"]["name"]
    assert data["expires_at"]


def test_acquire_lock_conflict_when_held_by_another_user(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-conflict-editor@test.com", "editor")

    r1 = _acquire(client, auth["headers"], ch["id"])
    assert r1.status_code == 200, r1.text

    r2 = _acquire(client, editor["headers"], ch["id"])
    assert r2.status_code == 423, r2.text  # spec: LOCKED


def test_acquire_lock_by_current_holder_refreshes_without_conflict(client, auth):
    """持有者本人重複取鎖視為 refresh，不會被自己的鎖擋下。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r1 = _acquire(client, auth["headers"], ch["id"])
    assert r1.status_code == 200, r1.text
    r2 = _acquire(client, auth["headers"], ch["id"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["lock_owner"] == auth["user"]["id"]


# ---------- 釋放鎖 ----------


def test_release_lock_by_holder_frees_it_for_others(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-release-editor@test.com", "editor")

    _acquire(client, auth["headers"], ch["id"])
    r = _release(client, auth["headers"], ch["id"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["released"] is True  # Q3：本人持鎖，確實釋放

    # 鎖已釋放，GET content 應顯示 lock=None
    r_content = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    assert r_content.json()["data"]["lock"] is None

    # 他人現在可取得鎖
    r2 = _acquire(client, editor["headers"], ch["id"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["lock_owner"] == editor["user"]["id"]


def test_release_lock_by_non_holder_does_not_release_others_lock(client, auth, user_factory):
    """非持有者呼叫釋放：lock_manager.release() 內部以 user_id 比對，實際不會
    清除他人持有的鎖。Q3 後端點回應反映實際結果 released=False（不再誤導性地
    恆回 success:true）；維持「僅持有者能真正釋放」語意，無安全漏洞。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-nonholder-editor@test.com", "editor")

    _acquire(client, auth["headers"], ch["id"])  # owner 持有鎖

    r = _release(client, editor["headers"], ch["id"])  # 非持有者呼叫釋放
    assert r.status_code == 200, r.text
    assert r.json()["data"]["released"] is False  # Q3：未持鎖，實際未釋放

    # 但鎖實際上仍由 owner 持有，其他人仍無法取得
    r2 = _acquire(client, editor["headers"], ch["id"])
    assert r2.status_code == 423, r2.text


def test_release_lock_when_none_held_returns_released_false(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = _release(client, auth["headers"], ch["id"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["released"] is False  # Q3：無鎖可釋放


# ---------- 權限矩陣 ----------


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_acquire_lock_reviewer_viewer_forbidden(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    member = _invite(client, auth, book_id, user_factory, f"lock-acq-{role}@test.com", role)
    r = _acquire(client, member["headers"], ch["id"])
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_release_lock_not_restricted_by_role(client, auth, user_factory, role):
    """釋放鎖端點本身未做 EDIT_ROLES 限制（reviewer/viewer 也能呼叫），
    但因未持有鎖，實際上是 no-op；此測試記錄現況行為（見報告）。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    member = _invite(client, auth, book_id, user_factory, f"lock-rel-{role}@test.com", role)
    r = _release(client, member["headers"], ch["id"])
    assert r.status_code == 200, r.text  # 未被 403 擋下


def test_acquire_lock_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="lock-stranger@test.com")
    r = _acquire(client, stranger["headers"], ch["id"])
    assert r.status_code == 404, r.text


# ---------- Idle 逾時釋放 ----------


def test_lock_idle_timeout_releases_automatically(client, auth, user_factory):
    """模擬 lock_idle_seconds 逾時：直接把鎖的 expires_at 往前調到已過期，
    驗證 get()/acquire() 視為鎖已釋放，允許他人取得（對應 FR-45）。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-idle-editor@test.com", "editor")

    r1 = _acquire(client, auth["headers"], ch["id"])
    assert r1.status_code == 200, r1.text

    # 直接操弄記憶體內鎖狀態，模擬 idle 超過 lock_idle_seconds 後過期
    entry = lock_manager._locks[ch["id"]]
    entry["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    # get() 應視為已過期並自動清除
    assert lock_manager.get(ch["id"]) is None

    # 他人現在可透過 API 正常取得鎖（不再被 423 擋下）
    r2 = _acquire(client, editor["headers"], ch["id"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["lock_owner"] == editor["user"]["id"]

    # GET content 反映新的持有者
    r3 = client.get(f"/api/chapters/{ch['id']}/content", headers=auth["headers"])
    assert r3.json()["data"]["lock"]["lock_owner"] == editor["user"]["id"]


def test_lock_not_yet_expired_still_blocks(client, auth, user_factory):
    """尚未逾時（expires_at 仍在未來）→ 仍應阻擋他人取鎖。"""
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    editor = _invite(client, auth, book_id, user_factory, "lock-notexpired-editor@test.com", "editor")

    _acquire(client, auth["headers"], ch["id"])
    entry = lock_manager._locks[ch["id"]]
    entry["expires_at"] = datetime.now(timezone.utc) + timedelta(seconds=30)

    r = _acquire(client, editor["headers"], ch["id"])
    assert r.status_code == 423, r.text


# ---------- 不同章節的鎖互相獨立 ----------


def test_locks_are_independent_per_chapter(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch1 = _create_chapter(client, auth["headers"], book_id, "章節一")
    ch2 = _create_chapter(client, auth["headers"], book_id, "章節二")
    editor = _invite(client, auth, book_id, user_factory, "lock-indep-editor@test.com", "editor")

    r1 = _acquire(client, auth["headers"], ch1["id"])
    assert r1.status_code == 200, r1.text

    # ch2 未被鎖定，editor 可正常取得
    r2 = _acquire(client, editor["headers"], ch2["id"])
    assert r2.status_code == 200, r2.text


# ---------- 跨書隔離 ----------


def test_cross_book_isolation_lock_404(client, auth, user_factory):
    book_a = _create_book(client, auth, "鎖定書A")
    ch_a = _create_chapter(client, auth["headers"], book_a, "A書章節")

    other_owner = user_factory(email="lock-other-owner@test.com")
    book_b = _create_book(client, other_owner, "鎖定書B")
    b_editor = _invite(client, other_owner, book_b, user_factory, "lock-b-editor@test.com", "editor")

    r = _acquire(client, b_editor["headers"], ch_a["id"])
    assert r.status_code == 404, r.text

    r2 = _release(client, b_editor["headers"], ch_a["id"])
    assert r2.status_code == 404, r2.text
