# 章節評論／審稿協作 — 設計

> 日期：2026-06-17　狀態：已核准，實作中

## 目標
讓 **reviewer（審閱者）** 從「唯讀」升級為「唯讀正文 + 可評論」，使協作審稿人員能在章節上留下評論（可附圖＋說明、可回覆、可標記已解決），且不會改動作者原稿。

## 角色與權限
- 沿用 owner / editor / reviewer / viewer 四角色。
- 新增 `COMMENT_ROLES = {owner, editor, reviewer}`（可發評論）；`viewer` 不可評論。
- 正文／章節／內容編輯仍受 `EDIT_ROLES = {owner, editor}` 保護（reviewer 不能改正文）。
- 媒體上傳 `POST /books/{id}/media` 放寬給 `COMMENT_ROLES`，讓 reviewer 能上傳評論用圖片（仍無法插入正文，因內容 PATCH 限 EDIT_ROLES）。

## 資料模型：`comments` 表
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | int PK | |
| chapter_id | int FK→chapters.id, index | 章節層級定位 |
| author_id | int FK→users.id, index | |
| parent_id | int? FK→comments.id, index | 空＝頂層；有值＝回覆（單層） |
| body | str | 評論文字 |
| image_url | str? | 附圖（可空） |
| resolved | bool=false | 僅作用在頂層串 |
| resolved_by | int? FK→users.id | |
| created_at / updated_at | str | ISO8601 UTC |
| deleted_at | str? index | 軟刪 |

新表由 `SQLModel.metadata.create_all` 於啟動自動建立，無需遷移。

## API（新 router `comments.py`）
- `GET /chapters/{cid}/comments` — VIEW_ROLES；回傳頂層串＋各自回覆＋作者名＋未解決數。
- `POST /chapters/{cid}/comments` — COMMENT_ROLES；body `{body, parent_id?, image_url?}`。回覆不可再被回覆（單層）。
- `PATCH /comments/{id}` — 僅作者本人；可改 `body` / `image_url`。
- `DELETE /comments/{id}` — 作者本人或 owner；軟刪（連同其回覆）。
- `POST /comments/{id}/resolve`、`DELETE /comments/{id}/resolve` — COMMENT_ROLES；僅頂層。

回應沿用 `{data: ...}` 信封；權限沿用 `resolve_chapter_book`。

## 前端
- 右側面板新增第三分頁 **「評論」**（與 統計／媒體 並列）：`CommentsPanel.jsx`。
  - 列出頂層串（已解決者淡化／收合），每串可展開回覆。
  - 輸入框：文字 + 附圖（上傳，顯示縮圖）+ 送出；回覆框；標記已解決切換；自己的可編輯／刪除。
- **評論數徽章**：頂端列「評論」入口顯示未解決評論數。
- `MembersModal`：審閱者說明改為「可檢視並評論（不可改正文）」。
- `api/client.js`：新增 comment 方法。

## MCP
- `mcp_server.py` 新增唯讀工具 `list_comments(chapter_id)`，回傳該章評論串（供 agent 檢視審稿意見）。

## 範圍外（未來）
- 即時同步（先用開啟章節 / 送出後重載）。
- 行內錨定註解（用章節層級取代）。
- 建議修改／追蹤修訂。

## 驗證
- 前端 `npm run build` 通過。
- 後端：以線上 API 建立／讀取／回覆／解決／刪除一條評論串實測；MCP `list_comments` 取回。
