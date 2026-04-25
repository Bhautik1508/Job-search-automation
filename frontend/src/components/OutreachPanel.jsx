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

  const patchDraft = async (draftId, payload) => {
    try {
      const resp = await apiFetch(`/api/outreach/${draftId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (resp.ok) {
        const updated = await resp.json()
        setDrafts((prev) => prev.map((d) => (d.id === updated.id ? updated : d)))
        return updated
      }
      setError(`Update failed: HTTP ${resp.status}`)
    } catch (err) {
      setError(err.message || 'Network error')
    }
    return null
  }

  return (
    <div className="modal__section" id="outreach-panel">
      <h3 className="modal__section-title">Outreach Drafts</h3>

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
          {generating ? 'Generating…' : 'Generate Draft'}
        </button>
      </div>

      {error && <div className="panel__error">{error}</div>}

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
            <DraftItem
              key={d.id}
              draft={d}
              onPatch={(payload) => patchDraft(d.id, payload)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function DraftItem({ draft, onPatch }) {
  const [editing, setEditing] = useState(false)
  const [body, setBody] = useState(draft.body)
  const [subject, setSubject] = useState(draft.subject || '')
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    setBody(draft.body)
    setSubject(draft.subject || '')
  }, [draft.id, draft.body, draft.subject])

  const dirty = body !== draft.body || subject !== (draft.subject || '')

  const handleSave = async () => {
    if (!dirty) {
      setEditing(false)
      return
    }
    setSaving(true)
    const payload = {}
    if (body !== draft.body) payload.body = body
    if (subject !== (draft.subject || '')) payload.subject = subject
    const updated = await onPatch(payload)
    setSaving(false)
    if (updated) setEditing(false)
  }

  const handleCancel = () => {
    setBody(draft.body)
    setSubject(draft.subject || '')
    setEditing(false)
  }

  const handleCopy = async () => {
    const text = subject ? `${subject}\n\n${body}` : body
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch {
      /* ignore */
    }
  }

  const isEmail = draft.channel === 'email'
  const link = draft.case_study_link
  const attachment = draft.case_study_attachment

  return (
    <li className="draft-item" id={`draft-${draft.id}`}>
      <div className="draft-item__header">
        <div className="draft-item__tags">
          <span className="badge badge--channel">{formatChannel(draft.channel)}</span>
          <span className="badge badge--tone">{draft.tone}</span>
          <span className={`badge badge--status-${draft.status}`}>{draft.status}</span>
        </div>
        <div className="draft-item__actions">
          {!editing && (
            <button
              className="panel__btn panel__btn--ghost"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
          )}
          <button
            className="panel__btn panel__btn--ghost"
            onClick={handleCopy}
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
          <select
            className="draft-item__status-select"
            value={draft.status}
            onChange={(e) => onPatch({ status: e.target.value })}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </div>

      {editing ? (
        <>
          {(draft.subject !== null || subject) && (
            <input
              type="text"
              className="draft-item__subject-input"
              placeholder="Subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          )}
          <textarea
            className="draft-item__body-textarea"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={Math.max(6, body.split('\n').length + 1)}
          />
          <div className="draft-item__edit-actions">
            <button
              className="panel__btn panel__btn--primary"
              onClick={handleSave}
              disabled={saving || !dirty}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              className="panel__btn panel__btn--ghost"
              onClick={handleCancel}
              disabled={saving}
            >
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          {draft.subject && (
            <div className="draft-item__subject">
              <strong>Subject:</strong> {draft.subject}
            </div>
          )}
          <pre className="draft-item__body">{draft.body}</pre>
        </>
      )}

      {(link || attachment) && (
        <div className="draft-item__case-study">
          <span className="draft-item__case-study-label">
            Case study {isEmail ? '— include below' : '— attach when sending'}:
          </span>
          {link && (
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="draft-item__case-study-link"
            >
              {link}
            </a>
          )}
          {attachment && (
            <span className="draft-item__case-study-file">
              {attachment}
            </span>
          )}
        </div>
      )}

      {draft.model && (
        <div className="draft-item__meta">
          {draft.model} · updated {new Date(draft.updated_at).toLocaleString()}
        </div>
      )}
    </li>
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
