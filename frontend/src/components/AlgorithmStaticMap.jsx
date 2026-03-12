import React, { useMemo, useState } from 'react'
import WorkflowCanvas from './WorkflowCanvas'

const DEFAULT_VISUAL_STYLE_NAME = 'Warm Watercolor Storybook Kids Style v3'
const DEFAULT_VISUAL_STYLE_ID = 'warm_watercolor_storybook_kids_v3'
const DEFAULT_VISUAL_STYLE_BLOCK =
  'House visual style: Warm Watercolor Storybook Kids Style v3. Create a premium child-friendly storybook illustration with watercolor-gouache softness and a polished picture-book finish. The image must feel warm, safe, playful, vivid, inviting, emotionally legible, and easy for AAC users and early learners to understand at a glance. Keep one clear focal subject and one clear action or concept, with a crisp polished focal subject, stronger contrast, vivid color richness, bright cheerful colors, warm golden sunlight, lively natural tones, and a premium picture-book finish. Use simple supportive backgrounds that do not compete with the subject. If a child is present, use oversized expressive eyes, rosy cheeks, soft rounded childlike anatomy, clear friendly emotion, and a readable silhouette. Avoid faded or muddy color, photorealism, realistic anatomy, dark mood, clutter, text, watermark, 3D render, and generic flashcard art. This house style overrides category-based photorealistic rendering.'
const DEFAULT_PHOTOREALISTIC_STYLE_BLOCK =
  'House visual style: AAC Clean Photorealistic Style v1. Create a clean premium photorealistic image with one clear focal subject, realistic materials, bright natural color, simple composition, and minimal distractors. Avoid illustration, cartoon styling, clutter, text, watermark, dramatic lighting, and unnecessary people.'

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
  'Decision rule:',
  '- If a person is needed for AAC clarity, the prompt should use an illustration and make the person central.',
  '- If a person is not needed for AAC clarity, the prompt should be photorealistic and should not include a person.',
  '',
  'Illustration style to use when a person is needed (<config.visual_style_name> / <config.visual_style_id>):',
  '<config.visual_style_prompt_block>',
  '',
  'Photorealistic style to use when a person is not needed (aac_clean_photorealistic_v1):',
  DEFAULT_PHOTOREALISTIC_STYLE_BLOCK,
].join('\n')

const STAGE3_CRITIQUE_PROMPT_TEMPLATE =
  'You are an expert AAC visual designer for children. Analyze the image for concept clarity. Return STRICT JSON with keys {"challenges":"...", "recommendations":"...", "person_needed_for_clarity":"yes|no", "person_presence_problem":"missing_person|unnecessary_person|none", "person_decision_reasoning":"..."}. Concept word: <entry.word>. Part of sentence: <entry.part_of_sentence>. Category: <entry.category>. Current system hypothesis: person needed = <decision.initial_need_person>. Current render style = <decision.render_style_mode>.'

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
  'Current decision from the system: <decision.reason>',
  'Resolved person-needed decision: <decision.resolved_need_person>',
  'Resolved render style: <decision.render_style_mode>',
  '<decision.person_instruction>',
  '',
  'Do not use text in the image.',
  "The word's category can add information in addition to its PoS.",
  'Illustration style to use when a person is needed (<config.visual_style_name> / <config.visual_style_id>):',
  '<config.visual_style_prompt_block>',
  '',
  'Photorealistic style to use when a person is not needed (aac_clean_photorealistic_v1):',
  DEFAULT_PHOTOREALISTIC_STYLE_BLOCK,
].join('\n')

const QUALITY_GATE_PROMPT_TEMPLATE =
  'Score the AAC concept image quality for a child user. Return STRICT JSON with fields: {"score":0-100, "explanation":"...", "failure_tags":["ambiguity","clutter","wrong_concept","text_in_image","distracting_details"]}. Word: <entry.word>. Part of sentence: <entry.part_of_sentence>. Category: <entry.category>. Pass threshold is <run.quality_threshold>. Expected render style is <decision.render_style_mode>.'

const WHITE_BG_PROMPT_TEMPLATE = [
  'remove the background - keep only the important elements of the image and make the background white.',
  'The image\'s main message is to represent the concept "<entry.word>".',
  'Do not add text in the image.',
].join(' ')

const VARIANT_FINAL_PROMPT_TEMPLATE =
  'Use the Stage 3 winner image as the base. First expand the white male kid baseline to the requested male age profiles. Then create one white female kid seed from the same Stage 3 winner. Then expand the white female kid seed to the requested female age profiles. Then create race variants from the matching white age+gender baseline for each requested profile. Keep the same AAC concept, composition, pose, props, and style.'

const VARIANT_WHITE_BG_PROMPT_TEMPLATE =
  'Take every final variant created in the prior stage and make its matching white-background version. Preserve the exact same avatar, age, gender, race, pose, framing, props, and object scale. Only change the background treatment to clean white.'

const VARIANT_STEP5_PROMPT_TEMPLATE =
  'Expand the white male kid baseline from the Stage 3 winner into the requested male age profiles. Preserve the same AAC concept, pose, props, framing, and style.'

const VARIANT_STEP6_PROMPT_TEMPLATE =
  'Create one white female kid seed directly from the Stage 3 winner. Preserve the same AAC concept, pose, props, framing, and style.'

const VARIANT_STEP7_PROMPT_TEMPLATE =
  'Expand the white female kid seed into the requested female age profiles. Preserve the same AAC concept, pose, props, framing, and style.'

const VARIANT_STEP8_PROMPT_TEMPLATE =
  'Create race variants from the matching white age+gender baseline for each requested profile. Preserve the same AAC concept, pose, props, framing, and style.'

const STAGE_DETAILS = {
  stage1_prompt: {
    apiCall: 'OpenAI Assistants v2 or model API',
    provider: 'Prompt Engineer',
    model: 'assistant configured in runtime or selected prompt model (OpenAI/Gemini)',
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
          aspect_ratio: '<config.image_aspect_ratio>',
          output_format: 'jpg',
        },
      },
      null,
      2,
    ),
    requestExample: {
      input: {
        prompt: '<stage1 first prompt>',
        aspect_ratio: '<config.image_aspect_ratio>',
        output_format: 'jpg',
      },
    },
  },
  stage3_critique: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gpt-5.4 | gemini-3-flash | gemini-3-pro',
    inputs: ['stage2/stage3 source image', 'word', 'part_of_sentence', 'category'],
    outputs: ['challenges', 'recommendations', 'person_needed_for_clarity', 'person_presence_problem', 'person_decision_reasoning'],
    instruction: STAGE3_CRITIQUE_PROMPT_TEMPLATE,
    requestExample: {
      content: [
        { type: 'text', text: STAGE3_CRITIQUE_PROMPT_TEMPLATE },
        { type: 'image_url', image_url: { url: '<data:image/...>' } },
      ],
    },
  },
  stage3_prompt_upgrade: {
    apiCall: 'OpenAI Assistants v2 or model API',
    provider: 'Prompt Engineer',
    model: 'assistant configured in runtime or selected prompt model (OpenAI/Gemini)',
    inputs: ['old prompt', 'critique', 'previous score feedback'],
    outputs: ['upgraded prompt', 'resolved person/style decision'],
    instruction: STAGE3_UPGRADE_PROMPT_TEMPLATE,
    requestExample: {
      assistant_input: STAGE3_UPGRADE_PROMPT_TEMPLATE,
    },
  },
  stage3_generate: {
    apiCall: 'Selected image provider API',
    provider: 'Google API or Replicate model selected in runtime config',
    model: 'flux-1.1-pro | imagen-3 | imagen-4 | nano-banana | nano-banana-2 | nano-banana-pro',
    inputs: ['upgraded prompt from stage 3.2'],
    outputs: ['stage3_upgraded image URL', 'stage3_upgraded asset'],
    instruction: [
      'Primary payload:',
      JSON.stringify(
        {
          input: {
            prompt: '<stage3 upgraded prompt>',
            aspect_ratio: '<config.image_aspect_ratio>',
            image_size: '<config.image_resolution>',
            output_format: 'jpg',
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
            aspect_ratio: '<config.image_aspect_ratio>',
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
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['highest-score winner image from stage3 attempts'],
    outputs: ['white background image URL', 'stage4_white_bg asset'],
    instruction: WHITE_BG_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: WHITE_BG_PROMPT_TEMPLATE,
        image_input: ['<data:image/...>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
    },
  },
  stage4_variant_generate: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['stage3 winner image', 'requested gender/age/skin combinations'],
    outputs: ['white male age variants', 'white female kid seed', 'white female age variants', 'race variants from matching white age/gender baselines'],
    instruction: VARIANT_FINAL_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_FINAL_PROMPT_TEMPLATE,
        image_input: ['<stage3 winner image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'white male age expansion -> female white kid seed -> white female age expansion -> race expansion from matching white age/gender baselines',
    },
  },
  stage5_male_age: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['Stage 3 winner image', 'requested male age profiles'],
    outputs: ['white male age variants'],
    instruction: VARIANT_STEP5_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_STEP5_PROMPT_TEMPLATE,
        image_input: ['<stage3 winner image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'expand white male kid baseline into requested male age profiles',
    },
  },
  stage6_female_seed: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['Stage 3 winner image'],
    outputs: ['white female kid seed'],
    instruction: VARIANT_STEP6_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_STEP6_PROMPT_TEMPLATE,
        image_input: ['<stage3 winner image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'create white female kid seed from the Stage 3 winner',
    },
  },
  stage7_female_age: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['white female kid seed', 'requested female age profiles'],
    outputs: ['white female age variants'],
    instruction: VARIANT_STEP7_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_STEP7_PROMPT_TEMPLATE,
        image_input: ['<white female kid seed image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'expand white female kid seed into requested female age profiles',
    },
  },
  stage8_race_expand: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['matching white gender+age baselines', 'requested race profiles'],
    outputs: ['race variants from matching white age/gender baselines'],
    instruction: VARIANT_STEP8_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_STEP8_PROMPT_TEMPLATE,
        image_input: ['<matching white age+gender baseline image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'create race variants from matching white age+gender baselines',
    },
  },
  stage9_variant_white_bg: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['all final variants from the previous steps'],
    outputs: ['matching white-background versions for every final variant'],
    instruction: VARIANT_WHITE_BG_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_WHITE_BG_PROMPT_TEMPLATE,
        image_input: ['<matching final variant image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'make white-background versions for every final variant created in the prior steps',
    },
  },
  stage5_variant_white_bg: {
    apiCall: 'Google Generative Language image API',
    provider: 'Google API',
    model: 'gemini-3.1-flash-image-preview (nano-banana-2)',
    inputs: ['all final variants from the previous stage'],
    outputs: ['matching white-background versions for every final variant'],
    instruction: VARIANT_WHITE_BG_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_WHITE_BG_PROMPT_TEMPLATE,
        image_input: ['<matching final variant image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
      branch_rule: 'make white-background versions for every final variant created in the prior stage',
    },
  },
  completed_pass: {
    apiCall: 'internal status transition',
    provider: 'system',
    model: 'N/A',
    inputs: ['quality_gate pass + base winner success + optional variant branches'],
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

function promptEngineerModeLabel(config) {
  if (config?.prompt_engineer_mode !== 'responses_api') return 'OpenAI Assistant'
  const model = String(config?.responses_prompt_engineer_model || '').toLowerCase()
  return model.startsWith('gemini-') ? 'Direct Model API' : 'Responses API + Vector Store'
}

export default function AlgorithmStaticMap({ assistantName = '', config = null }) {
  const [selectedNodeId, setSelectedNodeId] = useState('stage3_prompt_upgrade')
  const promptEngineerLabel = promptEngineerModeLabel(config)
  const visualStyleName = config?.visual_style_name || DEFAULT_VISUAL_STYLE_NAME
  const visualStyleId = config?.visual_style_id || DEFAULT_VISUAL_STYLE_ID
  const visualStyleBlock = config?.visual_style_prompt_block || DEFAULT_VISUAL_STYLE_BLOCK
  const stage1Instruction = (config?.stage1_prompt_template || STAGE1_PROMPT_TEMPLATE)
    .replaceAll('{visual_style_name}', visualStyleName)
    .replaceAll('{visual_style_id}', visualStyleId)
    .replaceAll('{visual_style_block}', visualStyleBlock)
  const stage3Instruction = (config?.stage3_prompt_template || STAGE3_UPGRADE_PROMPT_TEMPLATE)
    .replaceAll('{visual_style_name}', visualStyleName)
    .replaceAll('{visual_style_id}', visualStyleId)
    .replaceAll('{visual_style_block}', visualStyleBlock)

  const nodes = useMemo(
    () => [
      { id: 'stage1_prompt', label: 'Stage 1 Prompt Generation', subtitle: `${promptEngineerLabel} + initial person guess`, status: 'queued', x: 40, y: 235 },
      { id: 'stage2_draft', label: 'Stage 2 Draft Image', subtitle: 'flux-schnell', status: 'queued', x: 380, y: 235 },
      { id: 'stage3_critique', label: 'Stage 3.1 Vision Critique', subtitle: 'OpenAI/Gemini + person validation', status: 'queued', x: 760, y: 45 },
      { id: 'stage3_prompt_upgrade', label: 'Stage 3.2 Prompt Upgrade', subtitle: `${promptEngineerLabel} + resolved style`, status: 'queued', x: 760, y: 235 },
      { id: 'stage3_generate', label: 'Stage 3.3 Upgraded Image', subtitle: 'selected model', status: 'queued', x: 760, y: 425 },
      { id: 'quality_gate', label: 'Quality Gate', subtitle: 'OpenAI/Gemini score', status: 'queued', x: 1160, y: 235 },
      { id: 'stage4_background', label: 'Stage 4 White Background', subtitle: 'base winner white BG', status: 'queued', x: 1540, y: 235 },
      { id: 'stage5_male_age', label: 'Step 5 Male Age Expansion', subtitle: 'white male kid -> requested male ages', badge: 'backend: stage4_variant_generate', status: 'queued', x: 1910, y: 60 },
      { id: 'stage6_female_seed', label: 'Step 6 Female Seed', subtitle: 'white female kid from Stage 3 winner', badge: 'backend: stage4_variant_generate', status: 'queued', x: 1910, y: 250 },
      { id: 'stage7_female_age', label: 'Step 7 Female Age Expansion', subtitle: 'white female kid -> requested female ages', badge: 'backend: stage4_variant_generate', status: 'queued', x: 1910, y: 440 },
      { id: 'stage8_race_expand', label: 'Step 8 Race Expansion', subtitle: 'race variants from matching white age+gender baselines', badge: 'backend: stage4_variant_generate', status: 'queued', x: 2280, y: 250 },
      { id: 'stage9_variant_white_bg', label: 'Step 9 Variant White BG', subtitle: 'white background for every final variant', badge: 'backend: stage5_variant_white_bg', status: 'queued', x: 2650, y: 250 },
      { id: 'completed_pass', label: 'Completed Pass', subtitle: 'ready for export', status: 'ok', x: 3010, y: 250 },
      { id: 'completed_fail', label: 'Completed Fail', subtitle: 'below threshold', status: 'error', x: 1540, y: 395 },
    ],
    [promptEngineerLabel],
  )

  const edges = useMemo(
    () => [
      { from: 'stage1_prompt', to: 'stage2_draft', label: 'prompt 1 + initial style hypothesis', fromPort: 'right', toPort: 'left' },
      { from: 'stage2_draft', to: 'stage3_critique', label: 'start attempt 1', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_critique', to: 'stage3_prompt_upgrade', label: 'critique + person validation', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_prompt_upgrade', to: 'stage3_generate', label: 'upgraded prompt', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_generate', to: 'quality_gate', label: 'candidate image', fromPort: 'right', toPort: 'left' },
      { from: 'quality_gate', to: 'stage3_critique', label: 'fail + attempts remain', type: 'loop', fromPort: 'left', toPort: 'top' },
      { from: 'quality_gate', to: 'stage4_background', label: 'after final scoring: winner selected', fromPort: 'top', toPort: 'left' },
      { from: 'stage4_background', to: 'stage5_male_age', label: 'male ages', fromPort: 'right', toPort: 'left' },
      { from: 'stage4_background', to: 'stage6_female_seed', label: 'female seed', fromPort: 'right', toPort: 'left' },
      { from: 'stage6_female_seed', to: 'stage7_female_age', label: 'female ages', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage5_male_age', to: 'stage8_race_expand', label: 'male baselines', fromPort: 'right', toPort: 'left' },
      { from: 'stage7_female_age', to: 'stage8_race_expand', label: 'female baselines', fromPort: 'right', toPort: 'left' },
      { from: 'stage8_race_expand', to: 'stage9_variant_white_bg', label: 'all finals -> white BG', fromPort: 'right', toPort: 'left' },
      { from: 'stage9_variant_white_bg', to: 'completed_pass', label: 'done', fromPort: 'right', toPort: 'left' },
      { from: 'stage4_background', to: 'completed_fail', label: 'score below threshold', type: 'branch', fromPort: 'bottom', toPort: 'left' },
    ],
    [],
  )

  const selectedBase = STAGE_DETAILS[selectedNodeId] || STAGE_DETAILS.stage1_prompt
  const selected = useMemo(() => {
    if (selectedNodeId === 'stage1_prompt') {
      return {
        ...selectedBase,
        model: promptEngineerLabel,
        instruction: stage1Instruction,
        requestExample:
          config?.prompt_engineer_mode === 'responses_api'
            ? {
                model: config?.responses_prompt_engineer_model || 'gpt-5.4',
                input: stage1Instruction,
                ...(String(config?.responses_prompt_engineer_model || '').toLowerCase().startsWith('gemini-')
                  ? {}
                  : { tools: [{ type: 'file_search', vector_store_ids: [config?.responses_vector_store_id || 'vs_...'] }] }),
              }
            : { assistant_input: stage1Instruction },
      }
    }
    if (selectedNodeId === 'stage3_prompt_upgrade') {
      return {
        ...selectedBase,
        model: promptEngineerLabel,
        instruction: stage3Instruction,
        requestExample:
          config?.prompt_engineer_mode === 'responses_api'
            ? {
                model: config?.responses_prompt_engineer_model || 'gpt-5.4',
                input: stage3Instruction,
                ...(String(config?.responses_prompt_engineer_model || '').toLowerCase().startsWith('gemini-')
                  ? {}
                  : { tools: [{ type: 'file_search', vector_store_ids: [config?.responses_vector_store_id || 'vs_...'] }] }),
              }
            : { assistant_input: stage3Instruction },
      }
    }
    return selectedBase
  }, [config, promptEngineerLabel, selectedBase, selectedNodeId, stage1Instruction, stage3Instruction])

  return (
    <article className="card algo-static-card">
      <h2>Algorithm Architecture (Static)</h2>
      <p className="algo-subtitle">Full block-level map with the exact instruction text used by each AI call.</p>
      <p className="algo-assistant-name">
        <strong>Prompt engineer mode:</strong> {promptEngineerLabel}
      </p>
      <p className="algo-assistant-name">
        <strong>Assistant Name:</strong> {assistantName || 'Prompt generator -JSON output'} (used when prompt engineer mode is Assistant)
      </p>
      {config?.prompt_engineer_mode === 'responses_api' ? (
        <p className="algo-assistant-name">
          <strong>Prompt engineer model:</strong> {config?.responses_prompt_engineer_model || 'gpt-5.4'} {String(config?.responses_prompt_engineer_model || '').toLowerCase().startsWith('gemini-') ? '(direct model API, no vector store)' : `using vector store ${config?.responses_vector_store_id || '-'}`}
        </p>
      ) : null}
      <p className="algo-assistant-name">
        <strong>Illustration style:</strong> {visualStyleName} ({visualStyleId})
      </p>
      <p className="algo-assistant-name">
        <strong>Photorealistic style:</strong> AAC Clean Photorealistic Style v1 (built-in when the resolved decision is no person)
      </p>
      <p className="algo-assistant-name">
        <strong>Image output settings:</strong> aspect ratio {config?.image_aspect_ratio || '1:1'} | resolution {config?.image_resolution || '1K'}
      </p>
      <p className="algo-assistant-name">
        <strong>Loop logic:</strong> Stage 1 makes an initial guess about whether a person is needed -> Stage 2 creates the draft -> Stage 3.1 critique decides whether a person is actually needed for clarity -> Stage 3.2 prompt engineer uses that Stage 3.1 decision -> Stage 3.3 generates the upgraded image -> Quality Gate -> loop back to Stage 3.1 until pass or attempts exhausted -> Stage 4 creates the base white-background winner -> Stage 5 expands the white male kid baseline to requested male ages from the Stage 3 winner -> Stage 6 creates a white female kid seed from the Stage 3 winner -> Stage 7 expands that white female kid seed to requested female ages -> Stage 8 creates race variants from the matching white age/gender baselines -> Stage 9 makes white-background versions for every final variant.
      </p>
      <p className="algo-assistant-name">
        <strong>Variant staging in code:</strong> Steps 5-8 all run inside backend stage <code>stage4_variant_generate</code>. Step 9 runs inside backend stage <code>stage5_variant_white_bg</code>. If no extra variants are selected, the run completes after Stage 4.
      </p>

      <WorkflowCanvas
        nodes={nodes}
        edges={edges}
        width={3320}
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
