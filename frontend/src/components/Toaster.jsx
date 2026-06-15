import { useToast } from '../store/toast'

// Toast container (design.md §8: bottom-right, aria-live for a11y §12).
export default function Toaster() {
  const { toasts, dismiss } = useToast()
  return (
    <div className="toast-wrap" aria-live="polite" aria-atomic="false">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.type}`} role="status" onClick={() => dismiss(t.id)}>
          {t.message}
        </div>
      ))}
    </div>
  )
}
