import { useState, useEffect, useCallback, useRef } from 'react'
import KpiCards from './components/KpiCards'
import FilterBar from './components/FilterBar'
import JobTable from './components/JobTable'
import ScoreModal from './components/ScoreModal'
import CareersLinks from './components/CareersLinks'
import SchedulerIndicator from './components/SchedulerIndicator'
import { apiFetch } from './api'
import './App.css'

const DEFAULT_FILTERS = {
  search: '',
  priority: '',
  company_type: '',
  company_tier: '',
  verdict: '',
  scored_only: false,
  sort_by: 'relevancy_score',
  sort_dir: 'desc',
  page: 1,
  page_size: 25,
}

export default function App() {
  const [stats, setStats] = useState(null)
  const [jobs, setJobs] = useState([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [loading, setLoading] = useState(true)
  const [statsLoading, setStatsLoading] = useState(true)
  const [selectedJob, setSelectedJob] = useState(null)
  const debounceRef = useRef(null)

  // Action states
  const [scrapeStatus, setScrapeStatus] = useState({ running: false, result: null, error: null })
  const [scoreStatus, setScoreStatus] = useState({ running: false, result: null, error: null })
  const pollRef = useRef(null)

  // Fetch stats
  const fetchStats = useCallback(async () => {
    setStatsLoading(true)
    try {
      const resp = await apiFetch('/api/stats')
      if (resp.ok) {
        setStats(await resp.json())
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err)
    } finally {
      setStatsLoading(false)
    }
  }, [])

  // Fetch jobs
  const fetchJobs = useCallback(async (f) => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      params.set('page', f.page)
      params.set('page_size', f.page_size)
      params.set('sort_by', f.sort_by)
      params.set('sort_dir', f.sort_dir)
      if (f.search) params.set('search', f.search)
      if (f.priority) params.set('priority', f.priority)
      if (f.company_type) params.set('company_type', f.company_type)
      if (f.company_tier) params.set('company_tier', f.company_tier)
      if (f.verdict) params.set('verdict', f.verdict)
      if (f.scored_only) params.set('scored_only', 'true')

      const resp = await apiFetch(`/api/jobs?${params}`)
      if (resp.ok) {
        const data = await resp.json()
        setJobs(data.jobs)
        setTotal(data.total)
        setTotalPages(data.total_pages)
      }
    } catch (err) {
      console.error('Failed to fetch jobs:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  // Toggle applied
  const toggleApplied = useCallback(async (jobId, applied) => {
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}/applied?applied=${applied}`, {
        method: 'PATCH',
      })
      if (resp.ok) {
        setJobs((prev) =>
          prev.map((j) =>
            j.id === jobId ? { ...j, applied } : j
          )
        )
        fetchStats()
      }
    } catch (err) {
      console.error('Failed to toggle applied:', err)
    }
  }, [fetchStats])

  // Poll action status
  const pollActions = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/actions/status')
      if (resp.ok) {
        const data = await resp.json()

        const prevScrapeRunning = scrapeStatus.running
        const prevScoreRunning = scoreStatus.running

        setScrapeStatus({
          running: data.scrape.running,
          result: data.scrape.last_result,
          error: data.scrape.error,
        })
        setScoreStatus({
          running: data.score.running,
          result: data.score.last_result,
          error: data.score.error,
        })

        // If an action just finished, refresh data
        if ((prevScrapeRunning && !data.scrape.running) ||
            (prevScoreRunning && !data.score.running)) {
          fetchStats()
          fetchJobs(filters)
        }

        // Stop polling if nothing is running
        if (!data.scrape.running && !data.score.running) {
          clearInterval(pollRef.current)
          pollRef.current = null
        }
      }
    } catch {
      // Ignore polling errors
    }
  }, [scrapeStatus.running, scoreStatus.running, fetchStats, fetchJobs, filters])

  // Start polling when an action starts
  useEffect(() => {
    if ((scrapeStatus.running || scoreStatus.running) && !pollRef.current) {
      pollRef.current = setInterval(pollActions, 3000)
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [scrapeStatus.running, scoreStatus.running, pollActions])

  // Trigger scrape
  const handleScrape = async () => {
    try {
      const resp = await apiFetch('/api/scrape', { method: 'POST' })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        const msg = resp.status === 401 || resp.status === 403
          ? 'Unauthorized — check VITE_API_KEY matches API_KEY on backend'
          : `HTTP ${resp.status}${text ? `: ${text.slice(0, 200)}` : ''}`
        setScrapeStatus({ running: false, result: null, error: msg })
        return
      }
      const data = await resp.json()
      if (data.status === 'started') {
        setScrapeStatus({ running: true, result: null, error: null })
      } else if (data.status === 'already_running') {
        setScrapeStatus((prev) => ({ ...prev, running: true }))
      }
    } catch (err) {
      setScrapeStatus({ running: false, result: null, error: err.message || 'Network error' })
    }
  }

  // Trigger score
  const handleScore = async () => {
    try {
      const resp = await apiFetch('/api/score', { method: 'POST' })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        const msg = resp.status === 401 || resp.status === 403
          ? 'Unauthorized — check VITE_API_KEY matches API_KEY on backend'
          : `HTTP ${resp.status}${text ? `: ${text.slice(0, 200)}` : ''}`
        setScoreStatus({ running: false, result: null, error: msg })
        return
      }
      const data = await resp.json()
      if (data.status === 'started') {
        setScoreStatus({ running: true, result: null, error: null })
      } else if (data.status === 'already_running') {
        setScoreStatus((prev) => ({ ...prev, running: true }))
      }
    } catch (err) {
      setScoreStatus({ running: false, result: null, error: err.message || 'Network error' })
    }
  }

  // Initial load
  useEffect(() => {
    fetchStats()
  }, [fetchStats])

  // Debounced filter fetch
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      fetchJobs(filters)
    }, filters.search ? 300 : 0)
    return () => clearTimeout(debounceRef.current)
  }, [filters, fetchJobs])

  // Handle row click — fetch full job details
  const handleRowClick = useCallback(async (job) => {
    try {
      const resp = await apiFetch(`/api/jobs/${job.id}`)
      if (resp.ok) {
        setSelectedJob(await resp.json())
      }
    } catch {
      setSelectedJob(job)
    }
  }, [])

  return (
    <div className="dashboard" id="dashboard">
      {/* Header */}
      <header className="dashboard__header animate-in" id="dashboard-header">
        <div className="dashboard__header-content">
          <div className="dashboard__header-top">
            <div>
              <h1 className="dashboard__title">
                <span className="dashboard__title-icon">🎯</span>
                Job Search Dashboard
              </h1>
              <p className="dashboard__subtitle">
                AI-powered relevancy scoring · {stats?.total_jobs ?? '—'} jobs tracked
                <SchedulerIndicator />
              </p>
            </div>
            <div className="dashboard__actions">
              <button
                id="btn-scrape"
                className={`dashboard__action-btn dashboard__action-btn--scrape ${scrapeStatus.running ? 'dashboard__action-btn--loading' : ''}`}
                onClick={handleScrape}
                disabled={scrapeStatus.running}
              >
                {scrapeStatus.running ? (
                  <><span className="spinner" /> Scraping...</>
                ) : (
                  <>🔄 Fetch New Jobs</>
                )}
              </button>
              <button
                id="btn-score"
                className={`dashboard__action-btn dashboard__action-btn--score ${scoreStatus.running ? 'dashboard__action-btn--loading' : ''}`}
                onClick={handleScore}
                disabled={scoreStatus.running}
              >
                {scoreStatus.running ? (
                  <><span className="spinner" /> Scoring...</>
                ) : (
                  <>⚡ Score All Jobs</>
                )}
              </button>
            </div>
          </div>

          {/* Action status toasts */}
          {scrapeStatus.result && !scrapeStatus.running && (
            <div className="action-toast action-toast--success">
              ✅ Scrape complete: {scrapeStatus.result.new_inserted} new jobs added
              {' · '}{scrapeStatus.result.total_raw ?? 0} raw from engines
              {scrapeStatus.result.title_filtered_out > 0 && ` · ${scrapeStatus.result.title_filtered_out} filtered by title`}
              {scrapeStatus.result.duplicates_skipped > 0 && ` · ${scrapeStatus.result.duplicates_skipped} duplicates skipped`}
              {scrapeStatus.result.per_engine_counts && Object.keys(scrapeStatus.result.per_engine_counts).length > 0 && (
                <div style={{ marginTop: 4, fontSize: '0.85em', opacity: 0.8 }}>
                  Per engine: {Object.entries(scrapeStatus.result.per_engine_counts)
                    .map(([name, n]) => `${name}=${n}`).join(' · ')}
                </div>
              )}
              {scrapeStatus.result.per_engine_errors && Object.keys(scrapeStatus.result.per_engine_errors).length > 0 && (
                <div style={{ marginTop: 4, fontSize: '0.85em', color: '#f43f5e' }}>
                  Errors: {Object.entries(scrapeStatus.result.per_engine_errors)
                    .map(([name, err]) => `${name}: ${err}`).join(' · ')}
                </div>
              )}
            </div>
          )}
          {scrapeStatus.error && !scrapeStatus.running && (
            <div className="action-toast action-toast--error">
              ❌ Scrape error: {scrapeStatus.error}
            </div>
          )}
          {scoreStatus.result && !scoreStatus.running && (
            <div className="action-toast action-toast--success">
              ✅ Scoring complete: {scoreStatus.result.scored} jobs scored
              {scoreStatus.result.failed > 0 && ` · ${scoreStatus.result.failed} failed`}
            </div>
          )}
          {scoreStatus.error && !scoreStatus.running && (
            <div className="action-toast action-toast--error">
              ❌ Score error: {scoreStatus.error}
            </div>
          )}
        </div>
      </header>

      {/* KPI Cards */}
      <section className="dashboard__section">
        <KpiCards stats={stats} loading={statsLoading} />
      </section>

      {/* Careers Links (Phase 6) */}
      <section className="dashboard__section">
        <CareersLinks />
      </section>

      {/* Filters */}
      <section className="dashboard__section">
        <FilterBar filters={filters} onChange={setFilters} />
      </section>

      {/* Job Table */}
      <section className="dashboard__section">
        <JobTable
          jobs={jobs}
          total={total}
          page={filters.page}
          totalPages={totalPages}
          loading={loading}
          filters={filters}
          onFiltersChange={setFilters}
          onRowClick={handleRowClick}
          onToggleApplied={toggleApplied}
        />
      </section>

      {/* Score Modal */}
      {selectedJob && (
        <ScoreModal
          job={selectedJob}
          onClose={() => setSelectedJob(null)}
        />
      )}
    </div>
  )
}
