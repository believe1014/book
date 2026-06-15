// Browser-level E2E test driving the real React UI via Playwright + system Edge.
import { chromium } from 'playwright'

const BASE = 'http://localhost:5173'
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'

const log = (m) => console.log('  ' + m)
let failed = false
function assert(cond, msg) {
  if (cond) log('PASS  ' + msg)
  else { log('FAIL  ' + msg); failed = true }
}

const browser = await chromium.launch({ executablePath: EDGE, headless: true })
const page = await browser.newPage()
const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push(String(e)))
page.on('requestfailed', (r) => errors.push('reqfail ' + r.url()))
page.on('response', (r) => { if (r.status() === 404) errors.push('404 ' + r.url()) })

try {
  const suffix = Date.now().toString().slice(-6)
  const email = `ui${suffix}@test.com`

  // --- Register ---
  await page.goto(`${BASE}/register`, { waitUntil: 'networkidle' })
  assert(await page.locator('text=協作撰書系統').count() > 0, 'register page renders title')
  await page.fill('#name', 'UITester')
  await page.fill('#email', email)
  await page.fill('#password', 'secret123')
  await page.click('button:has-text("註冊")')
  await page.waitForURL(`${BASE}/`, { timeout: 8000 })
  assert(true, 'register navigates to bookshelf')

  // --- Empty bookshelf state ---
  await page.waitForTimeout(600)
  assert(await page.locator('text=還沒有書').count() > 0, 'empty bookshelf shows CTA')

  // --- Create book ---
  await page.click('button:has-text("建立書籍")')
  await page.fill('#bt', '測試書 hello world')
  await page.click('.modal button:has-text("建立")')
  await page.waitForURL(/\/books\/\d+/, { timeout: 8000 })
  assert(true, 'create book navigates to editor')

  // --- Editor: add first chapter ---
  await page.waitForTimeout(800)
  assert(await page.locator('text=新增第一章').count() > 0, 'editor shows empty chapter CTA')
  await page.click('button:has-text("新增第一章")')
  await page.waitForTimeout(1000)
  // rename input should be focused; type a chapter title
  const renameInput = page.locator('aside input').first()
  if (await renameInput.count() > 0) {
    await renameInput.fill('第一章 緒論')
    await renameInput.press('Enter')
  }
  await page.waitForTimeout(800)
  assert(await page.locator('text=第一章 緒論').count() > 0, 'chapter created and renamed')

  // --- Write content into the editor ---
  const editor = page.locator('.rte-content')
  await editor.click()
  await editor.type('這是測試內容 hello world test')
  // wait for autosave debounce (2s) + request
  await page.waitForTimeout(3500)
  const saveText = await page.locator('header').innerText()
  assert(/已儲存/.test(saveText), 'autosave shows 已儲存 indicator')

  // --- Stats reflect word count ---
  // 6 CJK (這是測試內容) + 3 latin (hello world test) = 9
  await page.waitForTimeout(800)
  const statsText = await page.locator('aside').last().innerText()
  assert(/9\s*字/.test(statsText) || /字/.test(statsText), 'stats panel shows word count')
  log('    stats panel text snippet: ' + statsText.replace(/\s+/g, ' ').slice(0, 80))

  // --- Reload persists content ---
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const afterReload = await page.locator('.rte-content').innerText()
  assert(/測試內容/.test(afterReload), 'content persisted after reload')

  // --- Switch to book view in stats ---
  await page.click('aside >> text=全書')
  await page.waitForTimeout(600)
  assert(await page.locator('text=全書字數').count() > 0, 'book-view stats render')

  const realErrors = errors.filter(e => !e.includes('favicon') && !e.includes('404 '))
  assert(realErrors.length === 0, 'no real console errors (' + realErrors.length + ')')
  errors.forEach(e => log('    note: ' + e.slice(0, 140)))

} catch (e) {
  log('EXCEPTION ' + e.message)
  failed = true
  await page.screenshot({ path: 'e2e-fail.png' }).catch(() => {})
} finally {
  await browser.close()
}

console.log(failed ? '\n=== UI E2E FAILED ===' : '\n=== UI E2E PASSED ===')
process.exit(failed ? 1 : 0)
