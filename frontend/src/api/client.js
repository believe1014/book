// API client for 協作撰書系統 backend (spec §5).
// Unwraps the { data } / { error } envelope and throws ApiError on failure.

const TOKEN_KEY = 'book_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function request(method, path, body, isForm = false) {
  const headers = {}
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  let payload
  if (isForm) {
    payload = body // FormData
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const resp = await fetch(`/api${path}`, { method, headers, body: payload })
  let json = null
  try {
    json = await resp.json()
  } catch {
    /* no body */
  }

  if (!resp.ok) {
    const err = json?.error || {}
    throw new ApiError(err.code || 'INTERNAL_ERROR', err.message || '發生錯誤', resp.status)
  }
  return json?.data
}

export const api = {
  // Auth
  register: (b) => request('POST', '/auth/register', b),
  login: (b) => request('POST', '/auth/login', b),
  me: () => request('GET', '/auth/me'),

  // Books
  listBooks: (q = '') => request('GET', `/books${q}`),
  createBook: (b) => request('POST', '/books', b),
  getBook: (id) => request('GET', `/books/${id}`),
  updateBook: (id, b) => request('PATCH', `/books/${id}`, b),
  deleteBook: (id) => request('DELETE', `/books/${id}`),
  restoreBook: (id) => request('POST', `/books/${id}/restore`),
  trash: () => request('GET', '/books/trash'),

  // Members
  listMembers: (id) => request('GET', `/books/${id}/members`),
  inviteMember: (id, b) => request('POST', `/books/${id}/members`, b),
  updateRole: (id, uid, b) => request('PATCH', `/books/${id}/members/${uid}`, b),
  removeMember: (id, uid) => request('DELETE', `/books/${id}/members/${uid}`),
  acceptInvite: (token) => request('POST', '/invitations/accept', { token }),

  // Chapters
  listChapters: (id) => request('GET', `/books/${id}/chapters`),
  createChapter: (id, b) => request('POST', `/books/${id}/chapters`, b),
  updateChapter: (cid, b) => request('PATCH', `/chapters/${cid}`, b),
  reorderChapters: (id, items) => request('PATCH', `/books/${id}/chapters/reorder`, items),
  deleteChapter: (cid) => request('DELETE', `/chapters/${cid}`),

  // Content
  getContent: (cid) => request('GET', `/chapters/${cid}/content`),
  patchContent: (cid, b) => request('PATCH', `/chapters/${cid}/content`, b),
  acquireLock: (cid) => request('POST', `/chapters/${cid}/lock`),
  releaseLock: (cid) => request('DELETE', `/chapters/${cid}/lock`),

  // Versions
  listVersions: (cid, page = 1) => request('GET', `/chapters/${cid}/versions?page=${page}`),
  getVersion: (cid, v) => request('GET', `/chapters/${cid}/versions/${v}`),
  restoreVersion: (cid, v) => request('POST', `/chapters/${cid}/versions/${v}/restore`),

  // Stats
  bookStats: (id) => request('GET', `/books/${id}/stats`),
  chapterStats: (cid) => request('GET', `/chapters/${cid}/stats`),

  // Comments (review)
  listComments: (cid) => request('GET', `/chapters/${cid}/comments`),
  createComment: (cid, b) => request('POST', `/chapters/${cid}/comments`, b),
  updateComment: (id, b) => request('PATCH', `/comments/${id}`, b),
  deleteComment: (id) => request('DELETE', `/comments/${id}`),
  resolveComment: (id) => request('POST', `/comments/${id}/resolve`),
  unresolveComment: (id) => request('DELETE', `/comments/${id}/resolve`),

  // Media
  listMedia: (id, q = '') => request('GET', `/books/${id}/media${q}`),
  uploadMedia: (id, form) => request('POST', `/books/${id}/media`, form, true),
  refMedia: (assetId) => request('POST', `/media/${assetId}/ref`),
  deleteMedia: (assetId) => request('DELETE', `/media/${assetId}`),
}
