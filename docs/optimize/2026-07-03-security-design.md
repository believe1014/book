# book 系統優化・子專案 2：安全加固 — 設計文件

- 日期：2026-07-03
- 分支：optimize-security（自 main，含子專案1 的 225 測試護欄）
- 前置：子專案1 已建立 225 個整合測試（回歸網）。安全修復以測試鎖定「修復後」行為。
- 環境警示：工具讀取間歇遭注入污染，一律以 git show/pytest/git 為準。

## 範圍（依嚴重度）

### S1 [高] 邀請 token 劫持 — accept 未驗證受邀信箱

`routers/books.py::accept_invitation`：以 token 查到 pending 邀請後，直接用**當前登入者**的 id 建立 `BookMember`，未檢查 `inv.email == user.email`。任何已登入者取得外流 token 即可冒名加入書籍。
- **修**：accept 時比對 `inv.email.lower() == user.email.lower()`，不符回 `errors.forbidden("此邀請並非發給您的帳號")`（403）。保留「受邀信箱本人接受」的設計意圖。
- **測試**：改寫既有鎖定漏洞的 `test_books.py::test_accept_invitation_by_logged_in_holder_of_token` → 斷言 email 不符者得 403、且**未**建立成員；新增 email 相符者可正常接受（200 + 成員建立）。

### S2 [中] JWT 預設密鑰生產防護

`config.py::jwt_secret` 預設 `"dev-secret-change-me-in-production-please"`；Dockerfile 未設 → 生產若未經 env 覆寫，token 可被任何知道預設值者偽造（預設值在原始碼公開）。
- **修**：新增設定 `environment: str = "development"`（env `BOOK_ENVIRONMENT`）。於 `main.py` lifespan 啟動時：若 `environment == "production"` 且 `jwt_secret` 仍為預設值（或長度 < 32）→ `raise RuntimeError`（拒絕啟動，fail-fast）；非 production 僅 `logger.warning`。
- **文件**：DEPLOY/README 註明生產須設 `BOOK_JWT_SECRET`（強隨機 ≥32）與 `BOOK_ENVIRONMENT=production`。
- **測試**：以 monkeypatch 設 environment=production + 預設 secret，呼叫該檢查函式應 raise；設強 secret 則通過；development + 預設僅警告不 raise。（把檢查抽成可單元測試的函式 `assert_secure_config(settings)`。）

### S3 [中] 登入暴力破解限流

`/api/auth/login` 無任何限流，可無限嘗試密碼。
- **修**：加**行程內**輕量滑動視窗限流（dependency-free，keyed by client IP + email），例如每 IP+email 每 5 分鐘上限 10 次失敗，超過回 `errors.too_many_requests`（新增 429 helper）。成功登入清除計數。註明多副本部署時為 per-process（如需跨副本用 Redis，屬後續）。
- **測試**：連續失敗達上限後回 429；成功登入重置；不同 email/IP 各自獨立計數。

### S4 [低] 註冊密碼強度

`RegisterIn` 未限制密碼長度（bcrypt 只截 72）。
- **修**：`schemas.py::RegisterIn` 的 password 加 `min_length=8`（pydantic）。回應 422/400（既有驗證處理器）。
- **測試**：< 8 字密碼註冊被拒；≥8 通過。（既有 test_auth 用 pw-123456＝9 字，不受影響。）

### S5 [低] 安全回應標頭

單容器同時服務 SPA，缺少常見安全標頭。
- **修**：加輕量 middleware 設 `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: same-origin`（不設過度嚴格 CSP 以免破壞現有前端；CSP 留待子專案4/後續評估）。CORS 維持 env 可設（生產經 `BOOK_CORS_ORIGINS` 設正式網域）。
- **測試**：任一回應含上述三個標頭。

## 非目標（本子專案不做）
- CSP（需盤點前端 inline/資源，留子專案4或後續）
- 跨副本限流（Redis）
- MCP 端點深度稽核（掛載於 /mcp，已 Bearer-JWT；如需另立子任務）

## 測試策略
- 全程 `cd backend && venv/Scripts/python.exe -m pytest`，基線 225 綠不得回歸。
- 每項新增/改寫測試鎖定「修復後」行為；S1 改寫既有漏洞鎖定測試。
- 安全檢查邏輯（S2 assert_secure_config、S3 限流器）抽成可單元測試的純函式/類別。

## 部署影響
- 生產需新增 env：`BOOK_JWT_SECRET`（強隨機≥32）、`BOOK_ENVIRONMENT=production`、（選）`BOOK_CORS_ORIGINS`。README/DEPLOY 更新。
- 無 DB migration。
