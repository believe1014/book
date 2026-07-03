# 協作撰書系統 (Collaborative Book Writing System)

一套多人協作撰書平台，以「書籍 → 章節 → 內容」三層結構組織寫作專案。
依 [spec.md](./spec.md) 與 [design.md](./design.md) 實作。

- 後端：Python 3.11+ / FastAPI / SQLModel / SQLite (WAL) / JWT / WebSocket
- 前端：React 18 + Vite / React Router / Zustand / 富文本編輯（contentEditable，保留 ProseMirror 風格 JSON 結構）

---

## 專案結構

```
book/
├─ backend/
│  ├─ app/
│  │  ├─ main.py          FastAPI 入口、路由與錯誤處理註冊
│  │  ├─ config.py        設定（JWT、儲存、配額、鎖閒置秒數）
│  │  ├─ database.py      SQLite 引擎（WAL + FK pragma）
│  │  ├─ models.py        SQLModel 資料表（spec §4 全部）
│  │  ├─ schemas.py       Pydantic v2 I/O
│  │  ├─ auth.py          bcrypt + JWT
│  │  ├─ deps.py          認證 + 權限矩陣依賴
│  │  ├─ errors.py        統一錯誤碼/封包（spec §5.1）
│  │  ├─ routers/         auth / books / chapters / content / media / ws
│  │  └─ services/        wordcount / stats / locks / ws_manager
│  ├─ tests/e2e_smoke.py  後端端到端煙霧測試
│  └─ requirements.txt
└─ frontend/
   ├─ src/
   │  ├─ pages/           AuthPage / Bookshelf / Editor / InviteLanding
   │  ├─ components/      ChapterTree / RichTextEditor / StatsPanel / MediaPanel /
   │  │                   MembersModal / BookSettings / VersionHistory / Modal / Toaster
   │  ├─ hooks/           useChapterSocket（WebSocket 協作）
   │  ├─ api/client.js    API client（封包解封 + ApiError）
   │  ├─ store/           auth / toast（Zustand）
   │  └─ styles.css       design tokens（design.md §10）
   ├─ e2e-ui.mjs          瀏覽器端 UI 測試（核心流程）
   ├─ e2e-ui2.mjs         瀏覽器端 UI 測試（媒體/版本/權限）
   └─ package.json
```

---

## 啟動方式（Windows / PowerShell）

### 1. 後端

```powershell
cd backend
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

- API base：`http://127.0.0.1:8000/api`
- OpenAPI 文件（FastAPI 自動產生）：`http://127.0.0.1:8000/docs`
- 資料庫檔案 `backend/app.db` 會在首次啟動時自動建立（WAL 模式）。

> **正式部署（Zeabur 等 PaaS）的資料持久化**
> 容器檔案系統是「用完即丟」，SQLite 檔會在每次重新部署時被清空（帳號、書稿全失）。
> 請改用託管資料庫：設定環境變數 `DATABASE_URL`（標準 PostgreSQL 連線字串，
> 例如 `postgresql://user:pass@host:5432/dbname`）即可。設了它就優先使用 PostgreSQL，
> 沒設則退回本機 SQLite。注意：上傳的媒體檔（`backend/storage/`）若要持久化，仍需另接
> 物件儲存或掛載磁碟。
- 上傳檔案存於 `backend/storage/{book_id}/`，並由 `/storage/...` 提供。

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

- 開發站台：`http://localhost:5173`
- Vite 會把 `/api`、`/storage`、`/ws` 代理到後端 `127.0.0.1:8000`（見 `vite.config.js`）。
- 兩個服務都啟動後，瀏覽器開 `http://localhost:5173` 即可使用。

---

## 部署安全設定

正式部署前，**務必**設定下列環境變數（皆以 `BOOK_` 為前綴）：

| 環境變數 | 必要性 | 說明 |
|----------|--------|------|
| `BOOK_JWT_SECRET` | **必設** | JWT 簽章密鑰，須為強隨機值、長度 **≥ 32** 字元。切勿沿用原始碼內的開發預設值。可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 產生。 |
| `BOOK_ENVIRONMENT` | 建議設 `production` | 標記生產環境，啟用啟動期安全 fail-fast 檢查。 |
| `BOOK_CORS_ORIGINS` | 選用 | 允許的前端來源清單（逗號分隔），例如 `https://your-domain.com`。 |

> **Fail-fast 保護（S2）**
> 啟動時 `security_checks.assert_secure_config` 會檢查 JWT 密鑰。若密鑰仍為預設值或
> 長度不足（< 32），且**偵測到生產環境**——即 `BOOK_ENVIRONMENT=production` **或**
> 已設定 `DATABASE_URL`（正式部署通常會掛託管 PostgreSQL）——服務會直接
> **拒絕啟動（RuntimeError）**，避免以不安全設定上線。
>
> 因此即使一時忘了設 `BOOK_ENVIRONMENT`，只要接上了正式資料庫，弱密鑰也會被擋下。
> 純本機 SQLite 開發（未設 `DATABASE_URL` 且非 production）則僅記錄警告、不阻擋。

登入端點內建暴力破解／撞庫限流（S3，5 分鐘滑動視窗）：同一 (IP, Email) 失敗達門檻，
或單一 IP 對大量帳號的總失敗達較高門檻，皆會回 `429`。限流狀態存於單一行程記憶體，
多副本部署時各副本各自計數（如需跨副本一致，應改接 Redis 等共享後端）。

---

## MCP server（讓 AI 助理操作你的書）

後端內建一個 **MCP server**（遠端 streamable HTTP），隨 app 一起部署，端點為：

```
http://127.0.0.1:8000/mcp/        # 本機
https://<你的網域>/mcp/            # 部署後（注意結尾的斜線）
```

- **認證**：與網頁版共用 JWT。MCP client 需在 `Authorization` 標頭帶入 `Bearer <token>`，
  token 由 `POST /api/auth/login` 取得。權限沿用書籍角色矩陣（owner/editor 可寫，其餘唯讀）。
- **提供的工具**：`list_books`、`create_book`、`get_book`、`create_chapter`、`rename_chapter`、
  `set_chapter_status`、`delete_chapter`、`get_chapter_content`、`update_chapter_content`。
  章節內容以純文字讀寫（換行＝段落），寫入會自動 +1 版本並建立版本快照。
- 在 claude.ai / Claude Desktop 新增自訂連接器時，填上 `/mcp/` 結尾的網址並設定上述 Bearer 標頭即可。

---

## 測試

後端煙霧測試（需先啟動後端 server）：

```powershell
cd backend
.\venv\Scripts\python.exe tests\e2e_smoke.py
```

瀏覽器端 UI 測試（需先啟動前端 + 後端；使用系統 Edge 驅動）：

```powershell
cd frontend
node e2e-ui.mjs    # 核心流程：註冊→建書→建章→寫內容→自動儲存→統計→重整持久化
node e2e-ui2.mjs   # 媒體上傳 / 版本歷史 / 成員邀請 / Viewer 唯讀
```

---

## 已實作功能對應 spec

| 範圍 | 功能 | FR |
|------|------|----|
| 帳號 | 註冊 / 登入 / JWT / me；保護路由 401 | FR-01~03 |
| 書籍 | CRUD、搜尋/排序/狀態篩選、軟刪除/還原（30 天）、回收桶 | FR-10~15 |
| 成員 | Email 邀請（含 pending 自動加入）、角色調整/移除、權限矩陣 | FR-20~24 |
| 章節 | CRUD、改名、拖曳排序、兩層限制、連動軟刪 | FR-30~35 |
| 內容 | 富文本 JSON、自動儲存（debounce 2s）、版本+1、版本快照、版本衝突 409 | FR-40~43 |
| 協作 | 章節軟鎖定（423）、閒置/斷線釋放、WebSocket presence/cursor/content_updated/lock_changed | FR-44~52 |
| 統計 | 書籍/章節層級、目標達成率、今日新增、貢獻者佔比、中英混合字數 | FR-60~63 |
| 版本 | 列出/預覽/還原（還原建立新版本）、保留策略 | FR-70~73 |
| 多媒體 | 上傳/外部連結、格式/大小/配額驗證、篩選/搜尋、插入、ref_count | FR-80~84 |

字數規則（spec §4.3）：中文按 CJK 字元、英文/數字按空白 token，混合內容正確計數。

---

## 已知限制與設計取捨（對齊 spec §8.3 / design §13）

- 即時協作為「章節軟鎖定 + WebSocket 廣播」，非字元級 CRDT 共筆（v2.0 演進）。
- WebSocket room 與鎖狀態存於單一行程記憶體，不支援多實例水平擴展。
- 富文本採 contentEditable + 工具列（spec 允許的等價方案），儲存為 ProseMirror 風格 JSON；
  進階節點（表格等）未完整支援。
- 衝突解決為「最後寫入者為準 + 提示並載入最新」，未做三方合併 / diff 視圖。
- 邀請不實際寄信，以「複製邀請連結」交付（spec §8.3）。
- P2 段落評論 / @提及未實作（spec 延後）。
