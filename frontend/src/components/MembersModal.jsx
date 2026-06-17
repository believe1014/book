import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { toast } from '../store/toast'
import Modal from './Modal'

const ROLE_LABEL = { owner: '擁有者', editor: '編輯者', reviewer: '審閱者', viewer: '讀者' }

// Member management + invite (design.md §7.3, S7/S8). FR-20/22/24.
export default function MembersModal({ bookId, myRole, onClose }) {
  const isOwner = myRole === 'owner'
  const [members, setMembers] = useState(null)
  const [invitations, setInvitations] = useState([])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('editor')
  const [busy, setBusy] = useState(false)

  function load() {
    api.listMembers(bookId).then((d) => { setMembers(d.members); setInvitations(d.invitations) })
      .catch(() => setMembers([]))
  }
  useEffect(load, [bookId])

  async function invite() {
    if (!email) return
    setBusy(true)
    try {
      const { invitation } = await api.inviteMember(bookId, { email, role })
      if (invitation.registered) toast.success('已加入成員')
      else toast.info('已建立邀請，請複製連結給對方')
      setEmail('')
      load()
    } catch (e) {
      toast.error(e.message || '邀請失敗')
    } finally {
      setBusy(false)
    }
  }

  async function changeRole(uid, newRole) {
    try {
      await api.updateRole(bookId, uid, { role: newRole })
      load()
    } catch (e) {
      toast.error(e.message || '更新失敗')
    }
  }

  async function remove(uid) {
    if (!confirm('確定移除此成員？')) return
    try {
      await api.removeMember(bookId, uid)
      load()
    } catch (e) {
      toast.error(e.message || '移除失敗')
    }
  }

  function copyLink(token) {
    const url = `${location.origin}/invite/${token}`
    navigator.clipboard?.writeText(url)
    toast.success('已複製邀請連結')
  }

  return (
    <Modal title="成員管理" onClose={onClose}>
      {isOwner && (
        <div style={{ marginBottom: 20 }}>
          <div className="field">
            <label>邀請 Email</label>
            <input className="input" type="email" value={email}
              onChange={(e) => setEmail(e.target.value)} placeholder="someone@example.com" />
          </div>
          <div className="field">
            <label>角色</label>
            <select className="select" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="editor">編輯者 Editor — 可編寫章節、上傳素材</option>
              <option value="reviewer">審閱者 Reviewer — 可檢視並評論（不可改正文）</option>
              <option value="viewer">讀者 Viewer — 僅可唯讀檢視</option>
            </select>
          </div>
          <button className="btn btn-primary" onClick={invite} disabled={busy || !email}>送出邀請</button>
        </div>
      )}

      <div className="text-xs muted" style={{ marginBottom: 8 }}>目前成員</div>
      {members === null ? (
        <div className="skeleton" style={{ height: 40 }} />
      ) : members.map((m) => (
        <div key={m.user_id} className="spread" style={{ padding: '10px 0', borderBottom: '1px solid var(--border-default)' }}>
          <div>
            <div style={{ fontWeight: 500 }}>◎ {m.name}</div>
            <div className="text-xs muted">{m.email}</div>
          </div>
          {m.role === 'owner' ? (
            <span className="badge">擁有者（不可變更）</span>
          ) : isOwner ? (
            <div className="row gap-2">
              <select className="select" style={{ width: 'auto' }} value={m.role}
                onChange={(e) => changeRole(m.user_id, e.target.value)}>
                <option value="editor">編輯者</option>
                <option value="reviewer">審閱者</option>
                <option value="viewer">讀者</option>
              </select>
              <button className="btn btn-ghost btn-sm" style={{ color: 'var(--danger)' }} onClick={() => remove(m.user_id)}>移除</button>
            </div>
          ) : (
            <span className="badge">{ROLE_LABEL[m.role]}</span>
          )}
        </div>
      ))}

      {invitations.length > 0 && (
        <>
          <div className="text-xs muted" style={{ margin: '14px 0 8px' }}>待接受邀請</div>
          {invitations.map((i) => (
            <div key={i.id} className="spread" style={{ padding: '8px 0', borderBottom: '1px solid var(--border-default)' }}>
              <div className="text-sm">⏳ {i.email} <span className="muted">· {ROLE_LABEL[i.role]}</span></div>
              {isOwner && <button className="btn btn-ghost btn-sm" onClick={() => copyLink(i.token)}>複製邀請連結</button>}
            </div>
          ))}
        </>
      )}
      {!isOwner && <p className="text-xs muted" style={{ marginTop: 12 }}>僅擁有者可管理成員。</p>}
    </Modal>
  )
}
