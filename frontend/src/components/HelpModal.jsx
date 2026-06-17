import Modal from './Modal'
import reviewerMd from '../guides/reviewer.md?raw'
import { renderMarkdown } from '../utils/markdown'

// Floating help window — renders the usage guide in a modal (no page navigation).
export default function HelpModal({ onClose }) {
  return (
    <Modal title="使用說明" onClose={onClose} wide
      footer={
        <>
          <a className="btn btn-ghost" href="/guide/reviewer" target="_blank" rel="noopener">在新分頁開啟</a>
          <button className="btn btn-primary" onClick={onClose}>關閉</button>
        </>
      }>
      <article className="guide" style={{ fontFamily: 'var(--font-reading)', lineHeight: 1.8 }}
        dangerouslySetInnerHTML={{ __html: renderMarkdown(reviewerMd) }} />
    </Modal>
  )
}
