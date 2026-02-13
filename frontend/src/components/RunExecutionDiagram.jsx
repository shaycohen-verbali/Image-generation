import React, { useEffect, useMemo, useState } from 'react'
import RunNodeDetailCard from './RunNodeDetailCard'
import WorkflowCanvas from './WorkflowCanvas'
import { buildRunDiagram, getAvailableAttempts } from '../lib/runDiagram'

function humanStatus(status) {
  const value = String(status || '').toLowerCase()
  if (value === 'ok') return 'Done'
  if (value === 'running') return 'In progress'
  if (value === 'queued') return 'Waiting'
  if (value === 'skipped') return 'Skipped'
  if (value === 'error') return 'Failed'
  return status || '-'
}

function prettyStage(stage) {
  if (stage === 'queued') return 'Waiting to start'
  if (stage === 'stage1_prompt') return 'Stage 1: First prompt'
  if (stage === 'stage2_draft') return 'Stage 2: Draft image'
  if (stage === 'stage3_upgrade') return 'Stage 3: Improve + generate'
  if (stage === 'quality_gate') return 'Quality check'
  if (stage === 'stage4_background') return 'Stage 4: White background'
  if (stage === 'completed') return 'Completed'
  return stage || '-'
}

function prettyRunStatus(status) {
  if (status === 'completed_pass') return 'Completed (Pass)'
  if (status === 'completed_fail_threshold') return 'Completed (Below threshold)'
  if (status === 'failed_technical') return 'Failed (Technical)'
  if (status === 'running') return 'Running'
  if (status === 'queued') return 'Queued'
  if (status === 'retry_queued') return 'Queued for retry'
  return status || '-'
}

function stage4StatusText({ run, selectedSummary }) {
  const threshold = Number(run.quality_threshold || 95)
  const stage4 = selectedSummary?.stage4Status
  if (stage4 === 'ok') {
    return `Background removal completed for this attempt.`
  }
  if (run.status === 'completed_fail_threshold') {
    return `Background removal was skipped because no attempt reached the quality threshold (${threshold}).`
  }
  if (run.status === 'running' && run.current_stage !== 'stage4_background' && run.current_stage !== 'completed') {
    return `Background removal has not started yet. It runs only after a passing quality score (>= ${threshold}).`
  }
  if (run.status === 'failed_technical' && run.current_stage === 'stage4_background') {
    return `Background removal failed technically on this run.`
  }
  if (stage4 === 'skipped' || stage4 === 'queued') {
    return `Background removal is waiting for a pass on quality score (>= ${threshold}).`
  }
  if (stage4 === 'error') {
    return `Background removal failed for this attempt.`
  }
  return `Background removal runs only after a passing quality score (>= ${threshold}).`
}

const assetStageOrder = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage4_white_bg: 3,
}

function stageImageLabel(stageName) {
  if (stageName === 'stage2_draft') return 'Stage 2 Draft'
  if (stageName === 'stage3_upgraded') return 'Stage 3 Upgraded'
  if (stageName === 'stage4_white_bg') return 'Stage 4 White Background'
  return stageName || 'Image'
}

function attemptLabel(asset) {
  if (asset.stage_name === 'stage2_draft') return 'Base draft'
  if (typeof asset.attempt === 'number' && asset.attempt > 0) return `Attempt ${asset.attempt}`
  return 'Attempt -'
}

function defaultAttempt(detail, attempts) {
  const current = Number(detail?.run?.optimization_attempt || 0)
  if (current > 0 && attempts.includes(current)) return current
  return attempts[attempts.length - 1] || 1
}

export default function RunExecutionDiagram({ detail, assistantName = '' }) {
  const attempts = useMemo(() => getAvailableAttempts(detail), [detail?.run?.id, detail?.run?.updated_at, detail])
  const [selectedAttempt, setSelectedAttempt] = useState(defaultAttempt(detail, attempts))
  const diagram = useMemo(() => buildRunDiagram(detail, selectedAttempt), [detail, selectedAttempt])
  const [selectedNodeId, setSelectedNodeId] = useState('stage3_generate')

  useEffect(() => {
    const next = defaultAttempt(detail, attempts)
    setSelectedAttempt((current) => (attempts.includes(current) ? current : next))
  }, [detail?.run?.id, detail?.run?.updated_at, attempts])

  useEffect(() => {
    if (!diagram.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(diagram.nodes[0]?.id || '')
    }
  }, [diagram.nodes, selectedNodeId])

  if (!detail) {
    return <p>Select a run row to see live execution.</p>
  }

  const selectedNode = diagram.nodes.find((node) => node.id === selectedNodeId) || diagram.nodes[0] || null
  const currentAttempt = Number(detail.run.optimization_attempt || 0)
  const maxAttempts = Number(detail.run.max_optimization_attempts || 0) + 1
  const selectedSummary = diagram.attemptSummaries.find((summary) => summary.attempt === selectedAttempt)
  const allRunAssets = useMemo(
    () =>
      [...(detail.assets || [])].sort((left, right) => {
        const leftAttempt = Number(left.attempt || 0)
        const rightAttempt = Number(right.attempt || 0)
        if (leftAttempt !== rightAttempt) return leftAttempt - rightAttempt
        const leftOrder = assetStageOrder[left.stage_name] || 99
        const rightOrder = assetStageOrder[right.stage_name] || 99
        if (leftOrder !== rightOrder) return leftOrder - rightOrder
        return String(left.created_at || '').localeCompare(String(right.created_at || ''))
      }),
    [detail.assets],
  )
  const canvasNodes = useMemo(
    () =>
      diagram.nodes.map((node) => {
        const position = {
          stage1_prompt: { x: 40, y: 235 },
          stage2_draft: { x: 380, y: 235 },
          stage3_critique: { x: 760, y: 45 },
          stage3_prompt_upgrade: { x: 760, y: 235 },
          stage3_generate: { x: 760, y: 425 },
          quality_gate: { x: 1160, y: 235 },
          stage4_background: { x: 1540, y: 120 },
          completed: { x: 1910, y: 120 },
        }[node.id] || { x: 40, y: 185 }

        const badge =
          node.id === 'stage3_critique' ||
          node.id === 'stage3_prompt_upgrade' ||
          node.id === 'stage3_generate' ||
          node.id === 'quality_gate' ||
          node.id === 'stage4_background'
            ? `Attempt ${selectedAttempt}`
            : ''

        return {
          ...node,
          ...position,
          badge,
        }
      }),
    [diagram.nodes, selectedAttempt],
  )

  return (
    <div className="run-diagram-root">
      <div className="run-diagram-head">
        <h3>Live Execution Diagram</h3>
        <p>
          <strong>What is happening now:</strong> {prettyRunStatus(detail.run.status)}. Current step: {prettyStage(detail.run.current_stage)}.
        </p>
        <p>
          <strong>Attempt progress:</strong> {currentAttempt > 0 ? currentAttempt : 1} / {maxAttempts} (selected: Attempt {selectedAttempt})
        </p>
      </div>

      <div className="run-help-card">
        <p><strong>How to read this:</strong> each attempt is one full try to improve the image and pass the quality score.</p>
        <p>Attempt flow: Stage 3 improve -> Quality check -> if pass then Stage 4 white background.</p>
      </div>

      <div className="run-help-card stage4-help-card">
        <p><strong>Stage 4 (Background Removal):</strong> {stage4StatusText({ run: detail.run, selectedSummary })}</p>
      </div>

      <div className="attempt-chip-row">
        {attempts.map((attempt) => (
          <button
            key={attempt}
            className={attempt === selectedAttempt ? 'attempt-chip active' : 'attempt-chip'}
            onClick={() => setSelectedAttempt(attempt)}
          >
            Attempt {attempt}
          </button>
        ))}
      </div>

      <div className="attempt-summary-row">
        {diagram.attemptSummaries.map((summary) => (
          <div key={summary.attempt} className={summary.attempt === selectedAttempt ? 'attempt-summary active' : 'attempt-summary'}>
            <p><strong>Attempt {summary.attempt}</strong></p>
            <p>Image improvement: {humanStatus(summary.stage3Status)}</p>
            <p>Quality check: {humanStatus(summary.qualityStatus)}</p>
            <p>White background: {humanStatus(summary.stage4Status)}</p>
            <p>Score: {summary.score ?? '-'}</p>
          </div>
        ))}
      </div>

      <WorkflowCanvas
        nodes={canvasNodes}
        edges={diagram.edges}
        width={2200}
        height={640}
        selectedNodeId={selectedNodeId}
        onSelectNode={setSelectedNodeId}
      />

      <p className="run-diagram-loop-note">If quality fails and attempts remain, the system loops to the next attempt automatically. Use zoom +/- if needed.</p>

      <RunNodeDetailCard node={selectedNode} assistantName={assistantName} />

      <div className="run-all-images-section">
        <div className="run-all-images-header">
          <h3>All Images Created In This Run</h3>
          <p>{allRunAssets.length} image{allRunAssets.length === 1 ? '' : 's'} saved across all attempts</p>
        </div>
        {allRunAssets.length === 0 ? (
          <p>No images available yet.</p>
        ) : (
          <div className="asset-grid">
            {allRunAssets.map((asset) => (
              <div key={asset.id} className="asset-card run-asset-card">
                <h4>{stageImageLabel(asset.stage_name)}</h4>
                {asset.origin_url ? (
                  <img className="asset-image" src={asset.origin_url} alt={`${asset.stage_name} ${attemptLabel(asset)}`} />
                ) : (
                  <p className="asset-meta-empty">Image URL unavailable.</p>
                )}
                <div className="asset-meta">
                  <p><strong>{attemptLabel(asset)}</strong></p>
                  <p>{asset.file_name || '-'}</p>
                  <p>{asset.model_name || '-'}</p>
                  {asset.origin_url ? (
                    <a href={asset.origin_url} target="_blank" rel="noreferrer">
                      Open Full Image
                    </a>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
