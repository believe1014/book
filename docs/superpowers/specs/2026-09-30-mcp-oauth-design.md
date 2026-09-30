# kkbook MCP OAuth 設計(比照 govmeet)

- 日期:2026-09-30
- 狀態:設計已核准(2026-09-30);§15 四項已由使用者決定(全採建議),實作計畫見 `docs/superpowers/plans/2026-09-30-mcp-oauth.md`
- 範圍:只含設計;不改程式、不部署

## 1. 目標與不做的事

**目標**:使用者在 Claude Code `/mcp` 按下 kkbook 的授權 → 瀏覽器開 book.believe.center → (未登入就先登入)→ 按同意 → 回到 Claude Code 即可用,不用再手動貼 `Authorization` token。

**不做**:
- 不接 kklab.me 站群單一登入(kkbook 帳號系統獨立,govmeet 的 `kk_home` cookie 那條路不適用)。
- 不做 scope 細分(一個 token 等同該使用者全部權限,和現在的 PAT 一樣;權限仍由 book_members 角色矩陣管)。
- 不做「已授權應用程式」管理頁(govmeet 也沒有);撤銷先靠 RFC 7009 `/revoke` 與 DB。需要時再加。
- 不移除 JWT / PAT:三種 token 並存。
- 不做 client 過期清理、不做 JWKS / JWT 型 access token(opaque 就夠)。

## 2. govmeet 怎麼做(參照基準)

| 項目 | govmeet 做法 | 位置 |
|---|---|---|
| AS metadata | `/.well-known/oauth-authorization-server`,issuer 由 Host header 動態組;只支援 `code`、`authorization_code`+`refresh_token`、`S256`、`token_endpoint_auth_methods=['none']` | `lab/meet/src/oauth/routes.js:15-27` |
| PRM | `/.well-known/oauth-protected-resource` → `{resource: ${base}/mcp, authorization_servers: [base]}` | `routes.js:29-35` |
| DCR | `POST /oauth/register`,限流 10 次/小時;`client_name` ≤100 字、`redirect_uris` 只收 `https://` 或 `http://localhost|127.0.0.1`;一律 public client | `routes.js:37-40`、`service.js:11-19` |
| authorize | 驗 client_id + redirect_uri 精確比對(失敗顯示錯誤頁、**絕不轉導**);`response_type=code`、`code_challenge_method=S256` 必填;未登入 → 302 到 `/meet/login?next=<原 authorize URL>`;已登入 → 後端直接渲染同意頁(`script-src 'none'`、`form-action` 只多放行該 redirect origin) | `routes.js:42-71` |
| decision | `POST /oauth/authorize/decision`(需登入),hidden 欄位重驗 client/redirect_uri,產 code 帶 state 轉回 | `routes.js:73-83` |
| token | `POST /oauth/token` 限流 30 次/分;失敗一律回 `400 {error:"invalid_grant"}` 並記 log | `routes.js:85-99` |
| code | 32 bytes 隨機、只存 sha256、效期 10 分鐘、`DELETE ... RETURNING` 單次使用、比對 client_id/redirect_uri/PKCE | `service.js:6,26-37,49-58` |
| token 格式 | opaque(32 bytes base64url),DB 只存 sha256;access 30 天(env `MCP_TOKEN_TTL_DAYS`)、refresh 90 天 | `service.js:4-8,39-47` |
| refresh | 同一列原地輪替(新 access + 新 refresh,舊 refresh 立即失效),檢查 `revoked_at IS NULL` 與 refresh 未過期 | `service.js:60-72` |
| 資料表 | `oauth_clients` / `oauth_codes` / `oauth_tokens`(含 `revoked_at`、`last_used_at`) | `lab/meet/migrations/033_oauth.sql` |
| 撤銷 | 只有 `revoked_at` 欄位,**沒有** revoke 端點或 UI | grep 全 `src/` 無 oauth_tokens 撤銷程式 |
| 401 | `/mcp` 驗 Bearer 失敗 → `401` + `WWW-Authenticate: Bearer resource_metadata="${base}/.well-known/oauth-protected-resource"` | `lab/meet/src/mcp/http.js:9-21` |
| /mcp 接受的 token | **只收 OAuth token**(`resolveAccessMember`),舊 `mcp_tokens` 不走 /mcp | `http.js:13` |
| 與站群登入 | `requireAuth` 接受自家 `gm_session` 或 kklab.me/org 發的 HMAC cookie `kk_home`,所以在 kklab.me 登入過,authorize 就直接到同意頁 | `lab/meet/src/plugins/auth.js:52-75`、`src/auth/kklab-session.js` |
| 客戶端設定 | mcp.yaml 只寫 `type: http` + `url: https://kklab.me/mcp`,無 header,只給 claude-cli | `dotfiles-ai/sources/mcp.yaml:48-52` |

## 3. kkbook 現況(要改的點)

- `backend/app/deps.py:41-56` `user_from_token`:`kkb_` 開頭查 PAT,否則當 JWT 解。
- `deps.py:76-83` `get_current_user_jwt` 只擋 `kkb_`;**新增 OAuth token 後若不改,OAuth token 會被當 JWT 以外的「非 PAT」放行去建 PAT**(雖然 decode 會失敗回 401,但規則要明寫成「只收 JWT」)。
- `backend/app/mcp_server.py:176-187` FastMCP `stateless_http=True, json_response=True`;`mcp_server.py:193-201` `_current_user` 在**工具內**丟 `ToolError`。→ 未帶/帶錯 token 時 HTTP 仍回 200,Claude Code 永遠看不到 401,**不會觸發 OAuth**。這是本案核心要改的地方。
- `backend/app/main.py:72` `app.mount("/mcp", mcp_server.streamable_http_app())`;`main.py:97-108` SPA catch-all `GET /{full_path:path}`(新的根層路由必須註冊在它之前)。
- `backend/app/models.py:137-149` `PersonalAccessToken`(無 refresh / client 欄位)。
- `backend/app/database.py:78-83` 用 `create_all`,**不會 ALTER 既有表**(已有一個手寫 ALTER 的前例)。
- `backend/app/auth.py:25-28` 網頁 JWT 24h;`frontend/src/api/client.js:7-11` JWT 存 **localStorage**(非 cookie)。
- `frontend/src/App.jsx:11-17` `RequireAuth` 會帶 `state.from` 導到 `/login`,但 `frontend/src/pages/AuthPage.jsx:41` 登入後固定 `navigate('/')`,**不回原頁**。
- MCP Python SDK:`backend/requirements.txt` 釘 `mcp==1.27.2`,venv 已裝同版。SDK 內建:
  - AS 路由 `mcp/server/auth/routes.py:69-147` `create_auth_routes()`(metadata、`/authorize`、`/token`、`/register`、`/revoke`;路徑固定於 `:50-53`);
  - PRM `routes.py:209-252` `create_protected_resource_routes()`,路徑規則 `/.well-known/oauth-protected-resource<resource path>`(`:190-206`);
  - RS 端 `fastmcp/server.py:974-1036`:給 `auth=AuthSettings(...)` + `token_verifier` 時,用 `RequireAuthMiddleware` 包住 MCP 路由,未授權回 401 + `WWW-Authenticate: Bearer error=..., resource_metadata="..."`(`middleware/bearer_auth.py:123-142`);
  - authorize 參數模型 `code_challenge` 必填、method 限 `S256`(`handlers/authorize.py:31-32`);redirect_uri 精確比對(`mcp/shared/auth.py:102-111`)。

## 4. 採用方案

**用 SDK 現成元件,自己只寫一個 DB-backed provider + 一個同意頁。**

1. **Authorization Server**:實作 `OAuthAuthorizationServerProvider`(新檔 `backend/app/oauth.py`),呼叫 SDK 的 `create_auth_routes(provider, issuer_url=BOOK_PUBLIC_BASE_URL, client_registration_options=ClientRegistrationOptions(enabled=True), revocation_options=RevocationOptions(enabled=True))`,把回傳的 Starlette routes **掛在主 app 根層**(插在 SPA catch-all 之前)。
   - 不用 `FastMCP(auth_server_provider=...)` 的理由:那會把 AS 路由放進掛在 `/mcp` 的子 app,實際網址變成 `/mcp/.well-known/...`,違反 RFC 8414 根層 discovery。
2. **Resource Server**:`FastMCP(..., auth=AuthSettings(issuer_url=BASE, resource_server_url=f"{BASE}/mcp/"), token_verifier=KkbookTokenVerifier())`。SDK 自動在 `/mcp/` 前面加 `RequireAuthMiddleware` → 未授權回 401 + `WWW-Authenticate`。
   - `KkbookTokenVerifier.verify_token(token)` 直接呼叫既有 `user_from_token`,有使用者就回 `AccessToken(token=token, client_id=..., scopes=[])`。所以 **JWT、PAT、OAuth 三者都通過同一個 verifier**。
   - `_current_user` 不動(它仍從 header 取 token 再走 `user_from_token`),工具程式零修改。
   - PRM 路由由我們在根層自行用 `create_protected_resource_routes()` 註冊(子 app 內的那份同樣會落在 `/mcp/` 底下,不可靠),並額外註冊裸 `/.well-known/oauth-protected-resource` 供只查根層的客戶端。
3. **授權/同意頁放前端 SPA**(與 govmeet 不同,理由見 §6.3)。

## 5. 端點清單與行為

| 端點 | 來源 | 行為 |
|---|---|---|
| `GET /.well-known/oauth-authorization-server` | SDK | issuer=`BOOK_PUBLIC_BASE_URL`;`authorization_endpoint=/authorize`、`token_endpoint=/token`、`registration_endpoint=/register`、`revocation_endpoint=/revoke`;`code_challenge_methods_supported=["S256"]` |
| `GET /.well-known/oauth-protected-resource/mcp/` 與 `/.well-known/oauth-protected-resource` | SDK 函式,我們註冊 | `{resource: "<BASE>/mcp/", authorization_servers: ["<BASE>"]}` |
| `POST /register` | SDK handler + provider.register_client | RFC 7591。provider 內加限制:`redirect_uris` 全為 `https://` 或 `http://localhost|127.0.0.1`(同 govmeet regex)、`client_name` ≤100 字、每 IP 10 次/小時(見 §8)。違反 → `RegistrationError("invalid_redirect_uri")` |
| `GET /authorize` | SDK handler + provider.authorize | SDK 已驗 client、redirect_uri 精確比對(不合法不轉導)、PKCE S256 必填。provider.authorize 把請求參數(client_id、redirect_uri、redirect_uri_provided_explicitly、code_challenge、state、scopes、resource)用 `jose.jwt` 以 `BOOK_JWT_SECRET` 簽成 10 分鐘的 `req` 字串(`typ="oauth_req"`),回傳 `<BASE>/oauth/consent?req=<req>` 讓瀏覽器 302 過去 |
| `GET /oauth/consent`(前端路由) | SPA | `RequireAuth` 包住:未登入 → `/login`(登入後回本頁,見 §7);已登入 → 顯示同意卡 |
| `GET /api/oauth/consent?req=` | 新 API,需 JWT | 驗 `req` 簽章/效期/typ;回 `{client_name, redirect_origin}` 供同意頁顯示 |
| `POST /api/oauth/consent` `{req, approve}` | 新 API,**只收網頁 JWT**(`get_current_user_jwt`) | 重驗 `req`;再查一次 client 與 redirect_uri(防 client 已被刪);approve → 建 code(存 hash),回 `{redirect_url: redirect_uri?code=..&state=..}`;deny → `{redirect_url: redirect_uri?error=access_denied&state=..}`。前端 `window.location.assign(redirect_url)` |
| `POST /token` | SDK handler + provider | `authorization_code`:SDK 驗 code 效期、redirect_uri、PKCE,provider 單次取出(刪除)後發 token;`refresh_token`:原地輪替(同 govmeet) |
| `POST /revoke` | SDK handler + provider.revoke_token | RFC 7009;設 `revoked_at` |
| `POST /mcp/`(與 GET/DELETE) | FastMCP | 前置 `RequireAuthMiddleware`:無效 → `401` + `WWW-Authenticate: Bearer error="invalid_token", ..., resource_metadata="<BASE>/.well-known/oauth-protected-resource/mcp/"` |

`/authorize`、`/token`、`/register`、`/revoke` 在根層。SPA 也有 `GET /register` 前端頁,但 SDK 的 `/register` 只宣告 `POST/OPTIONS`:Starlette 對 method 不符只算 partial match,會繼續找到後面的 SPA catch-all。**已用 venv 實測**(FastAPI 0.115.6 / Starlette 0.41.3):`GET /register` → SPA、`POST /register` → SDK,不衝突。SDK 路由仍須插在 SPA catch-all 之前(否則 `GET /authorize` 會被 SPA 吃掉)。

## 6. 資料表

### 6.1 新表(不沿用 PersonalAccessToken)

```text
oauth_clients : client_id PK(text) | client_info(text, OAuthClientInformationFull 的 JSON) | created_at
oauth_codes   : code_hash PK | client_id FK | user_id FK | redirect_uri | redirect_uri_provided_explicitly(bool)
                | code_challenge | scopes(text) | expires_at | created_at
oauth_tokens  : id PK | token_hash UNIQUE | refresh_hash UNIQUE | client_id FK | user_id FK(index)
                | expires_at | refresh_expires_at | revoked_at | created_at | last_used_at
```

用 SQLModel 定義,`create_all` 啟動時自動建,**零遷移程式**。時間欄位沿用專案慣例的 ISO 字串(`utcnow()`)。

### 6.2 為什麼不擴充 PersonalAccessToken

- PAT 沒有 refresh_hash / refresh_expires_at / client_id;要加欄位就得寫 ALTER(`create_all` 不改既有表),SQLite + Postgres 各要測。
- `/api/tokens` 清單語意是「使用者手動建的 token」,混入 OAuth 自動發的會讓清單膨脹(每次重新授權多一列)。
- govmeet 也是分開三表,照抄結構風險最低。

### 6.3 token 格式

- access:`kko_` + `secrets.token_urlsafe(32)`;refresh:`kkr_` + `secrets.token_urlsafe(32)`;code:`secrets.token_urlsafe(32)`。DB 只存 sha256(沿用 `deps.hash_pat`)。
- **與 govmeet 不同**:govmeet 無前綴。kkbook 的 `user_from_token` 靠前綴分流 JWT / PAT,所以 OAuth token 也要前綴,才不會落到 JWT decode。
- `user_from_token` 新增分支:`kko_` → 查 `oauth_tokens`(未撤銷、未過期)→ 更新 `last_used_at` → 回 User。
- `get_current_user_jwt` 改成「只收 JWT」:任何 `kkb_` / `kko_` / `kkr_` 前綴都拒絕(不能用 OAuth token 建 PAT 或核准新授權)。

## 7. 授權頁 UX

**與 govmeet 不同:同意頁放前端 SPA,不由後端渲染。** 理由:govmeet 的網頁登入是 cookie,後端在 `/oauth/authorize` 就知道誰登入;kkbook 網頁登入是 localStorage 裡的 JWT,瀏覽器導到後端時**不會帶上**,後端無從判斷是否已登入。改成 cookie 牽動整個前端 auth,範圍過大。

流程:
1. Claude Code 開瀏覽器到 `/authorize?...` → SDK 驗完 → 302 到 `/oauth/consent?req=...`。
2. SPA `RequireAuth`:
   - **已登入**:直接顯示同意卡——「**Claude Code**(client_name)要求以你的身分使用 kkbook MCP 工具(列書、讀寫章節);授權後將轉往 `http://localhost:xxxx`」,按鈕「同意」「拒絕」。一次點擊完成。
   - **未登入**:導 `/login`,登入後**回到同意頁**。需小改 `AuthPage.jsx:41`:`navigate(location.state?.from ?? '/')`(只接受站內相對路徑,防 open redirect)。
3. 同意 → `POST /api/oauth/consent` → 前端整頁跳 `redirect_url` → Claude Code 收到 code → `/token` 換 token → 完成。
4. 錯誤態:`req` 過期(>10 分鐘)→ 顯示「授權請求已過期,請回 Claude Code 重新按授權」,不轉導。

「已登入則直接同意」解讀為**已登入時只需按一次同意**,不自動核准(使用者已決定,§15-2)。

## 8. 效期與限流

| 項目 | govmeet | kkbook(已決定) |
|---|---|---|
| authorization code | 10 分鐘、單次 | 同 |
| consent `req` | (無,參數放 hidden form) | 10 分鐘(JWT exp) |
| access token | 30 天(env 可調) | **30 天**,env `BOOK_OAUTH_ACCESS_TTL_DAYS` |
| refresh token | 90 天,原地輪替 | 同 |
| DCR 限流 | 10 次/小時 | 同,per-IP 記憶體計數 |
| token 限流 | 30 次/分 | 同 |

access 不採「短效(如 1 小時)」的理由:Claude Code 多個並行 session 共用同一份 OAuth 憑證,輪替式 refresh 在並發時會互相作廢(使用者已踩過 Claude Code 自身同類 bug,見 memory「Claude Code 反覆登入問題」)。access 長 → refresh 很少發生 → 競態機率低。這是有取捨的選擇,使用者已決定採 30 天 / 90 天(§15-1)。

限流:kkbook 已有 `backend/app/services/rate_limit.py`(`login_rate_limiter`,用於 `routers/auth.py:66-75`)。實作時先看它能否泛用;不能就寫最小的 per-IP 滑動窗(`# ponytail:` 單程序記憶體,多副本時改 DB/Redis)。SDK 的 handler 本身不限流,需在 provider 或以 Starlette middleware 包 `/register`、`/token`。

## 9. 與 JWT / PAT 並存

| token | 形式 | 可用於 | 可建 PAT / 核准 OAuth |
|---|---|---|---|
| 網頁 JWT | `eyJ...`,24h | REST + MCP | 是 |
| PAT | `kkb_...`,預設 365 天 | REST + MCP | 否 |
| OAuth access | `kko_...`,30 天 | REST + MCP | 否 |

**與 govmeet 不同**:govmeet `/mcp` 只收 OAuth token。kkbook 三者都收,理由:Claude Desktop / codex 若暫時保留 PAT header(§11),`/mcp` 必須繼續收 PAT;而 JWT 收或不收對安全無差(它本來就能打全部 REST)。OAuth token 也能打 REST 是順帶結果(共用 `user_from_token`);使用者已決定**不限縮**(§15-4)。日後若要限縮只給 MCP,在 `get_current_user` 擋 `kko_` 即可,一行。

## 10. 安全

- **PKCE 必填 S256**:SDK 模型已強制;token 端 SDK 比對 `sha256(verifier)`。
- **redirect_uri 精確比對**:SDK `validate_redirect_uri` 用完整字串 `in` 比對;consent POST 時再比一次。不合法一律顯示錯誤、不轉導。
- **localhost 回呼**:DCR 只收 `https://` 與 `http://localhost|127.0.0.1`(帶埠)。Claude Code 每次會用不同埠重新註冊,精確比對不受影響。
- **DCR 限制**:上述 redirect 規則、`client_name` 長度、per-IP 限流;`token_endpoint_auth_method` 若客戶端要求 `client_secret_post`,SDK 會發 secret 並在 `/token` 驗(`handlers/register.py:53-60`),接受即可。
- **state**:原樣回傳(SDK 與我們的 consent API 都帶);CSRF 由客戶端驗 state + PKCE 保證。
- **consent CSRF**:consent POST 需 `Authorization: Bearer <JWT>`(localStorage 取出,非 cookie),第三方頁面無法代送;`req` 有簽章與 10 分鐘效期,竄改即失效。
- **code 單次使用**:`load_authorization_code` 後在 `exchange_authorization_code` 內刪除(同 govmeet `DELETE ... RETURNING`)。
- **撤銷**:`POST /revoke`(access 或 refresh 皆可)→ `revoked_at`;refresh 查詢條件含 `revoked_at IS NULL`。緊急全撤:`UPDATE oauth_tokens SET revoked_at=now()`。
- **只存 hash**:access / refresh / code 都只存 sha256;明文只在回應出現一次,不寫 log。
- **issuer 固定**:`BOOK_PUBLIC_BASE_URL` 由設定給,不從 Host 推(與 govmeet 不同:SDK 的 metadata 是啟動時靜態建;且固定值可防 Host header 偽造污染 metadata)。production 缺此設定時 `security_checks.assert_secure_config` 應拒絕啟動。
- **transport security**:既有 `BOOK_MCP_ALLOWED_HOSTS` 行為不變。

## 11. dotfiles-ai mcp.yaml 改法

現況(`sources/mcp.yaml:13-34`):base 為 mcp-remote + `--header Authorization:${AUTH_TOKEN}`;claude-cli override 用 http + 固定 header;claude-desktop override 用全域 mcp-remote.cmd + header。override 是**整段取代** base(`sync/loader.py:151-153`)。

改成(claude-cli 比照 govmeet 去掉 header):

```yaml
  kkbook:
    # base = mcp-remote 橋接(codex/claude-desktop 用)。claude-cli 走原生 http + OAuth(/mcp 按授權,免 token)。
    command: "npx"
    args: ["-y", "mcp-remote", "https://book.believe.center/mcp/", "--header", "Authorization:${AUTH_TOKEN}"]
    env:
      AUTH_TOKEN: "${AUTH_TOKEN}"
    targets: [claude-cli, claude-desktop, codex]
    overrides:
      claude-cli:
        type: http
        url: "https://book.believe.center/mcp/"
      claude-desktop:
        command: "C:/Users/admin/AppData/Roaming/npm/mcp-remote.cmd"
        args: ["https://book.believe.center/mcp/", "--header", "Authorization:${AUTH_TOKEN}"]
        env:
          AUTH_TOKEN: "${AUTH_TOKEN}"
```

改完跑 dotfiles-ai sync(見 institutions/04 維護協議),不手改各工具設定檔。

### Claude Desktop / codex

- **mcp-remote 支援 OAuth(已查證)**:全域安裝版 0.1.38 的 README 寫明支援 MCP Authorization spec、預設在 `localhost:3334` 收回呼、token 存 `~/.mcp-auth`;帶 `--header` 時則直接用 header。所以 Desktop / codex 只要**拿掉 `--header` 參數**即可改走 OAuth。
- **codex 原生支援 HTTP + OAuth(已查證 CLI)**:codex-cli 0.144.1 有 `codex mcp add --url` 與 `codex mcp login <name>`。但 dotfiles-ai 的 http override 會把 `type: http` 一起寫進 `config.toml`,codex 是否接受多出的 `type` 鍵**未確認**;走 mcp-remote 無此問題。
- **已決定**:第一階段 Desktop / codex **維持 PAT header 不動**(PAT 仍被接受,零風險),等 claude-cli 驗收通過再另議是否切(§15-3)。
- `secrets.env` 的 `AUTH_TOKEN` 在 Desktop/codex 仍使用,**不可刪**。

## 12. 測試策略

pytest + 既有 `TestClient`(`backend/tests/conftest.py`),新增 `backend/tests/test_oauth.py` 一檔:

1. metadata:`/.well-known/oauth-authorization-server` 200,含 `S256`、四個端點;PRM 兩條路徑 `resource` 為 `<BASE>/mcp/`。
2. `/mcp/` 無 token → 401 且 `WWW-Authenticate` 含 `resource_metadata=`;帶 JWT / PAT / OAuth access → 200(`tools/list`)。
3. 全流程:`/register`(localhost redirect)→ `/authorize`(302 到 `/oauth/consent?req=`)→ `POST /api/oauth/consent`(JWT)→ 取 code → `/token`(verifier)→ 用 access 呼叫 `list_books` 工具成功。
4. 負向:錯 verifier、code 重用、code 過期、redirect_uri 不符、`evil.com` http redirect 註冊被拒、用 PAT/OAuth token 打 `POST /api/oauth/consent` 與 `POST /api/tokens` 被拒、`req` 竄改或過期被拒。
5. refresh:換發後舊 refresh 失效;撤銷後 access 與 refresh 都失效。
6. 迴歸:`GET /register` 與 `GET /login` 仍回 SPA(§5 已實測,留測試防退化)、既有 `test_tokens.py`、`test_mcp_markdown.py` 全綠(`test_mcp_markdown` 若直接打 `/mcp/` 未帶 token 需補 header)。
7. 前端:`AuthPage` 登入後回 `state.from`;同意頁手動點過一次(本機 `npm run dev` + 後端)。

## 13. 部署與回滾(只寫計畫,不執行)

- 目標:Linode `/opt/believe/book`,網域 `book.believe.center`。實際部署步驟依 `/opt/believe/HANDOFF.md`(**本次未讀到內容,未確認**;本機無副本,需部署者開檔確認)。
- 步驟:
  1. 本機 `pytest` 全綠 + 前端 build 成功。
  2. 設環境變數 `BOOK_PUBLIC_BASE_URL=https://book.believe.center`(新增);其餘不變。
  3. 依 HANDOFF 重建容器。啟動時 `create_all` 自動建三張新表(**只加表、不改既有表**)。
  4. 線上煙霧測試:`curl` 兩個 `.well-known` 200;`curl -X POST /mcp/` 無 token → 401 帶 `WWW-Authenticate`;帶現有 PAT → 200。
  5. 反向代理需把 `/.well-known/*`、`/authorize`、`/token`、`/register`、`/revoke` 送到同一個後端(若代理只轉 `/api`、`/mcp`、SPA,需補;**未確認**代理設定)。
  6. 再改 dotfiles-ai mcp.yaml 並 sync(順序:伺服器先上、客戶端後改)。
- 回滾:退回前一版映像即可。新表留著無害(舊版不讀);PAT 全程可用,客戶端把 claude-cli override 的 `headers` 加回即恢復舊行為。

## 14. 驗收標準

1. 線上 `https://book.believe.center/.well-known/oauth-authorization-server` 與 PRM 回 200 且內容正確。
2. 未帶 token `POST /mcp/` → 401 + `WWW-Authenticate: Bearer ... resource_metadata="..."`。
3. mcp.yaml 改後 sync,Claude Code `/mcp` 看到 kkbook「需要驗證」→ 按授權 → 瀏覽器登入 kkbook → 同意 → 回 Claude Code 顯示已連線。
4. 在 Claude Code 呼叫 `list_books` 成功回傳書單。
5. 既有 PAT(Desktop/codex 用的)仍可呼叫 `list_books`。
6. `pytest` 全綠,含 `test_oauth.py`。

## 15. 使用者已決定事項(2026-09-30,四項全採建議)

1. **access token 效期**:**access 30 天、refresh 90 天**(同 govmeet),env `BOOK_OAUTH_ACCESS_TTL_DAYS` 可調。理由:短效 access 在 Claude Code 多 session 並發 refresh 時會互相作廢、反覆要求授權;kkbook 是個人/小團隊工具,洩漏面仍小於 PAT(365 天)。
2. **已登入時不自動核准**:保留一次點擊「同意」(同 govmeet)。理由:DCR 是公開的,自動核准等於任何人註冊一個 client 再騙使用者點連結就能拿 token。
3. **Claude Desktop / codex 第一階段不改**:維持 PAT header;claude-cli 驗收通過後再另議(屆時 Desktop/codex 只需拿掉 mcp-remote 的 `--header`,mcp-remote 0.1.38 支援 OAuth;代價是首次啟動要開瀏覽器、回呼逾時預設 30 秒)。
4. **OAuth token 不限縮**:與 PAT 一致,可打 REST 與 `/mcp`。

## 16. 未確認事項

- `/opt/believe/HANDOFF.md` 內容與 Linode 反向代理是否轉發根層 `/.well-known`、`/authorize`、`/token`、`/register`、`/revoke`。
- Claude Code 對 `resource` 帶結尾斜線(`/mcp/`)的比對是否嚴格;以驗收 3 實測為準,不行就兩條 PRM 路徑都回同一 resource 或把 MCP URL 統一成無斜線。
- codex 的 `config.toml` 能否接受 dotfiles-ai 寫入的 `type = "http"` 鍵。
- 正式站 DB 是 SQLite 還是 Postgres(`create_all` 兩者皆可,不影響設計)。
