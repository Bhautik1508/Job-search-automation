/**
 * FilterBar — Search, dropdowns, and score range controls.
 */
export default function FilterBar({ filters, onChange }) {
  const update = (key, value) => {
    onChange({ ...filters, [key]: value, page: 1 })
  }

  return (
    <div className="filter-bar glass" id="filter-bar">
      {/* Search */}
      <div className="filter-bar__group filter-bar__search">
        <span className="filter-bar__icon">🔍</span>
        <input
          id="filter-search"
          type="text"
          placeholder="Search jobs or companies..."
          value={filters.search || ''}
          onChange={(e) => update('search', e.target.value)}
          className="filter-bar__input"
        />
      </div>

      {/* Priority */}
      <div className="filter-bar__group">
        <select
          id="filter-priority"
          value={filters.priority || ''}
          onChange={(e) => update('priority', e.target.value)}
          className="filter-bar__select"
        >
          <option value="">All Priorities</option>
          <option value="APPLY_NOW">🚀 Apply Now</option>
          <option value="REVIEW_FIRST">👀 Review First</option>
          <option value="SKIP">⏭️ Skip</option>
        </select>
      </div>

      {/* Company Type */}
      <div className="filter-bar__group">
        <select
          id="filter-company-type"
          value={filters.company_type || ''}
          onChange={(e) => update('company_type', e.target.value)}
          className="filter-bar__select"
        >
          <option value="">All Companies</option>
          <option value="fintech">💳 Fintech</option>
          <option value="bank">🏦 Bank</option>
          <option value="nbfc">📊 NBFC</option>
          <option value="other">🏢 Other</option>
        </select>
      </div>

      {/* Verdict */}
      <div className="filter-bar__group">
        <select
          id="filter-verdict"
          value={filters.verdict || ''}
          onChange={(e) => update('verdict', e.target.value)}
          className="filter-bar__select"
        >
          <option value="">All Verdicts</option>
          <option value="STRONG_FIT">Strong Fit</option>
          <option value="GOOD_FIT">Good Fit</option>
          <option value="MODERATE_FIT">Moderate Fit</option>
          <option value="WEAK_FIT">Weak Fit</option>
          <option value="POOR_FIT">Poor Fit</option>
        </select>
      </div>

      {/* Scored Only Toggle */}
      <label className="filter-bar__toggle" id="filter-scored-only">
        <input
          type="checkbox"
          checked={filters.scored_only || false}
          onChange={(e) => update('scored_only', e.target.checked)}
        />
        <span className="filter-bar__toggle-label">Scored only</span>
      </label>

      {/* Clear Filters */}
      {hasActiveFilters(filters) && (
        <button
          id="filter-clear"
          className="filter-bar__clear"
          onClick={() =>
            onChange({
              search: '',
              priority: '',
              company_type: '',
              verdict: '',
              scored_only: false,
              sort_by: 'relevancy_score',
              sort_dir: 'desc',
              page: 1,
              page_size: 25,
            })
          }
        >
          ✕ Clear
        </button>
      )}
    </div>
  )
}

function hasActiveFilters(f) {
  return f.search || f.priority || f.company_type || f.verdict || f.scored_only
}
