import React, { useEffect, useMemo, useState } from 'react'
import { listRuns, retryRun } from '../lib/api'

function roundsLabel(run) {
  const totalRounds = (run.max_optimization_attempts ?? 0) + 1
  const currentRound = Math.max(run.optimization_attempt ?? 0, 1)
  if (run.status === 'running' && run.current_stage === 'stage4_background') {
    return `Rounds done (${currentRound}/${totalRounds}), finalizing`
  }
  if (run.status === 'running') {
    return `Round ${currentRound}/${totalRounds} in progress`
  }
  if (run.status.startsWith('completed')) {
    return `Completed on round ${currentRound}/${totalRounds}`
  }
  return `Planned rounds: ${totalRounds}`
}

export default function RunsPage() {
  const [filters, setFilters] = useState({ status: '', entry_id: '' })
  const [runs, setRuns] = useState([])
  const [message, setMessage] = useState('')
  const [warningsOnly, setWarningsOnly] = useState(false)

  const query = useMemo(() => {
    const next = {}
    if (filters.status) next.status = filters.status
    if (filters.entry_id) next.entry_id = filters.entry_id
    return next
  }, [filters])

  const visibleRuns = useMemo(() => {
    if (!warningsOnly) return runs
    return runs.filter((run) => run.review_warning)
  }, [runs, warningsOnly])

  async function refresh() {
    try {
      const data = await listRuns(query)
      setRuns(data)
      setMessage(`Loaded ${data.length} runs`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [query.status, query.entry_id])

  const onRetry = async (runId) => {
    try {
      await retryRun(runId)
      setMessage(`Run ${runId} queued for retry`)
      refresh()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Live Runs</h2>
        <div className="inline-fields">
          <label>
            Status
            <input value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} />
          </label>
          <label>
            Entry ID
            <input value={filters.entry_id} onChange={(e) => setFilters({ ...filters, entry_id: e.target.value })} />
          </label>
          <label className="checkbox-label">
            <input type="checkbox" checked={warningsOnly} onChange={(e) => setWarningsOnly(e.target.checked)} />
            Warning only
          </label>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run ID</th>
                <th>Entry ID</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Score</th>
                <th>Rounds</th>
                <th>Warning</th>
                <th>Retry</th>
              </tr>
            </thead>
            <tbody>
              {visibleRuns.map((run) => (
                <tr key={run.id}>
                  <td>{run.id}</td>
                  <td>{run.entry_id}</td>
                  <td>{run.status}</td>
                  <td>{run.current_stage}</td>
                  <td>{run.quality_score ?? '-'}</td>
                  <td>{roundsLabel(run)}</td>
                  <td>
                    {run.review_warning ? (
                      <span className="warning-badge" title={run.review_warning_reason || ''}>
                        Review recommended
                      </span>
                    ) : (
                      '-'
                    )}
                  </td>
                  <td>
                    <button onClick={() => onRetry(run.id)} disabled={!run.status.startsWith('failed')}>
                      Retry
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>
    </section>
  )
}
