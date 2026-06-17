// Export helpers: turn the book/chapter ProseMirror-style content_json into
// Markdown or PDF on the client. Reuses the same node shape the editor
// persists (see RichTextEditor.jsx): doc → {paragraph|heading|blockquote|
// bulletList|orderedList|listItem|image|text(+marks)}.
import { docToHtml } from '../components/RichTextEditor'

// Resolve a possibly-relative media URL (e.g. "/storage/1/x.png") to absolute
// so it works inside a print window / rasterized PDF.
function absUrl(src) {
  const s = String(src || '')
  if (!s || /^(https?:|data:)/i.test(s)) return s
  if (s.startsWith('/')) return window.location.origin + s
  return s
}

// ---------- Markdown serialization ----------
function inlineToMd(node) {
  if (!node) return ''
  if (node.type === 'image') return `![${node.attrs?.alt || ''}](${absUrl(node.attrs?.src)})`
  if (node.type !== 'text') return ''
  let t = node.text || ''
  for (const m of node.marks || []) {
    if (m.type === 'bold') t = `**${t}**`
    else if (m.type === 'italic') t = `*${t}*`
    else if (m.type === 'underline') t = `<u>${t}</u>`
  }
  return t
}

function inlines(node) {
  return (node.content || []).map(inlineToMd).join('')
}

function blockToMd(node, depth = 0) {
  if (!node) return ''
  const pad = '  '.repeat(depth)
  switch (node.type) {
    case 'heading':
      return '#'.repeat(node.attrs?.level || 2) + ' ' + inlines(node)
    case 'paragraph':
      return inlines(node)
    case 'image':
      return `![${node.attrs?.alt || ''}](${absUrl(node.attrs?.src)})`
    case 'blockquote':
      return (node.content || [])
        .map((c) => blockToMd(c, depth))
        .join('\n')
        .split('\n')
        .map((l) => '> ' + l)
        .join('\n')
    case 'bulletList':
      return (node.content || []).map((li) => pad + '- ' + listItemText(li, depth)).join('\n')
    case 'orderedList':
      return (node.content || [])
        .map((li, i) => pad + `${i + 1}. ` + listItemText(li, depth))
        .join('\n')
    case 'listItem':
      return listItemText(node, depth)
    default:
      return inlines(node)
  }
}

function listItemText(li, depth) {
  // A listItem holds block children (usually paragraphs, sometimes nested lists).
  return (li.content || [])
    .map((c) => (c.type === 'bulletList' || c.type === 'orderedList' ? '\n' + blockToMd(c, depth + 1) : blockToMd(c, depth)))
    .join(' ')
    .trim()
}

export function docToMarkdown(doc) {
  if (!doc || !doc.content) return ''
  return doc.content
    .map((n) => blockToMd(n, 0))
    .filter((s) => s !== undefined)
    .join('\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// ---------- chapter tree helpers ----------
// Flatten the two-level tree into reading order with hierarchy level (1 top, 2 child).
export function flattenChapters(tree) {
  const out = []
  for (const top of tree || []) {
    out.push({ id: top.id, title: top.title, level: 1 })
    for (const kid of top.children || []) {
      out.push({ id: kid.id, title: kid.title, level: 2 })
    }
  }
  return out
}

// ---------- assemble combined documents ----------
// sections: [{ title, level, doc }]
export function sectionsToMarkdown(bookTitle, sections, { includeBookTitle }) {
  const parts = []
  if (includeBookTitle && bookTitle) parts.push(`# ${bookTitle}\n`)
  for (const s of sections) {
    const hashes = '#'.repeat(includeBookTitle ? Math.min(s.level + 1, 6) : s.level)
    parts.push(`${hashes} ${s.title}`)
    const body = docToMarkdown(s.doc)
    if (body) parts.push(body)
  }
  return parts.join('\n\n').replace(/\n{3,}/g, '\n\n').trim() + '\n'
}

export function sectionsToHtml(bookTitle, sections, { includeBookTitle }) {
  const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  const blocks = []
  if (includeBookTitle && bookTitle) {
    blocks.push(`<h1 class="book-title">${esc(bookTitle)}</h1>`)
  }
  sections.forEach((s, i) => {
    const cls = i === 0 && !(includeBookTitle && bookTitle) ? 'chap chap-first' : 'chap'
    const tag = s.level === 1 ? 'h1' : 'h2'
    // Absolutize image URLs in the rendered content so they load in the PDF.
    const html = docToHtml(s.doc).replace(/(<img[^>]*\bsrc=")([^"]*)"/g, (m, p, url) => `${p}${esc(absUrl(url))}"`)
    blocks.push(`<section class="${cls}"><${tag} class="chap-title">${esc(s.title)}</${tag}>${html}</section>`)
  })
  return blocks.join('\n')
}

const PRINT_CSS = `
  *{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  body{ font-family:"Noto Serif TC","Songti TC","PingFang TC","Microsoft JhengHei",serif;
        color:#1a1a1a; line-height:1.75; font-size:15px; margin:0; }
  .book-title{ font-size:30px; text-align:center; margin:40px 0 24px; }
  section.chap{ page-break-before: always; }
  section.chap.chap-first{ page-break-before: avoid; }
  .chap-title{ font-size:22px; margin:0 0 14px; border-bottom:2px solid #d6a15e; padding-bottom:6px; }
  h1,h2,h3{ line-height:1.35; }
  h1{font-size:20px} h2{font-size:17px} h3{font-size:15px}
  p{ margin:0 0 12px; }
  img{ max-width:100%; height:auto; display:block; margin:10px auto; }
  blockquote{ border-left:3px solid #ccc; margin:12px 0; padding:4px 14px; color:#555; }
  ul,ol{ margin:0 0 12px; padding-left:1.4em; }
`

// ---------- downloads ----------
export function downloadText(filename, text, mime = 'text/markdown;charset=utf-8') {
  const blob = new Blob([text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// One-click PDF via html2pdf.js (lazy-loaded; rasterizes via html2canvas).
export async function exportPdf(filename, bodyHtml) {
  const { default: html2pdf } = await import('html2pdf.js')
  const holder = document.createElement('div')
  holder.style.cssText = 'position:fixed;left:-99999px;top:0;width:794px;background:#fff;padding:24px;'
  holder.innerHTML = `<style>${PRINT_CSS}</style>` + bodyHtml
  document.body.appendChild(holder)
  try {
    await html2pdf()
      .set({
        filename,
        margin: [12, 10, 14, 10],
        image: { type: 'jpeg', quality: 0.95 },
        html2canvas: { scale: 2, useCORS: true, backgroundColor: '#ffffff', logging: false },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
        pagebreak: { mode: ['css', 'legacy'] },
      })
      .from(holder)
      .save()
  } finally {
    holder.remove()
  }
}

export function safeFileName(s) {
  return String(s || 'book').replace(/[\\/:*?"<>|]+/g, '_').replace(/\s+/g, ' ').trim().slice(0, 80)
}
