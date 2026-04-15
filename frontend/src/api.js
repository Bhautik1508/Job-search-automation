/**
 * API utility — all fetch calls go through here.
 *
 * In development: VITE_API_URL is empty, requests use Vite proxy (/api/...).
 * In production:  VITE_API_URL is the Render backend URL (https://xxx.onrender.com).
 */

const API_BASE = import.meta.env.VITE_API_URL || ''

export async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`
  const resp = await fetch(url, options)
  return resp
}
