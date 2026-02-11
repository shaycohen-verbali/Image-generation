import React, { useState } from 'react'
import { getRun } from '../lib/api'

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

function isTerminalRun(status) {
  return status === 'completed_pass' || status === 'completed_fail_threshold'
}

function runProgress(run) {
  const totalRounds = (run.max_optimization_attempts ?? 0) + 1
  const currentRound = Math.max(run.optimization_attempt ?? 0, 1)
  if (run.status === 'running' && run.current_stage === 'stage4_background') {
    return `Rounds completed (${currentRound}/${totalRounds}). Creating final white background image now.`
  }
  if (run.status === 'running') {
    return `Round ${currentRound}/${totalRounds} is in progress at ${run.current_stage}.`
  }
  if (run.status === 'queued' || run.status === 'retry_queued') {
    return `Queued. Up to ${totalRounds} rounds are planned.`
  }
  if (run.status === 'completed_pass') {
    return `Completed on round ${currentRound}/${totalRounds}.`
  }
  if (run.status === 'completed_fail_threshold') {
    return `Stopped after ${currentRound}/${totalRounds} rounds (threshold not reached).`
  }
  return `Status: ${run.status}`
}

export default function EntryDetailPage() {
  const [runId, setRunId] = useState('')
  const [data, setData] = useState(null)
  const [message, setMessage] = useState('')

  const load = async () => {
    if (!runId) return
    setMessage('Loading run...')
    try {
      const detail = await getRun(runId)
      setData(detail)
      setMessage(`Loaded run ${runId}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const sortedAssets = data?.assets
    ? [...data.assets].sort((left, right) => {
        const leftOrder = stagePriority[left.stage_name] || 99
        const rightOrder = stagePriority[right.stage_name] || 99
        if (leftOrder !== rightOrder) return leftOrder - rightOrder
        return (left.attempt || 0) - (right.attempt || 0)
      })
    : []

  const latestFinalAsset = [...sortedAssets]
    .filter((asset) => asset.stage_name === 'stage4_white_bg')
    .sort((left, right) => (left.attempt || 0) - (right.attempt || 0))
    .pop()
  const showFinalImage = Boolean(data && isTerminalRun(data.run.status) && latestFinalAsset?.origin_url)

  return (
    <section className="card-grid">
      <article className="card">
        <h2>Run Details</h2>
        <div className="inline-fields">
          <label>
            Run ID
            <input value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="run_xxx" />
          </label>
          <button onClick={load}>Load</button>
        </div>

        {data && (
          <>
            {data.run.review_warning ? (
              <div className="warning-banner">
                <strong>Review recommended:</strong> {data.run.review_warning_reason || 'This abstract concept may be hard to interpret.'}
              </div>
            ) : null}

            <h3>Run</h3>
            <pre>{JSON.stringify(data.run, null, 2)}</pre>
            <p className="progress-note">{runProgress(data.run)}</p>

            <h3>Final Image</h3>
            {showFinalImage ? (
              <div className="asset-card">
                <img className="asset-image" src={latestFinalAsset.origin_url} alt="Final white background output" />
                <div className="asset-meta">
                  <p>{latestFinalAsset.file_name}</p>
                  <a href={latestFinalAsset.origin_url} target="_blank" rel="noreferrer">
                    Open Full Image
                  </a>
                </div>
              </div>
            ) : (
              <p>Final image is available only after all rounds finish and status becomes completed.</p>
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
            <pre>{JSON.stringify(data.prompts, null, 2)}</pre>

            <h3>Scores</h3>
            <pre>{JSON.stringify(data.scores, null, 2)}</pre>
          </>
        )}
      </article>

      <article className="card message-card">
        <h2>Status</h2>
        <p>{message}</p>
      </article>
    </section>
  )
}
