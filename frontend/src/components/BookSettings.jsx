import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { toast } from '../store/toast'
import Modal from './Modal'

// Book settings (design.md §7 S9). Metadata/status/goal + danger zone (FR-12/13/14).
export default function BookSettings({ book, myRole, onClose, onUpdated }) {
  const navigate = useNavigate()
  const isOwner = myRole === 'owner'
  const [title, setTitle] = useState(book.title)
  const [description, setDescription] = useState(book.description || '')
  const [status, setStatus] = useState(book.status)
  const [tags, setTags] = useState((book.tags || []).join(', '))
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      const tagArr = tags.split(',').map((t) => t.trim()).filter(Boolean)
      const { book: updated } = await api.updateBook(book.id, {
        title: title.trim(), description, status, tags: tagArr,
      })
      toast.success('已儲存設定')
      onUpdated?.(updated)
      onClose()
    } catch (e) {
      toast.error(e.message || '儲存失敗')
      setBusy(false)
    }
  }

  async function del() {
    if (!confirm('移至回收桶，30 天內可還原。確認刪除書籍？')) return
    try {
      await api.deleteBook(book.id)
      toast.success('已移至回收桶')
      navigate('/')
    } catch (e) {
      toast.error(e.message || '刪除失敗')
    }
  }

  return (
    <Modal title="書籍設定" onClose={onClose}
      footer={isOwner && (
        <>
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>{busy ? '儲存中…' : '儲存'}</button>
        </>
      )}>
      {!isOwner && <p className="text-xs muted" style={{ marginBottom: 12 }}>僅擁有者可編輯設定。</p>}
      <div className="field">
        <label>書名</label>
        <input className="input" value={title} disabled={!isOwner} maxLength={200}
          onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label>簡介</label>
        <textarea className="textarea" value={description} disabled={!isOwner}
          onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="field">
        <label>狀態</label>
        <select className="select" value={status} disabled={!isOwner} onChange={(e) => setStatus(e.target.value)}>
          <option value="draft">草稿</option>
          <option value="writing">進行中</option>
          <option value="completed">完成</option>
          <option value="archived">封存</option>
        </select>
      </div>
      <div className="field">
        <label>標籤（以逗號分隔）</label>
        <input className="input" value={tags} disabled={!isOwner} onChange={(e) => setTags(e.target.value)} />
      </div>

      {isOwner && (
        <div style={{ marginTop: 24, padding: 16, border: '1px solid var(--danger)', borderRadius: 8 }}>
          <div style={{ fontWeight: 600, color: 'var(--danger)', marginBottom: 4 }}>危險區</div>
          <p className="text-xs muted" style={{ marginBottom: 12 }}>刪除書籍會移至回收桶，30 天內可還原。</p>
          <button className="btn btn-danger" onClick={del}>刪除書籍</button>
        </div>
      )}
    </Modal>
  )
}
