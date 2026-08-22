// 后端 API 统一封装。
import { getToken, http, readSse, safeText } from './http'

// ---- 认证 ----
export const authApi = {
  register: (data) => http.post('/api/auth/register', data),
  login: (data) => http.post('/api/auth/login', data),
  logout: () => http.post('/api/auth/logout'),
  me: () => http.get('/api/auth/me'),
  updateMe: (data) => http.put('/api/auth/me', data),
}

// ---- 会话 / 聊天 ----
export const sessionApi = {
  start: (data) => http.post('/api/session/start', data),
  get: () => http.get('/api/session'),
  reset: () => http.post('/api/session/reset'),
  history: () => http.get('/api/session/history'),
}

/**
 * SSE 流式聊天。
 * @param {string} message
 * @param {{onDelta?: (s:string)=>void, onDone?: (ev:object)=>void, onError?: (s:string)=>void}} handlers
 */
export async function chatStream(message, handlers = {}) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify({ message }),
  })
  if (!res.ok || !res.body) {
    handlers.onError?.(await safeText(res))
    return
  }
  await readSse(
    res.body.getReader(),
    (ev) => {
      if (ev.type === 'delta') handlers.onDelta?.(ev.content)
      else if (ev.type === 'done') handlers.onDone?.(ev)
      else if (ev.type === 'error') handlers.onError?.(ev.message)
    },
    () => {},
    (e) => handlers.onError?.(String(e)),
  )
}

// ---- 题库 / 收藏 ----
export const bankApi = {
  browse: (params) => http.get('/api/questions', { params }),
  meta: () => http.get('/api/questions/meta'),
  add: (data) => http.post('/api/questions', data),
  importCsv: (content) => http.post('/api/questions/import', { content }),
  favoriteIds: () => http.get('/api/favorites'),
  addFavorite: (qid) => http.post(`/api/favorites/${qid}`),
  removeFavorite: (qid) => http.delete(`/api/favorites/${qid}`),
}

// ---- 定制面试 ----
export const customApi = {
  /** SSE 生成定制面试题 */
  generate(jobTitle, jd, handlers = {}) {
    return (async () => {
      const res = await fetch('/api/custom/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken()}` },
        body: JSON.stringify({ job_title: jobTitle, jd }),
      })
      if (!res.ok || !res.body) {
        handlers.onError?.(await safeText(res))
        return
      }
      await readSse(
        res.body.getReader(),
        (ev) => {
          if (ev.type === 'progress') handlers.onProgress?.(ev.message)
          else if (ev.type === 'done') handlers.onDone?.(ev)
          else if (ev.type === 'error') handlers.onError?.(ev.message)
        },
        () => {},
        (e) => handlers.onError?.(String(e)),
      )
    })()
  },
  status: () => http.get('/api/custom/status'),
  clear: () => http.delete('/api/custom'),
}

// ---- 语音页运行时配置 ----
export const configApi = {
  voice: () => http.get('/api/config/voice'),
}
