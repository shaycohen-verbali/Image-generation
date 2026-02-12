import React from 'react'
import { FLOW_EDGES, STAGE_DEFINITIONS } from '../lib/runDiagram'

const STATIC_STAGE_DETAILS = {
  stage1_prompt: {
    apiCall: 'POST /openai/assistants/{assistant_id}/runs',
    promptTemplate:
      'Task: create first prompt JSON.\nInput: word, part_of_sentence, category, context, boy_or_girl.\nOutput JSON: { "first prompt": "...", "need a person": "yes|no" }',
  },
  stage2_draft: {
    apiCall: 'POST /replicate/models/black-forest-labs/flux-schnell/predictions',
    promptTemplate: '',
  },
  stage3_upgrade: {
    apiCall: 'POST /openai/assistants/{assistant_id}/runs and POST /replicate/models/black-forest-labs/flux-1.1-pro/predictions',
    promptTemplate:
      'Task: create upgraded prompt JSON.\nInput: old prompt + vision critique + score feedback.\nOutput JSON: { "upgraded prompt": "..." }',
  },
  quality_gate: {
    apiCall: 'POST /openai/chat/completions (vision scoring)',
    promptTemplate:
      'Task: score upgraded image against AAC clarity rubric.\nOutput JSON: { score, explanation, failure_tags }',
  },
  stage4_background: {
    apiCall: 'POST /replicate/models/google/nano-banana/predictions',
    promptTemplate:
      'Prompt: remove background, keep main concept, solid white background, no text.',
  },
  completed: {
    apiCall: 'Internal state transition only',
    promptTemplate: '',
  },
}

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
              <p className="algo-static-api"><strong>API:</strong> {STATIC_STAGE_DETAILS[stage.id]?.apiCall || 'N/A'}</p>
              <details>
                <summary>Contract</summary>
                <p><strong>Inputs:</strong> {stage.inputs.join(', ')}</p>
                <p><strong>Expected:</strong> {stage.expected.join(', ')}</p>
                <p><strong>Retry:</strong> {stage.retryPolicy}</p>
                {STATIC_STAGE_DETAILS[stage.id]?.promptTemplate ? (
                  <>
                    <p><strong>Prompt sent:</strong></p>
                    <pre className="algo-prompt-box">{STATIC_STAGE_DETAILS[stage.id].promptTemplate}</pre>
                  </>
                ) : null}
              </details>
            </div>
            {index < STAGE_DEFINITIONS.length - 1 ? <div className="algo-static-arrow">-&gt;</div> : null}
          </React.Fragment>
        ))}
      </div>

      <div className="algo-loop-banner">
        <strong>Loop:</strong> quality_gate fail -&gt; stage3_upgrade next attempt (until max attempts).
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
