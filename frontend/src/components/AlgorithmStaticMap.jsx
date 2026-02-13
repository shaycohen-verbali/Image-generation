import React, { useMemo, useState } from 'react'
import WorkflowCanvas from './WorkflowCanvas'

const PHOTOREALISTIC_HINT =
  'If category is one of: Drinks, animals, food, food: fruits, food: vegetables, food: Sweets & desserts, shapes, school supplies, transportation - use a photorealistic style.'

const STAGE1_PROMPT_TEMPLATE = [
  'Task: Create the first image prompt for the given word and decide if the prompt needs a person.',
  'Return STRICT JSON with keys exactly:',
  '{ "first prompt": "<string>", "need a person": "yes" | "no" }',
  '',
  'Context: <entry.context>',
  'Word: <entry.word>',
  'Part of speech: <entry.part_of_sentence>',
  'Category: <entry.category>',
  'If a person is present, use a: <entry.boy_or_girl>',
  '',
  PHOTOREALISTIC_HINT,
].join('\n')

const STAGE3_CRITIQUE_PROMPT_TEMPLATE =
  'You are an expert AAC visual designer for children. Analyze the image for concept clarity. Return STRICT JSON with keys {"challenges":"...", "recommendations":"..."}. Concept word: <entry.word>. Part of sentence: <entry.part_of_sentence>. Category: <entry.category>.'

const STAGE3_UPGRADE_PROMPT_TEMPLATE = [
  'Create an upgraded image prompt for the given word. Return STRICT JSON:',
  '{ "upgraded prompt": "<string>" }',
  '',
  'context for the image: <entry.context>',
  'Old prompt: <previous_prompt>',
  'challenges and improvements with the old image: challenges=<analysis.challenges>; recommendations=<analysis.recommendations + previous_score_feedback>',
  'word: <entry.word>',
  'part of sentence: <entry.part_of_sentence>',
  'Category: <entry.category>',
  'If a person is present, use a <entry.boy_or_girl> as the person.',
  '',
  'Do not use text in the image.',
  "The word's category can add information in addition to its PoS.",
  PHOTOREALISTIC_HINT,
].join('\n')

const QUALITY_GATE_PROMPT_TEMPLATE =
  'Score the AAC concept image quality for a child user. Return STRICT JSON with fields: {"score":0-100, "explanation":"...", "failure_tags":["ambiguity","clutter","wrong_concept","text_in_image","distracting_details"]}. Word: <entry.word>. Part of sentence: <entry.part_of_sentence>. Category: <entry.category>. Pass threshold is <run.quality_threshold>.'

const WHITE_BG_PROMPT_TEMPLATE = [
  'remove the background - keep only the important elements of the image and make the background white.',
  'The image\'s main message is to represent the concept "<entry.word>".',
  'Do not add text in the image.',
].join(' ')

const STAGE_DETAILS = {
  stage1_prompt: {
    apiCall: 'OpenAI Assistants v2',
    provider: 'OpenAI Assistant',
    model: 'assistant configured in runtime',
    inputs: ['word', 'part_of_sentence', 'category (optional)', 'context', 'boy_or_girl'],
    outputs: ['first prompt', 'need a person'],
    instruction: STAGE1_PROMPT_TEMPLATE,
    requestExample: {
      assistant_input: STAGE1_PROMPT_TEMPLATE,
    },
  },
  stage2_draft: {
    apiCall: 'Replicate via Cloudflare AI Gateway',
    provider: 'black-forest-labs/flux-schnell',
    model: 'black-forest-labs/flux-schnell',
    inputs: ['prompt from Stage 1'],
    outputs: ['draft image URL', 'stage2_draft asset'],
    instruction: JSON.stringify(
      {
        input: {
          prompt: '<stage1 first prompt>',
          output_format: 'jpg',
        },
      },
      null,
      2,
    ),
    requestExample: {
      input: {
        prompt: '<stage1 first prompt>',
        output_format: 'jpg',
      },
    },
  },
  stage3_critique: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gemini-3-flash | gemini-3-pro',
    inputs: ['stage2/stage3 source image', 'word', 'part_of_sentence', 'category'],
    outputs: ['challenges', 'recommendations'],
    instruction: STAGE3_CRITIQUE_PROMPT_TEMPLATE,
    requestExample: {
      content: [
        { type: 'text', text: STAGE3_CRITIQUE_PROMPT_TEMPLATE },
        { type: 'image_url', image_url: { url: '<data:image/...>' } },
      ],
    },
  },
  stage3_prompt_upgrade: {
    apiCall: 'OpenAI Assistants v2',
    provider: 'OpenAI Assistant',
    model: 'assistant configured in runtime',
    inputs: ['old prompt', 'critique', 'previous score feedback'],
    outputs: ['upgraded prompt'],
    instruction: STAGE3_UPGRADE_PROMPT_TEMPLATE,
    requestExample: {
      assistant_input: STAGE3_UPGRADE_PROMPT_TEMPLATE,
    },
  },
  stage3_generate: {
    apiCall: 'Replicate via Cloudflare AI Gateway',
    provider: 'Replicate model selected in runtime config',
    model: 'flux-1.1-pro | imagen-3 | imagen-4 | nano-banana | nano-banana-pro',
    inputs: ['upgraded prompt from stage 3.2'],
    outputs: ['stage3_upgraded image URL', 'stage3_upgraded asset'],
    instruction: [
      'Primary payload:',
      JSON.stringify(
        {
          input: {
            prompt: '<stage3 upgraded prompt>',
            aspect_ratio: '4:3',
            output_format: 'jpg',
            output_quality: 80,
            prompt_upsampling: false,
            safety_tolerance: 2,
            seed: 10000,
          },
        },
        null,
        2,
      ),
      '',
      'Fallback payload:',
      JSON.stringify(
        {
          input: {
            prompt: '<stage3 upgraded prompt>',
            num_outputs: 1,
            aspect_ratio: '4:3',
            output_format: 'jpg',
            output_quality: 80,
            prompt_upsampling: true,
            safety_tolerance: 2,
          },
        },
        null,
        2,
      ),
    ].join('\n'),
    requestExample: {
      prompt_source: 'stage3 upgraded prompt',
      fallback_enabled: true,
    },
  },
  quality_gate: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gemini-3-flash | gemini-3-pro',
    inputs: ['stage3 upgraded image', 'word', 'part_of_sentence', 'category', 'threshold'],
    outputs: ['score', 'explanation', 'failure_tags', 'winner selection input'],
    instruction: QUALITY_GATE_PROMPT_TEMPLATE,
    requestExample: {
      content: [
        { type: 'text', text: QUALITY_GATE_PROMPT_TEMPLATE },
        { type: 'image_url', image_url: { url: '<data:image/...>' } },
      ],
    },
  },
  stage4_background: {
    apiCall: 'Replicate via Cloudflare AI Gateway',
    provider: 'google/nano-banana',
    model: 'google/nano-banana',
    inputs: ['highest-score winner image from stage3 attempts'],
    outputs: ['white background image URL', 'stage4_white_bg asset'],
    instruction: WHITE_BG_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: WHITE_BG_PROMPT_TEMPLATE,
        image_input: ['<data:image/...>'],
        aspect_ratio: 'match_input_image',
        output_format: 'jpg',
      },
    },
  },
  completed_pass: {
    apiCall: 'internal status transition',
    provider: 'system',
    model: 'N/A',
    inputs: ['quality_gate pass + stage4 success'],
    outputs: ['completed_pass'],
    instruction: 'No AI instruction. System updates run.status=completed_pass.',
    requestExample: { status: 'completed_pass' },
  },
  completed_fail: {
    apiCall: 'internal status transition',
    provider: 'system',
    model: 'N/A',
    inputs: ['quality_gate fail and attempts exhausted'],
    outputs: ['completed_fail_threshold'],
    instruction: 'No AI instruction. System updates run.status=completed_fail_threshold.',
    requestExample: { status: 'completed_fail_threshold' },
  },
}

export default function AlgorithmStaticMap({ assistantName = '' }) {
  const [selectedNodeId, setSelectedNodeId] = useState('stage3_prompt_upgrade')

  const nodes = useMemo(
    () => [
      { id: 'stage1_prompt', label: 'Stage 1 Prompt Generation', subtitle: 'OpenAI Assistant', status: 'queued', x: 40, y: 235 },
      { id: 'stage2_draft', label: 'Stage 2 Draft Image', subtitle: 'flux-schnell', status: 'queued', x: 380, y: 235 },
      { id: 'stage3_critique', label: 'Stage 3.1 Vision Critique', subtitle: 'OpenAI/Gemini', status: 'queued', x: 760, y: 45 },
      { id: 'stage3_prompt_upgrade', label: 'Stage 3.2 Prompt Upgrade', subtitle: 'OpenAI Assistant', status: 'queued', x: 760, y: 235 },
      { id: 'stage3_generate', label: 'Stage 3.3 Upgraded Image', subtitle: 'selected model', status: 'queued', x: 760, y: 425 },
      { id: 'quality_gate', label: 'Quality Gate', subtitle: 'OpenAI/Gemini score', status: 'queued', x: 1160, y: 235 },
      { id: 'stage4_background', label: 'Stage 4 White Background', subtitle: 'nano-banana', status: 'queued', x: 1540, y: 120 },
      { id: 'completed_pass', label: 'Completed Pass', subtitle: 'ready for export', status: 'ok', x: 1910, y: 120 },
      { id: 'completed_fail', label: 'Completed Fail', subtitle: 'below threshold', status: 'error', x: 1540, y: 395 },
    ],
    [],
  )

  const edges = useMemo(
    () => [
      { from: 'stage1_prompt', to: 'stage2_draft', label: 'prompt 1', fromPort: 'right', toPort: 'left' },
      { from: 'stage2_draft', to: 'stage3_critique', label: 'start attempt 1', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_critique', to: 'stage3_prompt_upgrade', label: 'challenges + recommendations', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_prompt_upgrade', to: 'stage3_generate', label: 'upgraded prompt', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_generate', to: 'quality_gate', label: 'candidate image', fromPort: 'right', toPort: 'left' },
      { from: 'quality_gate', to: 'stage3_critique', label: 'fail + attempts remain', type: 'loop', fromPort: 'left', toPort: 'top' },
      { from: 'quality_gate', to: 'stage4_background', label: 'after final scoring: winner selected', fromPort: 'top', toPort: 'left' },
      { from: 'stage4_background', to: 'completed_pass', label: 'final image', fromPort: 'right', toPort: 'left' },
      { from: 'stage4_background', to: 'completed_fail', label: 'score below threshold', type: 'branch', fromPort: 'bottom', toPort: 'left' },
    ],
    [],
  )

  const selected = STAGE_DETAILS[selectedNodeId] || STAGE_DETAILS.stage1_prompt

  return (
    <article className="card algo-static-card">
      <h2>Algorithm Architecture (Static)</h2>
      <p className="algo-subtitle">Full block-level map with the exact instruction text used by each AI call.</p>
      <p className="algo-assistant-name">
        <strong>Assistant Name:</strong> {assistantName || 'Prompt generator -JSON output'}
      </p>

      <WorkflowCanvas
        nodes={nodes}
        edges={edges}
        width={2200}
        height={640}
        selectedNodeId={selectedNodeId}
        onSelectNode={setSelectedNodeId}
      />

      <div className="algo-static-detail">
        <h3>Selected Block: {nodes.find((node) => node.id === selectedNodeId)?.label || 'N/A'}</h3>
        <p><strong>Provider/API:</strong> {selected.apiCall}</p>
        <p><strong>Model:</strong> {selected.model}</p>
        <p><strong>Inputs:</strong> {selected.inputs.join(', ')}</p>
        <p><strong>Outputs:</strong> {selected.outputs.join(', ')}</p>
        <p><strong>Exact AI instruction:</strong></p>
        <pre className="algo-prompt-box">{selected.instruction}</pre>
        <details>
          <summary>Provider request shape</summary>
          <pre className="algo-prompt-box">{JSON.stringify(selected.requestExample || {}, null, 2)}</pre>
        </details>
      </div>
    </article>
  )
}
