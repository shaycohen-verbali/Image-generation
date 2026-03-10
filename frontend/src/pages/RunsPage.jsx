import React, { useEffect, useMemo, useRef, useState } from 'react'
import { buildAssetContentUrl, getConfig, getRun, listRuns, retryRun, updateConfig } from '../lib/api'
import PageErrorBoundary from '../components/PageErrorBoundary'
import RunExecutionDiagram from '../components/RunExecutionDiagram'

const SELECTED_RUN_STORAGE_KEY = 'aac:selectedRunId'

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
  const selectedRunIdRef = useRef('')
  const detailRef = useRef(null)

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
    setStoredRunId(selectedRunId)
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

  async function loadRunDetail(runId) {
    try {
      const data = await getRun(runId)
      if (selectedRunIdRef.current && selectedRunIdRef.current !== runId) {
        return
      }
      setDetail((previous) => mergeRunDetail(previous, data))
    } catch (error) {
      setMessage(`Error loading detail: ${error.message}`)
    }
  }

  function scrollToDetail() {
    window.requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function selectRun(runId, options = {}) {
    setSelectedRunId(runId)
    selectedRunIdRef.current = runId
    loadRunDetail(runId)
    if (options.scrollToDetail) {
      scrollToDetail()
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
        setStoredRunId(data[0].id)
        loadRunDetail(data[0].id)
      } else if (activeRunId) {
        const exists = data.some((run) => run.id === activeRunId)
        if (exists) {
          loadRunDetail(activeRunId)
        } else if (data.length > 0) {
          setSelectedRunId(data[0].id)
          selectedRunIdRef.current = data[0].id
          setStoredRunId(data[0].id)
          loadRunDetail(data[0].id)
        } else {
          setSelectedRunId('')
          selectedRunIdRef.current = ''
          setStoredRunId('')
          setDetail(null)
        }
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
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
      })
      setPromptEngineerMode(updated.prompt_engineer_mode)
      setResponsesPromptEngineerModel(updated.responses_prompt_engineer_model)
      setResponsesVectorStoreId(updated.responses_vector_store_id)
      setVisualStyleId(updated.visual_style_id)
      setVisualStyleName(updated.visual_style_name)
      setVisualStylePromptBlock(updated.visual_style_prompt_block)
      setStage1PromptTemplate(updated.stage1_prompt_template)
      setStage3PromptTemplate(updated.stage3_prompt_template)
      setMessage('Saved prompt engineer and visual style configuration')
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
                  <th>Retry</th>
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
                }}
                onSavePromptEngineerConfig={onSavePromptEngineerConfig}
              />
            </PageErrorBoundary>
          ) : (
            <>
              <h3>Run</h3>
              <pre>{JSON.stringify(detail.run, null, 2)}</pre>

              <h3>Final Image</h3>
              {finalAsset?.id ? (
                <div className="asset-card">
                  <img className="asset-image" src={buildAssetContentUrl(finalAsset)} alt="Final white background output" />
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
                        <img className="asset-image" src={buildAssetContentUrl(asset)} alt={`${asset.stage_name} attempt ${asset.attempt}`} />
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
