import React from 'react'
import { FLOW_EDGES, STAGE_DEFINITIONS } from '../lib/runDiagram'

export default function AlgorithmStaticMap() {
  return (
    <article className="card algo-static-card">
      <h2>Algorithm Map</h2>
      <p className="algo-subtitle">Static architecture of the pipeline and loop behavior.</p>

      <div className="algo-static-lane">
        {STAGE_DEFINITIONS.map((stage, index) => (
          <React.Fragment key={stage.id}>
            <div className="algo-static-node">
              <h3>{stage.label}</h3>
              <p className="algo-static-provider">{stage.provider}</p>
              <details>
                <summary>Contract</summary>
                <p><strong>Inputs:</strong> {stage.inputs.join(', ')}</p>
                <p><strong>Expected:</strong> {stage.expected.join(', ')}</p>
                <p><strong>Retry:</strong> {stage.retryPolicy}</p>
              </details>
            </div>
            {index < STAGE_DEFINITIONS.length - 1 ? <div className="algo-static-arrow">-&gt;</div> : null}
          </React.Fragment>
        ))}
      </div>

      <div className="algo-branches">
        {FLOW_EDGES.map((edge) => (
          <p key={`${edge.from}-${edge.to}-${edge.label}`}>
            <strong>{edge.from}</strong> -&gt; <strong>{edge.to}</strong>: {edge.label}
          </p>
        ))}
      </div>
    </article>
  )
}
