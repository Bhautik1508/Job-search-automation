import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

/**
 * SchedulerIndicator — small inline badge showing when the background worker
 * last ran. Helps users see whether scheduled scrape/score is alive without
 * clicking anything.
 *
 * Hidden entirely if we've never scraped (keeps fresh installs uncluttered).
 */
export default function SchedulerIndicator() {
  const [status, setStatus] = useState(null)

  useEffect(() => {
    let cancelled = false
    const load = () =>
      apiFetch('/api/scheduler/status')
        .then((resp) => (resp.ok ? resp.json() : null))
        .then((data) => !cancelled && setStatus(data))
        .catch(() => {})
    load()
    const interval = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  if (!status || !status.last_scrape_at) return null

  const scrapeAge = formatAge(status.last_scrape_at)
  const scoreAge = status.last_score_at ? formatAge(status.last_score_at) : null
  const scrapeStale = isStale(status.last_scrape_at, 8)

  return (
    <div
      className="scheduler-indicator"
      id="scheduler-indicator"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '4px 10px',
        borderRadius: 999,
        fontSize: '0.72rem',
        background: scrapeStale ? 'rgba(244,63,94,0.12)' : 'rgba(16,185,129,0.12)',
        color: scrapeStale ? '#f43f5e' : '#10b981',
        marginLeft: 8,
      }}
      title={
        `Last scrape: ${scrapeAge}` +
        (status.last_scrape_new_jobs != null
          ? ` · ${status.last_scrape_new_jobs} new jobs`
          : '') +
        (scoreAge ? `\nLast score: ${scoreAge}` : '') +
        `\nScored in last 24h: ${status.scored_jobs_last_24h}`
      }
    >
      <span>{scrapeStale ? '⚠️' : '🟢'}</span>
      <span>Worker · scraped {scrapeAge}</span>
    </div>
  )
}

function formatAge(iso) {
  const then = new Date(iso).getTime()
  const diffMs = Date.now() - then
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function isStale(iso, thresholdHours) {
  const diffH = (Date.now() - new Date(iso).getTime()) / 3_600_000
  return diffH > thresholdHours
}
