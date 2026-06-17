import { useEffect, useImperativeHandle, useRef, forwardRef } from 'react'

/*
 RichText editor using contentEditable + a simple toolbar (spec §8.1 allows
 contentEditable fallback that still keeps a rich-text JSON structure).

 We persist a ProseMirror/TipTap-compatible doc JSON so the backend word
 counter (services/wordcount.py) can extract text correctly:
   { type:"doc", content:[ {type:"paragraph"|"heading", content:[{type:"text", text, marks?}]} ] }
*/

// ----- HTML <-> doc JSON conversion -----
function blockToNode(el) {
  const tag = el.tagName.toLowerCase()
  // Standalone (block-level) image, e.g. a figure inserted between paragraphs.
  if (tag === 'img') {
    return {
      type: 'image',
      attrs: { src: el.getAttribute('src') || '', alt: el.getAttribute('alt') || '' },
    }
  }
  // Code/diagram block (e.g. mermaid). Source text round-trips; the rendered
  // preview (a .mermaid-preview sibling) is excluded in htmlToDoc.
  if (tag === 'pre') {
    const text = el.textContent || ''
    const lang = el.getAttribute('data-lang') || ''
    return {
      type: 'codeBlock',
      attrs: { language: lang },
      ...(text ? { content: [{ type: 'text', text }] } : {}),
    }
  }
  let type = 'paragraph'
  const attrs = {}
  if (tag === 'h1' || tag === 'h2' || tag === 'h3') {
    type = 'heading'
    attrs.level = Number(tag[1])
  } else if (tag === 'blockquote') {
    type = 'blockquote'
  } else if (tag === 'ul') {
    type = 'bulletList'
  } else if (tag === 'ol') {
    type = 'orderedList'
  } else if (tag === 'li') {
    type = 'listItem'
  }

  const content = []
  el.childNodes.forEach((child) => {
    if (child.nodeType === Node.TEXT_NODE) {
      if (child.textContent) content.push({ type: 'text', text: child.textContent })
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const ctag = child.tagName.toLowerCase()
      if (ctag === 'img') {
        // Inline image (e.g. inserted inside a paragraph by execCommand).
        content.push({
          type: 'image',
          attrs: { src: child.getAttribute('src') || '', alt: child.getAttribute('alt') || '' },
        })
      } else if (['ul', 'ol', 'li', 'blockquote'].includes(ctag)) {
        content.push(blockToNode(child))
      } else {
        const marks = []
        if (['b', 'strong'].includes(ctag)) marks.push({ type: 'bold' })
        if (['i', 'em'].includes(ctag)) marks.push({ type: 'italic' })
        if (['u'].includes(ctag)) marks.push({ type: 'underline' })
        const text = child.textContent
        if (text) content.push({ type: 'text', text, ...(marks.length ? { marks } : {}) })
      }
    }
  })
  const node = { type }
  if (Object.keys(attrs).length) node.attrs = attrs
  if (content.length) node.content = content
  return node
}

export function htmlToDoc(root) {
  const content = []
  root.childNodes.forEach((el) => {
    if (el.nodeType === Node.ELEMENT_NODE) {
      // Skip rendered diagram previews — they're derived from the source block.
      if (el.classList && el.classList.contains('mermaid-preview')) return
      content.push(blockToNode(el))
    } else if (el.nodeType === Node.TEXT_NODE && el.textContent.trim()) {
      content.push({ type: 'paragraph', content: [{ type: 'text', text: el.textContent }] })
    }
  })
  if (content.length === 0) content.push({ type: 'paragraph' })
  return { type: 'doc', content }
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeAttr(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function imgToHtml(node) {
  const src = escapeAttr(node.attrs?.src)
  const alt = escapeAttr(node.attrs?.alt)
  return `<img src="${src}" alt="${alt}">`
}

function inlineToHtml(node) {
  if (node.type !== 'text') {
    if (node.type === 'image') return imgToHtml(node)
    if (['bulletList', 'orderedList', 'listItem', 'blockquote'].includes(node.type)) {
      return docNodeToHtml(node)
    }
    return ''
  }
  let html = escapeHtml(node.text || '')
  for (const m of node.marks || []) {
    if (m.type === 'bold') html = `<strong>${html}</strong>`
    if (m.type === 'italic') html = `<em>${html}</em>`
    if (m.type === 'underline') html = `<u>${html}</u>`
  }
  return html
}

function docNodeToHtml(node) {
  if (node.type === 'image') return imgToHtml(node)
  if (node.type === 'codeBlock') {
    const lang = node.attrs?.language || node.attrs?.lang || ''
    const src = (node.content || []).map((n) => escapeHtml(n.text || '')).join('')
    const cls = 'code-block' + (lang === 'mermaid' ? ' mermaid-src' : '')
    return `<pre class="${cls}" data-lang="${escapeAttr(lang)}"><code>${src || '<br>'}</code></pre>`
  }
  const inner = (node.content || []).map(inlineToHtml).join('')
  switch (node.type) {
    case 'heading': return `<h${node.attrs?.level || 2}>${inner || '<br>'}</h${node.attrs?.level || 2}>`
    case 'blockquote': return `<blockquote>${inner || '<br>'}</blockquote>`
    case 'bulletList': return `<ul>${inner}</ul>`
    case 'orderedList': return `<ol>${inner}</ol>`
    case 'listItem': return `<li>${inner || '<br>'}</li>`
    default: return `<p>${inner || '<br>'}</p>`
  }
}

export function docToHtml(doc) {
  if (!doc || !doc.content) return '<p><br></p>'
  return doc.content.map(docNodeToHtml).join('') || '<p><br></p>'
}

const RichTextEditor = forwardRef(function RichTextEditor(
  { initialDoc, readOnly, onChange, onCursor, placeholder }, ref
) {
  const elRef = useRef(null)
  const lastHtml = useRef('')
  const mermaidReady = useRef(false)
  const mermaidTimer = useRef(null)

  useImperativeHandle(ref, () => ({
    getDoc: () => htmlToDoc(elRef.current),
    setDoc: (doc) => {
      const html = docToHtml(doc)
      if (elRef.current) { elRef.current.innerHTML = html; lastHtml.current = html; setTimeout(renderMermaid, 0) }
    },
    insertImage: (url) => {
      elRef.current?.focus()
      document.execCommand('insertImage', false, url)
      handleInput()
    },
    insertLink: (url, text) => {
      elRef.current?.focus()
      const html = `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(text || url)}</a>`
      document.execCommand('insertHTML', false, html)
      handleInput()
    },
  }))

  useEffect(() => {
    if (elRef.current) {
      const html = docToHtml(initialDoc)
      elRef.current.innerHTML = html
      lastHtml.current = html
      setTimeout(renderMermaid, 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleInput() {
    const html = elRef.current.innerHTML
    if (html !== lastHtml.current) {
      lastHtml.current = html
      onChange?.(htmlToDoc(elRef.current))
    }
    if (mermaidTimer.current) clearTimeout(mermaidTimer.current)
    mermaidTimer.current = setTimeout(renderMermaid, 600)
  }

  // Render every <pre class="mermaid-src"> into a non-editable preview sibling.
  // Lazy-loads mermaid on first use; skips unchanged sources.
  async function renderMermaid() {
    const root = elRef.current
    if (!root) return
    // drop orphan previews (whose source block was deleted)
    root.querySelectorAll('.mermaid-preview').forEach((p) => {
      const prev = p.previousElementSibling
      if (!prev || !prev.classList.contains('mermaid-src')) p.remove()
    })
    const blocks = root.querySelectorAll('pre.mermaid-src')
    if (!blocks.length) return
    let mermaid
    try { mermaid = (await import('mermaid')).default } catch { return }
    if (!mermaidReady.current) {
      mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'neutral', fontFamily: 'inherit' })
      mermaidReady.current = true
    }
    for (const pre of blocks) {
      const src = (pre.textContent || '').trim()
      let preview = pre.nextElementSibling
      if (!preview || !preview.classList.contains('mermaid-preview')) {
        preview = document.createElement('div')
        preview.className = 'mermaid-preview'
        preview.setAttribute('contenteditable', 'false')
        pre.after(preview)
      }
      if (preview.getAttribute('data-src') === src) continue
      preview.setAttribute('data-src', src)
      if (!src) { preview.innerHTML = ''; continue }
      try {
        const { svg } = await mermaid.render('mmd-' + Math.random().toString(36).slice(2), src)
        preview.innerHTML = svg
      } catch {
        preview.innerHTML = '<div class="mermaid-error">⚠ 圖表語法錯誤，請檢查 Mermaid 語法</div>'
      }
    }
  }

  function insertMermaid() {
    if (readOnly) return
    const sample = 'graph TD\n  A[開始] --> B[結束]'
    elRef.current?.focus()
    document.execCommand('insertHTML', false,
      `<pre class="code-block mermaid-src" data-lang="mermaid"><code>${escapeHtml(sample)}</code></pre><p><br></p>`)
    handleInput()
    setTimeout(renderMermaid, 0)
  }

  function cmd(command, value) {
    if (readOnly) return
    document.execCommand(command, false, value)
    elRef.current?.focus()
    handleInput()
  }

  return (
    <div>
      {!readOnly && (
        <div style={toolbar} role="toolbar" aria-label="文字格式工具列">
          <ToolBtn onClick={() => cmd('bold')} title="粗體 (Ctrl+B)"><b>B</b></ToolBtn>
          <ToolBtn onClick={() => cmd('italic')} title="斜體 (Ctrl+I)"><i>I</i></ToolBtn>
          <ToolBtn onClick={() => cmd('underline')} title="底線 (Ctrl+U)"><u>U</u></ToolBtn>
          <Sep />
          <ToolBtn onClick={() => cmd('formatBlock', 'H1')} title="標題 1">H1</ToolBtn>
          <ToolBtn onClick={() => cmd('formatBlock', 'H2')} title="標題 2">H2</ToolBtn>
          <ToolBtn onClick={() => cmd('formatBlock', 'P')} title="內文">¶</ToolBtn>
          <Sep />
          <ToolBtn onClick={() => cmd('formatBlock', 'BLOCKQUOTE')} title="引用">❝</ToolBtn>
          <ToolBtn onClick={() => cmd('insertUnorderedList')} title="項目清單">≣</ToolBtn>
          <ToolBtn onClick={() => cmd('insertOrderedList')} title="編號清單">1.</ToolBtn>
          <Sep />
          <ToolBtn onClick={() => {
            const url = prompt('輸入連結網址')
            if (url) cmd('createLink', url)
          }} title="連結">🔗</ToolBtn>
          <Sep />
          <ToolBtn onClick={insertMermaid} title="插入流程圖 (Mermaid)">📊</ToolBtn>
        </div>
      )}
      <div
        ref={elRef}
        className="rte-content"
        contentEditable={!readOnly}
        suppressContentEditableWarning
        onInput={handleInput}
        onKeyUp={() => onCursor?.()}
        onMouseUp={() => onCursor?.()}
        data-placeholder={placeholder}
        style={{
          ...contentStyle,
          background: readOnly ? 'var(--bg-subtle)' : 'transparent',
        }}
        aria-readonly={readOnly}
        role="textbox"
        aria-multiline="true"
      />
    </div>
  )
})

function ToolBtn({ children, onClick, title }) {
  return (
    <button type="button" className="btn btn-ghost btn-sm" title={title}
      onMouseDown={(e) => e.preventDefault()} onClick={onClick}
      style={{ minWidth: 32, padding: '4px 8px', border: 'none' }}>
      {children}
    </button>
  )
}
function Sep() {
  return <span style={{ width: 1, height: 20, background: 'var(--border-default)', margin: '0 4px' }} />
}

const toolbar = {
  display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap',
  padding: '8px 0', borderBottom: '1px solid var(--border-default)', marginBottom: 16,
  position: 'sticky', top: 0, background: 'var(--bg-canvas)', zIndex: 5,
}
const contentStyle = {
  maxWidth: 720, margin: '0 auto', minHeight: 'calc(100vh - 200px)',
  fontSize: 16, lineHeight: 1.75, outline: 'none', padding: '8px 4px',
  fontFamily: 'var(--font-reading)',
}

export default RichTextEditor
