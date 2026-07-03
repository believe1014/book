"""Media 路由整合測試（上傳、外部連結、配額/檔案大小限制、列表/刪除、跨書隔離）。

涵蓋（見 app/routers/media.py + app/config.py）：
- 上傳檔案：權限（owner/editor/reviewer 可上傳，COMMENT_ROLES；viewer 403）
- 不支援的副檔名 → 400
- 單檔超過 max_file_size（預設 50MB，測試中以 monkeypatch 調小門檻）→ 413
- 書籍配額超過 book_quota（預設 1GB，測試中以 monkeypatch 調小門檻）→ 413
- 外部連結（url 型別）：不佔配額
- 列表（含 quota_used/quota_total、type 篩選、search 篩選）
- 刪除：僅 EDIT_ROLES（owner/editor）可刪除，reviewer/viewer 403
- ref_count 遞增：僅 EDIT_ROLES
- 跨書隔離：非成員一律 404
"""
import pytest

from app.config import settings


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


def _upload(client, headers, book_id, filename="pic.png", content=b"fake-png-bytes", mime="image/png"):
    return client.post(
        f"/api/books/{book_id}/media",
        files={"file": (filename, content, mime)},
        headers=headers,
    )


# ---------- 上傳：權限矩陣 ----------


@pytest.mark.parametrize("role", ["editor", "reviewer"])
def test_upload_media_allowed_roles(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    member = _invite(client, auth, book_id, user_factory, f"media-{role}@test.com", role)
    r = _upload(client, member["headers"], book_id)
    assert r.status_code == 200, r.text
    asset = r.json()["data"]["asset"]
    assert asset["type"] == "image"
    assert asset["filename"] == "pic.png"
    assert asset["size_bytes"] == len(b"fake-png-bytes")
    assert asset["uploaded_by"] == member["user"]["id"]
    assert asset["url"].startswith(f"/storage/{book_id}/")


def test_upload_media_owner_allowed(client, auth):
    book_id = _create_book(client, auth)
    r = _upload(client, auth["headers"], book_id)
    assert r.status_code == 200, r.text


def test_upload_media_viewer_forbidden(client, auth, user_factory):
    book_id = _create_book(client, auth)
    viewer = _invite(client, auth, book_id, user_factory, "media-viewer@test.com", "viewer")
    r = _upload(client, viewer["headers"], book_id)
    assert r.status_code == 403, r.text


def test_upload_media_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="media-stranger@test.com")
    r = _upload(client, stranger["headers"], book_id)
    assert r.status_code == 404, r.text


def test_upload_media_unsupported_extension_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = _upload(client, auth["headers"], book_id, filename="malware.exe", mime="application/octet-stream")
    assert r.status_code == 400, r.text


def test_upload_media_no_file_or_url_rejected(client, auth):
    book_id = _create_book(client, auth)
    r = client.post(f"/api/books/{book_id}/media", headers=auth["headers"])
    assert r.status_code == 400, r.text


# ---------- 外部連結（不佔配額）----------


def test_upload_external_link_does_not_count_toward_quota(client, auth):
    book_id = _create_book(client, auth)
    r = client.post(
        f"/api/books/{book_id}/media",
        data={"url": "https://example.com/cover.png", "type": "link"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    asset = r.json()["data"]["asset"]
    assert asset["type"] == "link"
    assert asset["url"] == "https://example.com/cover.png"
    assert asset["size_bytes"] is None

    r2 = client.get(f"/api/books/{book_id}/media", headers=auth["headers"])
    assert r2.json()["data"]["quota_used"] == 0  # link 不計入配額


# ---------- 檔案大小 / 配額限制 ----------


def test_upload_media_exceeds_max_file_size_413(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "max_file_size", 10)  # 調小門檻方便測試
    book_id = _create_book(client, auth)
    r = _upload(client, auth["headers"], book_id, content=b"x" * 100)
    assert r.status_code == 413, r.text


def test_upload_media_within_file_size_limit_ok(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "max_file_size", 10)
    book_id = _create_book(client, auth)
    r = _upload(client, auth["headers"], book_id, content=b"x" * 5)
    assert r.status_code == 200, r.text


def test_upload_media_exceeds_book_quota_413(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "book_quota", 50)  # 每本書配額調小
    book_id = _create_book(client, auth)
    r1 = _upload(client, auth["headers"], book_id, filename="a.png", content=b"x" * 30)
    assert r1.status_code == 200, r1.text
    r2 = _upload(client, auth["headers"], book_id, filename="b.png", content=b"x" * 30)
    assert r2.status_code == 413, r2.text  # 累計 60 > 50 配額上限

    # 確認第二次真的沒有寫入（列表只有一筆）
    r3 = client.get(f"/api/books/{book_id}/media", headers=auth["headers"])
    assert len(r3.json()["data"]["items"]) == 1


def test_quota_is_per_book_not_shared(client, auth, monkeypatch):
    """配額應以「每本書」為單位，不同書之間互不影響。"""
    monkeypatch.setattr(settings, "book_quota", 50)
    book_a = _create_book(client, auth, "配額書A")
    book_b = _create_book(client, auth, "配額書B")
    r1 = _upload(client, auth["headers"], book_a, filename="a.png", content=b"x" * 30)
    assert r1.status_code == 200, r1.text
    r2 = _upload(client, auth["headers"], book_b, filename="b.png", content=b"x" * 30)
    assert r2.status_code == 200, r2.text  # 書B的配額未被書A佔用


# ---------- 列表（type/search 篩選、quota 回報）----------


def test_list_media_filters_by_type_and_search(client, auth):
    book_id = _create_book(client, auth)
    _upload(client, auth["headers"], book_id, filename="cover.png", content=b"aaa")
    _upload(client, auth["headers"], book_id, filename="clip.mp4", content=b"bbbb", mime="video/mp4")

    r = client.get(f"/api/books/{book_id}/media", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["items"]) == 2
    assert data["quota_used"] == len(b"aaa") + len(b"bbbb")
    assert data["quota_total"] == settings.book_quota

    r2 = client.get(f"/api/books/{book_id}/media", params={"type": "video"}, headers=auth["headers"])
    assert len(r2.json()["data"]["items"]) == 1
    assert r2.json()["data"]["items"][0]["filename"] == "clip.mp4"

    r3 = client.get(f"/api/books/{book_id}/media", params={"search": "cover"}, headers=auth["headers"])
    assert len(r3.json()["data"]["items"]) == 1
    assert r3.json()["data"]["items"][0]["filename"] == "cover.png"


def test_list_media_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="media-list-stranger@test.com")
    r = client.get(f"/api/books/{book_id}/media", headers=stranger["headers"])
    assert r.status_code == 404, r.text


# ---------- 刪除：僅 EDIT_ROLES ----------


def test_delete_media_editor_allowed_reviewer_viewer_forbidden(client, auth, user_factory):
    book_id = _create_book(client, auth)
    reviewer = _invite(client, auth, book_id, user_factory, "media-del-reviewer@test.com", "reviewer")
    viewer = _invite(client, auth, book_id, user_factory, "media-del-viewer@test.com", "viewer")
    asset = _upload(client, auth["headers"], book_id).json()["data"]["asset"]

    r1 = client.delete(f"/api/media/{asset['id']}", headers=reviewer["headers"])
    assert r1.status_code == 403, r1.text

    r2 = client.delete(f"/api/media/{asset['id']}", headers=viewer["headers"])
    assert r2.status_code == 403, r2.text

    r3 = client.delete(f"/api/media/{asset['id']}", headers=auth["headers"])
    assert r3.status_code == 200, r3.text
    assert r3.json()["data"]["success"] is True

    r4 = client.get(f"/api/books/{book_id}/media", headers=auth["headers"])
    assert r4.json()["data"]["items"] == []


def test_delete_media_editor_role_allowed(client, auth, user_factory):
    book_id = _create_book(client, auth)
    editor = _invite(client, auth, book_id, user_factory, "media-del-editor@test.com", "editor")
    asset = _upload(client, auth["headers"], book_id).json()["data"]["asset"]
    r = client.delete(f"/api/media/{asset['id']}", headers=editor["headers"])
    assert r.status_code == 200, r.text


def test_delete_nonexistent_media_404(client, auth):
    r = client.delete("/api/media/999999", headers=auth["headers"])
    assert r.status_code == 404, r.text


# ---------- ref_count 遞增：僅 EDIT_ROLES ----------


def test_increment_ref_edit_roles_only(client, auth, user_factory):
    book_id = _create_book(client, auth)
    reviewer = _invite(client, auth, book_id, user_factory, "media-ref-reviewer@test.com", "reviewer")
    asset = _upload(client, auth["headers"], book_id).json()["data"]["asset"]

    r1 = client.post(f"/api/media/{asset['id']}/ref", headers=reviewer["headers"])
    assert r1.status_code == 403, r1.text

    r2 = client.post(f"/api/media/{asset['id']}/ref", headers=auth["headers"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["ref_count"] == 1

    r3 = client.post(f"/api/media/{asset['id']}/ref", headers=auth["headers"])
    assert r3.json()["data"]["ref_count"] == 2


def test_increment_ref_nonexistent_404(client, auth):
    r = client.post("/api/media/999999/ref", headers=auth["headers"])
    assert r.status_code == 404, r.text


# ---------- 跨書隔離 ----------


def test_cross_book_isolation_media_404(client, auth, user_factory):
    book_a = _create_book(client, auth, "素材書A")
    asset_a = _upload(client, auth["headers"], book_a).json()["data"]["asset"]

    other_owner = user_factory(email="media-other-owner@test.com")
    book_b = _create_book(client, other_owner, "素材書B")
    b_editor = _invite(client, other_owner, book_b, user_factory, "media-b-editor@test.com", "editor")

    # 書B的 editor 不能看/傳/刪 書A 的素材清單
    r = client.get(f"/api/books/{book_a}/media", headers=b_editor["headers"])
    assert r.status_code == 404, r.text

    r2 = _upload(client, b_editor["headers"], book_a)
    assert r2.status_code == 404, r2.text

    # 素材 id 屬於書A，書B的 editor 嘗試刪除 → get_book_for_member 以素材的
    # book_id 檢查成員關係，非本書成員一律 404
    r3 = client.delete(f"/api/media/{asset_a['id']}", headers=b_editor["headers"])
    assert r3.status_code == 404, r3.text

    r4 = client.post(f"/api/media/{asset_a['id']}/ref", headers=b_editor["headers"])
    assert r4.status_code == 404, r4.text
