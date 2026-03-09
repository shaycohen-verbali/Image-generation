import React, { useState } from 'react'

function shortHash(value) {
  if (!value) return ''
  const text = String(value)
  if (text.length <= 16) return text
  return `${text.slice(0, 8)}...${text.slice(-8)}`
}

export default function RunNodeDetailCard({ node, assistantName = '' }) {
  const [showRaw, setShowRaw] = useState(false)
  const [showRawPrompt, setShowRawPrompt] = useState(false)
  const [showRawScore, setShowRawScore] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')

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

  return (
    <div className="run-node-detail">
      <div className="run-node-detail-head">
        <h3>{node.label}</h3>
        <span className={`status-pill status-${node.status}`}>{node.statusLabel}</span>
      </div>

      <div className="run-debug-card">
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
      </div>

      <div className="run-node-grid">
        <div className="run-node-section">
          <h4>Execution</h4>
          {node.subtitle ? <p><strong>Summary:</strong> {node.subtitle}</p> : null}
          {typeof node.attempt === 'number' && node.attempt > 0 ? <p><strong>Attempt:</strong> Attempt {node.attempt}</p> : null}
          <p><strong>Provider:</strong> {node.provider}</p>
          <p><strong>Model:</strong> {node.model || 'N/A'}</p>
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
          {decision.resolved_need_person ? <p><strong>Resolved need person:</strong> {decision.resolved_need_person}</p> : null}
          {decision.resolved_need_person_reasoning ? <p><strong>Decision reason:</strong> {decision.resolved_need_person_reasoning}</p> : null}
          {node.stageErrorDetail ? <p><strong>Error detail:</strong> {node.stageErrorDetail}</p> : null}
        </div>

        <div className="run-node-section">
          <h4>AI Instruction</h4>
          <p><strong>Source:</strong> {node.aiInstructionSource || 'N/A'}</p>
          {node.aiInstruction ? (
            <pre className="prompt-doc-box">{node.aiInstruction}</pre>
          ) : (
            <p>No instruction text recorded for this block.</p>
          )}
        </div>

        <div className="run-node-section">
          <h4>Prompt Lineage</h4>
          <p><strong>Prompt source:</strong> {node.promptSource || 'N/A'}</p>
          <p><strong>Need person:</strong> {node.promptNeedsPerson || 'N/A'}</p>
          <p><strong>Prompt created:</strong> {node.promptCreatedAt || 'N/A'}</p>
          {node.promptText ? (
            <>
              <p><strong>Prompt text:</strong></p>
              <pre className="prompt-doc-box">{node.promptText}</pre>
            </>
          ) : (
            <p><strong>Prompt text:</strong> N/A</p>
          )}
          <button onClick={() => setShowRawPrompt((value) => !value)}>
            {showRawPrompt
              ? 'Hide prompt engineer response JSON'
              : 'View prompt engineer response JSON'}
          </button>
          {showRawPrompt ? <pre>{JSON.stringify(node.promptRaw || {}, null, 2)}</pre> : null}
        </div>

        <div className="run-node-section">
          <h4>Request / Response</h4>
          <p><strong>Request keys:</strong> {node.requestKeys.length > 0 ? node.requestKeys.join(', ') : 'none'}</p>
          <p><strong>Response keys:</strong> {node.responseKeys.length > 0 ? node.responseKeys.join(', ') : 'none'}</p>
          <button type="button" onClick={() => copyJson('Stage request/response JSON', {
            request_json: node.requestJson,
            response_json: node.responseJson,
          })}>
            Copy stage request/response JSON
          </button>
        </div>

        <div className="run-node-section">
          <h4>Quality</h4>
          {node.score ? (
            <>
              <p><strong>Score:</strong> {node.score.score_0_100}</p>
              <p><strong>Pass:</strong> {node.score.pass_fail ? 'yes' : 'no'}</p>
              <p><strong>Stage:</strong> {node.score.stage_name || 'quality_gate'}</p>
              <button onClick={() => setShowRawScore((value) => !value)}>
                {showRawScore ? 'Hide score rubric JSON' : 'View score rubric JSON'}
              </button>
              {showRawScore ? <pre>{JSON.stringify(node.scoreRubric || {}, null, 2)}</pre> : null}
            </>
          ) : (
            <p>No score payload for this block.</p>
          )}
        </div>
      </div>

      <details>
        <summary>Contract</summary>
        <p><strong>Inputs:</strong> {node.inputs.join(', ')}</p>
        <p><strong>Expected:</strong> {node.expected.join(', ')}</p>
        <p><strong>Retry:</strong> {node.retryPolicy}</p>
      </details>

      {node.asset ? (
        <div className="run-node-section run-node-asset-section">
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
