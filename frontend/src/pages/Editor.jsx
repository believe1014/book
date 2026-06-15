import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useAuth } from '../store/auth'
import { toast } from '../store/toast'
import ChapterTree from '../components/ChapterTree'
import RichTextEditor from '../components/RichTextEditor'
import StatsPanel from '../components/StatsPanel'
import MediaPanel from '../components/MediaPanel'
import MembersModal from '../components/MembersModal'
import BookSettings from '../components/BookSettings'
import VersionHistory from '../components/VersionHistory'
import { useChapterSocket } from '../hooks/useChapterSocket'

const EDIT_ROLES = new Set(['owner', 'editor'])
const AUTOSAVE_MS = 2000 // FR-41: debounce 2s

export default function Editor() {
  const { id } = useParams()
  const bookId = Number(id)
  const navigate = useNavigate()
  const { user } = useAuth()

  const [book, setBook] = useState(null)
  const [myRole, setMyRole] = useState(null)
  const [chapters, setChapters] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [content, setContent] = useState(null) // {version, doc}
  const [loadingContent, setLoadingContent] = useState(false)
  const [saveStatus, setSaveStatus] = useState('saved') // saved|saving|just-saved|conflict|offline
  const [rightTab, setRightTab] = useState('stats') // stats|media
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  const [statsRefresh, setStatsRefresh] = useState(0)
  const [showMembers, setShowMembers] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showVersions, setShowVersions] = useState(false)

  const editorRef = useRef(null)
  const saveTimer = useRef(null)
  const versionRef = useRef(1)
  const pendingDoc = useRef(null)

  const canEditRole = EDIT_ROLES.has(myRole)
  const selectedChapter = findChapter(chapters, selectedId)

  // WebSocket presence / lock / live updates (FR-50/51/52)
  const { presence, lockOwner } = useChapterSocket(selectedId, {
    onContentUpdated: (v) => {
      // Someone else saved a newer version → pull latest if we're behind (FR-52)
      if (v > versionRef.current) reloadContent(selectedId)
    },
  })
  const lockedByOther = lockOwner != null && lockOwner !== user?.id
  const readOnly = !canEditRole || lockedByOther

  // co-editing map for the tree: chapter_id -> editor name (only current chapter known here)
  const coEditingMap = {}
  if (selectedId && lockedByOther) {
    const ed = presence.find((p) => p.user_id === lockOwner)
    if (ed) coEditingMap[selectedId] = ed.name
  }

  // ---- load book + chapters ----
  useEffect(() => {
    api.getBook(bookId)
      .then((d) => { setBook(d.book); setMyRole(d.my_role) })
      .catch((e) => {
        toast.error(e.code === 'NOT_FOUND' ? '找不到書籍或無權存取' : e.message)
        navigate('/')
      })
    reloadChapters()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId])

  const reloadChapters = useCallback(async () => {
    const d = await api.listChapters(bookId)
    setChapters(d.chapters)
    return d.chapters
  }, [bookId])

  // auto-select first chapter
  useEffect(() => {
    if (selectedId == null && chapters.length > 0) {
      const first = chapters[0]
      setSelectedId(first.children?.length ? first.id : first.id)
    }
  }, [chapters, selectedId])

  // ---- load content when chapter changes ----
  useEffect(() => {
    if (selectedId == null) { setContent(null); return }
    reloadContent(selectedId)
    return () => {
      // release lock when leaving a chapter
      if (canEditRole) api.releaseLock(selectedId).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  async function reloadContent(cid) {
    setLoadingContent(true)
    try {
      const d = await api.getContent(cid)
      versionRef.current = d.version
      setContent({ version: d.version, doc: d.content_json })
      editorRef.current?.setDoc(d.content_json)
      setSaveStatus('saved')
      // Auto-acquire lock if our role can edit (design.md §13.3 assumption).
      if (d.can_edit) {
        api.acquireLock(cid).catch(() => {})
      }
    } catch (e) {
      toast.error(e.message || '載入內容失敗')
    } finally {
      setLoadingContent(false)
    }
  }

  // ---- autosave (FR-41/42/43) ----
  function onDocChange(doc) {
    if (readOnly) return
    pendingDoc.current = doc
    setSaveStatus('saving-pending')
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => doSave(), AUTOSAVE_MS)
  }

  async function doSave() {
    if (pendingDoc.current == null) return
    const doc = pendingDoc.current
    setSaveStatus('saving')
    try {
      const res = await api.patchContent(selectedId, {
        content_json: doc, base_version: versionRef.current,
      })
      versionRef.current = res.version
      setSaveStatus('just-saved')
      pendingDoc.current = null
      setStatsRefresh((n) => n + 1)
      setTimeout(() => setSaveStatus((s) => (s === 'just-saved' ? 'saved' : s)), 1500)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setSaveStatus('conflict')
        toast.error('他人已更新此章節，已載入最新內容')
        await reloadContent(selectedId) // last-writer-wins + notify (§6.2)
      } else if (e instanceof ApiError && e.status === 423) {
        setSaveStatus('offline')
        toast.error('章節正由他人編輯')
      } else {
        // offline / network → keep local, save to localStorage (§6.2 offline)
        setSaveStatus('offline')
        try { localStorage.setItem(`draft_${selectedId}`, JSON.stringify(doc)) } catch {}
      }
    }
  }

  // Ctrl/Cmd+S immediate save (a11y §12)
  useEffect(() => {
    function onKey(e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        if (saveTimer.current) clearTimeout(saveTimer.current)
        doSave()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  function insertMedia(asset) {
    if (asset.type === 'image') editorRef.current?.insertImage(asset.url)
    else editorRef.current?.insertLink(asset.url, asset.filename)
    toast.success('已插入')
    // trigger save shortly after
    onDocChange(editorRef.current.getDoc())
  }

  if (!book) {
    return <div style={{ display: 'grid', placeItems: 'center', height: '100vh', color: 'var(--text-muted)' }}>載入中…</div>
  }

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      {/* Top bar (design.md §7.2 ①) */}
      <header style={topbar}>
        <div className="row gap-3">
          <button className="btn btn-ghost btn-sm" onClick={() => navigate('/')} title="回書架">📕 ▾</button>
          <strong>{book.title}</strong>
        </div>
        <div className="row gap-4">
          <Presence users={presence} />
          <SaveIndicator status={saveStatus} />
          {selectedId && <button className="btn btn-ghost btn-sm" onClick={() => setShowVersions(true)}>版本歷史</button>}
          <button className="btn btn-ghost btn-sm" onClick={() => setShowMembers(true)}>分享</button>
          <button className="btn btn-ghost btn-sm" onClick={() => setShowSettings(true)}>⚙ 設定</button>
        </div>
      </header>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Left: chapter tree */}
        {!leftCollapsed && (
          <aside style={leftCol}>
            <div className="spread" style={{ marginBottom: 12 }}>
              <span className="text-sm muted">章節</span>
              <button className="btn btn-ghost btn-sm" onClick={() => setLeftCollapsed(true)} title="收合">⟨</button>
            </div>
            <ChapterTree
              bookId={bookId}
              chapters={chapters}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onReload={reloadChapters}
              canEdit={canEditRole}
              coEditingMap={coEditingMap}
            />
          </aside>
        )}
        {leftCollapsed && (
          <button className="btn btn-ghost btn-sm" style={{ alignSelf: 'flex-start', margin: 8 }} onClick={() => setLeftCollapsed(false)} title="展開章節">☰</button>
        )}

        {/* Center: editor */}
        <main style={centerCol}>
          {selectedId == null ? (
            <div className="empty-state">
              <div className="icon">✍️</div>
              <p>{canEditRole ? '新增第一章開始撰寫' : '此書尚無章節'}</p>
            </div>
          ) : (
            <>
              {readOnly && (
                <div style={banner(lockedByOther)} role="status">
                  {lockedByOther
                    ? `✎ ${coEditingMap[selectedId] || '他人'} 正在編輯此章節・唯讀觀看（即時同步）`
                    : '🔒 您的角色為唯讀，無法編輯內容'}
                </div>
              )}
              <h1 style={{ maxWidth: 720, margin: '0 auto 8px', fontSize: 24 }}>{selectedChapter?.title}</h1>
              {loadingContent ? (
                <div style={{ maxWidth: 720, margin: '0 auto' }}>
                  <div className="skeleton" style={{ height: 20, marginBottom: 10 }} />
                  <div className="skeleton" style={{ height: 20, width: '80%', marginBottom: 10 }} />
                  <div className="skeleton" style={{ height: 20, width: '60%' }} />
                </div>
              ) : (
                <RichTextEditor
                  ref={editorRef}
                  initialDoc={content?.doc}
                  readOnly={readOnly}
                  onChange={onDocChange}
                  placeholder={`開始撰寫「${selectedChapter?.title}」…`}
                />
              )}
            </>
          )}
        </main>

        {/* Right: info panel */}
        {!rightCollapsed ? (
          <aside style={rightColStyle}>
            <div className="spread" style={{ marginBottom: 12 }}>
              <div className="row gap-2">
                <button className="btn btn-sm" style={rightTab === 'stats' ? activeTab : tab} onClick={() => setRightTab('stats')}>統計</button>
                <button className="btn btn-sm" style={rightTab === 'media' ? activeTab : tab} onClick={() => setRightTab('media')}>媒體</button>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setRightCollapsed(true)} title="收合">⟩</button>
            </div>
            {rightTab === 'stats' ? (
              <StatsPanel bookId={bookId} chapterId={selectedId} canEdit={canEditRole} refreshKey={statsRefresh} />
            ) : (
              <MediaPanel bookId={bookId} canEdit={canEditRole} onInsert={insertMedia} />
            )}
          </aside>
        ) : (
          <button className="btn btn-ghost btn-sm" style={{ alignSelf: 'flex-start', margin: 8 }} onClick={() => setRightCollapsed(false)} title="展開面板">📊</button>
        )}
      </div>

      {showMembers && <MembersModal bookId={bookId} myRole={myRole} onClose={() => setShowMembers(false)} />}
      {showSettings && <BookSettings book={book} myRole={myRole} onClose={() => setShowSettings(false)} onUpdated={setBook} />}
      {showVersions && selectedId && (
        <VersionHistory chapterId={selectedId} chapterTitle={selectedChapter?.title} canEdit={canEditRole}
          onClose={() => setShowVersions(false)} onRestored={() => reloadContent(selectedId)} />
      )}
    </div>
  )
}

function findChapter(tree, id) {
  for (const t of tree) {
    if (t.id === id) return t
    for (const k of t.children || []) if (k.id === id) return k
  }
  return null
}

function Presence({ users }) {
  if (!users.length) return null
  const colors = ['#2D6A4F', '#1971C2', '#D6336C', '#F08C00', '#6741D9']
  return (
    <div className="row" title={users.map((u) => u.name).join('、')} aria-label={`線上 ${users.length} 人`}>
      {users.slice(0, 4).map((u, i) => (
        <span key={u.user_id} style={{
          width: 26, height: 26, borderRadius: '50%', background: colors[i % colors.length],
          color: '#fff', display: 'grid', placeItems: 'center', fontSize: 12, marginLeft: i ? -6 : 0,
          border: '2px solid var(--bg-surface)',
        }}>{u.name[0]}</span>
      ))}
      <span className="text-xs muted" style={{ marginLeft: 8 }}>線上 {users.length} 人</span>
    </div>
  )
}

function SaveIndicator({ status }) {
  const map = {
    saved: { text: '✓ 已儲存', color: 'var(--text-muted)' },
    'saving-pending': { text: '編輯中…', color: 'var(--text-muted)' },
    saving: { text: '● 儲存中…', color: 'var(--brand-primary)' },
    'just-saved': { text: '✓ 已儲存', color: 'var(--success)' },
    conflict: { text: '⚠ 內容已更新', color: 'var(--warning)' },
    offline: { text: '⚠ 連線中斷・已暫存', color: 'var(--danger)' },
  }
  const s = map[status] || map.saved
  return <span className="text-sm" style={{ color: s.color }} aria-live="polite">{s.text}</span>
}

const topbar = { height: 'var(--topbar-h)', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border-default)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px', flexShrink: 0 }
const leftCol = { width: 'var(--left-col)', borderRight: '1px solid var(--border-default)', padding: 16, overflow: 'auto', background: 'var(--bg-surface)', flexShrink: 0 }
const centerCol = { flex: 1, overflow: 'auto', padding: '24px 32px', background: 'var(--bg-canvas)' }
const rightColStyle = { width: 'var(--right-col)', borderLeft: '1px solid var(--border-default)', padding: 16, overflow: 'auto', background: 'var(--bg-surface)', flexShrink: 0 }
const tab = { background: 'var(--bg-subtle)', color: 'var(--text-muted)' }
const activeTab = { background: 'var(--brand-primary)', color: '#fff' }
const banner = (other) => ({
  maxWidth: 720, margin: '0 auto 16px', padding: '10px 16px', borderRadius: 8,
  background: other ? '#FFF6E0' : 'var(--bg-subtle)',
  border: `1px solid ${other ? 'var(--warning)' : 'var(--border-default)'}`,
  color: other ? '#7a5b00' : 'var(--text-muted)', fontSize: 14,
})
