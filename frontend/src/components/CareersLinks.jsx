import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

/**
 * CareersLinks — collapsible registry of direct careers pages, grouped by tier.
 *
 * Complements the scraped job list: even when the scrapers miss a company,
 * users can one-click into the official careers portal.
 */
export default function CareersLinks() {
  const [links, setLinks] = useState([])
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/companies/careers')
      .then((resp) => (resp.ok ? resp.json() : []))
      .then((data) => {
        if (!cancelled) {
          setLinks(data)
          setLoading(false)
        }
      })
      .catch(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  if (loading || links.length === 0) return null

  const grouped = links.reduce((acc, link) => {
    const tier = link.tier || 'other'
    if (!acc[tier]) acc[tier] = []
    acc[tier].push(link)
    return acc
  }, {})

  const tierOrder = ['top_tier', 'unicorn', 'growth_startup', 'early_startup', 'other']
  const tierLabels = {
    top_tier: '🌟 Top Tier',
    unicorn: '🦄 Unicorn',
    growth_startup: '📈 Growth Startup',
    early_startup: '🌱 Early Startup',
    other: '🏢 Other',
  }

  return (
    <div className="careers-links glass" id="careers-links">
      <button
        className="careers-links__toggle"
        onClick={() => setOpen((v) => !v)}
        id="careers-links-toggle"
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'transparent',
          border: 'none',
          color: 'var(--text-strong)',
          cursor: 'pointer',
          fontSize: '0.95rem',
          fontWeight: 600,
          padding: '14px 18px',
        }}
      >
        <span>🎯 Direct apply · {links.length} companies</span>
        <span>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div
          className="careers-links__body"
          style={{ padding: '0 18px 18px 18px', display: 'grid', gap: 14 }}
        >
          {tierOrder
            .filter((tier) => grouped[tier]?.length)
            .map((tier) => (
              <div key={tier}>
                <div
                  style={{
                    fontSize: '0.78rem',
                    letterSpacing: '0.04em',
                    textTransform: 'uppercase',
                    color: 'var(--text-muted)',
                    marginBottom: 6,
                  }}
                >
                  {tierLabels[tier]}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {grouped[tier].map((link) => (
                    <a
                      key={link.name}
                      href={link.careers_url}
                      target="_blank"
                      rel="noreferrer"
                      className="badge"
                      style={{
                        padding: '4px 10px',
                        background: 'rgba(99,102,241,0.12)',
                        color: 'var(--text-accent)',
                        textDecoration: 'none',
                        fontSize: '0.78rem',
                      }}
                    >
                      {link.name} ↗
                    </a>
                  ))}
                </div>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}
