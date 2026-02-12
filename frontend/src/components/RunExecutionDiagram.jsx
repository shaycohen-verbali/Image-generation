import React, { useEffect, useMemo, useState } from 'react'
import RunNodeDetailCard from './RunNodeDetailCard'
import { buildRunDiagram, getAvailableAttempts } from '../lib/runDiagram'

function defaultAttempt(detail, attempts) {
  const current = Number(detail?.run?.optimization_attempt || 0)
  if (current > 0 && attempts.includes(current)) return current
  return attempts[attempts.length - 1] || 1
}

export default function RunExecutionDiagram({ detail }) {
  const attempts = useMemo(() => getAvailableAttempts(detail), [detail?.run?.id, detail?.run?.updated_at, detail])
  const [selectedAttempt, setSelectedAttempt] = useState(defaultAttempt(detail, attempts))
  const diagram = useMemo(() => buildRunDiagram(detail, selectedAttempt), [detail, selectedAttempt])
  const [selectedNodeId, setSelectedNodeId] = useState('stage3_upgrade')

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

  return (
    <div className="run-diagram-root">
      <div className="run-diagram-head">
        <h3>Live Execution Diagram</h3>
        <p>
          Status: <strong>{detail.run.status}</strong> | Current stage: <strong>{detail.run.current_stage}</strong>
        </p>
      </div>

      <div className="attempt-chip-row">
        {attempts.map((attempt) => (
          <button
            key={attempt}
            className={attempt === selectedAttempt ? 'attempt-chip active' : 'attempt-chip'}
            onClick={() => setSelectedAttempt(attempt)}
          >
            A{attempt}
          </button>
        ))}
      </div>

      <div className="attempt-summary-row">
        {diagram.attemptSummaries.map((summary) => (
          <div key={summary.attempt} className={summary.attempt === selectedAttempt ? 'attempt-summary active' : 'attempt-summary'}>
            <p><strong>A{summary.attempt}</strong></p>
            <p>Stage3: {summary.stage3Status}</p>
            <p>Quality: {summary.qualityStatus}</p>
            <p>Stage4: {summary.stage4Status}</p>
            <p>Score: {summary.score ?? '-'}</p>
          </div>
        ))}
      </div>

      <div className="run-diagram-lane">
        {diagram.nodes.map((node, index) => (
          <React.Fragment key={node.id}>
            <button
              className={
                node.id === selectedNodeId
                  ? `run-diagram-node selected status-${node.status}`
                  : `run-diagram-node status-${node.status}`
              }
              onClick={() => setSelectedNodeId(node.id)}
            >
              <span className="node-label">{node.label}</span>
              <span className="node-status">{node.statusLabel}</span>
              {node.subtitle ? <span className="node-subtitle">{node.subtitle}</span> : null}
            </button>
            {index < diagram.nodes.length - 1 ? <span className="run-diagram-arrow">-&gt;</span> : null}
          </React.Fragment>
        ))}
      </div>

      <p className="run-diagram-loop-note">Loop: Quality fail -> Stage 3 upgrade (next attempt)</p>

      <RunNodeDetailCard node={selectedNode} />
    </div>
  )
}
