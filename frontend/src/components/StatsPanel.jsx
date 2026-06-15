import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { toast } from '../store/toast'

// Right column stats panel (design.md §7.2 ④⑤, S6b). Chapter & book views (FR-60~63).
export default function StatsPanel({ bookId, chapterId, canEdit, refreshKey }) {
  const [view, setView] = useState('chapter') // 'chapter' | 'book'
  const [chapterStats, setChapterStats] = useState(null)
  const [bookStats, setBookStats] = useState(null)
  const [editingGoal, setEditingGoal] = useState(false)
  const [goalVal, setGoalVal] = useState('')

  useEffect(() => {
    if (chapterId) {
      api.chapterStats(chapterId).then(setChapterStats).catch(() => setChapterStats(null))
    } else {
      setChapterStats(null)
    }
  }, [chapterId, refreshKey])

  useEffect(() => {
    api.bookStats(bookId).then(setBookStats).catch(() => setBookStats(null))
  }, [bookId, refreshKey])

  async function saveGoal() {
    const n = parseInt(goalVal, 10)
    setEditingGoal(false)
    if (isNaN(n) || n < 0) return
    try {
      await api.updateBook(bookId, { word_count_goal: n })
      const s = await api.bookStats(bookId)
      setBookStats(s)
      toast.success('已設定字數目標')
    } catch (e) {
      toast.error(e.message || '設定失敗')
    }
  }

  return (
    <div>
      <div className="row gap-2" style={{ marginBottom: 16 }}>
        <button className="btn btn-sm" style={view === 'chapter' ? activeTab : tab}
          onClick={() => setView('chapter')}>章節</button>
        <button className="btn btn-sm" style={view === 'book' ? activeTab : tab}
          onClick={() => setView('book')}>全書</button>
      </div>

      {view === 'chapter' ? (
        !chapterId ? (
          <p className="muted text-sm">選擇章節以檢視統計</p>
        ) : !chapterStats ? (
          <div className="skeleton" style={{ height: 80 }} />
        ) : (
          <div>
            <Stat label="章節字數" value={`${chapterStats.word_count.toLocaleString()} 字`} big />
            <Stat label="段落" value={chapterStats.paragraph_count} />
            <Stat label="預估閱讀" value={`~${chapterStats.reading_minutes} 分`} />
          </div>
        )
      ) : !bookStats ? (
        <div className="skeleton" style={{ height: 160 }} />
      ) : (
        <div>
          <Stat label="全書字數" value={`${bookStats.total_words.toLocaleString()} 字`} big />
          <Stat label="章節" value={`${bookStats.completed_count}/${bookStats.chapter_count} 完成`} />
          <div style={{ margin: '10px 0' }}>
            <div className="text-xs muted">進度 {Math.round(bookStats.progress * 100)}%</div>
            <div className="progress" style={{ marginTop: 4 }}>
              <span style={{ width: `${Math.round(bookStats.progress * 100)}%` }} />
            </div>
          </div>

          {/* Goal (FR-62) */}
          <div style={{ margin: '14px 0' }}>
            {bookStats.goal ? (
              <>
                <div className="text-xs muted">
                  目標 {bookStats.goal.toLocaleString()} 字 · 達成 {Math.round((bookStats.goal_rate || 0) * 100)}%
                  {canEdit && <button className="btn btn-ghost btn-sm" style={{ padding: '0 6px' }} onClick={() => { setEditingGoal(true); setGoalVal(String(bookStats.goal)) }}>改</button>}
                </div>
                <div className="progress" style={{ marginTop: 4 }}>
                  <span style={{ width: `${Math.min(100, Math.round((bookStats.goal_rate || 0) * 100))}%`, background: 'var(--success)' }} />
                </div>
              </>
            ) : editingGoal ? (
              <div className="row gap-2">
                <input className="input" type="number" value={goalVal} autoFocus
                  onChange={(e) => setGoalVal(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && saveGoal()} placeholder="字數目標" />
                <button className="btn btn-primary btn-sm" onClick={saveGoal}>設定</button>
              </div>
            ) : canEdit ? (
              <button className="btn btn-ghost btn-sm" onClick={() => setEditingGoal(true)}>設定字數目標</button>
            ) : null}
          </div>

          <Stat label="今日新增（你）" value={`+${bookStats.today_words.toLocaleString()} 字`} />

          {/* Contributors (FR-60) */}
          <div style={{ marginTop: 16 }}>
            <div className="text-xs muted" style={{ marginBottom: 6 }}>貢獻者</div>
            {bookStats.contributors.length === 0 ? (
              <p className="text-xs muted">尚無貢獻資料</p>
            ) : bookStats.contributors.map((c) => (
              <div key={c.user_id} style={{ marginBottom: 8 }}>
                <div className="spread text-xs">
                  <span>◎ {c.name}</span>
                  <span className="muted">{Math.round(c.ratio * 100)}% · {c.words.toLocaleString()}</span>
                </div>
                <div className="progress" style={{ marginTop: 3 }}>
                  <span style={{ width: `${Math.round(c.ratio * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, big }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="text-xs muted">{label}</div>
      <div style={{ fontSize: big ? 22 : 15, fontWeight: big ? 600 : 500 }}>{value}</div>
    </div>
  )
}

const tab = { background: 'var(--bg-subtle)', color: 'var(--text-muted)', flex: 1 }
const activeTab = { background: 'var(--brand-primary)', color: '#fff', flex: 1 }
