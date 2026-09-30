# SDD ledger — plan: docs/superpowers/plans/2026-09-30-mcp-oauth.md
Worktree: C:\Users\admin\dev\book-oauth (branch feat/mcp-oauth, base db3cb27)
Ruling: 計畫寫的根目錄 C:\Users\admin\dev\book 一律改在 worktree C:\Users\admin\dev\book-oauth 執行;venv 未追蹤不在 worktree,測試用 `cd backend && ../../book/backend/venv/Scripts/python.exe -m pytest` — 隔離開發不碰 main 工作目錄的無關變更 — 若 venv 路徑解析錯,測試跑不起來會立即發現
Ruling: 使用者追加需求:T6 加入 uvicorn --proxy-headers 與可信代理(forwarded-allow-ips 限 nginx/docker 網段),讓限流與登入限流以真實 IP 計 — 使用者 2026-09-30 明示 — 若可信範圍設太寬,X-Forwarded-For 可被偽造繞過限流
Ruling: T6 部署、T7 dotfiles push:使用者有長期「改完即部署」授權(memory deploy-after-change);merge 回 main 在 final review 乾淨後進行 — 沿用既有授權 — 若使用者要求部署前再確認,需回滾

## Preflight scan
(2026-09-30,只讀掃描:計畫 vs 規格 vs worktree 實況;規格為最高權威)

### A. 任務對之間的共用介面
| 任務對 | 產出 vs 消費 | 一致? | 發現 / 建議裁定 |
|---|---|---|---|
| T1 -> T2 | `OAuthClientRow/OAuthCodeRow/OAuthTokenRow` 欄位(token_hash、refresh_hash、expires_at、refresh_expires_at、revoked_at、last_used_at、scopes、redirect_uri_provided_explicitly)被 oauth.py / deps.py 使用 | 一致 | 欄位名與型別逐一對上;T1 `Optional` 已在 models.py 既有 import(需實作時確認) |
| T2 -> T3 | `oauth.provider/client_by_id/decode_req/issue_code`、`NON_JWT_PREFIXES`、`settings.public_base_url` 被 consent router / auth_routes / main 消費 | 一致 | `issue_code(session, req, user_id)` 簽章與 consent 呼叫相符;`req["sc"]` 為 list,join 正確 |
| T2 -> T4 | `user_from_token` 三種 token;`settings.public_base_url` 給 FastMCP | 一致 | mcp_server.py 已 import Session/engine/user_from_token/settings/Optional,T4 只補 AccessToken/AuthSettings/run_in_threadpool,無缺 import |
| T3 -> T4 | T4 測試用 T3 的 `_http_tokens`、`_pkce`、`_http_register`(同檔 helper) | 一致 | 皆定義於 T3 段落,T4 在其後 |
| T3 -> T5 | `GET/POST /api/oauth/consent` 回 `{data:{...}}`;前端 `request()` 已解 `json.data`,`{redirect_url}` 解構正確;錯誤經 ApiError.message 顯示 | 一致 | 僅外觀:`<div className="error">` 在 styles.css 只有 `.field .error` 樣式,同意頁錯誤訊息無色。建議維持(不在驗收內),可在 T5 報告記一行 |
| T3 -> T6 | `BOOK_PUBLIC_BASE_URL` fail-fast(production 或有 DATABASE_URL 即檢查) | 一致 | T6 Step 3 在 rebuild 前寫入,順序正確 |
| T4 -> T6 | `/mcp/` 401 + PRM 路徑 `/.well-known/oauth-protected-resource/mcp/` | 一致 | 與 T6 Step 7 預期字串相符 |
| T6 -> T7 -> T8 | 線上 OAuth -> mcp.yaml 去 headers -> 使用者授權 | 一致 | 順序符合規格 §13.6(伺服器先上、客戶端後改) |

### B. 各任務自洽
| 任務 | 檢查 | 發現 / 建議裁定 |
|---|---|---|
| T1 | 1 測試 vs 3 表;FK 指向 users/oauth_clients | 自洽。SQLite 預設不強制 FK,測試不依賴 FK。一致 |
| T2 | 測試 15(T1 1 + T2 14:4+3+1+1+1+1+1+1+1) | 數量正確。`user_from_token(kkr_)` 落到 JWT decode -> None,與測試相符。provider 用同步 Session 於 async 方法,計畫已 ponytail 註記。一致 |
| T3 | 計畫寫 test_oauth 29 passed;實際 T3 新增 14 測試 = 29,但 `test_spa_get_routes_still_served` 在 **worktree 無 frontend/dist 會 skip** | **發現 1**:worktree 沒有 `frontend/dist`(已確認),T3、T4 執行時該測試必為 SKIPPED,故實際為「28 passed, 1 skipped」。建議裁定:審查/驗收接受 skip(T5 build 後可重跑一次補驗,要求 T5 implementer 在 build 後跑 `pytest tests/test_oauth.py -k spa` 並回報 passed) |
| T4 | 計畫寫最終 306 passed = 271 + test_oauth 33 + security 2 | **發現 2**:同上 skip 造成 305 passed + 1 skipped(dist 未建時);T5 build 後於本機跑全套才會 306。建議裁定:T4 驗收預期值改為「305 passed, 1 skipped」,T5 build 後全套重跑應 306 passed。另 test_tokens 兩處改斷言 401 會讓舊 `_mcp_list_books` 仍被其他測試使用(不刪,OK) |
| T5 | 無前端單元測試框架;驗收 = build + 手動清單 | **發現 3**:worktree 無 `frontend/node_modules`(已確認),Step 5 需先 `npm ci`(使用 lockfile,不新增依賴,符合 Global Constraints)。手動驗證 Step 6 需真瀏覽器,subagent 無法代勞 -> 應標「已做,待使用者驗證」而非完成。建議裁定:implementer 先 `npm ci`;Step 6 由使用者或 playwright(既有 e2e-ui.mjs 依賴)驗,無證據不報完成 |
| T6 | 「本機無程式變更」,但前置說 T1-T5 commits「已在本機 main」 | **發現 4**:commits 實際在分支 `feat/mcp-oauth`,非 main;Step 4 `git push origin main` 前須先 merge(ledger 已裁定 final review 乾淨後 merge)。建議裁定:T6 Step 4 改為「merge feat/mcp-oauth 到 main 後 push」 |
| T7 | 改 OneDrive 外部 repo;Step 3 使用者手跑 sync | 自洽。mcp.yaml 行號須實作時核對;本掃描未讀該檔(未確認) |
| T8 | 純驗收/回滾 | 自洽;回滾 `git revert <T1>^..<T5>` 在 merge 後需改用 merge commit 的 `-m 1` 或逐 commit revert(發現 4 的附帶項) |

### C. 與 Global Constraints / 規格衝突、會被審查視為缺陷者
| # | 項目 | 發現 / 建議裁定 |
|---|---|---|
| C1 | Commit 結尾署名:計畫寫 `Co-Authored-By: Claude Opus 5.5`,harness 附註要求 `Claude Sonnet 5.5` | 衝突。計畫非使用者 CLAUDE.md,不構成覆蓋。建議裁定:用實際執行模型署名(harness 指定值),並在 ledger 記一筆 |
| C2 | Global Constraints「指令從 C:\Users\admin\dev\book 起算、`venv/Scripts/python.exe`」 | 已由 ledger Ruling 處理(worktree + `../../book/backend/venv`,實測該 venv 存在於主目錄、worktree 無 venv)。一致 |
| C3 | T6 Step 1(c) 稱「uvicorn 目前沒開 --proxy-headers」並「本任務不改」;Review Focus 5 同 | **與使用者追加需求衝突**,且事實不準:uvicorn 0.34 的 `--proxy-headers` **預設已開**,只是 `forwarded-allow-ips` 預設只信 `127.0.0.1`;容器內看到的來源是 docker bridge 閘道 IP,故 X-Forwarded-For 被忽略。建議裁定:見 D |
| C4 | 規格 §10「production 缺 BOOK_PUBLIC_BASE_URL 應拒絕啟動」 | 計畫 T3 Step 3e 符合。一致 |
| C5 | 規格 §8 限流「per-IP」 vs 計畫 main.py 註解「反向代理後 client.host 是代理 IP」 | 套用 D 後該註解(main.py 新增處與 routers/auth.py:63-64)會過時;T6 加 proxy 設定時一併更新註解(純註解) |
| C6 | 逐字重複邏輯 | 無整段逐字重複。輕微近似:測試中「parse_qs(urlparse(_http_authorize(...).headers['location']).query)['req'][0]」出現 3 次(T3 deny/tamper/mint 測試);`load_refresh_token` 與 `load_access_token` 結構相似但欄位不同;`KkbookTokenVerifier` 與 `_current_user` 都呼叫 user_from_token。審查若嫌重複,建議裁定:可抽 `_req_from(resp)` 測試 helper(不影響計畫驗收),生產碼不動 |
| C7 | 不斷言任何事的測試 | 無。`test_spa_get_routes_still_served` 在 dist 缺時整個 skip(等同未驗,見發現 1);`test_rate_limiter_keys_are_independent` 有斷言。一致 |
| C8 | `test_oauth_tables_exist...` 以 IntegrityError 驗唯一性 | 有效(SQLite 會擋 UNIQUE)。一致 |
| C9 | SDK 介面核對(mcp 1.27.2 實際原始碼) | `construct_redirect_uri(base, **params)` 會略過 None(state 缺時不帶);`create_protected_resource_routes(resource_url, authorization_servers)` 簽章相符;`RevocationRequest.client_secret` 為必填(可空字串)與計畫測試註解相符;`create_auth_routes` 會 `validate_issuer_url`(非 https 僅允許 localhost),故開發預設 `http://localhost:8000` 可、其他 http 主機會啟動失敗,屬預期 |

### D. 補充檢查
| 項目 | 結果 |
|---|---|
| 計畫程式碼用到 worktree 內不存在的路徑 | (1) `backend/venv`:不在 worktree(ledger 已裁定);(2) `frontend/dist`:不存在 -> spa 測試 skip(發現 1);(3) `frontend/node_modules`:不存在 -> T5 需 `npm ci`(發現 3);(4) T6/T7/T8 用的 `~/.ssh/linode_hackathon_ed25519`、`~/.config/ai-ssot/secrets.env`、`OneDrive\dotfiles-ai` 非 repo 內路徑,本掃描未確認存在;(5) 其餘 backend/frontend 檔案(deps、mcp_server、main、security_checks、AuthPage、App、client.js、conftest、test_tokens)皆存在且行號與計畫大致吻合 |
| T6 是否需加 proxy-headers / 可信代理 | **需要**(使用者追加)。建議插入點:T6 **Step 3 與 Step 4 之間新增「Step 3.5」**,理由:必須在 Step 1(讀 compose/nginx、確認 X-Forwarded-For 與 bridge 閘道 IP)與 Step 2(nginx)之後;必須在 Step 4 推送/Step 6 rebuild 之前,因為若選 Dockerfile 方案它是程式變更需進 commit。同時把 Step 1(c) 的「本任務不改」與 Review Focus 5 改寫。做法二選一:(a) 最小:只在 `/opt/believe/book/.env`(或 compose environment)加 `FORWARDED_ALLOW_IPS=<docker 網段/閘道 IP>`,無程式變更、無需 commit;(b) 顯式:Dockerfile CMD 加 `--proxy-headers --forwarded-allow-ips=<CIDR>`(需 commit,但有版本紀錄)。注意:不可用 `*`(任何能直連容器者可偽造 XFF 繞限流);閘道 IP 用 `docker network inspect <compose network>` 取得,勿猜;nginx 必須確實送 `X-Forwarded-For`(Step 1(c) 檢查,缺則補 `proxy_set_header X-Forwarded-For $remote_addr;`);驗證:部署後看容器 log 的來源 IP 是否為真實 client IP 而非 172.x.0.1,或用兩個不同來源打 /register 各自計數 |

### 結論
衝突/需裁定 7 項:發現 1(spa 測試 skip)、發現 2(測試總數 305+1skip)、發現 3(T5 需 npm ci、Step 6 待使用者驗)、發現 4(T6 merge 與回滾指令)、C1(署名模型)、C3(proxy-headers 追加,T6 Step 3.5)、C5(過時註解)。無阻擋性衝突;任務間介面全部一致。

## Preflight rulings
Ruling: spa 測試在 T3/T4 因無 frontend/dist 而 skip,預期值改為 T3「28 passed, 1 skipped」、T4「305 passed, 1 skipped」;T5 build 後全套須 306 passed — worktree 無 dist 屬環境差異非缺陷 — 若 skip 掩蓋路由衝突,T5 全套重跑會抓到
Ruling: T5 先 `npm ci`(依 package-lock,非新增依賴);瀏覽器手動驗證由 T8 使用者走授權時一併完成,T5 報告標「已做,待驗證」 — 無真瀏覽器可點同意頁 — 同意頁 UX 問題延到驗收才發現
Ruling: T6 推送前先在 main checkout 以 fast-forward merge feat/mcp-oauth;T8 回滾改用 `git revert -m1 <merge>` 或對分支範圍 revert — 計畫誤以為 commits 在 main — 無
Ruling: commit 署名維持計畫與主線 harness 指定的 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` — 以控制者 harness 為準 — 署名不影響功能
Ruling: T6 新增 Step 3.5:以 `docker network inspect` 取得 book 容器所在網段,於 `.env`/compose 設 `FORWARDED_ALLOW_IPS=<該網段 CIDR>`(不可 *),確認 nginx 送 X-Forwarded-For(缺則補 proxy_set_header 並 reload),並更新 main.py 限流註解與 routers/auth.py:63-64 過時註解 — 使用者追加需求;uvicorn 0.34 預設只信 127.0.0.1 — 網段設錯則 XFF 仍被忽略(退回共用一桶,不會更糟)
Task 1: dispatched (BASE db3cb27, implementer haiku)
Task 1: ⚠️ create_all 是否建新表 — 已解:三表與 PersonalAccessToken 同在 app/models.py,PAT 表上次部署即由 create_all 自動建立
Task 1: minor (deferred): test_oauth 只斷言 token_hash 唯一,refresh_hash 唯一未測
Task 1: complete (commits db3cb27..9da5aa8, review clean)
Task 2: dispatched (BASE 9da5aa8, implementer sonnet)
Task 2: implemented 90f5eb6; review dispatched
Task 2: 控制者重跑全套 `pytest -o addopts=""`:286 passed(=271+15)
Task 2: ⚠️「不合法 client/redirect 絕不轉導」在 SDK authorize handler 與 T3 consent route — 轉交 T3 審查必查
Ruling: DCR client_secret 明文存於 oauth_clients.client_info 接受 — SDK 以 client_secret_post 比對需要明文,且客戶端為 PKCE 公開客戶端 — 若 DB 外洩,攻擊者可冒用 client 身分,但仍需使用者同意+PKCE 才拿得到 token
Ruling: refresh 每次輪替重設 90 天(滑動效期)接受 — 與 govmeet 行為一致,符合使用者「只要重新授權」的便利目標 — 持續使用的客戶端永不需重登;要撤銷須靠 revoked_at
Task 2: minor (deferred): 過期未兌換的 authorization code 不會清除,oauth_codes 會增長
Task 2: minor (deferred): PKCE 不符/code 過期/refresh 過期未經 HTTP token 端點測試 — T3 整合測試應涵蓋
Task 2: complete (commits 9da5aa8..90f5eb6, review clean)
Task 3: dispatched (BASE 90f5eb6, implementer sonnet)
Task 3: implemented 6754f87 (301 passed, 1 skipped; no separate RED run); review dispatched
Task 3: review: Important×2 — (1) refresh 過期無測試 → 進 fix round 1(resume implementer);(2) 限流取代理 IP
Ruling: T3 finding(2)限流 IP 來源不在 T3 修,由 T6 Step 3.5(FORWARDED_ALLOW_IPS + nginx XFF)處理 — 屬部署拓樸,preflight 已裁定 — 若 T6 漏做,/register 與 /token 會被匿名者耗盡全站配額
Task 3: minor (deferred): oauth_rate_limit middleware 在最外層,429 不帶 CORS/安全標頭(移到 CORSMiddleware 之前註冊)
Task 3: minor (deferred): redirect 不一致測試未斷言 error 值;拒絕流程未先斷言 200
Task 3: fix round 1/5 dispatched (FIX_BASE 6754f87)
Task 3: fix round 1/5 (1 addressed, 0 open; commits 6754f87..d446041)
Task 3: minor (deferred): 新測試後只空一行(PEP8 要兩行)
Task 3: complete (commits 90f5eb6..d446041, review clean)
Task 4: dispatched (BASE d446041, implementer sonnet)
Task 4: implemented 6d4cf49 (RED 5 failed; GREEN 306 passed, 1 skipped); review dispatched
Task 4: ⚠️ 正式站 /mcp/ 無 token 回 401+header — 轉交 T6 部署後煙霧測試
Task 4: minor (deferred): 每次 MCP 呼叫 token 解析兩次(verifier + _current_user),PAT/kko_ 各多一次 last_used_at 寫入
Task 4: minor (deferred): WWW-Authenticate 測試只做子字串比對,未釘住 BOOK_PUBLIC_BASE_URL;無過期 kko_ 打 /mcp/ 測試;只測 POST
Task 4: minor (deferred): mcp_server.py:50 註解與 :76 instructions 未提 OAuth
Task 4: complete (commits d446041..6d4cf49, review clean)
Task 5: dispatched (BASE 6d4cf49, implementer sonnet)
Task 5: implemented c6529a4 (build ok; 307 passed 0 skipped; manual browser checks deferred to T8); review dispatched
Task 5: minor (deferred): AuthPage backTo 未擋 `/\evil.com`(目前不可利用:from 只來自 router location,pushState 跨源會拋錯)— 加固一行
Task 5: complete (commits 6d4cf49..c6529a4, review clean)
Ruling: 程式碼任務(T1–T5)完成後先做 whole-branch final review 與修正,再進 T6 部署/T7 dotfiles/T8 驗收 — 安全相關程式不應未經整體審查就上正式站 — 代價是 T6 晚一點開始
## Final review (db3cb27..c6529a4, opus): Ready with fixes — 控制者旁證 307 passed
Final: Important I-1 同意頁 redirect_origin 用 netloc,https userinfo 可偽裝 localhost → 修
Final: 一併修 M-1(非本機目的地警示)、MCP instructions 誤導文字、backTo 加固
Final: triage — 其餘 deferred minors 皆不擋上線(refresh_hash 測試、過期 code 清理、429 標頭、測試斷言、PEP8、雙重解析、WWW-Authenticate 子字串、M-2 req 無 resource 改規格、M-3 註冊大小、M-4 issuer 斜線交 T8 實測、M-5 同步 Session)
Ruling: T6 追加前提:book 容器 ports 只綁 127.0.0.1(或不 publish),否則 FORWARDED_ALLOW_IPS 信任的 bridge 網段可被外部偽造 XFF;煙霧測試比對完整 resource_metadata 網址 — final reviewer 指出 — 若漏,限流可被繞過
Final: fix wave dispatched (FIX_BASE c6529a4)
Final: fix wave 78b83cc (311 passed; RED 4 failed); scoped re-review dispatched
Final: re-review — 4/4 ADDRESSED, no new Critical/Important
Final: parked — DB 內修正前註冊、redirect 不合法的舊 client 在 /authorize 未被拒 — Ruling: 不修 — 本功能從未部署,正式 DB 尚無 oauth_clients 表,不存在舊 client — 若日後放寬再收緊規則,需補 get_client 過濾
Final: minor (deferred): redirect_origin 對 IPv6 少方括號([::1] 會誤觸非本機警示);空 userinfo/fragment(https://@good.com、結尾 #)仍放行,不能偽裝主機
Final review: clean after one fix wave (commits db3cb27..78b83cc)
Task 6: dispatched (BASE 78b83cc) — 部署
Task 6: deployed — main ff 到 78b83cc + 64c4a0c(註解)已 push;.env 加 BOOK_PUBLIC_BASE_URL、FORWARDED_ALLOW_IPS=172.19.0.1(gateway 單一 IP);compose 本就綁 127.0.0.1;DB 備份 /opt/believe/_backup/book-pre-oauth-20260930-232959.sql 7.8M;回滾點 34ed165
Task 6: smoke 全過(401+WWW-Authenticate 完整比對、PRM、AS metadata issuer 帶結尾斜線、register 擋偽裝、三表 0 筆、真實 IP 生效)
Ruling: T6 不另派任務審查 — 程式變更只有兩處註解,其餘為伺服器設定且已由逐項 smoke test 驗證 — 若設定有誤,T8 實機授權會暴露
Task 6: complete (commits 78b83cc..64c4a0c on main, smoke clean)
Task 7: dispatched
Task 7: dotfiles 53c0479 pushed(只改 sources/mcp.yaml);59 passed;dry-run 無變更 — sync 的 _deep_merge 不刪鍵(06 踩雷已知),~/.claude.json 的 kkbook headers 殘留
Ruling: 採選項 1(使用者先 `claude mcp remove kkbook -s user` 再 sync)而非改 sync/targets.py — 04 協議把 sync/*.py 列為先問;一次性清除即可 — 若日後再有要刪的鍵,仍會踩同一個坑
Task 7: complete (dotfiles 53c0479) — 待使用者執行移除+sync
Task 8: 待使用者 — 移除舊項、sync、/mcp 授權、同意頁手動檢查
Task 8: 使用者 /mcp 授權成功;OAuth token 呼叫 list_books(4 本)與 get_chapter_content(120,Markdown 帶 > )成功 — complete
