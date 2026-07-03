# book 系統優化・子專案 3：效能優化 — 設計文件

- 日期：2026-07-03；分支：optimize-perf（自 main，含 247 測試護欄）
- 原則：此 app 為小規模（SQLite/單容器/協作寫書），只做**有明確證據、行為不變、可測**的效率修正，不製造無謂效能工作。

## 盤點結論
- `list_chapters`：單查詢載入全部章節、記憶體建樹 → 無 N+1。
- `get_book_stats`：單一書籍 → 無 list N+1。
- **`list_books` 與 `list_trash`：每本書呼叫 `_book_dict`，各跑 2 個查詢（該書 chapters + 這些 chapters 的 contents）→ N 本書 = O(2N+1) 查詢（典型 N+1）。書架多書時明顯變慢。** ← 唯一明確的效能標的。
- DB 索引：models 已對 FK 與常查欄位設 `Field(index=True)`（owner_id/book_id/user_id/parent_id/deleted_at/chapter_id unique/token unique），覆蓋良好；無 migration 框架，不新增 schema 變更（避免既有 DB 不同步）。

## 範圍

### P1 [主] list_books / list_trash 的 N+1 批次化（行為不變）
- 現況：`items = [_book_dict(session, b) for b in books]`，`_book_dict` 內對每本書各查 chapters 與 contents。
- **改**：新增批次序列化路徑：對本次要回傳的所有 book_id，**一次查**全部未軟刪 chapters（`Chapter.book_id.in_(ids)`）、**一次查**這些 chapter 的全部 contents（`ChapterContent.chapter_id.in_(all_chapter_ids)`），在記憶體依 book_id 聚合 word_count（sum content.word_count）與 progress（done/total，rounding 4 位，空章節=0.0），組出與現行 `_book_dict` **逐欄位相同**的 dict。
- 保留單書路徑（`create_book`、`get_book` 等仍可用原 `_book_dict`）；只有列表端點改走批次。
- **驗收＝行為不變**：既有 test_books 的列表測試（word_count/progress/欄位/排序）全綠；新增測試以 monkeypatch 計數 session.exec 呼叫次數，證明列 M 本書時查詢數為常數級（不隨 M 線性成長），且結果與逐本 `_book_dict` 一致。

### 非目標（不做，留記錄）
- BookMember 複合唯一索引：需 schema 變更但無 migration 框架，既有 DB 不會回填 → 跳過，記錄為後續（導入 Alembic 後再做）。
- lock_manager 改 Redis（跨副本）：架構性，屬部署決策，留待使用者。
- 前端 bundle 分割/lazy load：輕量檢查 build 產物大小；若無明顯問題則僅記錄，不動架構（前端優化偏主觀，留子專案4或使用者決策）。

## 測試策略
- `cd backend && venv/Scripts/python.exe -m pytest`（exit code 為準），基線 247 綠不得回歸。
- 新增查詢計數測試證明 N+1 消除且結果等價。

## 部署影響
- 無 schema、無新依賴、無對外契約變更。
