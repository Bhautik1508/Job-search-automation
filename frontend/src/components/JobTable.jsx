import ScoreBar, { getScoreColor } from './ScoreBar'

/**
 * JobTable — Sortable, paginated table of jobs.
 */
export default function JobTable({
  jobs,
  total,
  page,
  totalPages,
  loading,
  filters,
  onFiltersChange,
  onRowClick,
  onToggleApplied,
}) {
  const handleSort = (column) => {
    const isSame = filters.sort_by === column
    onFiltersChange({
      ...filters,
      sort_by: column,
      sort_dir: isSame && filters.sort_dir === 'desc' ? 'asc' : 'desc',
    })
  }

  const SortIcon = ({ column }) => {
    if (filters.sort_by !== column) return <span className="sort-icon">↕</span>
    return (
      <span className="sort-icon sort-icon--active">
        {filters.sort_dir === 'asc' ? '↑' : '↓'}
      </span>
    )
  }

  if (loading) {
    return (
      <div className="job-table-wrap glass" id="job-table">
        {[...Array(8)].map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 48, marginBottom: 4 }} />
        ))}
      </div>
    )
  }

  if (!jobs || jobs.length === 0) {
    return (
      <div className="job-table-wrap glass" id="job-table">
        <div className="job-table__empty">
          <span className="job-table__empty-icon">📭</span>
          <p>No jobs found matching your filters.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="job-table-wrap" id="job-table">
      <div className="job-table-scroll">
        <table className="job-table glass">
          <thead>
            <tr>
              <th onClick={() => handleSort('relevancy_score')} className="job-table__th--sortable">
                Score <SortIcon column="relevancy_score" />
              </th>
              <th onClick={() => handleSort('title')} className="job-table__th--sortable">
                Job Title <SortIcon column="title" />
              </th>
              <th onClick={() => handleSort('company')} className="job-table__th--sortable">
                Company <SortIcon column="company" />
              </th>
              <th>Type</th>
              <th>Verdict</th>
              <th>Priority</th>
              <th>Source</th>
              <th onClick={() => handleSort('date_posted')} className="job-table__th--sortable">
                Posted <SortIcon column="date_posted" />
              </th>
              <th>Applied</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job, idx) => (
              <tr
                key={job.id}
                className="job-table__row animate-in"
                style={{ animationDelay: `${idx * 30}ms` }}
                onClick={() => onRowClick(job)}
                id={`job-row-${job.id}`}
              >
                <td className="job-table__score-cell">
                  <div className="job-table__score-value" style={{
                    color: job.relevancy_score != null ? getScoreColor(job.relevancy_score) : 'var(--text-muted)'
                  }}>
                    {job.relevancy_score != null ? job.relevancy_score.toFixed(1) : '—'}
                  </div>
                  <ScoreBar score={job.relevancy_score} height={4} />
                </td>
                <td className="job-table__title-cell">
                  <span className="job-table__title">{job.title}</span>
                  {job.location && (
                    <span className="job-table__location">📍 {job.location}</span>
                  )}
                </td>
                <td className="job-table__company">
                  <span>{job.company}</span>
                  {job.company_tier && job.company_tier !== 'other' && (
                    <span
                      className="badge"
                      style={{
                        marginLeft: 6,
                        background: getTierBackground(job.company_tier),
                        color: 'var(--text-strong)',
                        fontSize: '0.65rem',
                      }}
                      title={`${formatLabel(job.company_tier)}${job.funding_stage ? ' · ' + job.funding_stage : ''}`}
                    >
                      {getTierIcon(job.company_tier)} {formatLabel(job.company_tier)}
                    </span>
                  )}
                </td>
                <td>
                  {job.company_type && (
                    <span className="badge" style={{
                      background: 'rgba(99,102,241,0.12)',
                      color: 'var(--text-accent)',
                      fontSize: '0.7rem',
                    }}>
                      {job.company_type}
                    </span>
                  )}
                </td>
                <td>
                  <span className={`badge badge--${getVerdictClass(job.verdict)}`}>
                    {job.verdict ? formatLabel(job.verdict) : '—'}
                  </span>
                </td>
                <td>
                  <span className={`badge badge--${getPriorityClass(job.apply_priority)}`}>
                    {job.apply_priority ? formatLabel(job.apply_priority) : '—'}
                  </span>
                </td>
                <td className="job-table__source">{job.source_portal}</td>
                <td className="job-table__date">{formatDate(job.date_posted)}</td>
                <td>
                  <button
                    className={`job-table__applied-btn ${job.applied ? 'job-table__applied-btn--active' : ''}`}
                    onClick={(e) => {
                      e.stopPropagation()
                      onToggleApplied(job.id, !job.applied)
                    }}
                    id={`applied-btn-${job.id}`}
                    title={job.applied ? 'Unmark applied' : 'Mark as applied'}
                  >
                    {job.applied ? '✅' : '◻️'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="pagination" id="pagination">
        <span className="pagination__info">
          Showing {(page - 1) * filters.page_size + 1}–{Math.min(page * filters.page_size, total)} of {total}
        </span>
        <div className="pagination__controls">
          <button
            className="pagination__btn"
            disabled={page <= 1}
            onClick={() => onFiltersChange({ ...filters, page: page - 1 })}
            id="pagination-prev"
          >
            ← Prev
          </button>
          {generatePageNumbers(page, totalPages).map((p, i) =>
            p === '...' ? (
              <span key={`dots-${i}`} className="pagination__dots">…</span>
            ) : (
              <button
                key={p}
                className={`pagination__btn ${p === page ? 'pagination__btn--active' : ''}`}
                onClick={() => onFiltersChange({ ...filters, page: p })}
              >
                {p}
              </button>
            )
          )}
          <button
            className="pagination__btn"
            disabled={page >= totalPages}
            onClick={() => onFiltersChange({ ...filters, page: page + 1 })}
            id="pagination-next"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  )
}

/* Helpers */
function getVerdictClass(v) {
  const map = {
    STRONG_FIT: 'strong', GOOD_FIT: 'good', MODERATE_FIT: 'moderate',
    WEAK_FIT: 'weak', POOR_FIT: 'poor',
  }
  return map[v] || 'skip'
}

function getPriorityClass(p) {
  const map = { APPLY_NOW: 'apply', REVIEW_FIRST: 'review', SKIP: 'skip' }
  return map[p] || 'skip'
}

function getTierIcon(t) {
  const map = {
    top_tier: '🌟',
    unicorn: '🦄',
    growth_startup: '📈',
    early_startup: '🌱',
  }
  return map[t] || ''
}

function getTierBackground(t) {
  const map = {
    top_tier: 'rgba(245, 158, 11, 0.18)',
    unicorn: 'rgba(168, 85, 247, 0.18)',
    growth_startup: 'rgba(16, 185, 129, 0.18)',
    early_startup: 'rgba(14, 165, 233, 0.18)',
  }
  return map[t] || 'rgba(148, 163, 184, 0.18)'
}

function formatLabel(s) {
  if (!s) return '—'
  return s.replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatDate(d) {
  if (!d) return '—'
  const date = new Date(d)
  return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

function generatePageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages = []
  pages.push(1)
  if (current > 3) pages.push('...')
  for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
    pages.push(i)
  }
  if (current < total - 2) pages.push('...')
  pages.push(total)
  return pages
}
