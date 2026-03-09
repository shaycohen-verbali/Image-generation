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

function stage4StatusText({ run, selectedSummary, winnerStage4Attempt }) {
  if (typeof winnerStage4Attempt === 'number' && winnerStage4Attempt > 0) {
    return `Background removal completed on winner attempt ${winnerStage4Attempt}.`
  }
  const stage4 = selectedSummary?.stage4Status
  if (stage4 === 'ok') {
    return `Background removal completed for this attempt.`
  }
  if (run.status === 'running' && run.current_stage !== 'stage4_background' && run.current_stage !== 'completed') {
    return `Background removal has not started yet. It runs after scoring selects the winner attempt.`
  }
  if (run.status === 'failed_technical' && run.current_stage === 'stage4_background') {
    return `Background removal failed technically on this run.`
  }
  if (stage4 === 'skipped' || stage4 === 'queued') {
    return `Background removal is waiting for winner selection.`
  }
  if (stage4 === 'error') {
    return `Background removal failed for this attempt.`
  }
  return `Background removal runs after the winner attempt is selected.`
}

const assetStageOrder = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage4_white_bg: 3,
}

const IMAGE_FILTER = {
  DRAFT: 'draft',
  ATTEMPT: 'attempt',
  REMOVE_BACKGROUND: 'remove_background',
}

const DETAIL_TABS = {
  OVERVIEW: 'overview',
  IMAGES: 'images',
  PROCESS: 'process',
  SETTINGS: 'settings',
  DEBUG: 'debug',
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

function currentNodeId(detail) {
  const stage = String(detail?.run?.current_stage || '')
  if (stage === 'stage1_prompt') return 'stage1_prompt'
  if (stage === 'stage2_draft') return 'stage2_draft'
  if (stage === 'stage3_upgrade') return 'stage3_generate'
  if (stage === 'quality_gate') return 'quality_gate'
  if (stage === 'stage4_background') return 'stage4_background'
  if (stage === 'completed') return 'completed'
  return ''
}

function statusTone(status) {
  const value = String(status || '').toLowerCase()
  if (value.includes('completed_pass')) return 'ok'
  if (value.includes('running')) return 'running'
  if (value.includes('queued')) return 'queued'
  if (value.includes('fail')) return 'error'
  return 'queued'
}

function compactDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function runNarrative(detail, selectedSummary, threshold) {
  const run = detail.run || {}
  const score = selectedSummary?.score
  if (run.status === 'queued' || run.status === 'retry_queued') {
    return 'The run is waiting in the queue. No provider call is active yet.'
  }
  if (run.status === 'running') {
    return `The run is active. It is currently in ${prettyStage(run.current_stage)} and working on attempt ${Math.max(1, Number(run.optimization_attempt || 1))}.`
  }
  if (run.status === 'completed_pass') {
    return `The run completed successfully. Attempt ${run.optimization_attempt} met the quality threshold of ${threshold} and the winner image moved to white background processing.`
  }
  if (run.status === 'completed_fail_threshold') {
    return `The run completed without reaching the quality threshold of ${threshold}. The best scored attempt stayed below the acceptance rule.`
  }
  if (run.status === 'failed_technical') {
    return `The run stopped because of a technical failure during ${prettyStage(run.current_stage)}.`
  }
  if (score != null) {
    return `The latest visible score is ${score} against a threshold of ${threshold}.`
  }
  return 'Run details are available, but the final outcome is not complete yet.'
}

function renderOverviewSection({
  detail,
  selectedSummary,
  currentAttempt,
  maxAttempts,
  threshold,
  finalAsset,
  winnerStage4Asset,
  imageCreationFailed,
  scoreTooLow,
  selectedPromptEngineerMode,
  selectedResponsesModel,
  selectedVectorStoreId,
  usesGeminiPromptEngineer,
  selectedVisualStyleName,
  selectedVisualStyleId,
  attempts,
  setSelectedAttempt,
  setImageFilter,
  selectedAttempt,
}) {
  const run = detail.run
  const latestScore = selectedSummary?.score ?? run.quality_score ?? null
  const finalImageUrl = winnerStage4Asset?.origin_url || finalAsset?.origin_url || ''

  return (
    <div className="run-detail-section-grid">
      <section className="run-snapshot-card">
        <div className="run-snapshot-head">
          <div>
            <p className="detail-eyebrow">Selected run</p>
            <h3>{run.word || '-'}</h3>
            <p className="run-snapshot-meta">
              {run.part_of_sentence || 'No POS'}
              {run.category ? ` | ${run.category}` : ' | No category'}
            </p>
          </div>
          <span className={`status-pill status-${statusTone(run.status)}`}>{prettyRunStatus(run.status)}</span>
        </div>
        <p className="run-snapshot-summary">{runNarrative(detail, selectedSummary, threshold)}</p>
        <div className="run-snapshot-metrics">
          <div className="snapshot-metric">
            <span>Current step</span>
            <strong>{prettyStage(run.current_stage)}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Attempts</span>
            <strong>{currentAttempt > 0 ? currentAttempt : 1} / {maxAttempts}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Score</span>
            <strong>{latestScore ?? '-'}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Threshold</span>
            <strong>{threshold}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Updated</span>
            <strong>{compactDate(run.updated_at)}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Run id</span>
            <strong>{run.id}</strong>
          </div>
        </div>
      </section>

      <section className="run-kpi-grid">
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Prompt engineer</p>
          <h4>{selectedPromptEngineerMode === 'responses_api' ? (usesGeminiPromptEngineer ? 'Direct model API' : 'Responses API') : 'OpenAI Assistant'}</h4>
          {selectedPromptEngineerMode === 'responses_api' ? (
            <p>{selectedResponsesModel || '-'}{!usesGeminiPromptEngineer ? ` | ${selectedVectorStoreId || 'No vector store'}` : ''}</p>
          ) : (
            <p>Assistant-based prompt generation</p>
          )}
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Visual style</p>
          <h4>{selectedVisualStyleName || '-'}</h4>
          <p>{selectedVisualStyleId || 'No style id'}</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Final image</p>
          <h4>{winnerStage4Asset ? 'Ready' : 'Not ready yet'}</h4>
          <p>{winnerStage4Asset?.file_name || 'No white-background winner image yet'}</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Attention</p>
          <h4>{imageCreationFailed || scoreTooLow ? 'Needs review' : 'No alert'}</h4>
          <p>
            {imageCreationFailed
              ? 'A provider stage failed in this run.'
              : scoreTooLow
                ? `Visible attempt score is below ${threshold}.`
                : 'No failure or threshold warning is active.'}
          </p>
        </article>
      </section>

      {(imageCreationFailed || scoreTooLow) ? (
        <div className="run-flag-row">
          {imageCreationFailed ? <span className="run-flag run-flag-error">Flag: image creation failed in this run</span> : null}
          {scoreTooLow ? <span className="run-flag run-flag-warn">Flag: score is below threshold ({threshold})</span> : null}
        </div>
      ) : null}

      <section className="run-overview-split">
        <div className="run-overview-card">
          <div className="section-head-row">
            <div>
              <h4>Attempts</h4>
              <p>Choose an attempt to inspect its image, score, and process trace.</p>
            </div>
          </div>
          <div className="attempt-chip-row">
            {attempts.map((attempt) => (
              <button
                key={attempt}
                className={attempt === selectedAttempt ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => {
                  setSelectedAttempt(attempt)
                  setImageFilter(IMAGE_FILTER.ATTEMPT)
                }}
              >
                Attempt {attempt}
              </button>
            ))}
          </div>
          <div className="attempt-summary-row compact">
            {detail.diagramAttemptSummaries?.map((summary) => (
              <div key={summary.attempt} className={summary.attempt === selectedAttempt ? 'attempt-summary active' : 'attempt-summary'}>
                <p><strong>Attempt {summary.attempt}</strong></p>
                <p>Improve: {humanStatus(summary.stage3Status)}</p>
                <p>Score: {summary.score ?? '-'}</p>
                <p>Background: {humanStatus(summary.stage4Status)}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="run-overview-card">
          <div className="section-head-row">
            <div>
              <h4>Outcome Preview</h4>
              <p>The quickest answer to “what did the run produce?”</p>
            </div>
          </div>
          {finalImageUrl ? (
            <div className="run-hero-image-card">
              <img className="asset-image" src={finalImageUrl} alt="Final run output" />
              <div className="asset-meta">
                <p><strong>{winnerStage4Asset ? 'Winner white-background image' : 'Latest visible image'}</strong></p>
                <p>{winnerStage4Asset?.file_name || finalAsset?.file_name || '-'}</p>
                {finalImageUrl ? (
                  <a href={finalImageUrl} target="_blank" rel="noreferrer">
                    Open Full Image
                  </a>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="empty-state-card">
              <p>No final image yet.</p>
              <p>If the run is still active, the image may appear after scoring chooses a winner and Stage 4 finishes.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

export default function RunExecutionDiagram({
  detail,
  assistantName = '',
  promptEngineerConfig,
  onSavePromptEngineerConfig,
}) {
  const attempts = useMemo(() => getAvailableAttempts(detail), [detail?.run?.id, detail?.run?.updated_at, detail])
  const [selectedAttempt, setSelectedAttempt] = useState(defaultAttempt(detail, attempts))
  const [imageFilter, setImageFilter] = useState(IMAGE_FILTER.ATTEMPT)
  const diagram = useMemo(() => buildRunDiagram(detail, selectedAttempt), [detail, selectedAttempt])
  const [selectedNodeId, setSelectedNodeId] = useState(currentNodeId(detail) || 'stage3_generate')
  const [showRunJson, setShowRunJson] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const [activeTab, setActiveTab] = useState(DETAIL_TABS.OVERVIEW)

  async function copyJson(label, value) {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2))
      setCopyMessage(`${label} copied`)
      window.setTimeout(() => setCopyMessage(''), 1800)
    } catch (_error) {
      setCopyMessage(`Could not copy ${label.toLowerCase()}`)
      window.setTimeout(() => setCopyMessage(''), 1800)
    }
  }

  useEffect(() => {
    const next = defaultAttempt(detail, attempts)
    setSelectedAttempt((current) => (attempts.includes(current) ? current : next))
  }, [detail?.run?.id, detail?.run?.updated_at, attempts])

  useEffect(() => {
    if (!diagram.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(diagram.nodes[0]?.id || '')
    }
  }, [diagram.nodes, selectedNodeId])

  useEffect(() => {
    const nextNodeId = currentNodeId(detail)
    if (nextNodeId) {
      setSelectedNodeId(nextNodeId)
    }
  }, [detail?.run?.id, detail?.run?.updated_at, detail?.run?.current_stage, detail?.run?.status])

  useEffect(() => {
    setImageFilter(IMAGE_FILTER.ATTEMPT)
    setActiveTab(DETAIL_TABS.OVERVIEW)
  }, [detail?.run?.id])

  if (!detail) {
    return <p>Select a run row to see live execution.</p>
  }

  const selectedNode = diagram.nodes.find((node) => node.id === selectedNodeId) || diagram.nodes[0] || null
  const currentAttempt = Number(detail.run.optimization_attempt || 0)
  const maxAttempts = Number(detail.run.max_optimization_attempts || 0) + 1
  const selectedSummary = diagram.attemptSummaries.find((summary) => summary.attempt === selectedAttempt)
  const stage1Request = detail?.stages?.find((stage) => stage.stage_name === 'stage1_prompt')?.request_json || {}
  const selectedPromptEngineerMode = String(stage1Request.prompt_engineer_mode || 'assistant')
  const selectedResponsesModel = String(stage1Request.responses_model || '')
  const selectedVectorStoreId = String(stage1Request.responses_vector_store_id || '')
  const usesGeminiPromptEngineer = selectedResponsesModel.toLowerCase().startsWith('gemini-')
  const selectedVisualStyleName = String(stage1Request.visual_style_name || '')
  const selectedVisualStyleId = String(stage1Request.visual_style_id || '')
  const threshold = Number(detail.run.quality_threshold || 95)
  const selectedAttemptScore = selectedSummary?.score ?? null
  const imageCreationFailed = (() => {
    if (detail.run.status === 'failed_technical') return true
    return (detail.stages || []).some((stage) => {
      if (!['stage2_draft', 'stage3_upgrade', 'stage4_background'].includes(stage.stage_name)) return false
      const status = String(stage.status || '').toLowerCase()
      return status.includes('error') || status.includes('fail')
    })
  })()
  const scoreTooLow = (() => {
    if (detail.run.status === 'completed_fail_threshold') return true
    if (selectedAttemptScore == null) return false
    return Number(selectedAttemptScore) < threshold
  })()
  const allRunAssets = [...(detail.assets || [])].sort((left, right) => {
    const leftAttempt = Number(left.attempt || 0)
    const rightAttempt = Number(right.attempt || 0)
    if (leftAttempt !== rightAttempt) return leftAttempt - rightAttempt
    const leftOrder = assetStageOrder[left.stage_name] || 99
    const rightOrder = assetStageOrder[right.stage_name] || 99
    if (leftOrder !== rightOrder) return leftOrder - rightOrder
    return String(left.created_at || '').localeCompare(String(right.created_at || ''))
  })
  const winnerStage4Asset =
    allRunAssets
      .filter((asset) => asset.stage_name === 'stage4_white_bg')
      .sort((left, right) => Number(right.attempt || 0) - Number(left.attempt || 0))[0] || null
  const finalAsset = winnerStage4Asset || [...allRunAssets].reverse().find((asset) => asset.origin_url) || null
  const filteredRunAssets = (() => {
    if (imageFilter === IMAGE_FILTER.DRAFT) {
      return allRunAssets.filter((asset) => asset.stage_name === 'stage2_draft')
    }
    if (imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND) {
      return allRunAssets.filter((asset) => asset.stage_name === 'stage4_white_bg')
    }
    return allRunAssets.filter((asset) => {
      if (asset.stage_name === 'stage2_draft') return true
      return Number(asset.attempt || 0) === selectedAttempt
    })
  })()
  const canvasNodes = diagram.nodes.map((node) => {
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
  })

  const detailWithSummaries = {
    ...detail,
    diagramAttemptSummaries: diagram.attemptSummaries,
  }

  return (
    <div className="run-diagram-root">
      <div className="run-diagram-head refined">
        <div>
          <p className="detail-eyebrow">Run + Details</p>
          <h3>Run Timeline</h3>
          <p className="run-head-summary">
            Start here for the current answer, then move to Images, Process, or Debug only if you need more detail.
          </p>
        </div>
        <div className="run-head-status-block">
          <span className={`status-pill status-${statusTone(detail.run.status)}`}>{prettyRunStatus(detail.run.status)}</span>
          <p>Current step: <strong>{prettyStage(detail.run.current_stage)}</strong></p>
          <p>Attempt: <strong>{currentAttempt > 0 ? currentAttempt : 1} / {maxAttempts}</strong></p>
        </div>
      </div>

      <div className="detail-subnav-row">
        <button className={activeTab === DETAIL_TABS.OVERVIEW ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.OVERVIEW)}>Overview</button>
        <button className={activeTab === DETAIL_TABS.IMAGES ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.IMAGES)}>Images</button>
        <button className={activeTab === DETAIL_TABS.PROCESS ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.PROCESS)}>Process</button>
        <button className={activeTab === DETAIL_TABS.SETTINGS ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.SETTINGS)}>Settings</button>
        <button className={activeTab === DETAIL_TABS.DEBUG ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.DEBUG)}>Debug</button>
      </div>

      {activeTab === DETAIL_TABS.OVERVIEW ? renderOverviewSection({
        detail: detailWithSummaries,
        selectedSummary,
        currentAttempt,
        maxAttempts,
        threshold,
        finalAsset,
        winnerStage4Asset,
        imageCreationFailed,
        scoreTooLow,
        selectedPromptEngineerMode,
        selectedResponsesModel,
        selectedVectorStoreId,
        usesGeminiPromptEngineer,
        selectedVisualStyleName,
        selectedVisualStyleId,
        attempts,
        setSelectedAttempt,
        setImageFilter,
        selectedAttempt,
      }) : null}

      {activeTab === DETAIL_TABS.IMAGES ? (
        <div className="run-detail-section-grid">
          <section className="run-overview-card">
            <div className="section-head-row">
              <div>
                <h4>Image Gallery</h4>
                <p>Filter by draft, attempt, or final white-background output.</p>
              </div>
            </div>
            <div className="attempt-chip-row">
              <button
                className={imageFilter === IMAGE_FILTER.DRAFT ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => setImageFilter(IMAGE_FILTER.DRAFT)}
              >
                Draft
              </button>
              {attempts.map((attempt) => (
                <button
                  key={attempt}
                  className={attempt === selectedAttempt && imageFilter === IMAGE_FILTER.ATTEMPT ? 'attempt-chip active' : 'attempt-chip'}
                  onClick={() => {
                    setSelectedAttempt(attempt)
                    setImageFilter(IMAGE_FILTER.ATTEMPT)
                  }}
                >
                  Attempt {attempt}
                </button>
              ))}
              <button
                className={imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => setImageFilter(IMAGE_FILTER.REMOVE_BACKGROUND)}
              >
                White Background
              </button>
            </div>
            <div className="run-help-card compact-help-card">
              {imageFilter === IMAGE_FILTER.DRAFT ? <p>Showing only Stage 2 draft images.</p> : null}
              {imageFilter === IMAGE_FILTER.ATTEMPT ? <p>Showing Attempt {selectedAttempt} plus the original draft for comparison.</p> : null}
              {imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND ? <p>Showing only the winner image after background removal.</p> : null}
              {winnerStage4Asset ? <p>Winner attempt: <strong>{winnerStage4Asset.attempt}</strong></p> : null}
            </div>
          </section>

          <section className="run-all-images-section">
            {filteredRunAssets.length === 0 ? (
              <div className="empty-state-card">
                <p>
                  {imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND
                    ? 'No remove-background image yet.'
                    : selectedSummary?.stage3Status === 'error'
                      ? 'No Stage 3 image is available for this attempt because the process failed after the draft image.'
                      : 'No images available for this filter yet.'}
                </p>
              </div>
            ) : (
              <div className="asset-grid">
                {filteredRunAssets.map((asset) => (
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
          </section>
        </div>
      ) : null}

      {activeTab === DETAIL_TABS.PROCESS ? (
        <div className="run-detail-section-grid">
          <div className="run-help-card">
            <p><strong>How to read this:</strong> each attempt is one full try to improve the image and pass the quality score.</p>
            <p>Flow: Stage 1 prompt -> Stage 2 draft -> Stage 3 critique/prompt/image -> Quality Gate -> winner selection -> Stage 4 white background.</p>
            <p>If quality fails and attempts remain, the system loops from Quality Gate back to Stage 3 for the next attempt.</p>
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

          <WorkflowCanvas
            nodes={canvasNodes}
            edges={diagram.edges}
            width={2200}
            height={640}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
          />

          <RunNodeDetailCard node={selectedNode} assistantName={assistantName} />
        </div>
      ) : null}

      {activeTab === DETAIL_TABS.SETTINGS ? (
        <div className="run-detail-section-grid">
          <section className="run-debug-card">
            <div>
              <h4>Prompt Engineer Configuration</h4>
              <p>This affects new runs. It does not change historical payloads already stored for this selected run.</p>
            </div>
            <div className="form-grid">
              <label>
                Prompt engineer mode
                <select
                  value={promptEngineerConfig.promptEngineerMode}
                  onChange={(e) => promptEngineerConfig.setPromptEngineerMode(e.target.value)}
                >
                  <option value="assistant">Option 1: OpenAI Assistant</option>
                  <option value="responses_api">Option 2: Responses API / Direct Model</option>
                </select>
              </label>
              <label>
                Prompt engineer model
                <select
                  value={promptEngineerConfig.responsesPromptEngineerModel}
                  onChange={(e) => promptEngineerConfig.setResponsesPromptEngineerModel(e.target.value)}
                >
                  <option value="gpt-4o-mini">gpt-4o-mini</option>
                  <option value="gpt-4.1-mini">gpt-4.1-mini</option>
                  <option value="gpt-5.4">gpt-5.4</option>
                  <option value="gemini-3-flash">Gemini-3-flash</option>
                  <option value="gemini-3-pro">Gemini-3-pro</option>
                </select>
              </label>
              <label>
                Responses vector store id
                <input
                  value={promptEngineerConfig.responsesVectorStoreId}
                  onChange={(e) => promptEngineerConfig.setResponsesVectorStoreId(e.target.value)}
                />
              </label>
              <label>
                Visual style id
                <input value={promptEngineerConfig.visualStyleId} onChange={(e) => promptEngineerConfig.setVisualStyleId(e.target.value)} />
              </label>
              <label>
                Visual style name
                <input value={promptEngineerConfig.visualStyleName} onChange={(e) => promptEngineerConfig.setVisualStyleName(e.target.value)} />
              </label>
              <label>
                Visual style instructions
                <textarea rows="12" value={promptEngineerConfig.visualStylePromptBlock} onChange={(e) => promptEngineerConfig.setVisualStylePromptBlock(e.target.value)} />
              </label>
              <label>
                Stage 1 prompt engineer input
                <textarea rows="10" value={promptEngineerConfig.stage1PromptTemplate} onChange={(e) => promptEngineerConfig.setStage1PromptTemplate(e.target.value)} />
              </label>
              <label>
                Stage 3 prompt engineer input
                <textarea rows="10" value={promptEngineerConfig.stage3PromptTemplate} onChange={(e) => promptEngineerConfig.setStage3PromptTemplate(e.target.value)} />
              </label>
              <p className="config-help-text">
                Placeholders: {'{word}'}, {'{part_of_sentence}'}, {'{category}'}, {'{context}'}, {'{boy_or_girl}'}, {'{photorealistic_hint}'}, {'{visual_style_id}'}, {'{visual_style_name}'}, {'{visual_style_block}'}, {'{old_prompt}'}, {'{challenges}'}, {'{recommendations}'}.
              </p>
              <p className="config-help-text">
                OpenAI models use Responses API with the vector store. Gemini prompt engineer models use the direct Google API and do not use the vector store.
              </p>
              <button type="button" onClick={onSavePromptEngineerConfig}>Save Prompt Engineer Settings</button>
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === DETAIL_TABS.DEBUG ? (
        <div className="run-detail-section-grid">
          <div className="run-help-card stage4-help-card">
            <p>
              <strong>Stage 4 (Background Removal):</strong>{' '}
              {stage4StatusText({
                run: detail.run,
                selectedSummary,
                winnerStage4Attempt: winnerStage4Asset ? Number(winnerStage4Asset.attempt || 0) : null,
              })}
            </p>
          </div>

          <div className="run-debug-card">
            <div>
              <h4>Raw JSON</h4>
              <p>Use this when something fails. It includes the run, stages, prompts, assets, and scores exactly as stored.</p>
            </div>
            <div className="run-debug-actions">
              <button type="button" onClick={() => copyJson('Full run JSON', detail)}>
                Copy full run JSON
              </button>
              <button type="button" onClick={() => setShowRunJson((value) => !value)}>
                {showRunJson ? 'Hide full run JSON' : 'Show full run JSON'}
              </button>
            </div>
            {copyMessage ? <p className="run-debug-copy-message">{copyMessage}</p> : null}
            {showRunJson ? <pre>{JSON.stringify(detail, null, 2)}</pre> : null}
          </div>
        </div>
      ) : null}
    </div>
  )
}
