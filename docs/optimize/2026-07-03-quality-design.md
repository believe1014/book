# book 系統優化・子專案 4：品質/UX — 設計文件

- 日期：2026-07-03；分支：optimize-quality（自 main，含 251 測試護欄）
- 由子專案1 測試補強發掘的真實缺陷為主，聚焦有客觀對錯者。

## 範圍

### Q1 [高/功能壞] 修復即時協作廣播（核心賣點）
**根因**：`routers/content.py::_broadcast_safe` 在 FastAPI 以 threadpool 執行的**同步**端點（patch_content/acquire_lock/release_lock/restore_version）中呼叫 `asyncio.get_event_loop()`；worker thread 無執行中 loop → RuntimeError 被 `except: pass` 吞掉，`content_updated`/`lock_changed` 廣播從不送達任何 WS client。「協作者即時看到他人編輯/鎖變更」壞掉。

**修**（最小侵入，端點維持同步）：
1. 在 `ws_manager.RoomManager` 加：`set_loop(loop)` 儲存主 event loop 參照；`broadcast_threadsafe(chapter_id, message)` — 若有 loop 則 `asyncio.run_coroutine_threadsafe(self.broadcast(chapter_id, message), self._loop)`（fire-and-forget，不阻塞），無 loop 則安全 no-op。
2. `main.py` lifespan 啟動時 `room_manager.set_loop(asyncio.get_running_loop())`（WS 連線所在的主 loop）。
3. `content.py::_broadcast_safe` 改為呼叫 `room_manager.broadcast_threadsafe(chapter_id, message)`，移除失效的 `get_event_loop()`/`create_task` 路徑。
- **測試**：把既有斷言「壞掉現況」的 `test_ws.py::test_http_triggered_content_updated_broadcast_does_not_reach_ws_client_known_bug` **改寫**為斷言「修復後」：一個 WS client 連上章節，另一路徑經 `PATCH /chapters/{id}/content`（REST）觸發後，該 WS client 應在逾時內收到 `content_updated`；同理補一個 lock_changed 經 REST `POST/DELETE lock` 送達的測試。確認 TestClient 下 run_coroutine_threadsafe 能實際送達（以測試驗證）。

### Q2 [低] create_chapter 空白標題驗證一致化
`routers/chapters.py::create_chapter` 未檢查空白標題（books.py 對空白 title 回 400）。加 `title.strip()` 檢查，空則 `errors.bad_request`。更新既有斷言現況（接受空白）的 test_chapters 測試為斷言 400。

### Q3 [低] DELETE /chapters/{id}/lock 語意
現況：無角色限制、恆回 `success:true`（即使未持鎖）。內部仍以 user_id 比對持有者（非安全漏洞），但語意誤導。
- **修**：回應反映實際結果（釋放到鎖 vs 未持鎖），例如 `{"data": {"released": <bool>}}`；維持既有「僅持有者能真正釋放」語意（不需加角色限制，因鎖本就是誰持有誰釋放）。更新對應測試。

### Q4 [輕] 前端一致性/a11y 快查（僅明確項）
輕量檢查前端（React/Vite）明顯的客觀問題：build 是否乾淨、有無明顯 a11y 缺漏（表單 label、按鈕 aria）、錯誤處理一致性。**只做有明確對錯的小修**；主觀視覺/重排留給使用者。若無明顯項則僅記錄。

## 非目標
- 主觀前端視覺重設計、lock 改 Redis、跨副本廣播。

## 測試策略
- `cd backend && venv/Scripts/python.exe -m pytest`（exit code 為準），基線 251 綠不得回歸。
- Q1 的 known-bug 測試由「斷言壞掉」翻轉為「斷言修好」，是本子專案的核心驗收。
