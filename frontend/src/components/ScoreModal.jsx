import ScoreBar, { getScoreColor } from './ScoreBar'

/**
 * ScoreModal — Detailed score breakdown for a selected job.
 */
export default function ScoreModal({ job, onClose }) {
  if (!job) return null

  const scoreRows = [
    { label: 'Skills Match', value: job.skills_match_score, weight: '30%' },
    { label: 'Domain Fit', value: job.domain_fit_score, weight: '25%' },
    { label: 'Experience Match', value: job.experience_match_score, weight: '20%' },
    { label: 'Seniority Match', value: job.seniority_match_score, weight: '15%' },
    { label: 'Recency', value: job.recency_score, weight: '10%' },
  ]

  return (
    <div className="modal-overlay" id="score-modal-overlay" onClick={onClose}>
      <div
        className="modal glass animate-in"
        id="score-modal"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="modal__header">
          <div>
            <h2 className="modal__title">{job.title}</h2>
            <p className="modal__subtitle">{job.company} · {job.location || 'Remote'}</p>
          </div>
          <button className="modal__close" onClick={onClose} id="score-modal-close">
            ✕
          </button>
        </div>

        {/* Overall Score */}
        <div className="modal__score-hero">
          <div
            className="modal__score-circle"
            style={{
              borderColor: job.relevancy_score != null
                ? getScoreColor(job.relevancy_score)
                : 'var(--text-muted)',
            }}
          >
            <span className="modal__score-value">
              {job.relevancy_score != null ? job.relevancy_score.toFixed(1) : '—'}
            </span>
            <span className="modal__score-label">Score</span>
          </div>
          <div className="modal__verdict-badges">
            <span className={`badge badge--${getVerdictClass(job.verdict)}`}>
              {formatVerdict(job.verdict)}
            </span>
            <span className={`badge badge--${getPriorityClass(job.apply_priority)}`}>
              {formatPriority(job.apply_priority)}
            </span>
            {job.company_type && (
              <span className="badge" style={{ background: 'rgba(99,102,241,0.15)', color: 'var(--text-accent)' }}>
                {job.company_type}
              </span>
            )}
          </div>
        </div>

        {/* Score Breakdown */}
        <div className="modal__section">
          <h3 className="modal__section-title">Score Breakdown</h3>
          <div className="modal__scores">
            {scoreRows.map((row) => (
              <div key={row.label} className="modal__score-row">
                <div className="modal__score-row-header">
                  <span>{row.label}</span>
                  <span className="modal__score-row-meta">
                    <span style={{ color: row.value != null ? getScoreColor(row.value) : 'var(--text-muted)' }}>
                      {row.value != null ? row.value.toFixed(1) : '—'}
                    </span>
                    <span className="modal__score-weight">({row.weight})</span>
                  </span>
                </div>
                <ScoreBar score={row.value} height={5} />
              </div>
            ))}
          </div>
        </div>

        {/* Reasoning */}
        {job.score_reasoning && (
          <div className="modal__section">
            <h3 className="modal__section-title">💡 AI Reasoning</h3>
            <p className="modal__text">{job.score_reasoning}</p>
          </div>
        )}

        {/* Missing Skills */}
        {job.missing_skills && (
          <div className="modal__section">
            <h3 className="modal__section-title">⚠️ Missing Skills</h3>
            <div className="modal__skills">
              {job.missing_skills.split(',').map((skill, i) => (
                <span key={i} className="modal__skill-tag">
                  {skill.trim()}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Job Link */}
        {job.job_url && (
          <a
            href={job.job_url}
            target="_blank"
            rel="noopener noreferrer"
            className="modal__apply-btn"
            id="score-modal-apply"
          >
            View Job Posting →
          </a>
        )}
      </div>
    </div>
  )
}

/* Helpers */
function getVerdictClass(v) {
  if (!v) return 'skip'
  const map = {
    STRONG_FIT: 'strong', GOOD_FIT: 'good', MODERATE_FIT: 'moderate',
    WEAK_FIT: 'weak', POOR_FIT: 'poor',
  }
  return map[v] || 'skip'
}

function getPriorityClass(p) {
  if (!p) return 'skip'
  const map = { APPLY_NOW: 'apply', REVIEW_FIRST: 'review', SKIP: 'skip' }
  return map[p] || 'skip'
}

function formatVerdict(v) {
  if (!v) return 'Unscored'
  return v.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
    .replace('Fit', 'Fit')
}

function formatPriority(p) {
  if (!p) return '—'
  return p.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
