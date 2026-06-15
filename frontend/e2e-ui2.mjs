// Second UI E2E: media upload, version history, and viewer read-only mode.
import { chromium } from 'playwright'

const BASE = 'http://localhost:5173'
const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const log = (m) => console.log('  ' + m)
let failed = false
const assert = (c, m) => { if (c) log('PASS  ' + m); else { log('FAIL  ' + m); failed = true } }

const browser = await chromium.launch({ executablePath: EDGE, headless: true })

async function newUser(ctx, email) {
  const page = await ctx.newPage()
  await page.goto(`${BASE}/register`, { waitUntil: 'networkidle' })
  await page.fill('#name', email.split('@')[0])
  await page.fill('#email', email)
  await page.fill('#password', 'secret123')
  await page.click('button:has-text("註冊")')
  await page.waitForURL(`${BASE}/`, { timeout: 8000 })
  return page
}

try {
  const s = Date.now().toString().slice(-6)
  const ownerEmail = `owner${s}@test.com`
  const viewerEmail = `viewer${s}@test.com`

  // Viewer registers first so the email exists for direct-add invite.
  const ctxV = await browser.newContext()
  const vpage = await newUser(ctxV, viewerEmail)

  // Owner creates a book + chapter + content
  const ctxO = await browser.newContext()
  const opage = await newUser(ctxO, ownerEmail)
  await opage.click('button:has-text("建立書籍")')
  await opage.fill('#bt', '權限測試書')
  await opage.click('.modal button:has-text("建立")')
  await opage.waitForURL(/\/books\/(\d+)/, { timeout: 8000 })
  const bookId = opage.url().match(/\/books\/(\d+)/)[1]
  await opage.waitForTimeout(600)
  await opage.click('button:has-text("新增第一章")')
  await opage.waitForTimeout(1000)
  const ri = opage.locator('aside input').first()
  if (await ri.count()) { await ri.fill('章節甲'); await ri.press('Enter') }
  await opage.waitForTimeout(600)
  const ed = opage.locator('.rte-content')
  await ed.click(); await ed.type('擁有者寫的內容測試')
  await opage.waitForTimeout(3200)
  assert(/已儲存/.test(await opage.locator('header').innerText()), 'owner content autosaved')

  // edit again to create a 2nd version
  await ed.click(); await ed.type(' 第二次編輯')
  await opage.waitForTimeout(3200)

  // Version history shows >=2 versions
  await opage.click('button:has-text("版本歷史")')
  await opage.waitForTimeout(1000)
  const vtext = await opage.locator('.modal').innerText()
  assert(/v\d/.test(vtext), 'version history lists versions')
  log('    versions snippet: ' + vtext.replace(/\s+/g, ' ').slice(0, 70))
  await opage.click('.modal button[aria-label="關閉"]')

  // Media: upload an image via the hidden file input
  await opage.waitForTimeout(300)
  await opage.click('aside >> text=媒體')
  await opage.waitForTimeout(500)
  // tiny 1x1 png
  const pngB64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
  const buf = Buffer.from(pngB64, 'base64')
  await opage.setInputFiles('aside input[type="file"]', {
    name: 'pixel.png', mimeType: 'image/png', buffer: buf,
  })
  await opage.waitForTimeout(1200)
  assert(await opage.locator('aside >> text=pixel.png').count() > 0, 'media upload appears in panel')

  // Owner invites viewer (already registered -> direct add)
  await opage.click('header button:has-text("分享")')
  await opage.waitForTimeout(600)
  await opage.fill('.modal input[type="email"]', viewerEmail)
  await opage.selectOption('.modal select', 'viewer')
  await opage.click('.modal button:has-text("送出邀請")')
  await opage.waitForTimeout(800)
  assert(await opage.locator('.modal').innerText().then(t => t.includes(viewerEmail)), 'viewer added to members')
  await opage.click('.modal button[aria-label="關閉"]')

  // Viewer opens the book -> should be read-only (no toolbar, read-only banner)
  await vpage.goto(`${BASE}/books/${bookId}`, { waitUntil: 'networkidle' })
  await vpage.waitForTimeout(1500)
  // select the chapter
  const ch = vpage.locator('aside >> text=章節甲')
  if (await ch.count()) await ch.first().click()
  await vpage.waitForTimeout(1200)
  const vbody = await vpage.locator('main').innerText()
  assert(/唯讀/.test(vbody), 'viewer sees read-only banner')
  const toolbarCount = await vpage.locator('[aria-label="文字格式工具列"]').count()
  assert(toolbarCount === 0, 'viewer has no editing toolbar')

} catch (e) {
  log('EXCEPTION ' + e.message); failed = true
} finally {
  await browser.close()
}
console.log(failed ? '\n=== UI E2E #2 FAILED ===' : '\n=== UI E2E #2 PASSED ===')
process.exit(failed ? 1 : 0)
