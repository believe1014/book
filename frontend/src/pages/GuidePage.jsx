import { useNavigate } from 'react-router-dom'
import reviewerMd from '../guides/reviewer.md?raw'
import { renderMarkdown } from '../utils/markdown'

// In-app help pages. Renders a bundled Markdown guide with a tiny renderer so we
// don't pull in a Markdown dependency. Content is trusted (shipped in the build).
const GUIDES = {
  reviewer: { title: '審稿人員指南', md: reviewerMd },
}

export default function GuidePage({ which = 'reviewer' }) {
  const navigate = useNavigate()
  const guide = GUIDES[which] || GUIDES.reviewer
  return (
    <div style={page}>
      <div style={bar}>
        <button className="btn btn-ghost btn-sm" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/'))}>← 返回</button>
        <span className="text-sm muted">說明文件</span>
      </div>
      <article className="guide" style={article} dangerouslySetInnerHTML={{ __html: renderMarkdown(guide.md) }} />
    </div>
  )
}

const page = { minHeight: '100vh', background: 'var(--bg-canvas)' }
const bar = {
  position: 'sticky', top: 0, height: 'var(--topbar-h)', background: 'var(--bg-surface)',
  borderBottom: '1px solid var(--border-default)', display: 'flex', alignItems: 'center',
  justifyContent: 'space-between', padding: '0 20px',
}
const article = {
  maxWidth: 760, margin: '0 auto', padding: '28px 24px 80px',
  fontFamily: 'var(--font-reading)', fontSize: 16, lineHeight: 1.8, color: 'var(--text-default)',
}
