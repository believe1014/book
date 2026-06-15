import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { toast } from '../store/toast'

// Right column media panel (design.md §7.4, S6c). Upload/link/filter/insert (FR-80~84).
const TYPE_FILTERS = [
  { key: '', label: '全部' }, { key: 'image', label: '圖' },
  { key: 'video', label: '影' }, { key: 'audio', label: '音' },
  { key: 'file', label: '檔' }, { key: 'link', label: '連結' },
]
const TYPE_ICON = { image: '🖼', video: '▶', audio: '🎵', file: '📄', link: '🔗' }

function fmtSize(b) {
  if (!b) return ''
  if (b < 1024) return `${b}B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)}KB`
  return `${(b / 1024 / 1024).toFixed(1)}MB`
}

export default function MediaPanel({ bookId, canEdit, onInsert }) {
  const [assets, setAssets] = useState(null)
  const [quota, setQuota] = useState({ used: 0, total: 1 })
  const [type, setType] = useState('')
  const [search, setSearch] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef(null)

  function load() {
    const q = new URLSearchParams()
    if (type) q.set('type', type)
    if (search) q.set('search', search)
    const qs = q.toString() ? `?${q}` : ''
    api.listMedia(bookId, qs)
      .then((d) => { setAssets(d.items); setQuota({ used: d.quota_used, total: d.quota_total }) })
      .catch(() => setAssets([]))
  }

  useEffect(() => {
    const t = setTimeout(load, 200)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, search, bookId])

  async function upload(file) {
    const form = new FormData()
    form.append('file', file)
    try {
      await api.uploadMedia(bookId, form)
      toast.success('已上傳')
      load()
    } catch (e) {
      toast.error(e.message || '上傳失敗')
    }
  }

  async function addLink() {
    const url = prompt('貼上外部連結網址（如 YouTube）')
    if (!url) return
    try {
      await api.uploadMedia(bookId, jsonForm({ url, type: 'link' }))
      toast.success('已新增連結')
      load()
    } catch (e) {
      toast.error(e.message || '新增失敗')
    }
  }

  async function insert(a) {
    onInsert?.(a)
    try {
      await api.refMedia(a.id)
      load()
    } catch { /* ref count best-effort */ }
  }

  async function remove(a) {
    if (a.ref_count > 0 && !confirm(`此素材已被引用 ${a.ref_count} 次，仍要刪除？`)) return
    try {
      await api.deleteMedia(a.id)
      load()
    } catch (e) {
      toast.error(e.message || '刪除失敗')
    }
  }

  const pct = Math.round((quota.used / quota.total) * 100)

  return (
    <div>
      {canEdit && (
        <div
          style={{ ...dropZone, borderColor: dragOver ? 'var(--brand-primary)' : 'var(--border-default)', background: dragOver ? 'var(--bg-subtle)' : 'transparent' }}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) upload(f) }}
        >
          <div className="text-sm muted">⬆ 拖曳檔案至此上傳</div>
          <div className="row gap-2" style={{ justifyContent: 'center', marginTop: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()}>選擇檔案</button>
            <button className="btn btn-ghost btn-sm" onClick={addLink}>貼連結</button>
          </div>
          <input ref={fileRef} type="file" hidden onChange={(e) => { const f = e.target.files[0]; if (f) upload(f); e.target.value = '' }} />
        </div>
      )}

      <div className="row gap-2" style={{ flexWrap: 'wrap', margin: '12px 0' }}>
        {TYPE_FILTERS.map((f) => (
          <button key={f.key} className="badge" style={type === f.key ? activePill : pill}
            onClick={() => setType(f.key)}>{f.label}</button>
        ))}
      </div>
      <input className="input" placeholder="🔍 搜尋檔名" value={search}
        onChange={(e) => setSearch(e.target.value)} style={{ marginBottom: 12 }} />

      {assets === null ? (
        <div className="skeleton" style={{ height: 80 }} />
      ) : assets.length === 0 ? (
        <p className="muted text-sm" style={{ textAlign: 'center', padding: 16 }}>尚無素材，拖曳檔案或貼連結</p>
      ) : (
        <div style={mediaGrid}>
          {assets.map((a) => (
            <div key={a.id} className="card" style={{ padding: 8 }}>
              <div style={thumb} title={a.filename}>
                {a.type === 'image' ? <img src={a.url} alt={a.filename} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 4 }} /> : <span style={{ fontSize: 28 }}>{TYPE_ICON[a.type]}</span>}
              </div>
              <div className="text-xs" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 4 }}>{a.filename}</div>
              <div className="text-xs muted">{fmtSize(a.size_bytes)} · 引用×{a.ref_count}</div>
              <div className="row gap-2" style={{ marginTop: 4 }}>
                {canEdit && a.type === 'image' && <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px' }} onClick={() => insert(a)}>插入</button>}
                {canEdit && a.type !== 'image' && <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px' }} onClick={() => onInsert?.(a)}>插入連結</button>}
                {canEdit && <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px', color: 'var(--danger)' }} onClick={() => remove(a)}>刪</button>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <div className="text-xs muted">配額 {fmtSize(quota.used)} / {fmtSize(quota.total)}</div>
        <div className="progress" style={{ marginTop: 4 }}>
          <span style={{ width: `${pct}%`, background: pct > 85 ? 'var(--warning)' : 'var(--brand-primary)' }} />
        </div>
      </div>
    </div>
  )
}

// helper: build a FormData for the json-ish link upload path
function jsonForm(obj) {
  const fd = new FormData()
  Object.entries(obj).forEach(([k, v]) => fd.append(k, v))
  return fd
}

const dropZone = { border: '2px dashed', borderRadius: 8, padding: 16, textAlign: 'center', transition: 'all .1s' }
const mediaGrid = { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }
const thumb = { height: 70, background: 'var(--bg-subtle)', borderRadius: 4, display: 'grid', placeItems: 'center', overflow: 'hidden' }
const pill = { cursor: 'pointer', background: 'var(--bg-surface)', border: '1px solid var(--border-default)' }
const activePill = { cursor: 'pointer', background: 'var(--brand-primary)', color: '#fff' }
