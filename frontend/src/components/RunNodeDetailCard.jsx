import React, { useState } from 'react'

export default function RunNodeDetailCard({ node }) {
  const [showRaw, setShowRaw] = useState(false)

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

      {node.subtitle ? <p>{node.subtitle}</p> : null}
      {typeof node.attempt === 'number' && node.attempt > 0 ? <p><strong>Attempt:</strong> A{node.attempt}</p> : null}
      <p><strong>Provider:</strong> {node.provider}</p>
      {node.model ? <p><strong>Model:</strong> {node.model}</p> : null}
      {node.promptText ? <p><strong>Prompt:</strong> {node.promptText}</p> : null}
      {node.requestKeys.length > 0 ? <p><strong>Request keys:</strong> {node.requestKeys.join(', ')}</p> : null}
      {node.responseKeys.length > 0 ? <p><strong>Response keys:</strong> {node.responseKeys.join(', ')}</p> : null}
      {node.score ? (
        <p>
          <strong>Score:</strong> {node.score.score_0_100} ({node.score.pass_fail ? 'pass' : 'fail'})
        </p>
      ) : null}

      <details>
        <summary>Contract</summary>
        <p><strong>Inputs:</strong> {node.inputs.join(', ')}</p>
        <p><strong>Expected:</strong> {node.expected.join(', ')}</p>
        <p><strong>Retry:</strong> {node.retryPolicy}</p>
      </details>

      {node.asset?.origin_url ? (
        <div className="run-node-image">
          <img src={node.asset.origin_url} alt={`${node.label} output`} />
          <p>{node.asset.file_name}</p>
          <a href={node.asset.origin_url} target="_blank" rel="noreferrer">
            Open Image
          </a>
        </div>
      ) : null}

      <button onClick={() => setShowRaw((value) => !value)}>{showRaw ? 'Hide raw JSON' : 'View raw JSON'}</button>
      {showRaw ? (
        <pre>
          {JSON.stringify(
            {
              request_json: node.requestJson,
              response_json: node.responseJson,
              prompt_text: node.promptText,
              score: node.score,
              asset: node.asset,
            },
            null,
            2,
          )}
        </pre>
      ) : null}
    </div>
  )
}
