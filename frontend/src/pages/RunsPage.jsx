import React, { useEffect, useMemo, useRef, useState } from 'react'
import { buildAssetContentUrl, clearTerminalRuns, deleteRun, getConfig, getRun, listRuns, retryRun, stopRun, updateConfig } from '../lib/api'
import PageErrorBoundary from '../components/PageErrorBoundary'
import RunExecutionDiagram from '../components/RunExecutionDiagram'
import DeferredAssetImage from '../components/DeferredAssetImage'

const SELECTED_RUN_STORAGE_KEY = 'aac:selectedRunId'
const RUNS_POLL_MS = 30000
const DETAIL_POLL_RUNNING_MS = 12000
const DETAIL_POLL_WAITING_MS = 20000

function isTerminalRunStatus(status) {
  const value = String(status || '').toLowerCase()
  return ['completed_pass', 'completed_fail_threshold', 'failed_technical', 'canceled'].includes(value)
}

function isWaitingRunStatus(status) {
  const value = String(status || '').toLowerCase()
  return ['queued', 'retry_queued'].includes(value)
}

function canStopRun(status) {
  const value = String(status || '').toLowerCase()
  return ['queued', 'retry_queued', 'running', 'cancel_requested'].includes(value)
}

function shouldPollRuns(runs) {
  return (Array.isArray(runs) ? runs : []).some((run) => !isTerminalRunStatus(run?.status))
}

function getStoredRunId() {
  try {
    if (typeof window === 'undefined') return ''
    return window.sessionStorage.getItem(SELECTED_RUN_STORAGE_KEY) || ''
  } catch (_error) {
    return ''
  }
}

function setStoredRunId(runId) {
  try {
    if (typeof window === 'undefined') return
    if (runId) {
      window.sessionStorage.setItem(SELECTED_RUN_STORAGE_KEY, runId)
    } else {
      window.sessionStorage.removeItem(SELECTED_RUN_STORAGE_KEY)
    }
  } catch (_error) {
    // Ignore storage failures and keep the page usable.
  }
}

function dedupeById(items) {
  const map = new Map()
  ;(Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || !item.id) return
    map.set(item.id, item)
  })
  return Array.from(map.values())
}

function dedupeCostRows(items) {
  const map = new Map()
  ;(Array.isArray(items) ? items : []).forEach((item) => {
    if (!item) return
    const key = `${item.stage_name || 'stage'}:${item.attempt || 0}:${item.model || ''}:${item.estimate_basis || ''}`
    map.set(key, item)
  })
  return Array.from(map.values())
}

function mergeRunDetail(previous, next) {
  if (!next) return previous
  if (!previous) return next
  if (!previous.run || !next.run || previous.run.id !== next.run.id) return next
  return {
    ...previous,
    ...next,
    run: { ...previous.run, ...next.run },
    stages: dedupeById([...(previous.stages || []), ...(next.stages || [])]),
    prompts: dedupeById([...(previous.prompts || []), ...(next.prompts || [])]),
    assets: dedupeById([...(previous.assets || []), ...(next.assets || [])]),
    scores: dedupeById([...(previous.scores || []), ...(next.scores || [])]),
    cost_summary: {
      ...(previous.cost_summary || {}),
      ...(next.cost_summary || {}),
      stage_costs: dedupeCostRows([
        ...((previous.cost_summary || {}).stage_costs || []),
        ...((next.cost_summary || {}).stage_costs || []),
      ]),
    },
  }
}

function stageTitle(stageName) {
  if (stageName === 'stage2_draft') return 'Stage 2 Draft'
  if (stageName === 'stage3_upgraded') return 'Stage 3 Upgraded'
  if (stageName === 'stage4_white_bg') return 'Stage 4 White Background'
  if (stageName === 'stage4_variant_generate') return 'Character Variant Final'
  if (stageName === 'stage5_variant_white_bg') return 'Character Variant White Background'
  return stageName
}

const stagePriority = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage4_white_bg: 3,
  stage4_variant_generate: 4,
  stage5_variant_white_bg: 5,
}

export default function RunsPage() {
  const algoDiagramEnabled = import.meta.env.VITE_ALGO_DIAGRAM_ENABLED !== 'false'
  const [filters, setFilters] = useState({ status: '', word: '', part_of_sentence: '', category: '' })
  const [runs, setRuns] = useState([])
  const [message, setMessage] = useState('')
  const [selectedRunId, setSelectedRunId] = useState(() => getStoredRunId())
  const [detail, setDetail] = useState(null)
  const [assistantName, setAssistantName] = useState('')
  const [promptEngineerMode, setPromptEngineerMode] = useState('responses_api')
  const [responsesPromptEngineerModel, setResponsesPromptEngineerModel] = useState('gpt-5.4')
  const [responsesVectorStoreId, setResponsesVectorStoreId] = useState('vs_683f3d36223481919f59fc5623286253')
  const [visualStyleId, setVisualStyleId] = useState('warm_watercolor_storybook_kids_v3')
  const [visualStyleName, setVisualStyleName] = useState('Warm Watercolor Storybook Kids Style v3')
  const [visualStylePromptBlock, setVisualStylePromptBlock] = useState('')
  const [stage1PromptTemplate, setStage1PromptTemplate] = useState('')
  const [stage3PromptTemplate, setStage3PromptTemplate] = useState('')
  const [imageAspectRatio, setImageAspectRatio] = useState('1:1')
  const [imageResolution, setImageResolution] = useState('1K')
  const [selectedDetailTab, setSelectedDetailTab] = useState('overview')
  const [pageVisible, setPageVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })
  const selectedRunIdRef = useRef('')
  const runsRef = useRef([])
  const detailStateRef = useRef(null)
  const detailRef = useRef(null)

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
    setStoredRunId(selectedRunId)
  }, [selectedRunId])

  useEffect(() => {
    runsRef.current = runs
  }, [runs])

  useEffect(() => {
    detailStateRef.current = detail
  }, [detail])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined
    const handleVisibilityChange = () => {
      setPageVisible(document.visibilityState !== 'hidden')
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

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

  async function loadRunDetail(runId, { isPolling = false, includeDebug = false } = {}) {
    if (!runId) return
    try {
      const data = await getRun(runId, { includeDebug })
      if (selectedRunIdRef.current && selectedRunIdRef.current !== runId) {
        return
      }
      setDetail((previous) => mergeRunDetail(previous, data))
    } catch (error) {
      if (isPolling && detailStateRef.current?.run?.id === runId) {
        return
      }
      setMessage(`Error loading detail: ${error.message}`)
    }
  }

  function scrollToDetail() {
    window.requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function selectRun(runId, options = {}) {
    if (detailStateRef.current?.run?.id !== runId) {
      setDetail(null)
    }
    setSelectedRunId(runId)
    selectedRunIdRef.current = runId
    if (options.scrollToDetail) {
      scrollToDetail()
    }
  }

  async function refreshRuns({ isPolling = false } = {}) {
    try {
      const data = await listRuns(query)
      setRuns(data)

      const activeRunId = selectedRunIdRef.current
      if (!activeRunId && data.length > 0) {
        setSelectedRunId(data[0].id)
        selectedRunIdRef.current = data[0].id
        setStoredRunId(data[0].id)
      } else if (activeRunId) {
        const exists = data.some((run) => run.id === activeRunId)
        if (!exists && data.length > 0) {
          setSelectedRunId(data[0].id)
          selectedRunIdRef.current = data[0].id
          setStoredRunId(data[0].id)
        } else if (!exists) {
          setSelectedRunId('')
          selectedRunIdRef.current = ''
          setStoredRunId('')
          setDetail(null)
        }
      }
    } catch (error) {
      if (isPolling && (runsRef.current.length > 0 || detailStateRef.current?.run)) {
        return
      }
      setMessage(`Error loading runs: ${error.message}`)
    }
  }

  useEffect(() => {
    let mounted = true
    const loadConfig = async () => {
      try {
        const config = await getConfig()
        if (mounted && config?.openai_assistant_name) {
          setAssistantName(config.openai_assistant_name)
        }
        if (mounted && config?.prompt_engineer_mode) {
          setPromptEngineerMode(config.prompt_engineer_mode)
        }
        if (mounted && config?.responses_prompt_engineer_model) {
          setResponsesPromptEngineerModel(config.responses_prompt_engineer_model)
        }
        if (mounted && config?.responses_vector_store_id) {
          setResponsesVectorStoreId(config.responses_vector_store_id)
        }
        if (mounted && config?.visual_style_id) {
          setVisualStyleId(config.visual_style_id)
        }
        if (mounted && config?.visual_style_name) {
          setVisualStyleName(config.visual_style_name)
        }
        if (mounted && typeof config?.visual_style_prompt_block === 'string') {
          setVisualStylePromptBlock(config.visual_style_prompt_block)
        }
        if (mounted && typeof config?.stage1_prompt_template === 'string') {
          setStage1PromptTemplate(config.stage1_prompt_template)
        }
        if (mounted && typeof config?.stage3_prompt_template === 'string') {
          setStage3PromptTemplate(config.stage3_prompt_template)
        }
        if (mounted && config?.image_aspect_ratio) {
          setImageAspectRatio(config.image_aspect_ratio)
        }
        if (mounted && config?.image_resolution) {
          setImageResolution(config.image_resolution)
        }
      } catch (_error) {
        // Keep fallback value.
      }
    }
    loadConfig()
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    refreshRuns()
    const timer = setInterval(() => {
      if (!pageVisible) return
      if (!shouldPollRuns(runsRef.current)) return
      refreshRuns({ isPolling: true })
    }, RUNS_POLL_MS)
    return () => clearInterval(timer)
  }, [query, pageVisible])

  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null)
      return undefined
    }
    const includeDebug = selectedDetailTab === 'debug'
    const currentStatus = detailStateRef.current?.run?.status
    const pollMs = includeDebug
      ? DETAIL_POLL_WAITING_MS
      : isWaitingRunStatus(currentStatus)
        ? DETAIL_POLL_WAITING_MS
        : DETAIL_POLL_RUNNING_MS
    loadRunDetail(selectedRunId, { includeDebug })
    const timer = setInterval(() => {
      if (!pageVisible) return
      const activeRunId = selectedRunIdRef.current
      const activeDetail = detailStateRef.current
      const activeStatus = activeDetail?.run?.status
      if (!activeRunId) return
      if (isTerminalRunStatus(activeStatus)) return
      loadRunDetail(activeRunId, { isPolling: true, includeDebug })
    }, pollMs)
    return () => clearInterval(timer)
  }, [selectedRunId, selectedDetailTab, pageVisible, detail?.run?.status])

  useEffect(() => {
    if (!pageVisible) return undefined
    const handleFocus = () => {
      refreshRuns({ isPolling: true })
      if (selectedRunIdRef.current) {
        const includeDebug = selectedDetailTab === 'debug'
        loadRunDetail(selectedRunIdRef.current, { isPolling: true, includeDebug })
      }
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [pageVisible, selectedDetailTab])

  const onRetry = async (runId) => {
    try {
      await retryRun(runId)
      setMessage(`Run ${runId} queued for retry`)
      refreshRuns()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onStop = async (runId) => {
    try {
      const result = await stopRun(runId)
      setMessage(result.message || `Run ${runId} stop requested`)
      refreshRuns()
      if (selectedRunIdRef.current === runId) {
        loadRunDetail(runId, { includeDebug: selectedDetailTab === 'debug' })
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onDeleteRun = async (runId) => {
    try {
      const result = await deleteRun(runId)
      setMessage(`Deleted ${result.deleted_run_count} run`)
      if (selectedRunIdRef.current === runId) {
        setSelectedRunId('')
        selectedRunIdRef.current = ''
        setStoredRunId('')
        setDetail(null)
      }
      refreshRuns()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onClearTerminalHistory = async () => {
    try {
      const result = await clearTerminalRuns()
      setMessage(`Cleared ${result.deleted_run_count} terminal runs`)
      setSelectedRunId('')
      selectedRunIdRef.current = ''
      setStoredRunId('')
      setDetail(null)
      refreshRuns()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onSavePromptEngineerConfig = async () => {
    try {
      const updated = await updateConfig({
        prompt_engineer_mode: promptEngineerMode,
        responses_prompt_engineer_model: responsesPromptEngineerModel,
        responses_vector_store_id: responsesVectorStoreId,
        visual_style_id: visualStyleId,
        visual_style_name: visualStyleName,
        visual_style_prompt_block: visualStylePromptBlock,
        stage1_prompt_template: stage1PromptTemplate,
        stage3_prompt_template: stage3PromptTemplate,
        image_aspect_ratio: imageAspectRatio,
        image_resolution: imageResolution,
      })
      setPromptEngineerMode(updated.prompt_engineer_mode)
      setResponsesPromptEngineerModel(updated.responses_prompt_engineer_model)
      setResponsesVectorStoreId(updated.responses_vector_store_id)
      setVisualStyleId(updated.visual_style_id)
      setVisualStyleName(updated.visual_style_name)
      setVisualStylePromptBlock(updated.visual_style_prompt_block)
      setStage1PromptTemplate(updated.stage1_prompt_template)
      setStage3PromptTemplate(updated.stage3_prompt_template)
      setImageAspectRatio(updated.image_aspect_ratio)
      setImageResolution(updated.image_resolution)
      setMessage('Saved prompt engineer, visual style, and image output configuration')
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="runs-page-stack">
      <section className="runs-layout runs-layout-stacked">
        <article className="card runs-floor-card">
          <div className="runs-floor-head">
            <div>
              <p className="detail-eyebrow">First Floor</p>
              <h2>Runs</h2>
              <p className="runs-floor-copy">Choose a run here, then inspect it in full width below.</p>
            </div>
            <div className="runs-floor-summary">
              <span>{filteredRuns.length} shown</span>
              <span>{runs.length} total</span>
              <button type="button" onClick={() => refreshRuns()} className="button-secondary">Refresh</button>
              <button type="button" onClick={onClearTerminalHistory} className="button-secondary">Clear Terminal History</button>
            </div>
          </div>

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

          <div className="table-wrap runs-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Word</th>
                  <th>POS</th>
                  <th>Category</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Attempt</th>
                  <th>Est. cost</th>
                  <th>Est. avg / image</th>
                  <th>Stop</th>
                  <th>Retry</th>
                  <th>Delete</th>
                </tr>
              </thead>
              <tbody>
                {filteredRuns.map((run) => (
                  <tr
                    key={run.id}
                    className={run.id === selectedRunId ? 'selected-row' : 'clickable-row'}
                    onClick={() => selectRun(run.id, { scrollToDetail: true })}
                  >
                    <td>{run.word || '-'}</td>
                    <td>{run.part_of_sentence || '-'}</td>
                    <td>{run.category || '-'}</td>
                    <td>{run.status}</td>
                    <td>{run.quality_score ?? '-'}</td>
                    <td>{run.optimization_attempt}</td>
                    <td>{typeof run.estimated_total_cost_usd === 'number' ? `$${Number(run.estimated_total_cost_usd).toFixed(4)}` : '-'}</td>
                    <td>{run.estimated_cost_per_image_usd != null ? `$${Number(run.estimated_cost_per_image_usd).toFixed(4)}` : '-'}</td>
                    <td>
                      <button
                        onClick={(event) => {
                          event.stopPropagation()
                          onStop(run.id)
                        }}
                        disabled={!canStopRun(run.status)}
                      >
                        {String(run.status || '').toLowerCase() === 'cancel_requested' ? 'Stopping…' : 'Stop'}
                      </button>
                    </td>
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
                    <td>
                      <button
                        onClick={(event) => {
                          event.stopPropagation()
                          onDeleteRun(run.id)
                        }}
                        disabled={!isTerminalRunStatus(run.status)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>{message}</p>
        </article>

        <article ref={detailRef} className="card run-detail-floor-card">
          <div className="runs-floor-head">
            <div>
              <p className="detail-eyebrow">Second Floor</p>
              <h2>Run Detail</h2>
              <p className="runs-floor-copy">The selected run gets the full page width so the story, images, and process are easier to read.</p>
            </div>
          </div>

          {!detail ? (
            <p>Select a run row to see details.</p>
          ) : algoDiagramEnabled ? (
            <PageErrorBoundary resetKey={`${detail?.run?.id || ''}:${detail?.run?.updated_at || ''}`}>
              <RunExecutionDiagram
                detail={detail}
                assistantName={assistantName}
                onActiveTabChange={setSelectedDetailTab}
                promptEngineerConfig={{
                  promptEngineerMode,
                  setPromptEngineerMode,
                  responsesPromptEngineerModel,
                  setResponsesPromptEngineerModel,
                  responsesVectorStoreId,
                  setResponsesVectorStoreId,
                  visualStyleId,
                  setVisualStyleId,
                  visualStyleName,
                  setVisualStyleName,
                  visualStylePromptBlock,
                  setVisualStylePromptBlock,
                  stage1PromptTemplate,
                  setStage1PromptTemplate,
                  stage3PromptTemplate,
                  setStage3PromptTemplate,
                  imageAspectRatio,
                  setImageAspectRatio,
                  imageResolution,
                  setImageResolution,
                }}
                onSavePromptEngineerConfig={onSavePromptEngineerConfig}
                onStopRun={onStop}
              />
            </PageErrorBoundary>
          ) : (
            <>
              <h3>Run</h3>
              <pre>{JSON.stringify(detail.run, null, 2)}</pre>

              <h3>Final Image</h3>
              {finalAsset?.id ? (
                <div className="asset-card">
                  <img className="asset-image" src={buildAssetContentUrl(finalAsset)} alt="Final white background output" loading="lazy" decoding="async" />
                  <div className="asset-meta">
                    <p>{finalAsset.file_name}</p>
                    <a href={buildAssetContentUrl(finalAsset)} target="_blank" rel="noreferrer">
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
                      {asset.id ? (
                        <DeferredAssetImage asset={asset} alt={`${asset.stage_name} attempt ${asset.attempt}`} />
                      ) : (
                        <p>Image URL unavailable.</p>
                      )}
                      <div className="asset-meta">
                        <p>Attempt: {asset.attempt}</p>
                        <p>Model: {asset.model_name}</p>
                        {asset.id ? (
                          <a href={buildAssetContentUrl(asset)} target="_blank" rel="noreferrer">
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
    </section>
  )
}
