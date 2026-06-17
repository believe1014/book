import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './store/auth'
import Toaster from './components/Toaster'
import AuthPage from './pages/AuthPage'
import Bookshelf from './pages/Bookshelf'
import Editor from './pages/Editor'
import InviteLanding from './pages/InviteLanding'
import GuidePage from './pages/GuidePage'

function RequireAuth({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <FullLoader />
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}

function FullLoader() {
  return (
    <div style={{ display: 'grid', placeItems: 'center', height: '100vh', color: 'var(--text-muted)' }}>
      載入中…
    </div>
  )
}

export default function App() {
  const init = useAuth((s) => s.init)
  useEffect(() => { init() }, [init])

  return (
    <>
      <Routes>
        <Route path="/login" element={<AuthPage mode="login" />} />
        <Route path="/register" element={<AuthPage mode="register" />} />
        <Route path="/invite/:token" element={<InviteLanding />} />
        <Route path="/guide/reviewer" element={<GuidePage which="reviewer" />} />
        <Route path="/" element={<RequireAuth><Bookshelf /></RequireAuth>} />
        <Route path="/books/:id" element={<RequireAuth><Editor /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  )
}
