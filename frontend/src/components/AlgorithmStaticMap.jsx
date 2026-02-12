import React, { useMemo, useState } from 'react'
import WorkflowCanvas from './WorkflowCanvas'

const STATIC_STAGE_DETAILS = {
  stage1_prompt: {
    apiCall: 'POST /openai/assistants/{assistant_id}/runs',
    promptTemplate:
      'Task: create first prompt JSON.\nInput: word, part_of_sentence, category, context, boy_or_girl.\nOutput JSON: { "first prompt": "...", "need a person": "yes|no" }',
    inputs: ['word', 'part_of_sentence', 'category', 'context', 'boy_or_girl'],
    expected: ['first prompt', 'need a person'],
    retry: 'API retry + stage retry',
    requestExample: {
      word: 'apple',
      part_of_sentence: 'noun',
      category: 'food',
      context: 'single fruit',
      boy_or_girl: 'girl',
    },
    responseExample: {
      'first prompt': 'A photorealistic single apple on white background...',
      'need a person': 'no',
    },
    failureModes: ['assistant timeout', 'invalid JSON payload', 'missing first prompt'],
  },
  stage2_draft: {
    apiCall: 'POST /replicate/models/black-forest-labs/flux-schnell/predictions',
    promptTemplate: '',
    inputs: ['prompt 1'],
    expected: ['prediction status', 'output URL', 'draft image'],
    retry: 'API retry + stage retry',
    requestExample: {
      input: {
        prompt: '<prompt 1>',
        output_format: 'jpg',
      },
    },
    responseExample: {
      status: 'succeeded',
      id: 'pred_xxx',
      output: ['https://.../out-0.jpg'],
    },
    failureModes: ['prediction failed', 'no output URL', 'download/write failure'],
  },
  stage3_upgrade: {
    apiCall: 'POST /openai/assistants/{assistant_id}/runs and POST /replicate/models/black-forest-labs/flux-1.1-pro/predictions',
    promptTemplate:
      'Task: create upgraded prompt JSON.\nInput: old prompt + vision critique + score feedback.\nOutput JSON: { "upgraded prompt": "..." }',
    inputs: ['old prompt', 'critique', 'previous score explanation'],
    expected: ['upgraded prompt', 'upgraded image'],
    retry: 'API retry + stage retry',
    requestExample: {
      old_prompt: '<stage1 or previous stage3 prompt>',
      critique: { challenges: '...', recommendations: '...' },
      score_feedback: 'if prior attempt failed',
    },
    responseExample: {
      assistant: { 'upgraded prompt': '...' },
      generation: { status: 'succeeded', id: 'pred_stage3' },
    },
    failureModes: ['assistant no upgraded prompt', 'flux-pro fail', 'fallback fail'],
  },
  quality_gate: {
    apiCall: 'POST /openai/chat/completions (vision scoring)',
    promptTemplate:
      'Task: score upgraded image against AAC clarity rubric.\nOutput JSON: { score, explanation, failure_tags }',
    inputs: ['stage3 upgraded image', 'word/POS/category', 'quality threshold'],
    expected: ['score', 'explanation', 'failure_tags', 'pass_fail'],
    retry: 'API retry + stage retry',
    requestExample: {
      image: '<stage3 upgraded asset>',
      threshold: 95,
      rubric: ['clarity', 'concept match', 'no text'],
    },
    responseExample: {
      score: 92,
      explanation: 'good but still ambiguous',
      failure_tags: ['ambiguity'],
    },
    failureModes: ['invalid rubric JSON', 'vision timeout', 'score parse fallback'],
  },
  stage4_background: {
    apiCall: 'POST /replicate/models/google/nano-banana/predictions',
    promptTemplate:
      'Prompt: remove background, keep main concept, solid white background, no text.',
    inputs: ['passing stage3 image'],
    expected: ['white background image'],
    retry: 'API retry + stage retry',
    requestExample: {
      prompt: 'remove background...',
      image_input: ['<stage3 image>'],
      output_format: 'jpg',
    },
    responseExample: {
      status: 'succeeded',
      output: ['https://.../white_bg.jpg'],
    },
    failureModes: ['nano-banana fail', 'no output URL', 'download/write failure'],
  },
  completed_pass: {
    apiCall: 'Internal state transition only',
    promptTemplate: '',
    inputs: ['run.status == completed_pass'],
    expected: ['final white background asset'],
    retry: 'N/A',
    requestExample: { status: 'completed_pass' },
    responseExample: { export_ready: true },
    failureModes: ['N/A'],
  },
  completed_fail: {
    apiCall: 'Internal state transition only',
    promptTemplate: '',
    inputs: ['run.status == completed_fail_threshold'],
    expected: ['no passing attempt'],
    retry: 'N/A',
    requestExample: { status: 'completed_fail_threshold' },
    responseExample: { export_ready: true, note: 'below threshold' },
    failureModes: ['N/A'],
  },
}

export default function AlgorithmStaticMap() {
  const [selectedNodeId, setSelectedNodeId] = useState('stage3_upgrade')

  const nodes = useMemo(
    () => [
      { id: 'stage1_prompt', label: 'Stage 1 Prompt', subtitle: 'OpenAI Assistant', status: 'queued', x: 30, y: 120 },
      { id: 'stage2_draft', label: 'Stage 2 Draft', subtitle: 'flux-schnell', status: 'queued', x: 250, y: 120 },
      { id: 'stage3_upgrade', label: 'Stage 3 Upgrade', subtitle: 'Assistant + flux-pro', status: 'queued', x: 470, y: 120 },
      { id: 'quality_gate', label: 'Quality Gate', subtitle: 'gpt-4o-mini score', status: 'queued', x: 690, y: 120 },
      { id: 'stage4_background', label: 'Stage 4 White BG', subtitle: 'nano-banana', status: 'queued', x: 910, y: 40 },
      { id: 'completed_pass', label: 'Completed Pass', subtitle: 'final export ready', status: 'ok', x: 1130, y: 40 },
      { id: 'completed_fail', label: 'Completed Fail', subtitle: 'threshold not met', status: 'error', x: 910, y: 210 },
    ],
    [],
  )

  const edges = useMemo(
    () => [
      { from: 'stage1_prompt', to: 'stage2_draft', label: 'first prompt' },
      { from: 'stage2_draft', to: 'stage3_upgrade', label: 'attempt A1 starts' },
      { from: 'stage3_upgrade', to: 'quality_gate', label: 'score this upgraded image' },
      { from: 'quality_gate', to: 'stage3_upgrade', label: 'fail + attempts remain', type: 'loop' },
      { from: 'quality_gate', to: 'stage4_background', label: 'pass >= threshold' },
      { from: 'stage4_background', to: 'completed_pass', label: 'final white-bg image' },
      { from: 'quality_gate', to: 'completed_fail', label: 'attempts exhausted', type: 'branch' },
    ],
    [],
  )

  const selected = STATIC_STAGE_DETAILS[selectedNodeId] || STATIC_STAGE_DETAILS.stage1_prompt

  return (
    <article className="card algo-static-card">
      <h2>Algorithm Map</h2>
      <p className="algo-subtitle">n8n-style node flow with explicit pass/fail branches and retry loop.</p>

      <WorkflowCanvas nodes={nodes} edges={edges} width={1360} height={330} selectedNodeId={selectedNodeId} onSelectNode={setSelectedNodeId} />

      <div className="algo-static-detail">
        <h3>Selected Node Contract</h3>
        <p><strong>API call:</strong> {selected.apiCall}</p>
        <p><strong>Inputs:</strong> {selected.inputs.join(', ')}</p>
        <p><strong>Expected:</strong> {selected.expected.join(', ')}</p>
        <p><strong>Retry:</strong> {selected.retry}</p>
        {selected.promptTemplate ? (
          <>
            <p><strong>Prompt sent:</strong></p>
            <pre className="algo-prompt-box">{selected.promptTemplate}</pre>
          </>
        ) : (
          <p><strong>Prompt sent:</strong> N/A</p>
        )}
        <details>
          <summary>Request example</summary>
          <pre className="algo-prompt-box">{JSON.stringify(selected.requestExample || {}, null, 2)}</pre>
        </details>
        <details>
          <summary>Response example</summary>
          <pre className="algo-prompt-box">{JSON.stringify(selected.responseExample || {}, null, 2)}</pre>
        </details>
        <p><strong>Failure modes:</strong> {(selected.failureModes || []).join(', ') || 'N/A'}</p>
      </div>

      <div className="algo-branches">
        <p><strong>Loop:</strong> `quality_gate fail` -> `stage3_upgrade` (next attempt).</p>
        <p><strong>Pass path:</strong> `quality_gate pass` -> `stage4_background` -> `completed_pass`.</p>
        <p><strong>Fail path:</strong> `quality_gate fail and no attempts left` -> `completed_fail`.</p>
      </div>
    </article>
  )
}
