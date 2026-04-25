import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'

const CHANNELS = [
  { value: 'linkedin_note', label: 'LinkedIn Note (≤200 chars)' },
  { value: 'linkedin_inmail', label: 'LinkedIn InMail' },
  { value: 'email', label: 'Cold Email' },
  { value: 'referral_ask', label: 'Referral Ask' },
]

const TONES = [
  { value: 'peer-pm', label: 'Peer PM' },
  { value: 'founder-pitch', label: 'Founder Pitch' },
  { value: 'recruiter-formal', label: 'Recruiter Formal' },
]

const STATUSES = ['draft', 'sent', 'replied']

/**
 * OutreachPanel — generate + manage outreach drafts for a (job, contact).
 * Channel/tone pickers drive POST /api/outreach/draft; existing drafts
 * render with inline status controls that hit PATCH /api/outreach/:id.
 */
export default function OutreachPanel({ jobId, contactId }) {
  const [drafts, setDrafts] = useState([])
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState(null)
  const [channel, setChannel] = useState('linkedin_note')
  const [tone, setTone] = useState('peer-pm')

  const load = useCallback(async () => {
    if (!jobId) return
    setLoading(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/outreach`)
      if (resp.ok) {
        const data = await resp.json()
        setDrafts(data.drafts || [])
      } else {
        setError(`HTTP ${resp.status}`)
      }
    } catch (err) {
      setError(err.message || 'Network error')
    } finally {
      setLoading(false)
    }
  }, [jobId])

  useEffect(() => { load() }, [load])

  const handleGenerate = async () => {
    if (!contactId) {
      setError('Select a contact first.')
      return
    }
    setGenerating(true)
    setError(null)
    try {
      const resp = await apiFetch('/api/outreach/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId, contact_id: contactId, channel, tone }),
      })
      if (!resp.ok) {
        const t = await resp.text().catch(() => '')
        const msg = resp.status === 503
          ? 'Gemini API key not configured on backend'
          : resp.status === 401 || resp.status === 403
            ? 'Unauthorized — check VITE_API_KEY'
            : `HTTP ${resp.status}${t ? `: ${t.slice(0, 180)}` : ''}`
        setError(msg)
        return
      }
      await load()
    } catch (err) {
      setError(err.message || 'Network error')
    } finally {
      setGenerating(false)
    }
  }

  const handleStatusChange = async (draftId, newStatus) => {
    try {
      const resp = await apiFetch(`/api/outreach/${draftId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      })
      if (resp.ok) {
        const updated = await resp.json()
        setDrafts((prev) => prev.map((d) => (d.id === updated.id ? updated : d)))
      } else {
        setError(`Status update failed: HTTP ${resp.status}`)
      }
    } catch (err) {
      setError(err.message || 'Network error')
    }
  }

  const copyToClipboard = async (text, draftId) => {
    try {
      await navigator.clipboard.writeText(text)
      const el = document.getElementById(`copied-${draftId}`)
      if (el) {
        el.textContent = '✓ Copied'
        setTimeout(() => { el.textContent = '📋 Copy' }, 1400)
      }
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="modal__section" id="outreach-panel">
      <h3 className="modal__section-title">✍️ Outreach Drafts</h3>

      <div className="outreach__controls">
        <label className="outreach__field">
          <span>Channel</span>
          <select
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            id="outreach-channel"
          >
            {CHANNELS.map((c) => (
              <option key={c.value} value={c.value}>{c.label}</option>
            ))}
          </select>
        </label>
        <label className="outreach__field">
          <span>Tone</span>
          <select
            value={tone}
            onChange={(e) => setTone(e.target.value)}
            id="outreach-tone"
          >
            {TONES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </label>
        <button
          className="panel__btn panel__btn--primary"
          onClick={handleGenerate}
          disabled={generating || !contactId}
          id="btn-generate-outreach"
        >
          {generating ? <><span className="spinner" /> Generating…</> : '⚡ Generate Draft'}
        </button>
      </div>

      {error && <div className="panel__error">⚠️ {error}</div>}

      {loading && !drafts.length && (
        <div className="panel__empty">Loading drafts…</div>
      )}

      {!loading && !drafts.length && !error && (
        <div className="panel__empty">
          No drafts yet. Pick a channel + tone and click "Generate Draft".
        </div>
      )}

      {drafts.length > 0 && (
        <ul className="draft-list">
          {drafts.map((d) => (
            <li key={d.id} className="draft-item" id={`draft-${d.id}`}>
              <div className="draft-item__header">
                <div className="draft-item__tags">
                  <span className="badge badge--channel">{formatChannel(d.channel)}</span>
                  <span className="badge badge--tone">{d.tone}</span>
                  <span className={`badge badge--status-${d.status}`}>{d.status}</span>
                </div>
                <div className="draft-item__actions">
                  <button
                    className="panel__btn panel__btn--ghost"
                    onClick={() => copyToClipboard(
                      d.subject ? `${d.subject}\n\n${d.body}` : d.body,
                      d.id,
                    )}
                    id={`copied-${d.id}`}
                  >
                    📋 Copy
                  </button>
                  <select
                    className="draft-item__status-select"
                    value={d.status}
                    onChange={(e) => handleStatusChange(d.id, e.target.value)}
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </div>
              </div>
              {d.subject && (
                <div className="draft-item__subject">
                  <strong>Subject:</strong> {d.subject}
                </div>
              )}
              <pre className="draft-item__body">{d.body}</pre>
              {d.model && (
                <div className="draft-item__meta">
                  {d.model} · updated {new Date(d.updated_at).toLocaleString()}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function formatChannel(ch) {
  const map = {
    linkedin_note: 'LinkedIn Note',
    linkedin_inmail: 'LinkedIn InMail',
    email: 'Email',
    referral_ask: 'Referral Ask',
  }
  return map[ch] || ch
}
