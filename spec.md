# 協作撰書系統 功能規劃文件（Spec）

| 項目 | 內容 |
|------|------|
| 文件名稱 | 協作撰書系統 Technical Specification |
| 版本 | v1.0 |
| 撰寫日期 | 2026-06-14 |
| 對應文件 | [prd.md](./prd.md) |
| 技術棧 | Python（FastAPI）+ React + SQLite |
| 文件狀態 | Draft |

---

## 1. 功能概述／目標

### 1.1 功能概述
本系統為一套**多人協作撰書平台**，以「書籍 → 章節 → 內容」三層結構組織寫作專案。使用者可建立並管理多本書籍、邀請成員共同編寫、新增與重新命名章節、在章節中撰寫富文本內容，並透過編輯畫面右側面板即時檢視寫作統計數據與管理多媒體素材。

### 1.2 解決的問題
- 取代 Word／檔案來回傳遞的低效協作方式，提供集中化、結構化的線上撰書環境。
- 以章節為單位管理進度，讓寫作團隊清楚掌握全書完成度。
- 提供即時字數、進度、貢獻度等數據回饋。
- 集中管理文字與多媒體素材，方便引用與插入。

### 1.3 本版（v1.0）目標範圍
本 Spec 聚焦於 PRD 中 **P0 + P1** 功能，並對「即時協作」採務實做法（見 §1.4），以單機／小團隊部署為前提：

| 範圍 | 功能 | 來源 |
|------|------|------|
| ✅ 納入 | 使用者註冊／登入（JWT） | 基礎 |
| ✅ 納入 | 書籍 CRUD、軟刪除、狀態管理 | PRD 3.2 |
| ✅ 納入 | 成員與角色權限 | PRD 3.3 |
| ✅ 納入 | 章節 CRUD、重新命名、排序、層級 | PRD 3.4 |
| ✅ 納入 | 章節內容編輯、自動儲存 | PRD 3.5 |
| ✅ 納入 | 即時協作（WebSocket 廣播 + 章節級提示） | PRD 3.5 |
| ✅ 納入 | 統計數據面板 | PRD 3.6 |
| ✅ 納入 | 多媒體上傳與管理 | PRD 3.7 |
| ✅ 納入 | 版本歷史與還原 | PRD 3.8 |
| ⏭ 延後 | 段落評論、@提及（P2） | PRD 3.9 |

### 1.4 協作策略說明（技術取捨）
PRD 提及 CRDT/OT 即時共筆。考量本版採用 SQLite 與單機部署，v1.0 採行**「章節軟鎖定 + WebSocket 即時廣播」**策略：
- 同一章節同時間以**一位主要編輯者**為主（取得編輯權），其他人進入唯讀／即時觀看模式。
- 透過 WebSocket 廣播游標位置、編輯者在線狀態、內容變更通知。
- 完整字元級 CRDT 共筆列為 v2.0 演進項目（屆時可改接 Yjs + 持久化後端）。

---

## 2. 使用情境／使用者故事

### 2.1 角色
| 角色 | 代號 | 說明 |
|------|------|------|
| 擁有者 | Owner | 建立書籍者，擁有完整權限 |
| 編輯者 | Editor | 可編寫章節與內容、上傳素材 |
| 審閱者 | Reviewer | 可檢視、（未來）評論 |
| 讀者 | Viewer | 唯讀檢視 |

### 2.2 使用者故事（User Stories）

**書籍管理**
- US-01：作為 Owner，我想建立一本新書並填寫書名與簡介，以便開始一個寫作專案。
- US-02：作為使用者，我想在書架看到我參與的所有書籍並可搜尋／排序，以便快速找到要編輯的書。
- US-03：作為 Owner，我想刪除書籍且能在 30 天內還原，以避免誤刪。

**成員協作**
- US-04：作為 Owner，我想用 Email 邀請成員並指定角色，以便組成寫作團隊。
- US-05：作為 Editor，我想看到目前有誰在線上、正在編輯哪個章節，以避免重複編輯。

**章節管理**
- US-06：作為 Editor，我想新增章節並重新命名，以建立書籍大綱。
- US-07：作為 Editor，我想拖曳調整章節順序，以重新組織內容結構。
- US-08：作為 Editor，我想建立「章 → 節」的層級，以細分內容。

**內容撰寫**
- US-09：作為 Editor，我想在章節中撰寫富文本，且停止輸入後自動儲存，以免遺失內容。
- US-10：作為 Editor，我想在同一章節即時看到其他協作者的變更，以協調寫作。

**統計面板**
- US-11：作為作者，我想在右側即時看到章節字數、全書字數與完成進度，以掌握寫作量。
- US-12：作為 Owner，我想看到各作者的貢獻字數佔比，以了解團隊投入。

**多媒體**
- US-13：作為 Editor，我想上傳圖片／影片並插入到章節游標處，以豐富內容。

**版本**
- US-14：作為 Editor，我想檢視章節的歷史版本並還原，以回復先前內容。

---

## 3. 功能需求

> 規則編號 FR-x；標註 [P0]/[P1]。

### 3.1 帳號與驗證
- FR-01 [P0]：使用者可用 Email + 密碼註冊；Email 須唯一，密碼以 bcrypt 雜湊儲存。
- FR-02 [P0]：登入成功回傳 JWT（access token，預設 24h 效期）。
- FR-03 [P0]：所有 `/api/*`（除註冊／登入）須帶有效 JWT，否則回 401。

### 3.2 書籍管理
- FR-10 [P0]：登入者可建立書籍，需提供 `title`（必填，1–200 字）；建立者自動成為 Owner。
- FR-11 [P0]：書籍列表僅回傳「使用者為成員」的書籍，支援 `search`（書名模糊）、`sort`（updated_at / created_at / title）、`status` 篩選。
- FR-12 [P0]：Owner 可更新書籍 `title / description / cover / status / tags`。
- FR-13 [P0]：書籍狀態限定 `draft / writing / completed / archived`。
- FR-14 [P0]：刪除為軟刪除（設 `deleted_at`），僅 Owner 可執行；軟刪除後不出現在一般列表。
- FR-15 [P0]：軟刪除 30 天內 Owner 可還原；逾期由排程清理（背景工作，v1.0 可手動觸發）。

### 3.3 成員與權限
- FR-20 [P0]：Owner 可用 Email 邀請成員並指定角色（editor / reviewer / viewer）。
- FR-21 [P0]：若被邀請 Email 尚未註冊，建立 pending 邀請，待對方註冊後自動加入。
- FR-22 [P0]：每位使用者於單一書籍僅一個角色；Owner 角色不可被移除或降級（除非轉移）。
- FR-23 [P0]：權限依 PRD §3.3 矩陣強制執行（後端每個 endpoint 檢核）。
- FR-24 [P0]：Owner 可調整成員角色或移除成員（Owner 自身除外）。

### 3.4 章節管理
- FR-30 [P0]：Owner/Editor 可在書籍下新增章節，需 `title`；新章節 `order` 置於同層級末端。
- FR-31 [P0]：支援兩層結構：頂層章（`parent_id = null`）與子節（`parent_id` 指向章）。最多兩層。
- FR-32 [P0]：可重新命名章節 `title`。
- FR-33 [P0]：可批次更新同一書籍章節的 `order` 與 `parent_id`（拖曳排序）。
- FR-34 [P0]：章節狀態限定 `not_started / writing / reviewing / done`。
- FR-35 [P0]：刪除章節為軟刪除；刪除「章」時其下「節」一併軟刪除（連動）。

### 3.5 內容編輯
- FR-40 [P0]：每個章節對應一份內容文件（富文本，儲存為結構化 JSON，例如 ProseMirror/TipTap doc）。
- FR-41 [P0]：內容更新採 PATCH；前端於停止輸入 2 秒（debounce）後自動送出。
- FR-42 [P0]：每次成功儲存遞增 `version`，並寫入一筆版本快照（見 §3.8）。
- FR-43 [P0]：回應需回傳最新 `version` 與 `updated_at`，前端用以顯示「已儲存」狀態。
- FR-44 [P1]：同章節同時間僅一位「編輯權持有者」；其餘為唯讀，後端拒絕非持有者的內容 PATCH（回 423 Locked）。
- FR-45 [P1]：編輯權持有者離線（WebSocket 斷線）或閒置 60 秒後釋放，他人可接手。

### 3.6 即時協作（WebSocket）
- FR-50 [P1]：使用者開啟章節時建立 WebSocket 連線並加入該章節的 room。
- FR-51 [P1]：廣播事件：`presence`（在線成員）、`cursor`（游標位置）、`content_updated`（內容已更新，附 version）、`lock_changed`（編輯權變更）。
- FR-52 [P1]：收到 `content_updated` 且版本較新時，唯讀端自動拉取最新內容。

### 3.7 統計數據
- FR-60 [P0]：提供書籍層級統計：全書總字數、章節數、已完成章節數、完成進度（%）、各作者貢獻字數與佔比。
- FR-61 [P0]：提供章節層級統計：字數（中文以字元計、英文以單字計）、段落數、預估閱讀時間（中文 300 字/分、英文 200 字/分）、最近編輯時間。
- FR-62 [P0]：支援設定書籍「字數目標」，回傳達成率。
- FR-63 [P0]：「今日新增字數」依當日版本快照差值估算（個人維度）。

### 3.8 版本歷史
- FR-70 [P1]：每次內容儲存自動建立版本快照（含 `editor_id`、`word_count`、`created_at`）。
- FR-71 [P1]：可列出某章節的版本清單（分頁）。
- FR-72 [P1]：可取得單一版本內容並還原（還原會建立一筆新版本，不覆蓋歷史）。
- FR-73 [P1]：為控制體積，同章節版本超過 100 筆時保留最近 100 筆 + 每日首筆。

### 3.9 多媒體管理
- FR-80 [P1]：Owner/Editor 可上傳檔案（圖片／影片／音訊／文件）至書籍素材庫；也可貼外部連結（如 YouTube）。
- FR-81 [P1]：支援格式：圖片 jpg/png/gif/webp/svg；影片 mp4/webm；音訊 mp3/wav；文件 pdf/docx。
- FR-82 [P1]：單檔上限 50MB（可設定），書籍總配額 1GB（可設定）；超限回 413。
- FR-83 [P1]：素材庫可列出、依類型篩選、搜尋檔名；回傳檔名、大小、類型、上傳者、引用次數。
- FR-84 [P1]：刪除素材前若 `ref_count > 0` 須二次確認（前端），後端允許刪除但記錄。

---

## 4. 資料模型

> 資料庫：SQLite。主鍵採用 `INTEGER PRIMARY KEY AUTOINCREMENT`（或 UUID 字串，本 Spec 用整數）。時間欄位以 ISO8601 文字（UTC）儲存。富文本內容以 `TEXT`（JSON 字串）儲存。

### 4.1 ER 概念
```
User 1───* BookMember *───1 Book
Book 1───* Chapter (self-ref parent_id)
Chapter 1───1 ChapterContent
Chapter 1───* ContentVersion
Book 1───* MediaAsset
Book 1───* Invitation
```

### 4.2 資料表定義

#### users
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| email | TEXT | UNIQUE, NOT NULL | 登入帳號 |
| password_hash | TEXT | NOT NULL | bcrypt |
| name | TEXT | NOT NULL | 顯示名稱 |
| avatar_url | TEXT | NULL | 頭像 |
| created_at | TEXT | NOT NULL | |

#### books
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| title | TEXT | NOT NULL | 書名 |
| description | TEXT | NULL | 簡介 |
| cover_url | TEXT | NULL | 封面 |
| status | TEXT | NOT NULL, default 'draft' | draft/writing/completed/archived |
| tags | TEXT | NULL | JSON 陣列字串 |
| word_count_goal | INTEGER | NULL | 字數目標 |
| owner_id | INTEGER | FK→users.id | |
| created_at | TEXT | NOT NULL | |
| updated_at | TEXT | NOT NULL | |
| deleted_at | TEXT | NULL | 軟刪除標記 |

#### book_members
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| book_id | INTEGER | FK→books.id | |
| user_id | INTEGER | FK→users.id | |
| role | TEXT | NOT NULL | owner/editor/reviewer/viewer |
| created_at | TEXT | NOT NULL | |
| | | UNIQUE(book_id, user_id) | 一書一角色 |

#### invitations
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| book_id | INTEGER | FK→books.id | |
| email | TEXT | NOT NULL | 受邀者 |
| role | TEXT | NOT NULL | 預定角色 |
| status | TEXT | NOT NULL, default 'pending' | pending/accepted/revoked |
| token | TEXT | UNIQUE | 邀請連結 token |
| created_at | TEXT | NOT NULL | |

#### chapters
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| book_id | INTEGER | FK→books.id | |
| parent_id | INTEGER | FK→chapters.id, NULL | 章=null、節=章id |
| title | TEXT | NOT NULL | 章節名稱 |
| order_index | INTEGER | NOT NULL | 同層排序 |
| status | TEXT | NOT NULL, default 'not_started' | not_started/writing/reviewing/done |
| created_at | TEXT | NOT NULL | |
| updated_at | TEXT | NOT NULL | |
| deleted_at | TEXT | NULL | 軟刪除 |

#### chapter_contents
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| chapter_id | INTEGER | FK→chapters.id, UNIQUE | 一章節一份 |
| content_json | TEXT | NOT NULL, default '{}' | 富文本 JSON |
| word_count | INTEGER | NOT NULL, default 0 | 快取字數 |
| version | INTEGER | NOT NULL, default 1 | 版本號 |
| updated_by | INTEGER | FK→users.id | 最後編輯者 |
| updated_at | TEXT | NOT NULL | |

#### content_versions
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| chapter_id | INTEGER | FK→chapters.id | |
| version | INTEGER | NOT NULL | 對應版本號 |
| content_json | TEXT | NOT NULL | 該版本快照 |
| word_count | INTEGER | NOT NULL | |
| editor_id | INTEGER | FK→users.id | |
| created_at | TEXT | NOT NULL | |

#### media_assets
| 欄位 | 型別 | 限制 | 說明 |
|------|------|------|------|
| id | INTEGER | PK | |
| book_id | INTEGER | FK→books.id | |
| type | TEXT | NOT NULL | image/video/audio/file/link |
| url | TEXT | NOT NULL | 檔案路徑或外部連結 |
| filename | TEXT | NULL | 原始檔名 |
| mime_type | TEXT | NULL | |
| size_bytes | INTEGER | NULL | |
| ref_count | INTEGER | NOT NULL, default 0 | 引用次數 |
| uploaded_by | INTEGER | FK→users.id | |
| created_at | TEXT | NOT NULL | |

### 4.3 字數計算規則
- 中文：以 CJK 字元數計算（正則 `一-鿿` 等）。
- 英文／數字：以空白切分的 token 數計算。
- 富文本 JSON 先抽取純文字再計數；空白與標點不計入字數（標點可選）。

---

## 5. 介面／API 定義

> Base URL：`/api`。除註冊／登入外皆需 `Authorization: Bearer <JWT>`。
> 成功回應統一 `{ "data": ... }`；錯誤回應統一 `{ "error": { "code": "...", "message": "..." } }`。

### 5.1 通用錯誤碼
| HTTP | code | 說明 |
|------|------|------|
| 400 | BAD_REQUEST | 參數驗證失敗 |
| 401 | UNAUTHORIZED | 未登入／token 無效 |
| 403 | FORBIDDEN | 權限不足 |
| 404 | NOT_FOUND | 資源不存在或已軟刪除 |
| 409 | CONFLICT | 衝突（如 Email 已註冊） |
| 413 | PAYLOAD_TOO_LARGE | 檔案超限 |
| 423 | LOCKED | 章節編輯權被他人持有 |
| 500 | INTERNAL_ERROR | 伺服器錯誤 |

### 5.2 認證
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| POST | /auth/register | `{email, password, name}` | `{user, token}` | 409 Email 已存在；400 格式 |
| POST | /auth/login | `{email, password}` | `{user, token}` | 401 帳密錯誤 |
| GET | /auth/me | — | `{user}` | 401 |

### 5.3 書籍
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /books | query: `search, sort, status, page` | `{items, total}` | 401 |
| POST | /books | `{title, description?, tags?}` | `{book}` | 400 |
| GET | /books/{id} | — | `{book, my_role}` | 403/404 |
| PATCH | /books/{id} | `{title?, description?, cover_url?, status?, tags?, word_count_goal?}` | `{book}` | 403/404 |
| DELETE | /books/{id} | — | `{success:true}` | 403（非 Owner）/404 |
| POST | /books/{id}/restore | — | `{book}` | 403/404/410（逾期） |

`GET /books` 範例輸出：
```json
{ "data": { "items": [ { "id": 1, "title": "我的書", "status": "writing",
  "word_count": 12000, "progress": 0.4, "updated_at": "2026-06-14T03:00:00Z" } ],
  "total": 1 } }
```

### 5.4 成員與邀請
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /books/{id}/members | — | `{members}` | 403/404 |
| POST | /books/{id}/members | `{email, role}` | `{invitation}` | 403/400/409 |
| PATCH | /books/{id}/members/{userId} | `{role}` | `{member}` | 403（非 Owner） |
| DELETE | /books/{id}/members/{userId} | — | `{success:true}` | 403 |
| POST | /invitations/accept | `{token}` | `{book_id}` | 404/409 |

### 5.5 章節
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /books/{id}/chapters | — | `{chapters}`（樹狀） | 403/404 |
| POST | /books/{id}/chapters | `{title, parent_id?}` | `{chapter}` | 403/400 |
| PATCH | /chapters/{id} | `{title?, status?}` | `{chapter}` | 403/404 |
| PATCH | /books/{id}/chapters/reorder | `[{id, parent_id, order_index}]` | `{success:true}` | 403/400（超過兩層） |
| DELETE | /chapters/{id} | — | `{success:true}` | 403/404 |

### 5.6 章節內容
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /chapters/{id}/content | — | `{content_json, version, word_count, updated_at}` | 403/404 |
| PATCH | /chapters/{id}/content | `{content_json, base_version}` | `{version, word_count, updated_at}` | 403/404/409（版本衝突）/423（鎖定） |
| POST | /chapters/{id}/lock | — | `{lock_owner, expires_at}` | 423（他人持有） |
| DELETE | /chapters/{id}/lock | — | `{success:true}` | 403 |

`PATCH content` 規則：若 `base_version != 目前 version` 回 409，前端需先拉取最新再重試。

### 5.7 版本歷史
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /chapters/{id}/versions | query: `page` | `{items, total}` | 403/404 |
| GET | /chapters/{id}/versions/{ver} | — | `{content_json, editor, created_at}` | 404 |
| POST | /chapters/{id}/versions/{ver}/restore | — | `{version}` | 403/404 |

### 5.8 統計
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /books/{id}/stats | — | 見下 | 403/404 |
| GET | /chapters/{id}/stats | — | `{word_count, paragraph_count, reading_minutes, updated_at}` | 403/404 |

`GET /books/{id}/stats` 範例：
```json
{ "data": {
  "total_words": 45000, "chapter_count": 12, "completed_count": 5,
  "progress": 0.42, "goal": 100000, "goal_rate": 0.45,
  "today_words": 1200,
  "contributors": [ {"user_id":1,"name":"Amy","words":30000,"ratio":0.67} ]
} }
```

### 5.9 多媒體
| 方法 | 路徑 | 輸入 | 輸出 | 錯誤 |
|------|------|------|------|------|
| GET | /books/{id}/media | query: `type, search` | `{items}` | 403/404 |
| POST | /books/{id}/media | multipart：`file` 或 `{url, type}` | `{asset}` | 403/413（超限）/400（格式） |
| DELETE | /media/{assetId} | — | `{success:true}` | 403/404 |

### 5.10 WebSocket
- 連線：`WS /ws/chapters/{id}?token=<JWT>`
- Client → Server：`{type:"cursor", position}`、`{type:"ping"}`
- Server → Client：`{type:"presence", users:[...]}`、`{type:"cursor", user, position}`、`{type:"content_updated", version}`、`{type:"lock_changed", lock_owner}`
- 連線驗證失敗：關閉碼 4401；無權限：4403。

---

## 6. 流程與邊界條件

### 6.1 建立書籍（正常流程）
1. 使用者送 `POST /books`。
2. 後端建立 book（status=draft），同時建立一筆 `book_members`（role=owner）。
3. 回傳 book，前端導向編輯畫面。
- 邊界：`title` 空白 → 400。

### 6.2 章節內容自動儲存（含衝突）
1. 使用者編輯 → 前端 debounce 2s → `PATCH content`（帶 `base_version`）。
2. 後端比對版本：相同 → 更新內容、`version+1`、寫版本快照、更新字數、回新 version。
3. 前端顯示「已儲存」。
- 邊界（版本衝突）：`base_version` 落後 → 409；前端 `GET content` 取得最新並提示使用者（v1.0 以最後寫入者為準 + 提示，v2.0 改 CRDT 合併）。
- 邊界（鎖定）：非編輯權持有者 PATCH → 423。
- 邊界（離線）：前端暫存於 localStorage，重連後比對版本再送出。

### 6.3 章節拖曳排序
1. 前端組出受影響章節的 `[{id, parent_id, order_index}]`。
2. `PATCH /chapters/reorder` 於單一交易內更新。
- 邊界：使「節」變成第三層（parent 也有 parent）→ 400 拒絕。
- 邊界：跨書籍移動 → 403/400。

### 6.4 邀請成員
1. Owner `POST members`（email, role）。
2. Email 已是平台使用者 → 直接建立 `book_members`（或待接受，視設定）。
3. 未註冊 → 建立 `invitations`（pending）+ token；對方註冊／接受後轉 accepted 並建立成員。
- 邊界：邀請已是成員的人 → 409。
- 邊界：邀請自己 → 400。

### 6.5 多媒體上傳
1. `POST media`（multipart 或外部連結）。
2. 後端驗證副檔名／MIME、檔案大小、書籍配額。
3. 儲存至本地 `./storage/{book_id}/`（v1.0），寫入 `media_assets`。
- 邊界：格式不符 → 400；單檔超 50MB → 413；書籍總量超 1GB → 413。
- 邊界：外部連結（type=link）不佔配額，僅存 URL。

### 6.6 軟刪除與還原
- 刪除書籍：設 `deleted_at`，列表排除；30 天內可 `restore`。逾期 `restore` → 410。
- 刪除章節（章）：連同子節設 `deleted_at`。

### 6.7 權限檢核（橫切流程）
- 每個需授權的 endpoint：先查 `book_members` 取得角色，再對照權限矩陣；不符 → 403。
- 找不到書籍或已軟刪除 → 對非成員一律回 404（不洩漏存在性）。

---

## 7. 驗收標準

### 7.1 帳號與權限
- [ ] 可註冊、登入並取得 JWT；無 token 存取受保護 API 回 401。
- [ ] Viewer 嘗試編輯內容回 403；Editor 可編輯。
- [ ] 非 Owner 刪除書籍回 403。

### 7.2 書籍
- [ ] 建立書籍後建立者即為 Owner，並出現在其書架。
- [ ] 列表支援搜尋／排序／狀態篩選，且只顯示自己參與的書。
- [ ] 軟刪除後不出現在列表，30 天內可還原。

### 7.3 章節
- [ ] 可新增、重新命名、刪除章節。
- [ ] 可建立兩層結構；嘗試第三層被拒（400）。
- [ ] 拖曳排序後重新整理，順序持久化正確。
- [ ] 刪除「章」時其下「節」一併消失。

### 7.4 內容與協作
- [ ] 編輯停止 2 秒內自動儲存，UI 顯示「已儲存」。
- [ ] 版本衝突時回 409，前端能取得最新內容。
- [ ] 同章節非編輯權持有者送出內容回 423。
- [ ] 開啟同章節的兩個使用者可透過 WebSocket 看到彼此在線與游標。

### 7.5 統計
- [ ] 章節字數、全書字數、完成進度數值正確（含中英文混合）。
- [ ] 設定字數目標後達成率正確顯示。
- [ ] 各作者貢獻字數加總等於全書字數。

### 7.6 版本
- [ ] 每次儲存產生一筆版本快照。
- [ ] 可列出、檢視、還原版本；還原後產生新版本而非覆蓋。

### 7.7 多媒體
- [ ] 可上傳合法格式並出現在素材庫。
- [ ] 超大檔案／不支援格式被正確拒絕（413/400）。
- [ ] 可將素材插入章節，`ref_count` 正確增加。

### 7.8 整體
- [ ] 後端提供 OpenAPI 文件（FastAPI 自動產生 `/docs`）。
- [ ] 主要流程端到端可操作（建立書→新增章節→寫內容→看統計→上傳素材）。

---

## 8. 技術棧／限制

### 8.1 技術棧
| 層級 | 技術 |
|------|------|
| 前端 | React 18 + Vite、React Router、狀態管理（Zustand 或 Context）、富文本（TipTap / ProseMirror）、API（fetch/axios） |
| 後端 | Python 3.11+、FastAPI、Uvicorn、Pydantic v2 |
| ORM | SQLModel 或 SQLAlchemy 2.x |
| 資料庫 | SQLite（檔案 `app.db`，啟用 WAL 模式） |
| 即時 | FastAPI WebSocket（記憶體內 room 管理） |
| 認證 | JWT（python-jose）、密碼雜湊 bcrypt（passlib） |
| 檔案儲存 | 本地檔案系統 `./storage/`（v1.0） |
| 測試 | pytest（後端）、Vitest/RTL（前端） |

### 8.2 專案結構（建議）
```
book/
├─ backend/
│  ├─ app/
│  │  ├─ main.py          # FastAPI 入口、路由註冊
│  │  ├─ models.py        # SQLModel 資料表
│  │  ├─ schemas.py       # Pydantic I/O
│  │  ├─ auth.py          # JWT、密碼
│  │  ├─ deps.py          # 權限檢核依賴
│  │  ├─ routers/         # books/chapters/content/media/stats/ws
│  │  └─ services/        # 字數、版本、配額邏輯
│  ├─ tests/
│  └─ requirements.txt
└─ frontend/
   ├─ src/
   │  ├─ pages/           # 書架、編輯畫面
   │  ├─ components/      # 目錄樹、編輯器、統計面板、媒體面板
   │  ├─ api/             # API client
   │  └─ store/
   └─ package.json
```

### 8.3 限制與假設（v1.0）
- 採 SQLite，適用單機／中小團隊；高併發寫入有限制（WAL 緩解）。
- 即時協作為「章節軟鎖定 + 廣播」，非字元級 CRDT 共筆；完整共筆列為 v2.0。
- WebSocket room 狀態存於單一行程記憶體，不支援多實例水平擴展（v2.0 可改 Redis pub/sub）。
- 檔案存本地磁碟，未接物件儲存與 CDN；備份須另行處理。
- 不含 Email 實際寄送服務（邀請以 token 連結為主，寄信為選配）。
- 未實作 P2 評論／@提及。

### 8.4 非功能需求（對應 PRD §6）
- 效能：章節載入 < 1.5s；一般 API 回應 < 300ms（本地）。
- 安全：JWT 驗證、富文本內容前端渲染需消毒（XSS）、上傳檔案副檔名/MIME 雙重驗證。
- 相容性：支援 Chrome / Edge / Safari / Firefox 最新兩版。
- 國際化：UI 繁體中文優先，文案集中管理以利擴充。

---

## 9. 與 PRD 對照表
| PRD 功能 | Spec 對應 |
|----------|-----------|
| 3.2 書籍管理 | FR-10~15、§5.3、§6.1/6.6 |
| 3.3 成員權限 | FR-20~24、§5.4、§6.4/6.7 |
| 3.4 章節管理 | FR-30~35、§5.5、§6.3 |
| 3.5 內容／共筆 | FR-40~52、§5.6/5.10、§6.2 |
| 3.6 統計面板 | FR-60~63、§5.8 |
| 3.7 多媒體 | FR-80~84、§5.9、§6.5 |
| 3.8 版本歷史 | FR-70~73、§5.7 |
| 3.9 評論（P2） | 延後至 v2.0 |
