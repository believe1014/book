// Toast notifications store (design.md §8 Toast component).
import { create } from 'zustand'

let nextId = 1

export const useToast = create((set, get) => ({
  toasts: [],
  push(message, type = 'info') {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts, { id, message, type }] }))
    setTimeout(() => get().dismiss(id), 4000) // auto-dismiss 4s (design.md §8)
  },
  dismiss(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
  },
}))

export const toast = {
  success: (m) => useToast.getState().push(m, 'success'),
  error: (m) => useToast.getState().push(m, 'error'),
  info: (m) => useToast.getState().push(m, 'info'),
}
