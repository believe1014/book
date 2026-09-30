import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../api/client'

// MCP OAuth 同意頁(spec 2026-09-30-mcp-oauth-design §7)。RequireAuth 包住:到這裡一定已登入。
export default function OAuthConsent() {
  const [params] = useSearchParams()
  const req = params.get('req') || ''
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.oauthConsentInfo(req).then(setInfo).catch((e) => setErr(e.message || '授權請求無效'))
  }, [req])

  async function decide(approve) {
    setBusy(true)
    try {
      const { redirect_url } = await api.oauthConsent({ req, approve })
      window.location.assign(redirect_url)
    } catch (e) {
      setErr(e.message || '操作失敗')
      setBusy(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: '100%', maxWidth: 440, padding: 32 }}>
        <h1 style={{ fontSize: 20, marginTop: 0 }}>授權應用程式</h1>
        {err ? (
          <div className="error" role="alert">{err}</div>
        ) : !info ? (
          <p className="muted">載入中…</p>
        ) : (
          <>
            <p>
              <strong>{info.client_name}</strong> 要求以你的身分使用 kkbook MCP 工具(列書、讀寫章節)。
            </p>
            <p className="muted text-sm">授權後將轉往 <code>{info.redirect_origin}</code></p>
            <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
              <button className="btn btn-primary" disabled={busy} onClick={() => decide(true)}>同意</button>
              <button className="btn btn-ghost" disabled={busy} onClick={() => decide(false)}>拒絕</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
