import { useEffect, useRef, useState } from 'react'
import { getToken } from '../api/client'

// WebSocket connection for a chapter room (spec §5.10, FR-50/51).
// Returns presence list, lock owner, and a handler for incoming content_updated.
export function useChapterSocket(chapterId, { onContentUpdated } = {}) {
  const [presence, setPresence] = useState([])
  const [lockOwner, setLockOwner] = useState(null)
  const [cursors, setCursors] = useState({}) // user_id -> {name, position}
  const wsRef = useRef(null)
  const pingRef = useRef(null)
  const cbRef = useRef(onContentUpdated)
  cbRef.current = onContentUpdated

  useEffect(() => {
    if (!chapterId) return
    const token = getToken()
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/chapters/${chapterId}?token=${token}`)
    wsRef.current = ws

    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      switch (msg.type) {
        case 'presence':
          setPresence(msg.users || [])
          break
        case 'lock_changed':
          setLockOwner(msg.lock_owner ?? null)
          break
        case 'content_updated':
          cbRef.current?.(msg.version)
          break
        case 'cursor':
          setCursors((c) => ({ ...c, [msg.user.user_id]: { name: msg.user.name, position: msg.position } }))
          break
        default:
          break
      }
    }

    // keep-alive ping refreshes lock idle timer (FR-45)
    pingRef.current = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }))
    }, 15000)

    return () => {
      clearInterval(pingRef.current)
      setPresence([]); setLockOwner(null); setCursors({})
      try { ws.close() } catch { /* noop */ }
    }
  }, [chapterId])

  function sendCursor(position) {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'cursor', position }))
    }
  }

  return { presence, lockOwner, cursors, sendCursor }
}
