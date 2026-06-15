// Auth store (Zustand). Holds current user + token (spec FR-01~03).
import { create } from 'zustand'
import { api, getToken, setToken } from '../api/client'

export const useAuth = create((set) => ({
  user: null,
  loading: true,

  async init() {
    if (!getToken()) {
      set({ loading: false })
      return
    }
    try {
      const { user } = await api.me()
      set({ user, loading: false })
    } catch {
      setToken(null)
      set({ user: null, loading: false })
    }
  },

  async login(email, password) {
    const { user, token } = await api.login({ email, password })
    setToken(token)
    set({ user })
    return user
  },

  async register(email, password, name) {
    const { user, token } = await api.register({ email, password, name })
    setToken(token)
    set({ user })
    return user
  },

  logout() {
    setToken(null)
    set({ user: null })
  },
}))
