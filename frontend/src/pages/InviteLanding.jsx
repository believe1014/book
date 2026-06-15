import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../store/auth'
import { api } from '../api/client'
import { toast } from '../store/toast'

// S2 invite landing (spec FR-21, design.md §7 S2).
// If not logged in, redirect to register carrying the invite token.
export default function InviteLanding() {
  const { token } = useParams()
  const navigate = useNavigate()
  const { user, loading } = useAuth()
  const [msg, setMsg] = useState('處理邀請中…')

  useEffect(() => {
    if (loading) return
    if (!user) {
      navigate(`/register?invite=${token}`, { replace: true })
      return
    }
    api.acceptInvite(token)
      .then(({ book_id }) => {
        toast.success('已加入書籍')
        navigate(`/books/${book_id}`, { replace: true })
      })
      .catch((e) => {
        setMsg(e.message || '邀請無效')
        toast.error(e.message || '邀請無效')
        setTimeout(() => navigate('/', { replace: true }), 1500)
      })
  }, [user, loading, token, navigate])

  return (
    <div style={{ display: 'grid', placeItems: 'center', height: '100vh', color: 'var(--text-muted)' }}>
      {msg}
    </div>
  )
}
