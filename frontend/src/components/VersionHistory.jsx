import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { toast } from '../store/toast'
import Modal from './Modal'
import { docToHtml } from './RichTextEditor'

// Version history drawer (design.md §7.5, S10). List + preview + restore (FR-71/72).
export default function VersionHistory({ chapterId, chapterTitle, canEdit, onClose, onRestored }) {
  const [items, setItems] = useState(null)
  const [selected, setSelected] = useState(null)
  const [preview, setPreview] = useState(null)

  useEffect(() => {
    api.listVersions(chapterId).then((d) => {
      setItems(d.items)
      if (d.items[0]) selectVersion(d.items[0].version)
    }).catch(() => setItems([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapterId])

  function selectVersion(v) {
    setSelected(v)
    setPreview(null)
    api.getVersion(chapterId, v).then(setPreview).catch(() => setPreview(null))
  }

  async function restore() {
    if (!confirm('將以此版本內容建立一個新版本（不會刪除其他歷史）。確認還原？')) return
    try {
      await api.restoreVersion(chapterId, selected)
      toast.success('已還原')
      onRestored?.()
      onClose()
    } catch (e) {
      toast.error(e.message || '還原失敗')
    }
  }

  return (
    <Modal title={`${chapterTitle} · 版本歷史`} onClose={onClose} wide>
      <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 16, minHeight: 320 }}>
        <div style={{ borderRight: '1px solid var(--border-default)', paddingRight: 12, maxHeight: 400, overflow: 'auto' }}>
          {items === null ? (
            <div className="skeleton" style={{ height: 60 }} />
          ) : items.length === 0 ? (
            <p className="muted text-sm">尚無歷史版本</p>
          ) : items.map((it, idx) => (
            <button key={it.version}
              className="btn btn-ghost btn-sm"
              style={{ width: '100%', justifyContent: 'flex-start', flexDirection: 'column', alignItems: 'flex-start', padding: '8px', background: selected === it.version ? 'var(--bg-subtle)' : 'transparent', marginBottom: 4 }}
              onClick={() => selectVersion(it.version)}>
              <span style={{ fontWeight: 500 }}>v{it.version}{idx === 0 ? ' 目前' : ''}</span>
              <span className="text-xs muted">{new Date(it.created_at).toLocaleString('zh-TW')}</span>
              <span className="text-xs muted">◎ {it.editor_name} · {it.word_count} 字</span>
            </button>
          ))}
        </div>

        <div>
          {!preview ? (
            <div className="skeleton" style={{ height: 200 }} />
          ) : (
            <>
              <div className="text-xs muted" style={{ marginBottom: 8 }}>
                編輯者 ◎ {preview.editor.name} · {new Date(preview.created_at).toLocaleString('zh-TW')} · {preview.word_count} 字
              </div>
              <div style={previewBox} dangerouslySetInnerHTML={{ __html: docToHtml(preview.content_json) }} />
              {canEdit && (
                <div style={{ marginTop: 12 }}>
                  <button className="btn btn-primary" onClick={restore}>還原此版本</button>
                  <div className="text-xs muted" style={{ marginTop: 6 }}>還原將建立新版本，不會刪除其他歷史。</div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </Modal>
  )
}

const previewBox = { border: '1px solid var(--border-default)', borderRadius: 8, padding: 16, maxHeight: 300, overflow: 'auto', lineHeight: 1.75, fontFamily: 'var(--font-reading)' }
