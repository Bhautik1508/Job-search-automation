import { useState, useEffect, useCallback, useRef } from 'react'
import FilterBar from './components/FilterBar'
import JobTable from './components/JobTable'
import ScoreModal from './components/ScoreModal'
import { apiFetch } from './api'
import './App.css'

const DEFAULT_FILTERS = {
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
}

export default function App() {
  const [jobs, setJobs] = useState([])
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(1)
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [loading, setLoading] = useState(true)
  const [selectedJob, setSelectedJob] = useState(null)
  const debounceRef = useRef(null)

  const [scrapeStatus, setScrapeStatus] = useState({ running: false, result: null, error: null })
  const [scoreStatus, setScoreStatus] = useState({ running: false, result: null, error: null })
  const [statusLine, setStatusLine] = useState(null)
  const pollRef = useRef(null)

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
      if (f.verdict) params.set('verdict', f.verdict)
      if (f.status) params.set('status', f.status)
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

  const updateStatus = useCallback(async (jobId, status) => {
    try {
      const resp = await apiFetch(`/api/jobs/${jobId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      if (!resp.ok) return
      const data = await resp.json()
      // hidden/rejected drop out of the default view, so just remove the row
      // when the current filter would exclude it.
      setJobs((prev) => {
        if (!filters.status && (status === 'hidden' || status === 'rejected')) {
          return prev.filter((j) => j.id !== jobId)
        }
        return prev.map((j) =>
          j.id === jobId ? { ...j, status: data.status } : j,
        )
      })
    } catch (err) {
      console.error('Failed to update status:', err)
    }
  }, [filters.status])

  const pollActions = useCallback(async () => {
    try {
      const resp = await apiFetch('/api/actions/status')
      if (!resp.ok) return
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

      if (prevScrapeRunning && !data.scrape.running) {
        if (data.scrape.error) {
          setStatusLine({ kind: 'error', text: `Scrape failed: ${data.scrape.error}` })
        } else if (data.scrape.last_result) {
          const r = data.scrape.last_result
          setStatusLine({
            kind: 'success',
            text: `Scrape: ${r.new_inserted ?? 0} new · ${r.total_raw ?? 0} raw · ${r.duplicates_skipped ?? 0} duplicates`,
          })
        }
        fetchJobs(filters)
      }
      if (prevScoreRunning && !data.score.running) {
        if (data.score.error) {
          setStatusLine({ kind: 'error', text: `Score failed: ${data.score.error}` })
        } else if (data.score.last_result) {
          const r = data.score.last_result
          setStatusLine({
            kind: 'success',
            text: `Score: ${r.scored ?? 0} scored${r.failed ? ` · ${r.failed} failed` : ''}`,
          })
        }
        fetchJobs(filters)
      }

      if (!data.scrape.running && !data.score.running) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    } catch {
      /* ignore */
    }
  }, [scrapeStatus.running, scoreStatus.running, fetchJobs, filters])

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

  const handleScrape = async () => {
    setStatusLine({ kind: 'info', text: 'Scraping started…' })
    try {
      const resp = await apiFetch('/api/scrape', { method: 'POST' })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        setStatusLine({
          kind: 'error',
          text: resp.status === 401 || resp.status === 403
            ? 'Unauthorized — check VITE_API_KEY matches API_KEY on backend'
            : `Scrape failed: HTTP ${resp.status}${text ? `: ${text.slice(0, 160)}` : ''}`,
        })
        return
      }
      const data = await resp.json()
      if (data.status === 'started' || data.status === 'already_running') {
        setScrapeStatus((prev) => ({ ...prev, running: true }))
      }
    } catch (err) {
      setStatusLine({ kind: 'error', text: err.message || 'Network error' })
    }
  }

  const handleScore = async () => {
    setStatusLine({ kind: 'info', text: 'Scoring started…' })
    try {
      const resp = await apiFetch('/api/score', { method: 'POST' })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        setStatusLine({
          kind: 'error',
          text: resp.status === 401 || resp.status === 403
            ? 'Unauthorized — check VITE_API_KEY matches API_KEY on backend'
            : `Score failed: HTTP ${resp.status}${text ? `: ${text.slice(0, 160)}` : ''}`,
        })
        return
      }
      const data = await resp.json()
      if (data.status === 'started' || data.status === 'already_running') {
        setScoreStatus((prev) => ({ ...prev, running: true }))
      }
    } catch (err) {
      setStatusLine({ kind: 'error', text: err.message || 'Network error' })
    }
  }

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      fetchJobs(filters)
    }, filters.search ? 300 : 0)
    return () => clearTimeout(debounceRef.current)
  }, [filters, fetchJobs])

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

  const isRunning = scrapeStatus.running || scoreStatus.running

  return (
    <div className="dashboard">
      <header className="dashboard__header">
        <div className="dashboard__header-row">
          <div>
            <h1 className="dashboard__title">Job Search</h1>
            <p className="dashboard__subtitle">{total} jobs tracked</p>
          </div>
          <div className="dashboard__actions">
            <button
              className="btn btn--primary"
              onClick={handleScrape}
              disabled={scrapeStatus.running}
            >
              {scrapeStatus.running ? 'Scraping…' : 'Scrape'}
            </button>
            <button
              className="btn btn--primary"
              onClick={handleScore}
              disabled={scoreStatus.running}
            >
              {scoreStatus.running ? 'Scoring…' : 'Score'}
            </button>
          </div>
        </div>
        {(statusLine || isRunning) && (
          <div className={`status-line status-line--${statusLine?.kind ?? 'info'}`}>
            {isRunning && !statusLine ? 'Working…' : statusLine?.text}
          </div>
        )}
      </header>

      <section className="dashboard__section">
        <FilterBar filters={filters} onChange={setFilters} />
      </section>

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
          onUpdateStatus={updateStatus}
        />
      </section>

      {selectedJob && (
        <ScoreModal job={selectedJob} onClose={() => setSelectedJob(null)} />
      )}
    </div>
  )
}
