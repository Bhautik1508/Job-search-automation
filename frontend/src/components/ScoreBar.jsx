/**
 * ScoreBar — Horizontal bar showing 0–100 score with gradient color.
 */
export default function ScoreBar({ score, height = 6, showLabel = false }) {
  if (score == null) {
    return (
      <div className="score-bar-wrapper">
        <div className="score-bar" style={{ height }}>
          <div className="score-bar__empty" />
        </div>
        {showLabel && <span className="score-bar__label score-bar__label--na">N/A</span>}
      </div>
    )
  }

  const pct = Math.max(0, Math.min(100, score))
  const color = getScoreColor(pct)

  return (
    <div className="score-bar-wrapper">
      <div className="score-bar" style={{ height }}>
        <div
          className="score-bar__fill"
          style={{
            width: `${pct}%`,
            background: color,
          }}
        />
      </div>
      {showLabel && (
        <span className="score-bar__label" style={{ color }}>
          {pct.toFixed(1)}
        </span>
      )}
    </div>
  )
}

export function getScoreColor(score) {
  if (score >= 80) return 'var(--score-excellent)'
  if (score >= 65) return 'var(--score-good)'
  if (score >= 50) return 'var(--score-moderate)'
  if (score >= 35) return 'var(--score-low)'
  return 'var(--score-poor)'
}
