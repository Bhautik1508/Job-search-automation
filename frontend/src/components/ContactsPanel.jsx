import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'

/**
 * ContactsPanel — lists contacts linked to a job and allows triggering
 * single-job enrichment. Notifies parent when the selected contact changes
 * so the outreach panel can target the right person.
 */
export default function ContactsPanel({ jobId, selectedContactId, onSelectContact }) {
  const [contacts, setContacts] = useState([])
  const [loading, setLoading] = useState(false)
  const [enriching, setEnriching] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    if (!jobId) return
    setLoading(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/contacts`)
      if (resp.ok) {
        const data = await resp.json()
        setContacts(data.contacts || [])
        if (!selectedContactId && data.contacts?.length) {
          onSelectContact(data.contacts[0].id)
        }
      } else {
        setError(`HTTP ${resp.status}`)
      }
    } catch (err) {
      setError(err.message || 'Network error')
    } finally {
      setLoading(false)
    }
  }, [jobId, selectedContactId, onSelectContact])

  useEffect(() => { load() }, [load])

  const handleEnrich = async () => {
    setEnriching(true)
    setError(null)
    try {
      const resp = await apiFetch(`/api/enrich-contacts?job_id=${jobId}`, {
        method: 'POST',
      })
      if (!resp.ok) {
        const t = await resp.text().catch(() => '')
        setError(resp.status === 401 || resp.status === 403
          ? 'Unauthorized — check VITE_API_KEY'
          : `HTTP ${resp.status}${t ? `: ${t.slice(0, 160)}` : ''}`)
        return
      }
      await load()
    } catch (err) {
      setError(err.message || 'Network error')
    } finally {
      setEnriching(false)
    }
  }

  return (
    <div className="modal__section" id="contacts-panel">
      <div className="modal__section-header">
        <h3 className="modal__section-title">👥 Contacts</h3>
        <button
          className="panel__btn panel__btn--secondary"
          onClick={handleEnrich}
          disabled={enriching}
          id="btn-enrich-contacts"
        >
          {enriching ? <><span className="spinner" /> Enriching…</> : '🔍 Find Contacts'}
        </button>
      </div>

      {error && <div className="panel__error">⚠️ {error}</div>}

      {loading && !contacts.length && (
        <div className="panel__empty">Loading contacts…</div>
      )}

      {!loading && !contacts.length && !error && (
        <div className="panel__empty">
          No contacts yet. Click "Find Contacts" to search Apollo + fallback providers.
        </div>
      )}

      {contacts.length > 0 && (
        <ul className="contact-list">
          {contacts.map((c) => (
            <li
              key={c.id}
              className={`contact-item ${selectedContactId === c.id ? 'contact-item--selected' : ''}`}
              onClick={() => onSelectContact(c.id)}
              id={`contact-${c.id}`}
            >
              <div className="contact-item__main">
                <span className="contact-item__name">{c.name}</span>
                <span className={`badge badge--role-${c.role_type}`}>
                  {c.role_type === 'hm' ? 'Hiring Manager' : 'Recruiter'}
                </span>
              </div>
              {c.title && <div className="contact-item__title">{c.title}</div>}
              <div className="contact-item__meta">
                {c.source_provider && <span>via {c.source_provider}</span>}
                {c.confidence != null && (
                  <span> · {(c.confidence * 100).toFixed(0)}% match</span>
                )}
                {c.linkedin_url && (
                  <>
                    {' · '}
                    <a
                      href={c.linkedin_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      LinkedIn ↗
                    </a>
                  </>
                )}
                {c.email && (
                  <>
                    {' · '}
                    <a
                      href={`mailto:${c.email}`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {c.email}
                    </a>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
