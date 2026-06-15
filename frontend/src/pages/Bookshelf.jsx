import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useAuth } from '../store/auth'
import { toast } from '../store/toast'
import Modal from '../components/Modal'

const STATUS_LABELS = {
  draft: '草稿', writing: '進行中', completed: '完成', archived: '封存',
}
const STATUS_FILTERS = [
  { key: '', label: '全部' },
  { key: 'draft', label: '草稿' },
  { key: 'writing', label: '進行中' },
  { key: 'completed', label: '完成' },
  { key: 'archived', label: '封存' },
]

function relativeTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '剛剛'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
  return d.toLocaleDateString('zh-TW')
}

// Color block placeholder cover from book title first char (design.md BookCard).
function coverColor(title) {
  const palette = ['#2D6A4F', '#1971C2', '#D6336C', '#F08C00', '#6741D9']
  let h = 0
  for (const ch of title) h = (h + ch.charCodeAt(0)) % palette.length
  return palette[h]
}

export default function Bookshelf() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  const [books, setBooks] = useState(null) // null=loading
  const [error, setError] = useState(false)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('updated_at')
  const [status, setStatus] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [showTrash, setShowTrash] = useState(false)
  const [userMenu, setUserMenu] = useState(false)
  const debounceRef = useRef(null)

  function load() {
    setError(false)
    const q = new URLSearchParams()
    if (search) q.set('search', search)
    if (sort) q.set('sort', sort)
    if (status) q.set('status', status)
    const qs = q.toString() ? `?${q}` : ''
    api.listBooks(qs)
      .then((d) => setBooks(d.items))
      .catch(() => { setBooks([]); setError(true) })
  }

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(load, 250) // debounce search (FR-11)
    return () => clearTimeout(debounceRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, sort, status])

  return (
    <div style={{ minHeight: '100vh' }}>
      {/* Top bar (design.md §7.1 ①) */}
      <header style={topbar}>
        <div className="row gap-3">
          <span style={{ fontSize: 20 }}>📚</span>
          <strong>撰書系統</strong>
        </div>
        <div style={{ flex: 1, maxWidth: 420, margin: '0 24px' }}>
          <input className="input" placeholder="🔍 搜尋書名…"
            value={search} onChange={(e) => setSearch(e.target.value)} aria-label="搜尋書名" />
        </div>
        <div className="row gap-3">
          <button className="btn btn-ghost btn-sm" onClick={() => setShowTrash(true)}>回收桶</button>
          <div style={{ position: 'relative' }}>
            <button className="btn btn-ghost btn-sm" onClick={() => setUserMenu((v) => !v)}>
              ◎ {user?.name} ▾
            </button>
            {userMenu && (
              <div className="card" style={userMenuStyle}>
                <div className="text-xs muted" style={{ padding: '6px 12px' }}>{user?.email}</div>
                <button className="btn btn-ghost btn-sm" style={{ width: '100%', justifyContent: 'flex-start' }}
                  onClick={() => { logout(); navigate('/login') }}>登出</button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Sort / status filter (design.md §7.1 ③) */}
      <div style={filterBar}>
        <div className="row gap-2 text-sm">
          <span className="muted">排序</span>
          <select className="select" style={{ width: 'auto' }} value={sort}
            onChange={(e) => setSort(e.target.value)}>
            <option value="updated_at">最近編輯</option>
            <option value="created_at">建立時間</option>
            <option value="title">書名</option>
          </select>
        </div>
        <div className="row gap-2">
          {STATUS_FILTERS.map((f) => (
            <button key={f.key}
              className="badge"
              style={status === f.key ? activePill : pill}
              onClick={() => setStatus(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      <main style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
        {books === null ? (
          <div style={grid}>
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="card" style={{ padding: 16, height: 220 }}>
                <div className="skeleton" style={{ height: 90, marginBottom: 12 }} />
                <div className="skeleton" style={{ height: 16, width: '70%', marginBottom: 8 }} />
                <div className="skeleton" style={{ height: 12, width: '40%' }} />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="empty-state">
            <div className="icon">⚠️</div>
            <p>載入書架失敗</p>
            <button className="btn btn-ghost" onClick={load}>重試</button>
          </div>
        ) : books.length === 0 && !search && !status ? (
          <div className="empty-state">
            <div className="icon">📖</div>
            <p>還沒有書，建立你的第一本吧</p>
            <button className="btn btn-primary" onClick={() => setShowCreate(true)}>＋ 建立書籍</button>
          </div>
        ) : books.length === 0 ? (
          <div className="empty-state">
            <div className="icon">🔍</div>
            <p>找不到符合的書籍</p>
            <button className="btn btn-ghost" onClick={() => { setSearch(''); setStatus('') }}>清除篩選</button>
          </div>
        ) : (
          <div style={grid}>
            {books.map((b) => (
              <BookCard key={b.id} book={b} onClick={() => navigate(`/books/${b.id}`)} />
            ))}
            <button className="card" style={createCard} onClick={() => setShowCreate(true)}>
              <span style={{ fontSize: 40, color: 'var(--brand-primary)' }}>＋</span>
              <span style={{ marginTop: 8, fontWeight: 500 }}>建立新書</span>
            </button>
          </div>
        )}
      </main>

      {showCreate && <CreateBookModal onClose={() => setShowCreate(false)}
        onCreated={(book) => navigate(`/books/${book.id}`)} />}
      {showTrash && <TrashModal onClose={() => { setShowTrash(false); load() }} />}
    </div>
  )
}

function BookCard({ book, onClick }) {
  const pct = Math.round((book.progress || 0) * 100)
  return (
    <button className="card book-card" style={bookCardStyle} onClick={onClick}>
      {book.cover_url ? (
        <img src={book.cover_url} alt="" style={coverImg} />
      ) : (
        <div style={{ ...coverImg, background: coverColor(book.title), display: 'grid', placeItems: 'center', color: '#fff', fontSize: 32, fontWeight: 600 }}>
          {book.title[0]}
        </div>
      )}
      <div style={{ padding: '12px 14px', textAlign: 'left' }}>
        <div style={{ fontWeight: 600, marginBottom: 6, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {book.title}
        </div>
        <div className="text-xs muted" style={{ marginBottom: 8 }}>
          <span className={`status-dot status-${book.status}`} />
          {STATUS_LABELS[book.status]}
        </div>
        <div className="text-xs muted" style={{ marginBottom: 6 }}>{(book.word_count || 0).toLocaleString()} 字</div>
        <div className="progress" style={{ marginBottom: 6 }}><span style={{ width: `${pct}%` }} /></div>
        <div className="text-xs muted">{pct}% · {relativeTime(book.updated_at)}</div>
      </div>
    </button>
  )
}

function CreateBookModal({ onClose, onCreated }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [busy, setBusy] = useState(false)
  const valid = title.trim().length >= 1 && title.trim().length <= 200

  async function create() {
    if (!valid) return
    setBusy(true)
    try {
      const tagArr = tags.split(',').map((t) => t.trim()).filter(Boolean)
      const { book } = await api.createBook({ title: title.trim(), description: description || undefined, tags: tagArr.length ? tagArr : undefined })
      toast.success('已建立書籍')
      onCreated(book)
    } catch (e) {
      toast.error(e.message || '建立失敗，請重試')
      setBusy(false)
    }
  }

  return (
    <Modal title="建立書籍" onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={create} disabled={!valid || busy}>
            {busy ? '建立中…' : '建立'}
          </button>
        </>
      }>
      <div className="field">
        <label htmlFor="bt">書名 <span className="muted text-xs">（1–200 字）</span></label>
        <input id="bt" className="input" value={title} maxLength={200}
          onChange={(e) => setTitle(e.target.value)} placeholder="輸入書名" autoFocus
          onKeyDown={(e) => e.key === 'Enter' && create()} />
        {title.length > 0 && !valid && <div className="error">請輸入 1–200 字書名</div>}
      </div>
      <div className="field">
        <label htmlFor="bd">簡介（選填）</label>
        <textarea id="bd" className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="bg">標籤（選填，以逗號分隔）</label>
        <input id="bg" className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="小說, 散文" />
      </div>
    </Modal>
  )
}

function TrashModal({ onClose }) {
  const [items, setItems] = useState(null)
  useEffect(() => {
    api.trash().then((d) => setItems(d.items)).catch(() => setItems([]))
  }, [])

  async function restore(id) {
    try {
      await api.restoreBook(id)
      toast.success('已還原書籍')
      setItems((arr) => arr.filter((b) => b.id !== id))
    } catch (e) {
      toast.error(e.message || '還原失敗')
    }
  }

  return (
    <Modal title="回收桶" onClose={onClose}>
      {items === null ? (
        <div className="skeleton" style={{ height: 60 }} />
      ) : items.length === 0 ? (
        <p className="muted" style={{ textAlign: 'center', padding: 24 }}>回收桶是空的</p>
      ) : (
        items.map((b) => (
          <div key={b.id} className="spread" style={{ padding: '12px 0', borderBottom: '1px solid var(--border-default)' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{b.title}</div>
              <div className="text-xs muted">剩餘 {b.days_remaining} 天可還原</div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => restore(b.id)}>還原</button>
          </div>
        ))
      )}
    </Modal>
  )
}

// styles
const topbar = { height: 'var(--topbar-h)', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border-default)', display: 'flex', alignItems: 'center', padding: '0 24px' }
const filterBar = { maxWidth: 1200, margin: '0 auto', padding: '16px 24px 0', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }
const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 20 }
const bookCardStyle = { padding: 0, overflow: 'hidden', textAlign: 'left', display: 'block', cursor: 'pointer' }
const coverImg = { width: '100%', height: 110, objectFit: 'cover', display: 'block' }
const createCard = { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 240, border: '2px dashed var(--border-default)', background: 'transparent', color: 'var(--text-muted)' }
const pill = { cursor: 'pointer', background: 'var(--bg-surface)', border: '1px solid var(--border-default)' }
const activePill = { cursor: 'pointer', background: 'var(--brand-primary)', color: '#fff' }
const userMenuStyle = { position: 'absolute', right: 0, top: 36, minWidth: 160, padding: 8, zIndex: 50, boxShadow: 'var(--shadow-md)' }
