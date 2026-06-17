import { useState } from 'react'
import Modal from './Modal'
import { api } from '../api/client'
import { toast } from '../store/toast'
import {
  flattenChapters, sectionsToMarkdown, sectionsToHtml,
  downloadText, exportPdf, safeFileName,
} from '../utils/exportDoc'

// Export whole book or the current chapter as PDF or Markdown (client-side).
export default function ExportModal({ book, chapters, selectedId, selectedTitle, onClose }) {
  const [scope, setScope] = useState('book') // book | chapter
  const [format, setFormat] = useState('pdf') // pdf | md
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')

  const hasChapter = selectedId != null
  if (scope === 'chapter' && !hasChapter) setScope('book')

  // Build [{title, level, doc}] for the chosen scope, fetching content per chapter.
  async function gatherSections() {
    let list
    if (scope === 'chapter') {
      list = [{ id: selectedId, title: selectedTitle || '章節', level: 1 }]
    } else {
      list = flattenChapters(chapters)
    }
    if (list.length === 0) throw new Error('沒有可匯出的章節')
    const sections = []
    for (let i = 0; i < list.length; i++) {
      const c = list[i]
      setProgress(`讀取章節 ${i + 1}/${list.length}：${c.title}`)
      const d = await api.getContent(c.id)
      sections.push({ title: c.title, level: c.level, doc: d.content_json })
    }
    return sections
  }

  async function run() {
    setBusy(true)
    setProgress('準備中…')
    try {
      const sections = await gatherSections()
      const wholeBook = scope === 'book'
      const baseName = safeFileName(
        wholeBook ? book?.title : `${book?.title || 'book'} - ${selectedTitle || ''}`
      )
      if (format === 'md') {
        const md = sectionsToMarkdown(book?.title, sections, { includeBookTitle: wholeBook })
        downloadText(`${baseName}.md`, md)
        toast.success('已匯出 Markdown')
        onClose?.()
      } else {
        setProgress('排版並產生 PDF…（內容較多時可能需要數十秒）')
        const html = sectionsToHtml(book?.title, sections, { includeBookTitle: wholeBook })
        await exportPdf(`${baseName}.pdf`, html)
        toast.success('已匯出 PDF')
        onClose?.()
      }
    } catch (e) {
      toast.error(e.message || '匯出失敗')
    } finally {
      setBusy(false)
      setProgress('')
    }
  }

  return (
    <Modal title="匯出" onClose={busy ? undefined : onClose} footer={
      <>
        <button className="btn btn-ghost" onClick={onClose} disabled={busy}>取消</button>
        <button className="btn btn-primary" onClick={run} disabled={busy}>
          {busy ? '匯出中…' : `匯出 ${format === 'pdf' ? 'PDF' : 'Markdown'}`}
        </button>
      </>
    }>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <Field label="範圍">
          <Choice active={scope === 'book'} onClick={() => setScope('book')}
            title="全書" desc="依章節順序匯出整本書" />
          <Choice active={scope === 'chapter'} onClick={() => hasChapter && setScope('chapter')}
            disabled={!hasChapter}
            title="目前章節" desc={hasChapter ? (selectedTitle || '目前選取的章節') : '尚未選取章節'} />
        </Field>

        <Field label="格式">
          <Choice active={format === 'pdf'} onClick={() => setFormat('pdf')}
            title="PDF" desc="一鍵下載 .pdf（文字會點陣化，檔案較大）" />
          <Choice active={format === 'md'} onClick={() => setFormat('md')}
            title="Markdown" desc="下載 .md 純文字（圖片以連結保留）" />
        </Field>

        {busy && (
          <div className="text-sm muted" aria-live="polite" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="spinner" /> {progress}
          </div>
        )}
      </div>
    </Modal>
  )
}

function Field({ label, children }) {
  return (
    <div>
      <div className="text-sm muted" style={{ marginBottom: 8 }}>{label}</div>
      <div style={{ display: 'flex', gap: 10 }}>{children}</div>
    </div>
  )
}

function Choice({ active, disabled, onClick, title, desc }) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      style={{
        flex: 1, textAlign: 'left', padding: '12px 14px', borderRadius: 10, cursor: disabled ? 'not-allowed' : 'pointer',
        background: active ? 'var(--bg-subtle)' : 'var(--bg-surface)',
        border: `2px solid ${active ? 'var(--brand-primary)' : 'var(--border-default)'}`,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 2 }}>{title}</div>
      <div className="text-xs muted">{desc}</div>
    </button>
  )
}
