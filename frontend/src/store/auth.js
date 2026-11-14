// Auth store (Zustand). Holds current user + token (spec FR-01~03).
// believe 中央登入(SSO):後端 /api/auth/config 說 sso 開啟時,以 /api/auth/sso 換 JWT。
import { create } from 'zustand'
import { api, getToken, setToken } from '../api/client'

export const useAuth = create((set, get) => ({
  user: null,
  loading: true,
  accountUrl: null, // 非 null = SSO 開啟

  async init() {
    try {
      const cfg = await api.authConfig()
      if (cfg?.sso) set({ accountUrl: cfg.account_url })
    } catch { /* 舊後端或網路錯 → 走舊流程 */ }
    if (await get().syncSso()) return
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

  // 回 true = SSO 已決定登入狀態;false = SSO 未開或暫時失敗,沿用本地 JWT。
  async syncSso() {
    if (!get().accountUrl) return false
    try {
      const { user, token } = await api.sso()
      const prev = get().user
      setToken(token)
      set({ user, loading: false })
      // 中央換了人 → 回首頁重載,避免殘留前一人的畫面
      if (prev && prev.id !== user.id) window.location.assign('/')
      return true
    } catch (e) {
      if (e.status === 401) {
        setToken(null) // 中央已登出 → 這裡也登出
        set({ user: null, loading: false })
        return true
      }
      if (e.status === 404) set({ accountUrl: null }) // SSO 已關
      return false
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
    const acct = get().accountUrl
    if (acct) {
      window.location.assign(`${acct}/logout?next=${encodeURIComponent(window.location.origin + '/')}`)
      return true
    }
    return false
  },
}))
