import { useState, useEffect, useRef } from 'react'

/**
 * Animated counter that counts up from 0 to the target value.
 */
function AnimatedNumber({ value, duration = 800, decimals = 0 }) {
  const [display, setDisplay] = useState(0)
  const ref = useRef(null)

  useEffect(() => {
    if (value == null) return
    const start = 0
    const end = Number(value)
    const startTime = performance.now()

    function animate(now) {
      const elapsed = now - startTime
      const progress = Math.min(elapsed / duration, 1)
      // Ease-out cubic
      const eased = 1 - Math.pow(1 - progress, 3)
      setDisplay(start + (end - start) * eased)
      if (progress < 1) {
        ref.current = requestAnimationFrame(animate)
      }
    }

    ref.current = requestAnimationFrame(animate)
    return () => ref.current && cancelAnimationFrame(ref.current)
  }, [value, duration])

  return decimals > 0 ? display.toFixed(decimals) : Math.round(display)
}

const cards = [
  {
    key: 'total_jobs',
    label: 'Total Jobs',
    icon: '📋',
    gradient: 'var(--gradient-primary)',
    valueKey: 'total_jobs',
  },
  {
    key: 'avg_score',
    label: 'Avg. Score',
    icon: '🎯',
    gradient: 'var(--gradient-cyan)',
    valueKey: 'avg_score',
    decimals: 1,
    suffix: '/100',
  },
  {
    key: 'apply_now',
    label: 'Apply Now',
    icon: '🚀',
    gradient: 'var(--gradient-emerald)',
    valueKey: 'apply_now_count',
  },
  {
    key: 'applied',
    label: 'Applied',
    icon: '✅',
    gradient: 'var(--gradient-amber)',
    valueKey: 'applied_count',
  },
]

export default function KpiCards({ stats, loading }) {
  if (loading) {
    return (
      <div className="kpi-grid" id="kpi-cards">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="kpi-card skeleton" style={{ height: 130 }} />
        ))}
      </div>
    )
  }

  return (
    <div className="kpi-grid" id="kpi-cards">
      {cards.map((card, idx) => (
        <div
          key={card.key}
          className="kpi-card glass animate-in"
          style={{ animationDelay: `${idx * 80}ms` }}
          id={`kpi-${card.key}`}
        >
          <div className="kpi-card__icon-row">
            <span className="kpi-card__icon">{card.icon}</span>
            <span
              className="kpi-card__accent-dot"
              style={{ background: card.gradient }}
            />
          </div>
          <div className="kpi-card__value">
            <AnimatedNumber
              value={stats?.[card.valueKey] ?? 0}
              decimals={card.decimals || 0}
            />
            {card.suffix && (
              <span className="kpi-card__suffix">{card.suffix}</span>
            )}
          </div>
          <div className="kpi-card__label">{card.label}</div>

          {/* Mini sub-stats for specific cards */}
          {card.key === 'total_jobs' && stats && (
            <div className="kpi-card__sub">
              <span>{stats.scored_jobs} scored</span>
              <span className="kpi-card__sub-divider">·</span>
              <span>{stats.unscored_jobs} pending</span>
            </div>
          )}
          {card.key === 'avg_score' && stats && (
            <div className="kpi-card__sub">
              <span>Max: {stats.max_score}</span>
              <span className="kpi-card__sub-divider">·</span>
              <span>Min: {stats.min_score}</span>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
