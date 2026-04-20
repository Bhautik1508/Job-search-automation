/**
 * API utility — all fetch calls go through here.
 *
 * In development: VITE_API_URL is empty, requests use Vite proxy (/api/...).
 * In production:  VITE_API_URL is the Render backend URL (https://xxx.onrender.com).
 *
 * Mutation endpoints require an X-API-Key header in production. The key is
 * injected from VITE_API_KEY at build time.
 */

const API_BASE = import.meta.env.VITE_API_URL || ''
const API_KEY = import.meta.env.VITE_API_KEY || ''

export async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`
  const headers = { ...(options.headers || {}) }
  if (API_KEY) headers['X-API-Key'] = API_KEY
  const resp = await fetch(url, { ...options, headers })
  return resp
}
