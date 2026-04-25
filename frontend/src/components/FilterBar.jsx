export default function FilterBar({ filters, onChange }) {
  const update = (key, value) => {
    onChange({ ...filters, [key]: value, page: 1 })
  }

  return (
    <div className="filter-bar card">
      <input
        type="text"
        placeholder="Search jobs or companies"
        value={filters.search || ''}
        onChange={(e) => update('search', e.target.value)}
        className="filter-bar__input"
      />

      <select
        value={filters.priority || ''}
        onChange={(e) => update('priority', e.target.value)}
        className="filter-bar__select"
      >
        <option value="">All priorities</option>
        <option value="APPLY_NOW">Apply now</option>
        <option value="REVIEW_FIRST">Review first</option>
        <option value="SKIP">Skip</option>
      </select>

      <select
        value={filters.verdict || ''}
        onChange={(e) => update('verdict', e.target.value)}
        className="filter-bar__select"
      >
        <option value="">All verdicts</option>
        <option value="STRONG_FIT">Strong fit</option>
        <option value="GOOD_FIT">Good fit</option>
        <option value="MODERATE_FIT">Moderate fit</option>
        <option value="WEAK_FIT">Weak fit</option>
        <option value="POOR_FIT">Poor fit</option>
      </select>

      <select
        value={filters.status || ''}
        onChange={(e) => update('status', e.target.value)}
        className="filter-bar__select"
        title="Default view hides 'rejected' and 'hidden'"
      >
        <option value="">Active jobs</option>
        <option value="new">New</option>
        <option value="saved">Saved</option>
        <option value="applied">Applied</option>
        <option value="interviewing">Interviewing</option>
        <option value="offer">Offer</option>
        <option value="rejected">Rejected</option>
        <option value="hidden">Hidden</option>
        <option value="all">All (incl. hidden)</option>
      </select>

      <label className="filter-bar__toggle">
        <input
          type="checkbox"
          checked={filters.scored_only || false}
          onChange={(e) => update('scored_only', e.target.checked)}
        />
        <span>Scored only</span>
      </label>

      {hasActiveFilters(filters) && (
        <button
          className="filter-bar__clear"
          onClick={() =>
            onChange({
              search: '',
              priority: '',
              company_type: '',
              verdict: '',
              status: '',
              scored_only: false,
              sort_by: 'relevancy_score',
              sort_dir: 'desc',
              page: 1,
              page_size: 25,
            })
          }
        >
          Clear
        </button>
      )}
    </div>
  )
}

function hasActiveFilters(f) {
  return f.search || f.priority || f.verdict || f.status || f.scored_only
}
