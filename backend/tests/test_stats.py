"""字數統計與章節/書籍統計整合測試。

涵蓋兩層：
1. 純函式單元測試（app/services/wordcount.py）：CJK 逐字計數、拉丁字詞
   （含連字號/撇號合併為單一 token）、標點與空白不計入、段落計數只算
   非空段落/標題、閱讀分鐘估算（CJK 300 字/分、拉丁 200 詞/分）。
2. HTTP 端點整合測試：
   - GET /api/chapters/{id}/stats（app/routers/chapters.py，spec §5.8/FR-61）
   - GET /api/books/{id}/stats（app/routers/books.py + app/services/stats.py，
     spec FR-60/62/63）：總字數、章節數/完成數/進度、目標字數與達成率、
     依編輯者歸屬的貢獻度、當日字數增量。

注意：book_stats 的 today_words 以 ContentVersion.created_at 的日期字串
（YYYY-MM-DD 前綴）與今天比對，測試在同一天執行內建立的版本快照自然落在
「今天」，因此可直接驗證非零累加，不需要另外操弄時間。
"""
import json

import pytest

from app.services.locks import lock_manager
from app.services.wordcount import (
    count_paragraphs, count_words, count_words_from_text, extract_text,
    reading_minutes,
)


@pytest.fixture(autouse=True)
def _reset_locks():
    """lock_manager 是模組層級全域單例，不會隨每測試的 DB schema 重建而清空
    （見 test_content.py/test_locks.py 的相同做法）。本檔會透過 PATCH content
    觸發自動搶鎖，若不清空，跨測試的章節 id 重複時會誤判鎖仍被前一個測試的
    使用者持有。
    """
    lock_manager._locks.clear()
    yield
    lock_manager._locks.clear()


# ======================================================================
# 純函式單元測試：wordcount
# ======================================================================


def _doc(*paragraph_texts):
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": t}]}
            for t in paragraph_texts
        ],
    }


def test_count_words_latin_tokens_split_by_whitespace():
    assert count_words_from_text("hello world foo") == 3


def test_count_words_cjk_counted_per_character():
    assert count_words_from_text("你好世界") == 4


def test_count_words_mixed_cjk_and_latin():
    assert count_words_from_text("你好 hello world 世界") == 6  # 4 CJK + 2 latin


def test_count_words_hyphenated_and_apostrophe_tokens_count_as_one():
    # 連字號/撇號銜接的英文詞視為單一 token（見 _LATIN_TOKEN_RE）
    assert count_words_from_text("state-of-the-art can't stop") == 3


def test_count_words_ignores_punctuation_and_whitespace():
    assert count_words_from_text("你好，世界！Hello, world.") == 6  # 標點不計入
    assert count_words_from_text("   ,,,.!?   ") == 0


def test_count_words_empty_string_is_zero():
    assert count_words_from_text("") == 0


def test_count_words_from_invalid_json_gracefully_returns_zero():
    assert count_words("") == 0
    assert count_words("not-json-{{{") == 0
    assert count_words(None) == 0  # type: ignore[arg-type]


def test_count_words_from_rich_text_json_matches_count_words_from_text():
    content = json.dumps(_doc("hello world", "你好"), ensure_ascii=False)
    assert count_words(content) == count_words_from_text("hello world\n你好\n") == 4


def test_extract_text_separates_blocks_with_newline():
    content = json.dumps(_doc("第一段", "第二段"), ensure_ascii=False)
    assert extract_text(content) == "第一段\n第二段\n"


def test_count_paragraphs_ignores_empty_blocks():
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "有內容"}]},
            {"type": "paragraph", "content": []},  # 空段落，不計入
            {"type": "heading", "content": [{"type": "text", "text": "標題"}]},
        ],
    }
    content = json.dumps(doc, ensure_ascii=False)
    assert count_paragraphs(content) == 2


def test_count_paragraphs_empty_doc_is_zero():
    assert count_paragraphs("{}") == 0
    assert count_paragraphs("") == 0


def test_reading_minutes_cjk_rate_300_per_minute():
    text = "你好" * 150  # 300 個 CJK 字
    assert reading_minutes(text) == 1.0


def test_reading_minutes_latin_rate_200_per_minute():
    text = " ".join(["word"] * 200)  # 200 個拉丁詞
    assert reading_minutes(text) == 1.0


def test_reading_minutes_rounds_to_one_decimal():
    text = "你好" * 30  # 60 CJK 字 -> 60/300 = 0.2 分鐘
    assert reading_minutes(text) == 0.2


# ======================================================================
# HTTP 端點整合測試
# ======================================================================


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


def _patch_content(client, headers, chapter_id, text, base_version=1):
    r = client.patch(
        f"/api/chapters/{chapter_id}/content",
        json={"content_json": _doc(text), "base_version": base_version},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------- 章節統計 ----------


def test_chapter_stats_initial_state_is_zero(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    r = client.get(f"/api/chapters/{ch['id']}/stats", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["word_count"] == 0
    assert data["paragraph_count"] == 0
    assert data["reading_minutes"] == 0.0
    assert "updated_at" in data


def test_chapter_stats_reflects_patch_content(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    _patch_content(client, auth["headers"], ch["id"], "你好" * 150)  # 300 CJK 字

    r = client.get(f"/api/chapters/{ch['id']}/stats", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["word_count"] == 300
    assert data["paragraph_count"] == 1
    assert data["reading_minutes"] == 1.0


@pytest.mark.parametrize("role", ["owner", "editor", "reviewer", "viewer"])
def test_chapter_stats_readable_by_any_member_role(client, auth, user_factory, role):
    """chapter_stats 端點只做 resolve_chapter_book（無 EDIT_ROLES 檢查），
    任何成員角色皆可讀取（含 viewer/reviewer）。
    """
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    if role == "owner":
        member = auth
    else:
        member = _invite(client, auth, book_id, user_factory, f"stats-role-{role}@test.com", role)
    r = client.get(f"/api/chapters/{ch['id']}/stats", headers=member["headers"])
    assert r.status_code == 200, r.text


def test_chapter_stats_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    stranger = user_factory(email="stats-stranger@test.com")
    r = client.get(f"/api/chapters/{ch['id']}/stats", headers=stranger["headers"])
    assert r.status_code == 404, r.text


def test_chapter_stats_cross_book_isolation_404(client, auth, user_factory):
    book_a = _create_book(client, auth, "統計書A")
    ch_a = _create_chapter(client, auth["headers"], book_a, "A書章節")

    other_owner = user_factory(email="stats-other-owner@test.com")
    book_b = _create_book(client, other_owner, "統計書B")
    b_editor = _invite(client, other_owner, book_b, user_factory, "stats-b-editor@test.com", "editor")

    r = client.get(f"/api/chapters/{ch_a['id']}/stats", headers=b_editor["headers"])
    assert r.status_code == 404, r.text


def test_chapter_stats_nonexistent_chapter_404(client, auth):
    r = client.get("/api/chapters/999999/stats", headers=auth["headers"])
    assert r.status_code == 404, r.text


# ---------- 書籍統計 ----------


def test_book_stats_totals_and_progress(client, auth):
    book_id = _create_book(client, auth)
    ch1 = _create_chapter(client, auth["headers"], book_id, "第一章")
    ch2 = _create_chapter(client, auth["headers"], book_id, "第二章")

    _patch_content(client, auth["headers"], ch1["id"], "hello world")  # 2 words
    _patch_content(client, auth["headers"], ch2["id"], "你好世界")  # 4 words

    # 將第一章標記為完成
    r = client.patch(
        f"/api/chapters/{ch1['id']}", json={"status": "done"}, headers=auth["headers"]
    )
    assert r.status_code == 200, r.text

    r = client.get(f"/api/books/{book_id}/stats", headers=auth["headers"])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total_words"] == 6
    assert data["chapter_count"] == 2
    assert data["completed_count"] == 1
    assert data["progress"] == 0.5
    assert data["goal"] is None
    assert data["goal_rate"] is None
    assert data["today_words"] == 6  # 全部由 auth 本人今日編輯


def test_book_stats_goal_rate_computed_when_goal_set(client, auth):
    book_id = _create_book(client, auth)
    ch = _create_chapter(client, auth["headers"], book_id)
    _patch_content(client, auth["headers"], ch["id"], "hello world foo bar")  # 4 words

    r = client.patch(
        f"/api/books/{book_id}", json={"word_count_goal": 10}, headers=auth["headers"]
    )
    assert r.status_code == 200, r.text

    r2 = client.get(f"/api/books/{book_id}/stats", headers=auth["headers"])
    data = r2.json()["data"]
    assert data["goal"] == 10
    assert data["goal_rate"] == 0.4


def test_book_stats_contributors_attributed_by_last_editor_per_chapter(
    client, auth, user_factory
):
    book_id = _create_book(client, auth)
    ch1 = _create_chapter(client, auth["headers"], book_id, "章節一")
    ch2 = _create_chapter(client, auth["headers"], book_id, "章節二")
    editor = _invite(client, auth, book_id, user_factory, "stats-contrib-editor@test.com", "editor")

    _patch_content(client, auth["headers"], ch1["id"], "hello world")  # owner: 2 words
    _patch_content(client, editor["headers"], ch2["id"], "你好世界")  # editor: 4 words

    r = client.get(f"/api/books/{book_id}/stats", headers=auth["headers"])
    data = r.json()["data"]
    assert data["total_words"] == 6

    contributors = {c["user_id"]: c for c in data["contributors"]}
    assert contributors[auth["user"]["id"]]["words"] == 2
    assert contributors[auth["user"]["id"]]["ratio"] == pytest.approx(0.3333, abs=1e-4)
    assert contributors[editor["user"]["id"]]["words"] == 4
    assert contributors[editor["user"]["id"]]["ratio"] == pytest.approx(0.6667, abs=1e-4)

    # 排序：依貢獻字數由多到少
    assert [c["user_id"] for c in data["contributors"]] == [
        editor["user"]["id"], auth["user"]["id"],
    ]

    # today_words 僅計入「目前登入者本人」今日的貢獻，不含其他人
    r_owner = client.get(f"/api/books/{book_id}/stats", headers=auth["headers"])
    assert r_owner.json()["data"]["today_words"] == 2
    r_editor = client.get(f"/api/books/{book_id}/stats", headers=editor["headers"])
    assert r_editor.json()["data"]["today_words"] == 4


def test_book_stats_no_chapters_returns_zeroed_stats(client, auth):
    book_id = _create_book(client, auth)
    r = client.get(f"/api/books/{book_id}/stats", headers=auth["headers"])
    data = r.json()["data"]
    assert data["total_words"] == 0
    assert data["chapter_count"] == 0
    assert data["completed_count"] == 0
    assert data["progress"] == 0.0
    assert data["today_words"] == 0
    assert data["contributors"] == []


@pytest.mark.parametrize("role", ["owner", "editor", "reviewer", "viewer"])
def test_book_stats_readable_by_any_member_role(client, auth, user_factory, role):
    book_id = _create_book(client, auth)
    if role == "owner":
        member = auth
    else:
        member = _invite(client, auth, book_id, user_factory, f"bookstats-role-{role}@test.com", role)
    r = client.get(f"/api/books/{book_id}/stats", headers=member["headers"])
    assert r.status_code == 200, r.text


def test_book_stats_not_member_404(client, auth, user_factory):
    book_id = _create_book(client, auth)
    stranger = user_factory(email="bookstats-stranger@test.com")
    r = client.get(f"/api/books/{book_id}/stats", headers=stranger["headers"])
    assert r.status_code == 404, r.text


def test_book_stats_deleted_book_404(client, auth):
    book_id = _create_book(client, auth)
    r = client.delete(f"/api/books/{book_id}", headers=auth["headers"])
    assert r.status_code == 200, r.text
    r2 = client.get(f"/api/books/{book_id}/stats", headers=auth["headers"])
    assert r2.status_code == 404, r2.text
