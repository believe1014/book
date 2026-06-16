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
