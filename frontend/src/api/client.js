// Тонкая обёртка над REST API (§4). Базовый URL — из env, по умолчанию '' (dev-прокси Vite).
const BASE = import.meta.env.VITE_API_BASE || ''

async function _fail(res) {
  let detail = res.statusText
  try {
    const body = await res.json()
    detail = body.detail || detail
  } catch (_) { /* ignore */ }
  const err = new Error(detail)
  err.status = res.status
  return err
}

async function get(path, params) {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
    }
  }
  const res = await fetch(url.pathname + url.search)
  if (!res.ok) throw await _fail(res)
  return res.json()
}

async function post(path, body) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  if (!res.ok) throw await _fail(res)
  return res.json()
}

export const api = {
  // sources
  sources: (params) => get('/api/sources', params),
  source: (id) => get(`/api/sources/${id}`),
  toc: (id) => get(`/api/sources/${id}/toc`),
  page: (id, index) => get(`/api/sources/${id}/pages/${index}`),
  pdfUrl: (id) => `${BASE}/api/sources/${id}/pdf`,
  // search
  search: (params) => get('/api/search', params),
  // preparations
  preparations: (params) => get('/api/preparations', params),
  preparation: (id) => get(`/api/preparations/${id}`),
  // diseases
  diseases: (params) => get('/api/diseases', params),
  disease: (id) => get(`/api/diseases/${id}`),
  // health
  health: () => get('/api/health'),
  // chat
  chat: ({ message, history, conversation_id }) =>
    post('/api/chat', { message, history, conversation_id }),
  // conversations (история чатов)
  conversations: (params) => get('/api/conversations', params),
  conversation: (id) => get(`/api/conversations/${id}`),
  clearConversations: () => post('/api/conversations/clear', {}),
}
