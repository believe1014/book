import { useState } from 'react'
import { api } from '../api/client'
import { toast } from '../store/toast'

/*
 Left column chapter tree (design.md §7.2 ②, S6a).
 - add chapter / sub-chapter
 - rename (double-click / F2)
 - drag reorder with 2-level constraint (FR-31/33)
 - status color dots
 - co-editing badge from presence
*/
const STATUS_CYCLE = ['not_started', 'writing', 'reviewing', 'done']
const STATUS_TEXT = { not_started: '未開始', writing: '撰寫中', reviewing: '待審', done: '完成' }

export default function ChapterTree({
  bookId, chapters, selectedId, onSelect, onReload, canEdit, coEditingMap,
}) {
  const [renaming, setRenaming] = useState(null)
  const [renameVal, setRenameVal] = useState('')
  const [menuFor, setMenuFor] = useState(null)
  const [dragId, setDragId] = useState(null)

  // Collapsed sub-chapters, persisted per book so it survives reloads.
  const collapseKey = `chapterTree.collapsed.${bookId}`
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = localStorage.getItem(collapseKey)
      return new Set(raw ? JSON.parse(raw) : [])
    } catch {
      return new Set()
    }
  })

  function toggleCollapse(id) {
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      try { localStorage.setItem(collapseKey, JSON.stringify([...next])) } catch { /* ignore */ }
      return next
    })
  }

  async function addChapter(parentId = null) {
    const title = parentId ? '新節' : '新章節'
    try {
      const { chapter } = await api.createChapter(bookId, { title, parent_id: parentId })
      await onReload()
      onSelect(chapter.id)
      startRename(chapter.id, chapter.title)
    } catch (e) {
      toast.error(e.message || '新增失敗')
    }
  }

  function startRename(id, title) {
    setRenaming(id)
    setRenameVal(title)
    setMenuFor(null)
  }

  async function commitRename(id) {
    const val = renameVal.trim()
    setRenaming(null)
    if (!val) return
    try {
      await api.updateChapter(id, { title: val })
      await onReload()
    } catch (e) {
      toast.error(e.message || '改名失敗')
    }
  }

  async function cycleStatus(ch) {
    const next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(ch.status) + 1) % STATUS_CYCLE.length]
    try {
      await api.updateChapter(ch.id, { status: next })
      await onReload()
    } catch (e) {
      toast.error(e.message || '更新失敗')
    }
    setMenuFor(null)
  }

  async function removeChapter(ch) {
    if (!confirm(`刪除「${ch.title}」？${ch.children?.length ? '其下子節也會一併移除。' : ''}此動作會移到回收狀態。`)) return
    try {
      await api.deleteChapter(ch.id)
      await onReload()
    } catch (e) {
      toast.error(e.message || '刪除失敗')
    }
    setMenuFor(null)
  }

  // ---- drag & drop reorder ----
  function onDrop(target, asChild) {
    if (dragId == null || dragId === target.id) { setDragId(null); return }
    // Build reorder payload. Constraint: max two levels (FR-31/§6.3).
    const flat = []
    chapters.forEach((top) => {
      flat.push({ id: top.id, parent_id: null, order_index: 0 })
      ;(top.children || []).forEach((kid) => flat.push({ id: kid.id, parent_id: top.id, order_index: 0 }))
    })
    const dragged = flat.find((f) => f.id === dragId)
    if (!dragged) { setDragId(null); return }

    // dragging an item that HAS children cannot become a child (would be 3rd level)
    const draggedTop = chapters.find((c) => c.id === dragId)
    if (asChild && draggedTop && draggedTop.children?.length) {
      toast.error('最多支援兩層結構')
      setDragId(null)
      return
    }

    const newParent = asChild ? target.id : target.parent_id
    // target's parent having a parent => would be 3rd level
    if (asChild && target.parent_id != null) {
      toast.error('最多支援兩層結構')
      setDragId(null)
      return
    }
    dragged.parent_id = newParent

    // recompute order within each parent group, placing dragged near target
    const groups = {}
    flat.forEach((f) => { (groups[f.parent_id] = groups[f.parent_id] || []).push(f) })
    const items = []
    Object.entries(groups).forEach(([, arr]) => {
      arr.forEach((f, i) => items.push({ id: f.id, parent_id: f.parent_id, order_index: i }))
    })

    api.reorderChapters(bookId, items)
      .then(() => onReload())
      .catch((e) => toast.error(e.message || '排序失敗'))
    setDragId(null)
  }

  function renderNode(ch, level) {
    const isSel = ch.id === selectedId
    const coEditor = coEditingMap?.[ch.id]
    const hasChildren = ch.children?.length > 0
    const isCollapsed = collapsed.has(ch.id)
    return (
      <div key={ch.id}>
        <div
          role="treeitem"
          aria-level={level}
          aria-selected={isSel}
          aria-expanded={hasChildren ? !isCollapsed : undefined}
          draggable={canEdit && renaming !== ch.id}
          onDragStart={() => setDragId(ch.id)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => onDrop(ch, false)}
          className="tree-item"
          style={{
            ...treeItem,
            paddingLeft: 8 + level * 16,
            background: isSel ? 'var(--bg-subtle)' : 'transparent',
            borderLeft: isSel ? '3px solid var(--brand-primary)' : '3px solid transparent',
            fontWeight: isSel ? 600 : 400,
            opacity: dragId === ch.id ? 0.4 : 1,
          }}
          onClick={() => renaming !== ch.id && onSelect(ch.id)}
          onDoubleClick={() => canEdit && startRename(ch.id, ch.title)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') onSelect(ch.id)
            if (e.key === 'F2' && canEdit) startRename(ch.id, ch.title)
            if (hasChildren && e.key === 'ArrowRight' && isCollapsed) { e.preventDefault(); toggleCollapse(ch.id) }
            if (hasChildren && e.key === 'ArrowLeft' && !isCollapsed) { e.preventDefault(); toggleCollapse(ch.id) }
          }}
          tabIndex={0}
        >
          {hasChildren ? (
            <button
              className="tree-toggle"
              aria-label={isCollapsed ? '展開' : '收合'}
              aria-expanded={!isCollapsed}
              onClick={(e) => { e.stopPropagation(); toggleCollapse(ch.id) }}
            >
              {isCollapsed ? '▸' : '▾'}
            </button>
          ) : (
            <span className="tree-toggle-spacer" />
          )}
          <span className={`status-dot status-${ch.status}`} title={STATUS_TEXT[ch.status]} />
          {renaming === ch.id ? (
            <input autoFocus className="input" style={{ padding: '2px 6px', height: 26 }}
              value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
              onBlur={() => commitRename(ch.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename(ch.id)
                if (e.key === 'Escape') setRenaming(null)
              }} />
          ) : (
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {ch.title}
            </span>
          )}
          {coEditor && <span className="badge" style={{ background: 'var(--warning)', color: '#3a2c00', fontSize: 11 }} title={`${coEditor} 編輯中`}>✎</span>}
          {canEdit && renaming !== ch.id && (
            <button className="tree-menu-btn" onClick={(e) => { e.stopPropagation(); setMenuFor(menuFor === ch.id ? null : ch.id) }} aria-label="章節選單">⋮</button>
          )}
        </div>

        {menuFor === ch.id && (
          <div className="card" style={menuStyle} onMouseLeave={() => setMenuFor(null)}>
            {level === 1 && <MenuItem onClick={() => { addChapter(ch.id); setMenuFor(null) }}>＋ 新增子節</MenuItem>}
            <MenuItem onClick={() => startRename(ch.id, ch.title)}>改名</MenuItem>
            <MenuItem onClick={() => cycleStatus(ch)}>切換狀態（{STATUS_TEXT[ch.status]}）</MenuItem>
            <MenuItem danger onClick={() => removeChapter(ch)}>刪除</MenuItem>
          </div>
        )}

        {/* child drop zone */}
        {level === 1 && !isCollapsed && (
          <div onDragOver={(e) => e.preventDefault()} onDrop={() => onDrop(ch, true)}>
            {(ch.children || []).map((kid) => renderNode(kid, 2))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div role="tree" aria-label="章節樹">
      {chapters.length === 0 ? (
        <div className="empty-state" style={{ padding: '32px 16px' }}>
          <p className="text-sm">尚無章節</p>
          {canEdit && <button className="btn btn-primary btn-sm" onClick={() => addChapter(null)}>＋ 新增第一章</button>}
        </div>
      ) : (
        chapters.map((ch) => renderNode(ch, 1))
      )}
      {canEdit && chapters.length > 0 && (
        <button className="btn btn-ghost btn-sm" style={{ width: '100%', marginTop: 12 }}
          onClick={() => addChapter(null)}>＋ 新增章節</button>
      )}
      <div className="text-xs muted" style={{ marginTop: 16, lineHeight: 1.8 }}>
        <span className="status-dot status-done" />完成
        <span className="status-dot status-writing" style={{ marginLeft: 10 }} />撰寫中
        <span className="status-dot status-reviewing" style={{ marginLeft: 10 }} />待審
        <span className="status-dot status-not_started" style={{ marginLeft: 10 }} />未開始
      </div>
    </div>
  )
}

function MenuItem({ children, onClick, danger }) {
  return (
    <button className="btn btn-ghost btn-sm"
      style={{ width: '100%', justifyContent: 'flex-start', color: danger ? 'var(--danger)' : undefined }}
      onClick={onClick}>{children}</button>
  )
}

const treeItem = { display: 'flex', alignItems: 'center', gap: 6, padding: '7px 8px', borderRadius: 6, cursor: 'pointer', userSelect: 'none' }
const menuStyle = { padding: 6, margin: '2px 0 6px 24px', boxShadow: 'var(--shadow-md)', position: 'relative', zIndex: 20 }
