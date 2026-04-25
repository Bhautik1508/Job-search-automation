import ScoreBar, { getScoreColor } from './ScoreBar'

const STATUS_OPTIONS = [
  { value: 'new', label: 'New' },
  { value: 'saved', label: 'Saved' },
  { value: 'applied', label: 'Applied' },
  { value: 'interviewing', label: 'Interviewing' },
  { value: 'offer', label: 'Offer' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'hidden', label: 'Hidden' },
]

export default function JobTable({
  jobs,
  total,
  page,
  totalPages,
  loading,
  filters,
  onFiltersChange,
  onRowClick,
  onUpdateStatus,
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
      <div className="job-table-wrap card">
        {[...Array(8)].map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 44, margin: '0 12px 6px' }} />
        ))}
      </div>
    )
  }

  if (!jobs || jobs.length === 0) {
    return (
      <div className="job-table-wrap card">
        <div className="job-table__empty">No jobs match your filters.</div>
      </div>
    )
  }

  return (
    <div className="job-table-wrap card">
      <div className="job-table-scroll">
        <table className="job-table">
          <thead>
            <tr>
              <th onClick={() => handleSort('relevancy_score')} className="job-table__th--sortable">
                Score <SortIcon column="relevancy_score" />
              </th>
              <th onClick={() => handleSort('title')} className="job-table__th--sortable">
                Title <SortIcon column="title" />
              </th>
              <th onClick={() => handleSort('company')} className="job-table__th--sortable">
                Company <SortIcon column="company" />
              </th>
              <th>Verdict</th>
              <th onClick={() => handleSort('date_posted')} className="job-table__th--sortable">
                Posted <SortIcon column="date_posted" />
              </th>
              <th>Status</th>
              <th aria-label="Hide" />
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr
                key={job.id}
                className="job-table__row"
                onClick={() => onRowClick(job)}
              >
                <td className="job-table__score-cell">
                  <div className="job-table__score-value" style={{
                    color: job.relevancy_score != null ? getScoreColor(job.relevancy_score) : 'var(--text-muted)',
                  }}>
                    {job.relevancy_score != null ? job.relevancy_score.toFixed(1) : '—'}
                  </div>
                  <ScoreBar score={job.relevancy_score} height={3} />
                </td>
                <td className="job-table__title-cell">
                  <span className="job-table__title">{job.title}</span>
                  {job.location && (
                    <span className="job-table__location">{job.location}</span>
                  )}
                </td>
                <td className="job-table__company">{job.company}</td>
                <td>
                  <span className={`badge badge--${getVerdictClass(job.verdict)}`}>
                    {job.verdict ? formatLabel(job.verdict) : '—'}
                  </span>
                </td>
                <td className="job-table__date">{formatDate(job.date_posted)}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <select
                    className={`status-select status-select--${job.status || 'new'}`}
                    value={job.status || 'new'}
                    onChange={(e) => onUpdateStatus(job.id, e.target.value)}
                  >
                    {STATUS_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </td>
                <td onClick={(e) => e.stopPropagation()}>
                  <button
                    className="hide-btn"
                    onClick={() => onUpdateStatus(job.id, 'hidden')}
                    title="Hide this job"
                    disabled={job.status === 'hidden'}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pagination">
        <span className="pagination__info">
          {(page - 1) * filters.page_size + 1}–{Math.min(page * filters.page_size, total)} of {total}
        </span>
        <div className="pagination__controls">
          <button
            className="pagination__btn"
            disabled={page <= 1}
            onClick={() => onFiltersChange({ ...filters, page: page - 1 })}
          >
            Prev
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
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}

function getVerdictClass(v) {
  const map = {
    STRONG_FIT: 'strong', GOOD_FIT: 'good', MODERATE_FIT: 'moderate',
    WEAK_FIT: 'weak', POOR_FIT: 'poor',
  }
  return map[v] || 'skip'
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
