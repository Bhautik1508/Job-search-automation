import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api'

/**
 * ConnectionsPanel — Phase R4 warm-referral layer.
 *
 * Lists warm connections at this job's company (fuzzy-matched by name) and
 * lets the user drop a "Draft Intro Ask" message addressed to the warm peer,
 * asking them to introduce the candidate to the currently-selected hiring
 * manager. Also exposes a CSV import for first-time setup.
 */
export default function ConnectionsPanel({ jobId, targetContactId, onDraftCreated }) {
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [info, setInfo] = useState(null)
  const [importing, setImporting] = useState(false)
  const [drafting, setDrafting] = useState(null) // connection id being drafted
  const fileInputRef = useRef(null)

  const load = useCallback(async () => {
    if (!jobId) return
    setLoading(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/connections`)
      if (resp.ok) {
        const data = await resp.json()
        setConnections(data.connections || [])
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

  const handleImport = async (file) => {
    setImporting(true)
    setError(null)
    setInfo(null)
    try {
      const csv = await file.text()
      const source = file.name.toLowerCase().includes('linkedin') ? 'linkedin' : 'csv'
      const resp = await apiFetch('/api/connections/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv, source }),
      })
      if (!resp.ok) {
        const t = await resp.text().catch(() => '')
        setError(resp.status === 401 || resp.status === 403
          ? 'Unauthorized — check VITE_API_KEY'
          : `Import failed: HTTP ${resp.status}${t ? `: ${t.slice(0, 160)}` : ''}`)
        return
      }
      const data = await resp.json()
      setInfo(
        `Imported ${data.imported} new, updated ${data.updated}` +
        (data.skipped ? `, skipped ${data.skipped}` : '') +
        ` · ${data.total_connections} total`,
      )
      await load()
    } catch (err) {
      setError(err.message || 'Network error')
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDraftIntro = async (connectionId) => {
    if (!targetContactId) {
      setError('Pick a hiring-manager contact above first — that\'s who the intro is for.')
      return
    }
    setDrafting(connectionId)
    setError(null)
    setInfo(null)
    try {
      const resp = await apiFetch('/api/outreach/referral-ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: jobId,
          connection_id: connectionId,
          target_contact_id: targetContactId,
          tone: 'peer-pm',
        }),
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
      setInfo('Intro ask drafted — see Outreach Drafts below.')
      onDraftCreated?.()
    } catch (err) {
      setError(err.message || 'Network error')
    } finally {
      setDrafting(null)
    }
  }

  return (
    <div className="modal__section" id="connections-panel">
      <div className="modal__section-header">
        <h3 className="modal__section-title">🤝 Warm Connections</h3>
        <div className="connections__actions">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,text/csv"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) handleImport(f)
            }}
          />
          <button
            className="panel__btn panel__btn--secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            id="btn-import-connections"
          >
            {importing ? <><span className="spinner" /> Importing…</> : '📥 Import CSV'}
          </button>
        </div>
      </div>

      {error && <div className="panel__error">⚠️ {error}</div>}
      {info && <div className="panel__info">ℹ️ {info}</div>}

      {loading && !connections.length && (
        <div className="panel__empty">Loading connections…</div>
      )}

      {!loading && !connections.length && !error && (
        <div className="panel__empty">
          No warm connections at this company yet. Import your LinkedIn / Happenstance
          CSV to populate the list.
        </div>
      )}

      {connections.length > 0 && (
        <ul className="connection-list">
          {connections.map((c) => (
            <li key={c.id} className="connection-item" id={`connection-${c.id}`}>
              <div className="connection-item__main">
                <span className="connection-item__name">{c.name}</span>
                {c.source && (
                  <span className="badge badge--source">{c.source}</span>
                )}
              </div>
              {c.current_title && (
                <div className="connection-item__title">{c.current_title}</div>
              )}
              <div className="connection-item__meta">
                <span>{c.company}</span>
                {c.linkedin_url && (
                  <>
                    {' · '}
                    <a
                      href={c.linkedin_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      LinkedIn ↗
                    </a>
                  </>
                )}
              </div>
              <div className="connection-item__actions">
                <button
                  className="panel__btn panel__btn--primary"
                  onClick={() => handleDraftIntro(c.id)}
                  disabled={drafting === c.id || !targetContactId}
                  title={
                    !targetContactId
                      ? 'Select a hiring-manager contact above first'
                      : `Ask ${c.name} for an intro`
                  }
                >
                  {drafting === c.id ? 'Drafting…' : '✉️ Draft Intro Ask'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
