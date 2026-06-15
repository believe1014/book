"""End-to-end smoke test against a running server (spec §7 acceptance items).

Run: venv/Scripts/python.exe tests/e2e_smoke.py
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def req(method, path, body=None, token=None, expect=None):
    # Percent-encode non-ASCII (e.g. CJK) in the path/query.
    url = BASE + urllib.parse.quote(path, safe="/?=&")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            status = resp.status
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        status = e.code
        payload = json.loads(e.read().decode())
    if expect is not None:
        assert status == expect, f"{method} {path} -> {status} (want {expect}): {payload}"
    return status, payload


passed = []


def ok(name):
    passed.append(name)
    print(f"  PASS  {name}")


# --- Auth (FR-01/02/03) ---
import random
suffix = random.randint(1000, 9999)
amy_email = f"amy{suffix}@test.com"
ben_email = f"ben{suffix}@test.com"

s, p = req("POST", "/auth/register", {"email": amy_email, "password": "secret123", "name": "Amy"}, expect=200)
amy_token = p["data"]["token"]; amy_id = p["data"]["user"]["id"]
ok("register Amy returns token")

s, p = req("POST", "/auth/register", {"email": amy_email, "password": "secret123", "name": "Dup"})
assert s == 409 and p["error"]["code"] == "CONFLICT", p
ok("duplicate email -> 409 CONFLICT")

s, p = req("POST", "/auth/login", {"email": amy_email, "password": "wrong"})
assert s == 401, p
ok("wrong password -> 401")

s, p = req("GET", "/books")
assert s == 401, p
ok("no token on protected route -> 401")

s, p = req("POST", "/auth/register", {"email": ben_email, "password": "secret123", "name": "Ben"}, expect=200)
ben_token = p["data"]["token"]; ben_id = p["data"]["user"]["id"]
ok("register Ben")

# --- Books (FR-10/11/12/14) ---
s, p = req("POST", "/books", {"title": "我的第一本書", "description": "測試", "tags": ["小說"]}, token=amy_token, expect=200)
book_id = p["data"]["book"]["id"]
ok("create book")

s, p = req("GET", f"/books/{book_id}", token=amy_token, expect=200)
assert p["data"]["my_role"] == "owner", p
ok("creator is owner (my_role)")

s, p = req("POST", "/books", {"title": ""}, token=amy_token)
assert s == 400, p
ok("empty title -> 400")

s, p = req("GET", "/books", token=amy_token, expect=200)
assert p["data"]["total"] >= 1
ok("list books returns own books")

s, p = req("GET", "/books?search=不存在的書名XYZ", token=amy_token, expect=200)
assert p["data"]["total"] == 0
ok("search filter works")

# Ben cannot see Amy's book
s, p = req("GET", f"/books/{book_id}", token=ben_token)
assert s == 404, p  # don't leak existence (§6.7)
ok("non-member gets 404 (no existence leak)")

# Ben cannot delete Amy's book
s, p = req("DELETE", f"/books/{book_id}", token=ben_token)
assert s == 404, p
ok("non-member delete -> 404")

# --- Chapters (FR-30/31/32/35) ---
s, p = req("POST", f"/books/{book_id}/chapters", {"title": "第一章 緒論"}, token=amy_token, expect=200)
ch1 = p["data"]["chapter"]["id"]
ok("create chapter")

s, p = req("POST", f"/books/{book_id}/chapters", {"title": "1.1 研究背景", "parent_id": ch1}, token=amy_token, expect=200)
ch11 = p["data"]["chapter"]["id"]
ok("create sub-chapter (2 levels)")

# Third level rejected (FR-31)
s, p = req("POST", f"/books/{book_id}/chapters", {"title": "third", "parent_id": ch11}, token=amy_token)
assert s == 400, p
ok("third level -> 400")

s, p = req("PATCH", f"/chapters/{ch1}", {"title": "第一章 導論"}, token=amy_token, expect=200)
assert p["data"]["chapter"]["title"] == "第一章 導論"
ok("rename chapter")

s, p = req("GET", f"/books/{book_id}/chapters", token=amy_token, expect=200)
assert len(p["data"]["chapters"]) == 1 and len(p["data"]["chapters"][0]["children"]) == 1
ok("chapter tree structure correct")

# --- Content + word count (FR-40/42/43, §4.3) ---
# Mixed Chinese + English: 5 CJK chars ("這是測試書") + 2 latin tokens ("hello world") = 7
doc = {"type": "doc", "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "這是測試書 hello world"}]}
]}
s, p = req("GET", f"/chapters/{ch1}/content", token=amy_token, expect=200)
base_v = p["data"]["version"]
ok(f"get content (version={base_v})")

s, p = req("PATCH", f"/chapters/{ch1}/content", {"content_json": doc, "base_version": base_v}, token=amy_token, expect=200)
assert p["data"]["version"] == base_v + 1, p
assert p["data"]["word_count"] == 7, f"word_count={p['data']['word_count']} (want 7)"
ok(f"patch content: version+1, word_count={p['data']['word_count']} (CJK+latin)")

# Version conflict (§6.2)
s, p = req("PATCH", f"/chapters/{ch1}/content", {"content_json": doc, "base_version": base_v}, token=amy_token)
assert s == 409, p
ok("stale base_version -> 409 CONFLICT")

# --- Members & permissions (FR-20/22/24, §3.3) ---
s, p = req("POST", f"/books/{book_id}/members", {"email": ben_email, "role": "viewer"}, token=amy_token, expect=200)
ok("invite registered user as viewer")

# Ben (viewer) cannot edit content (403)
s, p = req("GET", f"/chapters/{ch1}/content", token=ben_token, expect=200)
ben_base = p["data"]["version"]
s, p = req("PATCH", f"/chapters/{ch1}/content", {"content_json": doc, "base_version": ben_base}, token=ben_token)
assert s == 403, p
ok("viewer edit content -> 403")

# invite self -> 400
s, p = req("POST", f"/books/{book_id}/members", {"email": amy_email, "role": "editor"}, token=amy_token)
assert s == 400, p
ok("invite self -> 400")

# invite existing member -> 409
s, p = req("POST", f"/books/{book_id}/members", {"email": ben_email, "role": "editor"}, token=amy_token)
assert s == 409, p
ok("invite existing member -> 409")

# pending invite for unregistered email (FR-21)
s, p = req("POST", f"/books/{book_id}/members", {"email": f"new{suffix}@test.com", "role": "editor"}, token=amy_token, expect=200)
assert p["data"]["invitation"]["status"] == "pending"
ok("invite unregistered -> pending invitation")

# upgrade Ben to editor, now he can edit
s, p = req("PATCH", f"/books/{book_id}/members/{ben_id}", {"role": "editor"}, token=amy_token, expect=200)
ok("owner changes member role")

# --- Stats (FR-60/61) ---
s, p = req("GET", f"/chapters/{ch1}/stats", token=amy_token, expect=200)
assert p["data"]["word_count"] == 7, p
ok(f"chapter stats word_count={p['data']['word_count']}, paragraphs={p['data']['paragraph_count']}, reading={p['data']['reading_minutes']}min")

s, p = req("PATCH", f"/books/{book_id}", {"word_count_goal": 100}, token=amy_token, expect=200)
s, p = req("GET", f"/books/{book_id}/stats", token=amy_token, expect=200)
d = p["data"]
assert d["total_words"] == 7 and d["goal"] == 100, d
ok(f"book stats: total={d['total_words']}, goal={d['goal']}, goal_rate={d['goal_rate']}, contributors={len(d['contributors'])}")

# --- Versions (FR-70/71/72) ---
s, p = req("GET", f"/chapters/{ch1}/versions", token=amy_token, expect=200)
assert p["data"]["total"] >= 1
vers = p["data"]["items"][0]["version"]
ok(f"version history: {p['data']['total']} versions")

s, p = req("POST", f"/chapters/{ch1}/versions/{vers}/restore", token=amy_token, expect=200)
ok(f"restore version -> new version {p['data']['version']}")

# --- Soft delete + restore (FR-14/15) ---
s, p = req("DELETE", f"/books/{book_id}", token=amy_token, expect=200)
ok("owner soft-deletes book")
s, p = req("GET", "/books", token=amy_token, expect=200)
assert all(b["id"] != book_id for b in p["data"]["items"])
ok("deleted book not in list")
s, p = req("GET", "/books/trash", token=amy_token, expect=200)
assert any(b["id"] == book_id for b in p["data"]["items"])
ok("deleted book appears in trash")
s, p = req("POST", f"/books/{book_id}/restore", token=amy_token, expect=200)
ok("restore book within 30 days")

# --- Chapter cascade soft-delete (FR-35) ---
s, p = req("DELETE", f"/chapters/{ch1}", token=amy_token, expect=200)
s, p = req("GET", f"/books/{book_id}/chapters", token=amy_token, expect=200)
assert len(p["data"]["chapters"]) == 0, p
ok("delete parent chapter cascades to child")

print(f"\n=== ALL {len(passed)} CHECKS PASSED ===")
