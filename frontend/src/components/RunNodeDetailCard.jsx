import React, { useMemo, useState } from 'react'

function shortHash(value) {
  if (!value) return ''
  const text = String(value)
  if (text.length <= 16) return text
  return `${text.slice(0, 8)}...${text.slice(-8)}`
}

function prettyValue(value) {
  if (value === undefined || value === null || value === '') return '-'
  if (Array.isArray(value)) return value.join(', ') || '-'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function summarizeNode(node) {
  if (!node) return []
  const response = node.responseJson || {}
  const score = node.score || null

  if (node.id === 'stage1_prompt') {
    const parsed = response.parsed || {}
    return [
      ['Need person', parsed['need a person'] || parsed.need_person || node.promptNeedsPerson || '-'],
      ['Prompt engineer output', parsed['first prompt'] ? 'First prompt returned' : 'No parsed prompt'],
      ['Render style', response.decision?.render_style_mode || '-'],
    ]
  }

  if (node.id === 'stage3_critique') {
    const analysis = response.analysis || response
    return [
      ['Person needed for clarity', analysis.person_needed_for_clarity || '-'],
      ['Presence issue', analysis.person_presence_problem || '-'],
      ['Why', analysis.person_decision_reasoning || '-'],
      ['Challenges', analysis.challenges || '-'],
      ['Recommendations', analysis.recommendations || '-'],
    ]
  }

  if (node.id === 'stage3_prompt_upgrade') {
    const parsed = response.parsed || response.prompt_engineer?.parsed || {}
    return [
      ['Resolved need person', response.decision?.resolved_need_person || '-'],
      ['Render style', response.decision?.render_style_mode || '-'],
      ['Upgraded prompt', parsed['upgraded prompt'] ? 'Returned' : 'Missing'],
    ]
  }

  if (node.id === 'stage3_generate') {
    const generation = response
    return [
      ['Generation model', generation.model || node.model || '-'],
      ['Status', generation.status || node.stageStatus || '-'],
      ['Output URL', generation.output || node.asset?.origin_url || '-'],
    ]
  }

  if (node.id === 'quality_gate') {
    const rubric = (response.rubric || node.scoreRubric?.rubric || {})
    return [
      ['Score', score?.score_0_100 ?? rubric.score ?? '-'],
      ['Pass', typeof score?.pass_fail === 'boolean' ? (score.pass_fail ? 'yes' : 'no') : '-'],
      ['Failure tags', rubric.failure_tags || '-'],
      ['Explanation', rubric.explanation || '-'],
    ]
  }

  if (node.id === 'stage4_background') {
    return [
      ['Background model', response.model || node.model || '-'],
      ['Status', response.status || node.stageStatus || '-'],
      ['Winner output', response.output || node.asset?.origin_url || '-'],
    ]
  }

  return [
    ['Stage status', node.stageStatus || node.status || '-'],
    ['Recorded at', node.stageCreatedAt || '-'],
  ]
}

export default function RunNodeDetailCard({ node, assistantName = '' }) {
  const [showRaw, setShowRaw] = useState(false)
  const [showRawPrompt, setShowRawPrompt] = useState(false)
  const [showRawScore, setShowRawScore] = useState(false)
  const [showRequestResponse, setShowRequestResponse] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const summaryRows = useMemo(() => summarizeNode(node), [node])

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

  if (!node) {
    return (
      <div className="run-node-detail">
        <p>Select a block to inspect details.</p>
      </div>
    )
  }

  const normalizedPromptModel = String(node.model || '').toLowerCase()
  const usesResponsesApi = normalizedPromptModel.includes('responses api')
  const usesDirectModel = normalizedPromptModel.includes('direct model')
  const decision = node.responseJson?.decision || node.requestJson || {}
  const hasPromptLineage = Boolean(node.promptText || node.promptNeedsPerson || (node.promptRaw && Object.keys(node.promptRaw).length))
  const hasQuality = Boolean(node.score || (node.scoreRubric && Object.keys(node.scoreRubric).length))

  return (
    <div className="run-node-detail">
      <div className="run-node-detail-head">
        <div>
          <p className="detail-eyebrow">Selected block</p>
          <h3>{node.label}</h3>
        </div>
        <span className={`status-pill status-${node.status}`}>{node.statusLabel}</span>
      </div>

      <div className="run-node-summary-strip">
        <div className="run-node-summary-card">
          <span>Attempt</span>
          <strong>{typeof node.attempt === 'number' && node.attempt > 0 ? `Attempt ${node.attempt}` : 'Base stage'}</strong>
        </div>
        <div className="run-node-summary-card">
          <span>Provider</span>
          <strong>{node.provider || '-'}</strong>
        </div>
        <div className="run-node-summary-card">
          <span>Model</span>
          <strong>{node.model || 'N/A'}</strong>
        </div>
        <div className="run-node-summary-card">
          <span>Person decision</span>
          <strong>{decision.resolved_need_person ? (decision.resolved_need_person === 'yes' ? 'Person required' : 'No person') : 'Not applicable'}</strong>
        </div>
      </div>

      <div className="run-node-main-grid">
        <section className="run-node-main-card">
          <h4>What this step did</h4>
          {node.subtitle ? <p><strong>Summary:</strong> {node.subtitle}</p> : null}
          {node.id === 'stage1_prompt' || node.id === 'stage3_prompt_upgrade' ? (
            usesResponsesApi ? (
              <p><strong>Prompt engineer:</strong> Responses API</p>
            ) : usesDirectModel ? (
              <p><strong>Prompt engineer:</strong> Direct model API</p>
            ) : (
              <p><strong>Assistant:</strong> {assistantName || 'Prompt generator -JSON output'}</p>
            )
          ) : null}
          <p><strong>Stage status:</strong> {node.stageStatus || node.status}</p>
          <p><strong>Recorded at:</strong> {node.stageCreatedAt || 'N/A'}</p>
          {decision.render_style_mode ? <p><strong>Resolved render style:</strong> {decision.render_style_mode}</p> : null}
          {decision.person_needed_for_clarity ? <p><strong>Stage 3.1 person decision:</strong> {decision.person_needed_for_clarity}</p> : null}
          {decision.person_presence_problem ? <p><strong>Presence issue:</strong> {decision.person_presence_problem}</p> : null}
          {decision.resolved_need_person_reasoning ? <p><strong>Decision reason:</strong> {decision.resolved_need_person_reasoning}</p> : null}
          {node.stageErrorDetail ? <p><strong>Error detail:</strong> {node.stageErrorDetail}</p> : null}
        </section>

        <section className="run-node-main-card">
          <h4>Important outputs</h4>
          {summaryRows.map(([label, value]) => (
            <p key={label}><strong>{label}:</strong> {prettyValue(value)}</p>
          ))}
        </section>
      </div>

      <section className="run-node-wide-card">
        <h4>AI Instruction</h4>
        <p><strong>Source:</strong> {node.aiInstructionSource || 'N/A'}</p>
        {node.aiInstruction ? (
          <pre className="prompt-doc-box">{node.aiInstruction}</pre>
        ) : (
          <p>No instruction text recorded for this block.</p>
        )}
      </section>

      {hasPromptLineage ? (
        <section className="run-node-wide-card">
          <div className="run-node-wide-head">
            <h4>Prompt Lineage</h4>
            <button type="button" onClick={() => setShowRawPrompt((value) => !value)}>
              {showRawPrompt ? 'Hide prompt engineer response JSON' : 'View prompt engineer response JSON'}
            </button>
          </div>
          <p><strong>Prompt source:</strong> {node.promptSource || 'N/A'}</p>
          <p><strong>Need person:</strong> {node.promptNeedsPerson || 'N/A'}</p>
          <p><strong>Prompt created:</strong> {node.promptCreatedAt || 'N/A'}</p>
          {node.promptText ? (
            <>
              <p><strong>Prompt text:</strong></p>
              <pre className="prompt-doc-box">{node.promptText}</pre>
            </>
          ) : null}
          {showRawPrompt ? <pre>{JSON.stringify(node.promptRaw || {}, null, 2)}</pre> : null}
        </section>
      ) : null}

      <section className="run-node-wide-card">
        <div className="run-node-wide-head">
          <h4>Request / Response</h4>
          <div className="run-node-inline-actions">
            <button type="button" onClick={() => copyJson('Stage request/response JSON', {
              request_json: node.requestJson,
              response_json: node.responseJson,
            })}>
              Copy stage request/response JSON
            </button>
            <button type="button" onClick={() => setShowRequestResponse((value) => !value)}>
              {showRequestResponse ? 'Hide request/response JSON' : 'Show request/response JSON'}
            </button>
          </div>
        </div>
        <p><strong>Request keys:</strong> {node.requestKeys.length > 0 ? node.requestKeys.join(', ') : 'none'}</p>
        <p><strong>Response keys:</strong> {node.responseKeys.length > 0 ? node.responseKeys.join(', ') : 'none'}</p>
        {showRequestResponse ? (
          <div className="run-node-main-grid">
            <div className="run-node-main-card">
              <h4>Request JSON</h4>
              <pre>{JSON.stringify(node.requestJson || {}, null, 2)}</pre>
            </div>
            <div className="run-node-main-card">
              <h4>Response JSON</h4>
              <pre>{JSON.stringify(node.responseJson || {}, null, 2)}</pre>
            </div>
          </div>
        ) : null}
      </section>

      {hasQuality ? (
        <section className="run-node-wide-card">
          <div className="run-node-wide-head">
            <h4>Quality</h4>
            <button type="button" onClick={() => setShowRawScore((value) => !value)}>
              {showRawScore ? 'Hide score rubric JSON' : 'View score rubric JSON'}
            </button>
          </div>
          {node.score ? (
            <>
              <p><strong>Score:</strong> {node.score.score_0_100}</p>
              <p><strong>Pass:</strong> {node.score.pass_fail ? 'yes' : 'no'}</p>
              <p><strong>Stage:</strong> {node.score.stage_name || 'quality_gate'}</p>
            </>
          ) : (
            <p>No score payload for this block.</p>
          )}
          {showRawScore ? <pre>{JSON.stringify(node.scoreRubric || {}, null, 2)}</pre> : null}
        </section>
      ) : null}

      <section className="run-debug-card">
        <div>
          <h4>Debug JSON</h4>
          <p>Use these buttons to copy the exact payload stored for this selected block.</p>
        </div>
        <div className="run-debug-actions">
          <button type="button" onClick={() => copyJson('Block JSON', {
            stage_result: {
              request_json: node.requestJson,
              response_json: node.responseJson,
              error_detail: node.stageErrorDetail || '',
              status: node.stageStatus || node.status,
              created_at: node.stageCreatedAt || '',
            },
            prompt: node.promptRaw || {},
            score: node.scoreRubric || {},
            asset: node.asset || null,
          })}>
            Copy selected block JSON
          </button>
          <button type="button" onClick={() => setShowRaw((value) => !value)}>
            {showRaw ? 'Hide selected block JSON' : 'Show selected block JSON'}
          </button>
        </div>
        {copyMessage ? <p className="run-debug-copy-message">{copyMessage}</p> : null}
        {showRaw ? (
          <pre>
            {JSON.stringify(
              {
                stage_result: {
                  request_json: node.requestJson,
                  response_json: node.responseJson,
                  error_detail: node.stageErrorDetail || '',
                  status: node.stageStatus || node.status,
                  created_at: node.stageCreatedAt || '',
                },
                prompt: node.promptRaw || {},
                score: node.scoreRubric || {},
                asset: node.asset || null,
              },
              null,
              2,
            )}
          </pre>
        ) : null}
      </section>

      <details>
        <summary>Contract</summary>
        <p><strong>Inputs:</strong> {node.inputs.join(', ')}</p>
        <p><strong>Expected:</strong> {node.expected.join(', ')}</p>
        <p><strong>Retry:</strong> {node.retryPolicy}</p>
      </details>

      {node.asset ? (
        <div className="run-node-wide-card run-node-asset-section">
          <h4>Asset Metadata</h4>
          <p><strong>File:</strong> {node.asset.file_name || 'N/A'}</p>
          <p><strong>Path:</strong> {node.asset.abs_path || 'N/A'}</p>
          <p><strong>Mime:</strong> {node.asset.mime_type || 'N/A'}</p>
          <p><strong>Dimensions:</strong> {node.asset.width || '-'} x {node.asset.height || '-'}</p>
          <p><strong>SHA256:</strong> {shortHash(node.asset.sha256)}</p>
          {node.asset.origin_url ? (
            <p>
              <a href={node.asset.origin_url} target="_blank" rel="noreferrer">
                Open source image URL
              </a>
            </p>
          ) : null}
          {node.asset.origin_url ? (
            <div className="run-node-image">
              <img src={node.asset.origin_url} alt={`${node.label} output`} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
