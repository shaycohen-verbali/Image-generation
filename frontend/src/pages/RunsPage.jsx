import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getRun, listRuns, retryRun } from '../lib/api'

const stagePriority = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage4_white_bg: 3,
}

function stageTitle(stageName) {
  if (stageName === 'stage2_draft') return 'Stage 2 Draft'
  if (stageName === 'stage3_upgraded') return 'Stage 3 Upgraded'
  if (stageName === 'stage4_white_bg') return 'Stage 4 White Background'
  return stageName
}

export default function RunsPage() {
  const [filters, setFilters] = useState({ status: '', word: '', part_of_sentence: '', category: '' })
  const [runs, setRuns] = useState([])
  const [message, setMessage] = useState('')
  const [selectedRunId, setSelectedRunId] = useState('')
  const [detail, setDetail] = useState(null)
  const selectedRunIdRef = useRef('')

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
  }, [selectedRunId])

  const query = useMemo(() => {
    const next = {}
    if (filters.status) next.status = filters.status
    return next
  }, [filters.status])

  const filteredRuns = useMemo(() => {
    return runs.filter((run) => {
      if (filters.word && !String(run.word || '').toLowerCase().includes(filters.word.toLowerCase())) return false
      if (
        filters.part_of_sentence &&
        !String(run.part_of_sentence || '').toLowerCase().includes(filters.part_of_sentence.toLowerCase())
      ) return false
      if (filters.category && !String(run.category || '').toLowerCase().includes(filters.category.toLowerCase())) return false
      return true
    })
  }, [runs, filters.word, filters.part_of_sentence, filters.category])

  async function loadRunDetail(runId) {
    try {
      const data = await getRun(runId)
      setDetail(data)
    } catch (error) {
      setMessage(`Error loading detail: ${error.message}`)
    }
  }

  async function refresh() {
    try {
      const data = await listRuns(query)
      setRuns(data)
      setMessage(`Loaded ${data.length} runs`)

      const activeRunId = selectedRunIdRef.current
      if (!activeRunId && data.length > 0) {
        setSelectedRunId(data[0].id)
        selectedRunIdRef.current = data[0].id
        loadRunDetail(data[0].id)
      } else if (activeRunId) {
        const exists = data.some((run) => run.id === activeRunId)
        if (exists) loadRunDetail(activeRunId)
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 3000)
    return () => clearInterval(timer)
  }, [query.status])

  const onRetry = async (runId) => {
    try {
      await retryRun(runId)
      setMessage(`Run ${runId} queued for retry`)
      refresh()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const sortedAssets = detail?.assets
    ? [...detail.assets].sort((left, right) => {
        const leftOrder = stagePriority[left.stage_name] || 99
        const rightOrder = stagePriority[right.stage_name] || 99
        if (leftOrder !== rightOrder) return leftOrder - rightOrder
        return (left.attempt || 0) - (right.attempt || 0)
      })
    : []

  const finalAsset = sortedAssets.reduce((latest, asset) => {
    if (asset.stage_name !== 'stage4_white_bg') return latest
    if (!latest) return asset
    return (asset.attempt || 0) >= (latest.attempt || 0) ? asset : latest
  }, null)

  return (
    <section className="runs-layout">
      <article className="card">
        <h2>Runs</h2>
        <div className="inline-fields">
          <label>
            Status
            <input value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} />
          </label>
          <label>
            Word
            <input value={filters.word} onChange={(e) => setFilters({ ...filters, word: e.target.value })} />
          </label>
          <label>
            POS
            <input
              value={filters.part_of_sentence}
              onChange={(e) => setFilters({ ...filters, part_of_sentence: e.target.value })}
            />
          </label>
          <label>
            Category
            <input value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })} />
          </label>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Word</th>
                <th>POS</th>
                <th>Category</th>
                <th>Status</th>
                <th>Score</th>
                <th>Attempt</th>
                <th>Retry</th>
              </tr>
            </thead>
            <tbody>
              {filteredRuns.map((run) => (
                <tr
                  key={run.id}
                  className={run.id === selectedRunId ? 'selected-row' : 'clickable-row'}
                  onClick={() => {
                    setSelectedRunId(run.id)
                    selectedRunIdRef.current = run.id
                    loadRunDetail(run.id)
                  }}
                >
                  <td>{run.word || '-'}</td>
                  <td>{run.part_of_sentence || '-'}</td>
                  <td>{run.category || '-'}</td>
                  <td>{run.status}</td>
                  <td>{run.quality_score ?? '-'}</td>
                  <td>{run.optimization_attempt}</td>
                  <td>
                    <button
                      onClick={(event) => {
                        event.stopPropagation()
                        onRetry(run.id)
                      }}
                      disabled={!run.status.startsWith('failed')}
                    >
                      Retry
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>{message}</p>
      </article>

      <article className="card">
        <h2>Run Detail</h2>
        {!detail ? (
          <p>Select a run row to see details.</p>
        ) : (
          <>
            <h3>Run</h3>
            <pre>{JSON.stringify(detail.run, null, 2)}</pre>

            <h3>Final Image</h3>
            {finalAsset?.origin_url ? (
              <div className="asset-card">
                <img className="asset-image" src={finalAsset.origin_url} alt="Final white background output" />
                <div className="asset-meta">
                  <p>{finalAsset.file_name}</p>
                  <a href={finalAsset.origin_url} target="_blank" rel="noreferrer">
                    Open Full Image
                  </a>
                </div>
              </div>
            ) : (
              <p>No final image yet.</p>
            )}

            <h3>Image History</h3>
            {sortedAssets.length > 0 ? (
              <div className="asset-grid">
                {sortedAssets.map((asset) => (
                  <div key={asset.id} className="asset-card">
                    <h4>{stageTitle(asset.stage_name)}</h4>
                    {asset.origin_url ? (
                      <img className="asset-image" src={asset.origin_url} alt={`${asset.stage_name} attempt ${asset.attempt}`} />
                    ) : (
                      <p>Image URL unavailable.</p>
                    )}
                    <div className="asset-meta">
                      <p>Attempt: {asset.attempt}</p>
                      <p>Model: {asset.model_name}</p>
                      {asset.origin_url ? (
                        <a href={asset.origin_url} target="_blank" rel="noreferrer">
                          Open Image
                        </a>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p>No images generated yet.</p>
            )}

            <h3>Prompts</h3>
            <pre>{JSON.stringify(detail.prompts, null, 2)}</pre>

            <h3>Scores</h3>
            <pre>{JSON.stringify(detail.scores, null, 2)}</pre>
          </>
        )}
      </article>
    </section>
  )
}
