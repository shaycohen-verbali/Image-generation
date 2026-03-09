import React, { useEffect, useMemo, useRef, useState } from 'react'
import { getConfig, getRun, listRuns, retryRun, updateConfig } from '../lib/api'
import RunExecutionDiagram from '../components/RunExecutionDiagram'

const SELECTED_RUN_STORAGE_KEY = 'aac:selectedRunId'

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
  const algoDiagramEnabled = import.meta.env.VITE_ALGO_DIAGRAM_ENABLED !== 'false'
  const [filters, setFilters] = useState({ status: '', word: '', part_of_sentence: '', category: '' })
  const [runs, setRuns] = useState([])
  const [message, setMessage] = useState('')
  const [selectedRunId, setSelectedRunId] = useState(() => window.sessionStorage.getItem(SELECTED_RUN_STORAGE_KEY) || '')
  const [detail, setDetail] = useState(null)
  const [assistantName, setAssistantName] = useState('')
  const [promptEngineerMode, setPromptEngineerMode] = useState('assistant')
  const [responsesPromptEngineerModel, setResponsesPromptEngineerModel] = useState('gpt-4.1-mini')
  const [responsesVectorStoreId, setResponsesVectorStoreId] = useState('vs_683f3d36223481919f59fc5623286253')
  const [visualStyleId, setVisualStyleId] = useState('warm_watercolor_storybook_kids_v3')
  const [visualStyleName, setVisualStyleName] = useState('Warm Watercolor Storybook Kids Style v3')
  const [visualStylePromptBlock, setVisualStylePromptBlock] = useState('')
  const [stage1PromptTemplate, setStage1PromptTemplate] = useState('')
  const [stage3PromptTemplate, setStage3PromptTemplate] = useState('')
  const selectedRunIdRef = useRef('')

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
    if (selectedRunId) {
      window.sessionStorage.setItem(SELECTED_RUN_STORAGE_KEY, selectedRunId)
    }
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
        window.sessionStorage.setItem(SELECTED_RUN_STORAGE_KEY, data[0].id)
        loadRunDetail(data[0].id)
      } else if (activeRunId) {
        const exists = data.some((run) => run.id === activeRunId)
        if (exists) {
          loadRunDetail(activeRunId)
        } else if (data.length > 0) {
          setSelectedRunId(data[0].id)
          selectedRunIdRef.current = data[0].id
          window.sessionStorage.setItem(SELECTED_RUN_STORAGE_KEY, data[0].id)
          loadRunDetail(data[0].id)
        } else {
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

  const selectedRunPromptEngineerMode =
    detail?.stages?.find((stage) => stage.stage_name === 'stage1_prompt')?.request_json?.prompt_engineer_mode || 'assistant'
  const selectedRunVisualStyleName =
    detail?.stages?.find((stage) => stage.stage_name === 'stage1_prompt')?.request_json?.visual_style_name || visualStyleName

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
    <section className="runs-page-stack">
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
        ) : algoDiagramEnabled ? (
          <>
            <div className="run-debug-card">
              <div>
                <h4>Prompt Engineer Details</h4>
                <p>Current runtime mode: <strong>{promptEngineerMode}</strong></p>
                <p>Selected run used: <strong>{selectedRunPromptEngineerMode}</strong></p>
                <p>House style for new runs: <strong>{visualStyleName}</strong></p>
                <p>Selected run style: <strong>{selectedRunVisualStyleName}</strong></p>
              </div>
              <div className="form-grid">
                <label>
                  Prompt engineer mode
                  <select value={promptEngineerMode} onChange={(e) => setPromptEngineerMode(e.target.value)}>
                    <option value="assistant">Option 1: OpenAI Assistant</option>
                    <option value="responses_api">Option 2: Responses API + Vector Store</option>
                  </select>
                </label>
                <label>
                  Responses API model
                  <input
                    value={responsesPromptEngineerModel}
                    onChange={(e) => setResponsesPromptEngineerModel(e.target.value)}
                  />
                </label>
                <label>
                  Responses vector store id
                  <input
                    value={responsesVectorStoreId}
                    onChange={(e) => setResponsesVectorStoreId(e.target.value)}
                  />
                </label>
                <label>
                  Visual style id
                  <input value={visualStyleId} onChange={(e) => setVisualStyleId(e.target.value)} />
                </label>
                <label>
                  Visual style name
                  <input value={visualStyleName} onChange={(e) => setVisualStyleName(e.target.value)} />
                </label>
                <label>
                  Visual style instructions
                  <textarea rows="12" value={visualStylePromptBlock} onChange={(e) => setVisualStylePromptBlock(e.target.value)} />
                </label>
                <label>
                  Stage 1 prompt engineer input
                  <textarea rows="10" value={stage1PromptTemplate} onChange={(e) => setStage1PromptTemplate(e.target.value)} />
                </label>
                <label>
                  Stage 3 prompt engineer input
                  <textarea rows="10" value={stage3PromptTemplate} onChange={(e) => setStage3PromptTemplate(e.target.value)} />
                </label>
                <p className="config-help-text">
                  Placeholders: {'{word}'}, {'{part_of_sentence}'}, {'{category}'}, {'{context}'}, {'{boy_or_girl}'}, {'{photorealistic_hint}'}, {'{visual_style_id}'}, {'{visual_style_name}'}, {'{visual_style_block}'}, {'{old_prompt}'}, {'{challenges}'}, {'{recommendations}'}.
                </p>
                <button type="button" onClick={onSavePromptEngineerConfig}>Save Prompt Engineer Details</button>
              </div>
            </div>
            <RunExecutionDiagram detail={detail} assistantName={assistantName} />
          </>
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
    </section>
  )
}
