import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { toast } from '../store/toast'

// Right-column review comments for the selected chapter (chapter-level threads,
// single-level replies). Reviewers/editors/owners can comment; viewers read only.
export default function CommentsPanel({ bookId, chapterId, canComment, currentUserId, isOwner, onCountChange }) {
  const [threads, setThreads] = useState(null)
  const [showResolved, setShowResolved] = useState(false)

  function load() {
    if (chapterId == null) { setThreads(null); return }
    api.listComments(chapterId)
      .then((d) => { setThreads(d.comments); onCountChange?.(d.unresolved) })
      .catch(() => setThreads([]))
  }
  useEffect(load, [chapterId]) // eslint-disable-line react-hooks/exhaustive-deps

  if (chapterId == null) {
    return <p className="muted text-sm" style={{ textAlign: 'center', padding: 16 }}>選擇章節以檢視評論</p>
  }

  const visible = (threads || []).filter((t) => showResolved || !t.resolved)
  const resolvedCount = (threads || []).filter((t) => t.resolved).length

  return (
    <div>
      <div className="spread" style={{ marginBottom: 8 }}>
        <span className="text-xs muted">章節評論</span>
        <a className="text-xs" href="/guide/reviewer" target="_blank" rel="noopener"
          style={{ color: 'var(--brand-primary)', textDecoration: 'none' }}>❓ 審稿說明</a>
      </div>
      {canComment && (
        <Composer bookId={bookId} chapterId={chapterId} onPosted={load} placeholder="新增評論…可附一張圖片＋說明" />
      )}

      {(threads?.length ?? 0) > 0 && resolvedCount > 0 && (
        <label className="text-xs muted row gap-2" style={{ margin: '10px 0' }}>
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} />
          顯示已解決（{resolvedCount}）
        </label>
      )}

      {threads === null ? (
        <div className="skeleton" style={{ height: 60, marginTop: 12 }} />
      ) : visible.length === 0 ? (
        <p className="muted text-sm" style={{ textAlign: 'center', padding: 16 }}>
          {threads.length === 0 ? '尚無評論' : '沒有未解決的評論 🎉'}
        </p>
      ) : (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {visible.map((t) => (
            <Thread key={t.id} thread={t} bookId={bookId} chapterId={chapterId}
              canComment={canComment} currentUserId={currentUserId} isOwner={isOwner} onChanged={load} />
          ))}
        </div>
      )}
    </div>
  )
}

function Thread({ thread, bookId, chapterId, canComment, currentUserId, isOwner, onChanged }) {
  const [replying, setReplying] = useState(false)
  const t = thread

  async function toggleResolve() {
    try {
      if (t.resolved) await api.unresolveComment(t.id)
      else await api.resolveComment(t.id)
      onChanged()
    } catch (e) { toast.error(e.message || '操作失敗') }
  }

  return (
    <div className="card" style={{ padding: 12, opacity: t.resolved ? 0.6 : 1 }}>
      <CommentItem c={t} currentUserId={currentUserId} isOwner={isOwner} onChanged={onChanged} />
      {t.resolved && <div className="text-xs" style={{ color: 'var(--success)', marginTop: 4 }}>✓ 已解決</div>}

      {t.replies?.map((r) => (
        <div key={r.id} style={{ marginLeft: 14, marginTop: 10, paddingLeft: 10, borderLeft: '2px solid var(--border-default)' }}>
          <CommentItem c={r} currentUserId={currentUserId} isOwner={isOwner} onChanged={onChanged} />
        </div>
      ))}

      {canComment && (
        <div className="row gap-2" style={{ marginTop: 8 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setReplying((v) => !v)}>{replying ? '取消' : '回覆'}</button>
          <button className="btn btn-ghost btn-sm" onClick={toggleResolve}>{t.resolved ? '重新開啟' : '標記已解決'}</button>
        </div>
      )}
      {replying && (
        <div style={{ marginTop: 8 }}>
          <Composer bookId={bookId} chapterId={chapterId} parentId={t.id}
            placeholder="回覆…" onPosted={() => { setReplying(false); onChanged() }} compact />
        </div>
      )}
    </div>
  )
}

function CommentItem({ c, currentUserId, isOwner, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [body, setBody] = useState(c.body)
  const mine = c.author_id === currentUserId

  async function save() {
    try {
      await api.updateComment(c.id, { body })
      setEditing(false)
      onChanged()
    } catch (e) { toast.error(e.message || '更新失敗') }
  }
  async function del() {
    if (!confirm('確定刪除這則評論？')) return
    try { await api.deleteComment(c.id); onChanged() }
    catch (e) { toast.error(e.message || '刪除失敗') }
  }

  return (
    <div>
      <div className="spread">
        <span className="text-sm" style={{ fontWeight: 600 }}>{c.author_name}</span>
        <span className="text-xs muted">{fmtTime(c.created_at)}</span>
      </div>
      {editing ? (
        <div style={{ marginTop: 6 }}>
          <textarea className="input" rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="row gap-2" style={{ marginTop: 4 }}>
            <button className="btn btn-primary btn-sm" onClick={save}>儲存</button>
            <button className="btn btn-ghost btn-sm" onClick={() => { setEditing(false); setBody(c.body) }}>取消</button>
          </div>
        </div>
      ) : (
        <>
          {c.body && <div className="text-sm" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{c.body}</div>}
          {c.image_url && (
            <a href={c.image_url} target="_blank" rel="noopener">
              <img src={c.image_url} alt="評論附圖" style={{ maxWidth: '100%', borderRadius: 6, marginTop: 6 }} />
            </a>
          )}
          {(mine || isOwner) && (
            <div className="row gap-2" style={{ marginTop: 4 }}>
              {mine && <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px' }} onClick={() => setEditing(true)}>編輯</button>}
              <button className="btn btn-ghost btn-sm" style={{ padding: '2px 6px', color: 'var(--danger)' }} onClick={del}>刪除</button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Composer({ bookId, chapterId, parentId, onPosted, placeholder, compact }) {
  const [body, setBody] = useState('')
  const [imageUrl, setImageUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const fileRef = useRef(null)

  async function pickImage(file) {
    if (!file) return
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const { asset } = await api.uploadMedia(bookId, form)
      setImageUrl(asset.url)
    } catch (e) {
      toast.error(e.message || '圖片上傳失敗')
    } finally {
      setBusy(false)
    }
  }

  async function submit() {
    if (!body.trim() && !imageUrl) return
    setBusy(true)
    try {
      await api.createComment(chapterId, { body: body.trim(), image_url: imageUrl || undefined, parent_id: parentId })
      setBody(''); setImageUrl('')
      onPosted?.()
    } catch (e) {
      toast.error(e.message || '送出失敗')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ marginBottom: compact ? 0 : 4 }}>
      <textarea className="input" rows={compact ? 2 : 3} value={body} placeholder={placeholder}
        onChange={(e) => setBody(e.target.value)} />
      {imageUrl && (
        <div style={{ position: 'relative', marginTop: 6 }}>
          <img src={imageUrl} alt="預覽" style={{ maxWidth: '100%', borderRadius: 6 }} />
          <button className="btn btn-ghost btn-sm" style={{ position: 'absolute', top: 4, right: 4, background: 'rgba(0,0,0,.55)', color: '#fff' }}
            onClick={() => setImageUrl('')}>移除圖片</button>
        </div>
      )}
      <div className="row gap-2" style={{ marginTop: 6 }}>
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => fileRef.current?.click()}>🖼 附圖</button>
        <button className="btn btn-primary btn-sm" disabled={busy || (!body.trim() && !imageUrl)} onClick={submit}>
          {busy ? '處理中…' : (parentId ? '回覆' : '送出評論')}
        </button>
        <input ref={fileRef} type="file" accept="image/*" hidden
          onChange={(e) => { pickImage(e.target.files[0]); e.target.value = '' }} />
      </div>
    </div>
  )
}

function fmtTime(iso) {
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}
