import React, { useEffect, useMemo, useState } from 'react'
import RunNodeDetailCard from './RunNodeDetailCard'
import WorkflowCanvas from './WorkflowCanvas'
import { buildRunDiagram, getAvailableAttempts } from '../lib/runDiagram'

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
  const canvasNodes = useMemo(
    () =>
      diagram.nodes.map((node) => {
        const position = {
          stage1_prompt: { x: 40, y: 185 },
          stage2_draft: { x: 320, y: 185 },
          stage3_critique: { x: 670, y: 40 },
          stage3_prompt_upgrade: { x: 670, y: 185 },
          stage3_generate: { x: 670, y: 330 },
          quality_gate: { x: 1000, y: 185 },
          stage4_background: { x: 1330, y: 95 },
          completed: { x: 1620, y: 95 },
        }[node.id] || { x: 40, y: 185 }

        const badge =
          node.id === 'stage3_critique' ||
          node.id === 'stage3_prompt_upgrade' ||
          node.id === 'stage3_generate' ||
          node.id === 'quality_gate' ||
          node.id === 'stage4_background'
            ? `A${selectedAttempt}`
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

      <WorkflowCanvas
        nodes={canvasNodes}
        edges={diagram.edges}
        width={1870}
        height={510}
        selectedNodeId={selectedNodeId}
        onSelectNode={setSelectedNodeId}
      />

      <p className="run-diagram-loop-note">Loop active when quality fails and attempts remain. Pass branch proceeds to white background.</p>

      <RunNodeDetailCard node={selectedNode} assistantName={assistantName} />
    </div>
  )
}
