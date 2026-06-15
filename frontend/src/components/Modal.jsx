import { useEffect, useRef } from 'react'

// Accessible modal (design.md §8/§12: role=dialog, aria-modal, Esc, focus trap).
export default function Modal({ title, onClose, children, footer, wide = false }) {
  const ref = useRef(null)

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose?.()
    }
    document.addEventListener('keydown', onKey)
    // move focus into the dialog
    const first = ref.current?.querySelector('input, textarea, select, button')
    first?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div
        className="modal"
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={wide ? { maxWidth: 720 } : undefined}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="關閉">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

// Confirm dialog for destructive actions (design.md §8 ConfirmDialog).
export function ConfirmDialog({ title, message, confirmText = '確認', danger = true, onConfirm, onCancel }) {
  return (
    <Modal
      title={title}
      onClose={onCancel}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onCancel}>取消</button>
          <button className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`} onClick={onConfirm}>
            {confirmText}
          </button>
        </>
      }
    >
      <p style={{ lineHeight: 1.7, margin: 0 }}>{message}</p>
    </Modal>
  )
}
