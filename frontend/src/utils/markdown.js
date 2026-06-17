// Minimal, safe-ish Markdown -> HTML for bundled guide content (trusted input):
// headings, lists, blockquote, hr, bold, inline code, links, paragraphs.
function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function inline(s) {
  let h = esc(s)
  h = h.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
  h = h.replace(/\*\*([^*]+)\*\*/g, (_, b) => `<strong>${b}</strong>`)
  h = h.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, t, u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`)
  return h
}

export function renderMarkdown(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const out = []
  let i = 0
  let para = []
  const flushPara = () => {
    if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = [] }
  }
  while (i < lines.length) {
    const t = lines[i].trim()
    if (t === '') { flushPara(); i++; continue }
    if (/^---+$/.test(t)) { flushPara(); out.push('<hr/>'); i++; continue }
    const h = t.match(/^(#{1,4})\s+(.*)$/)
    if (h) { flushPara(); const lv = h[1].length; out.push(`<h${lv}>${inline(h[2])}</h${lv}>`); i++; continue }
    if (/^>\s?/.test(t)) {
      flushPara()
      const buf = []
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) { buf.push(inline(lines[i].trim().replace(/^>\s?/, ''))); i++ }
      out.push(`<blockquote>${buf.join('<br/>')}</blockquote>`)
      continue
    }
    if (/^(\d+)\.\s+/.test(t)) {
      flushPara()
      const items = []
      while (i < lines.length && /^(\d+)\.\s+/.test(lines[i].trim())) { items.push(`<li>${inline(lines[i].trim().replace(/^(\d+)\.\s+/, ''))}</li>`); i++ }
      out.push(`<ol>${items.join('')}</ol>`)
      continue
    }
    if (/^[-*]\s+/.test(t)) {
      flushPara()
      const items = []
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) { items.push(`<li>${inline(lines[i].trim().replace(/^[-*]\s+/, ''))}</li>`); i++ }
      out.push(`<ul>${items.join('')}</ul>`)
      continue
    }
    para.push(t)
    i++
  }
  flushPara()
  return out.join('\n')
}
