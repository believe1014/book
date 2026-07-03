"""效能：list_books / list_trash 的 N+1 批次化回歸測試（效能設計 P1）。

兩條防線：
1. 等價性 — 批次序列化 `_book_dicts_batch` 與逐本 `_book_dict` 對相同資料輸出
   逐欄位相同，且 GET /api/books 回傳的 word_count/progress 與逐本算法一致。
2. 查詢常數級 — 以 SQLAlchemy 游標事件計數，證明列 M 本書時實際 SQL 執行數
   不隨 M 線性成長（M=1 與 M=5 的查詢數相同）。
"""
import contextlib

from sqlalchemy import event
from sqlmodel import Session, select

from app.database import engine
from app.models import Book, BookMember, Chapter, ChapterContent
from app.routers.books import _book_dict, _book_dicts_batch


# ---------- 輔助：直接以 Session 播種可控的字數/狀態 ----------


def _seed_book(session, owner_id, title, chapters):
    """建立一本書（owner 已是成員）與其章節/內容。

    chapters: list of (status, word_count | None)；word_count=None 表示該章無內容。
    回傳 Book。
    """
    book = Book(title=title, owner_id=owner_id, status="draft")
    session.add(book)
    session.commit()
    session.refresh(book)
    session.add(BookMember(book_id=book.id, user_id=owner_id, role="owner"))
    session.commit()
    for i, (status, wc) in enumerate(chapters):
        ch = Chapter(book_id=book.id, title=f"ch{i}", order_index=i, status=status)
        session.add(ch)
        session.commit()
        session.refresh(ch)
        if wc is not None:
            session.add(ChapterContent(chapter_id=ch.id, word_count=wc))
            session.commit()
    session.refresh(book)
    return book


@contextlib.contextmanager
def _count_sql():
    """計數區塊內實際送到 DB 的 SQL 陳述式數量。"""
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield counter
    finally:
        event.remove(engine, "before_cursor_execute", _before)


# ---------- 等價性 ----------


def test_batch_matches_per_book_serialization(auth):
    """_book_dicts_batch 對每本書的輸出與逐本 _book_dict 逐欄位相同。"""
    owner_id = auth["user"]["id"]
    with Session(engine) as session:
        b1 = _seed_book(session, owner_id, "書一", [
            ("done", 100), ("writing", 50), ("not_started", None),
        ])  # word_count=150, progress=round(1/3,4)
        b2 = _seed_book(session, owner_id, "書二", [])  # 空書：word=0 progress=0.0
        b3 = _seed_book(session, owner_id, "書三", [
            ("done", 10), ("done", 20),
        ])  # word=30 progress=1.0
        books = [b1, b2, b3]

        per_book = [_book_dict(session, b) for b in books]
        batched = _book_dicts_batch(session, books)

    assert batched == per_book
    # 明確驗證關鍵計算，避免兩種算法同錯
    assert per_book[0]["word_count"] == 150
    assert per_book[0]["progress"] == round(1 / 3, 4)
    assert per_book[1]["word_count"] == 0 and per_book[1]["progress"] == 0.0
    assert per_book[2]["word_count"] == 30 and per_book[2]["progress"] == 1.0


def test_list_books_endpoint_word_count_and_progress(client, auth):
    """GET /api/books 的 word_count/progress 與逐本 _book_dict 一致。"""
    owner_id = auth["user"]["id"]
    with Session(engine) as session:
        _seed_book(session, owner_id, "AAA", [("done", 100), ("writing", 40)])
        _seed_book(session, owner_id, "BBB", [("not_started", None)])
        books = session.exec(select(Book).where(Book.owner_id == owner_id)).all()
        expected = {b.title: _book_dict(session, b) for b in books}

    r = client.get("/api/books", headers=auth["headers"])
    assert r.status_code == 200, r.text
    for item in r.json()["data"]["items"]:
        exp = expected[item["title"]]
        assert item["word_count"] == exp["word_count"]
        assert item["progress"] == exp["progress"]


# ---------- 查詢常數級（N+1 已消除） ----------


def test_list_books_query_count_constant(client, auth):
    """列 1 本與 5 本書的實際 SQL 執行數相同（不隨書本數線性成長）。"""
    owner_id = auth["user"]["id"]
    with Session(engine) as session:
        _seed_book(session, owner_id, "only", [("done", 100), ("writing", 10)])

    with _count_sql() as c1:
        r = client.get("/api/books", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["items"]) == 1
    n1 = c1["n"]

    with Session(engine) as session:
        for i in range(4):
            _seed_book(session, owner_id, f"more{i}", [("done", 20), ("not_started", None)])

    with _count_sql() as c5:
        r = client.get("/api/books", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["items"]) == 5
    n5 = c5["n"]

    # 批次化後查詢數為常數；N+1 舊版會多出 2*(5-1)=8 個查詢。
    assert n5 == n1, f"查詢數隨書本數成長：M=1 用 {n1}，M=5 用 {n5}（疑似 N+1 回歸）"


def test_list_trash_query_count_constant(client, auth):
    """回收桶列 1 本與 5 本已刪書的 SQL 執行數相同。"""
    owner_id = auth["user"]["id"]

    def _soft_delete(session, book):
        from app.models import utcnow
        book.deleted_at = utcnow()
        session.add(book)
        session.commit()

    with Session(engine) as session:
        b = _seed_book(session, owner_id, "trash-only", [("done", 5)])
        _soft_delete(session, b)

    with _count_sql() as c1:
        r = client.get("/api/books/trash", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["items"]) == 1
    n1 = c1["n"]

    with Session(engine) as session:
        for i in range(4):
            b = _seed_book(session, owner_id, f"trash{i}", [("done", 5), ("writing", 3)])
            _soft_delete(session, b)

    with _count_sql() as c5:
        r = client.get("/api/books/trash", headers=auth["headers"])
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]["items"]) == 5
    n5 = c5["n"]

    assert n5 == n1, f"回收桶查詢數隨書本數成長：M=1 用 {n1}，M=5 用 {n5}"
