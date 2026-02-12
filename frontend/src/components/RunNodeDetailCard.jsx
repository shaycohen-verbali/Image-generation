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

  if (!node) {
    return (
      <div className="run-node-detail">
        <p>Select a block to inspect details.</p>
      </div>
    )
  }

  return (
    <div className="run-node-detail">
      <div className="run-node-detail-head">
        <h3>{node.label}</h3>
        <span className={`status-pill status-${node.status}`}>{node.statusLabel}</span>
      </div>

      <div className="run-node-grid">
        <div className="run-node-section">
          <h4>Execution</h4>
          {node.subtitle ? <p><strong>Summary:</strong> {node.subtitle}</p> : null}
          {typeof node.attempt === 'number' && node.attempt > 0 ? <p><strong>Attempt:</strong> A{node.attempt}</p> : null}
          <p><strong>Provider:</strong> {node.provider}</p>
          <p><strong>Model:</strong> {node.model || 'N/A'}</p>
          {node.id === 'stage1_prompt' || node.id === 'stage3_prompt_upgrade' ? (
            <p><strong>Assistant:</strong> {assistantName || 'Prompt generator -JSON output'}</p>
          ) : null}
          <p><strong>Stage status:</strong> {node.stageStatus || node.status}</p>
          <p><strong>Recorded at:</strong> {node.stageCreatedAt || 'N/A'}</p>
          {node.stageErrorDetail ? <p><strong>Error detail:</strong> {node.stageErrorDetail}</p> : null}
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
            {showRawPrompt ? 'Hide prompt raw JSON' : 'View prompt raw JSON'}
          </button>
          {showRawPrompt ? <pre>{JSON.stringify(node.promptRaw || {}, null, 2)}</pre> : null}
        </div>

        <div className="run-node-section">
          <h4>Request / Response</h4>
          <p><strong>Request keys:</strong> {node.requestKeys.length > 0 ? node.requestKeys.join(', ') : 'none'}</p>
          <p><strong>Response keys:</strong> {node.responseKeys.length > 0 ? node.responseKeys.join(', ') : 'none'}</p>
          <button onClick={() => setShowRaw((value) => !value)}>{showRaw ? 'Hide stage raw JSON' : 'View stage raw JSON'}</button>
          {showRaw ? (
            <pre>
              {JSON.stringify(
                {
                  request_json: node.requestJson,
                  response_json: node.responseJson,
                },
                null,
                2,
              )}
            </pre>
          ) : null}
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
