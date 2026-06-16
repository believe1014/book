import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../store/auth'
import { api } from '../api/client'
import { toast } from '../store/toast'

// S1 login / register (spec FR-01/02, design.md §7 S1).
export default function AuthPage({ mode }) {
  const isLogin = mode === 'login'
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const inviteToken = params.get('invite')
  const { login, register } = useAuth()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      if (isLogin) await login(email, password)
      else await register(email, password, name)

      // If arrived via invite link, accept it then go to that book.
      if (inviteToken) {
        try {
          const { book_id } = await api.acceptInvite(inviteToken)
          toast.success('已加入書籍')
          navigate(`/books/${book_id}`)
          return
        } catch {
          /* fall through to bookshelf */
        }
      }
      navigate('/')
    } catch (e2) {
      setErr(e2.message || '操作失敗')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <div className="card" style={{ width: '100%', maxWidth: 400, padding: 32 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <div style={{ fontSize: 28, marginBottom: 4 }}>📚</div>
          <h1 style={{ fontSize: 22, margin: 0 }}>協作撰書系統</h1>
          <p className="muted text-sm" style={{ marginTop: 6 }}>
            {isLogin ? '登入以繼續你的寫作' : '建立帳號，開始你的第一本書'}
          </p>
        </div>

        <form onSubmit={submit}>
          {!isLogin && (
            <div className="field">
              <label htmlFor="name">顯示名稱</label>
              <input id="name" className="input" value={name}
                onChange={(e) => setName(e.target.value)} required maxLength={100} />
            </div>
          )}
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" className="input" value={email}
              onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </div>
          <div className="field">
            <label htmlFor="password">密碼</label>
            <div className="input-with-icon">
              <input id="password" type={showPassword ? 'text' : 'password'} className="input" value={password}
                onChange={(e) => setPassword(e.target.value)} required minLength={6}
                autoComplete={isLogin ? 'current-password' : 'new-password'} />
              <button type="button" className="input-icon-btn"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? '隱藏密碼' : '顯示密碼'}
                aria-pressed={showPassword}
                title={showPassword ? '隱藏密碼' : '顯示密碼'}>
                {showPassword ? '🙈' : '👁'}
              </button>
            </div>
            {!isLogin && <div className="field-hint">至少 6 個字元</div>}
          </div>

          {err && <div className="field"><div className="error" role="alert">{err}</div></div>}

          <button className="btn btn-primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? '處理中…' : isLogin ? '登入' : '註冊'}
          </button>
        </form>

        <p className="text-sm" style={{ textAlign: 'center', marginTop: 20 }}>
          {isLogin ? (
            <>還沒有帳號？ <Link to={`/register${inviteToken ? `?invite=${inviteToken}` : ''}`}>註冊</Link></>
          ) : (
            <>已有帳號？ <Link to={`/login${inviteToken ? `?invite=${inviteToken}` : ''}`}>登入</Link></>
          )}
        </p>
      </div>
    </div>
  )
}
