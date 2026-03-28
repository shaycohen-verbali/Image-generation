import React, { useEffect, useMemo, useState } from 'react'
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
  'You are an expert AAC visual designer for children. Analyze the image for concept clarity. Return STRICT JSON with keys {"challenges":"...", "recommendations":"...", "person_needed_for_clarity":"yes|no", "person_presence_problem":"missing_person|unnecessary_person|none", "person_decision_reasoning":"...", "animal_present":"yes|no"}. Concept word: <entry.word>. Part of sentence: <entry.part_of_sentence>. Category: <entry.category>. Current system hypothesis: person needed = <decision.initial_need_person>. Current render style = <decision.render_style_mode>.'

const STAGE3_ANATOMY_CRITIQUE_PROMPT_TEMPLATE =
  'You are an expert children\'s image anatomy reviewer. Analyze the image for anatomy/body-integrity problems. Return STRICT JSON with keys {"anatomy_ok":"yes|no", "issues":"...", "correction_recommendations":"...", "body_integrity_problem":"none|extra_limbs|missing_limbs|detached_body_parts|half_body|animal_anatomy_error"}. Concept word: <entry.word>. Part of sentence: <entry.part_of_sentence>. Category: <entry.category>. Person expected or present: <yes|no>. Animal expected or present: <yes|no>.'

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

const VARIANT_STEP81_PROMPT_TEMPLATE =
  'Critique the generated variant image. Check whether the clothing and visible styling make sense for the target gender/age in this exact same scene. Return STRICT JSON with keys {"correction_needed":"yes|no", "issues":"...", "correction_prompt":"...", "reason":"..."}. Make requested changes minimal and do not change the scene.'

const VARIANT_STEP82_PROMPT_TEMPLATE =
  'Using the provided variant image as the base, make only the smallest clothing/styling fixes needed for the target profile. Keep the exact same scene, action, props, framing, lighting, and composition.'

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
    model: 'gpt-4o-mini | gpt-5.4 | gemini-3-flash-preview | gemini-3-pro-preview',
    inputs: ['stage2/stage3 source image', 'word', 'part_of_sentence', 'category'],
    outputs: ['challenges', 'recommendations', 'person_needed_for_clarity', 'person_presence_problem', 'person_decision_reasoning', 'animal_present'],
    instruction: STAGE3_CRITIQUE_PROMPT_TEMPLATE,
    requestExample: {
      content: [
        { type: 'text', text: STAGE3_CRITIQUE_PROMPT_TEMPLATE },
        { type: 'image_url', image_url: { url: '<data:image/...>' } },
      ],
    },
  },
  stage3_anatomy_critique: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gpt-5.4 | gemini-3-flash-preview | gemini-3-pro-preview',
    inputs: ['stage2/stage3 source image', 'stage 3.1 person/animal signal'],
    outputs: ['anatomy_ok', 'issues', 'correction_recommendations', 'body_integrity_problem'],
    instruction: STAGE3_ANATOMY_CRITIQUE_PROMPT_TEMPLATE,
    requestExample: {
      content: [
        { type: 'text', text: STAGE3_ANATOMY_CRITIQUE_PROMPT_TEMPLATE },
        { type: 'image_url', image_url: { url: '<data:image/...>' } },
      ],
    },
  },
  stage3_accessibility_critique: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gpt-5.4 | gemini-3-flash-preview | gemini-3-pro-preview',
    inputs: ['legacy stage reference only'],
    outputs: ['skipped for new runs'],
    instruction: 'This legacy Stage 3.16 block is kept for compatibility but is skipped for new runs.',
    requestExample: {
      status: 'skipped',
    },
  },
  stage3_post_quality_accessibility_critique: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gpt-5.4 | gemini-3-flash-preview | gemini-3-pro-preview',
    inputs: ['quality-gate winner image', 'AAC grid readability lens'],
    outputs: ['simplicity_ok', 'issues', 'correction_recommendations', 'simplicity_problem'],
    instruction: 'Review a conceptually good winner image and recommend only minor AAC-grid softening. Preserve the exact same scene and recommend no changes if the image is already simple enough.',
    requestExample: {
      content: [
        { type: 'text', text: 'Check whether the winning image only needs a tiny AAC readability softening pass.' },
        { type: 'image_url', image_url: { url: '<data:image/...>' } },
      ],
    },
  },
  stage3_post_quality_accessibility_generate: {
    apiCall: 'Google image edit API',
    provider: 'Google API',
    model: 'nano-banana | nano-banana-2 | nano-banana-pro',
    inputs: ['quality image', 'minor softening instruction'],
    outputs: ['optional softened AAC image'],
    instruction: 'Using the quality image as the base, keep the exact same scene and make only minimal softening changes when the critique says they are needed.',
    requestExample: {
      prompt_source: 'post-quality AAC softening instruction',
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
    model: 'gpt-4o-mini | gemini-3-flash-preview | gemini-3-pro-preview',
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
  stage81_variant_critique: {
    apiCall: 'OpenAI or Gemini Vision',
    provider: 'OpenAI Vision / Google Gemini',
    model: 'gpt-4o-mini | gpt-5.4 | gemini-3-flash-preview | gemini-3-pro-preview',
    inputs: ['generated variant image', 'target gender/age profile'],
    outputs: ['correction_needed', 'issues', 'correction_prompt', 'reason'],
    instruction: VARIANT_STEP81_PROMPT_TEMPLATE,
    requestExample: {
      content: [
        { type: 'text', text: VARIANT_STEP81_PROMPT_TEMPLATE },
        { type: 'image_url', image_url: { url: '<variant image data URI>' } },
      ],
    },
  },
  stage82_variant_correction: {
    apiCall: 'Selected image edit model',
    provider: 'Google image-edit path with selected/fallback runtime model',
    model: 'flux-1.1-pro | imagen-3 | imagen-4 | nano-banana | nano-banana-2 | nano-banana-pro',
    inputs: ['variant image', 'step 8.1 correction prompt'],
    outputs: ['corrected variant image when needed'],
    instruction: VARIANT_STEP82_PROMPT_TEMPLATE,
    requestExample: {
      input: {
        prompt: VARIANT_STEP82_PROMPT_TEMPLATE,
        image_input: ['<variant image data URI>'],
        aspect_ratio: '<config.image_aspect_ratio>',
        image_size: '<config.image_resolution>',
        output_format: 'jpg',
      },
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
  variant_requested: {
    apiCall: 'decision node',
    provider: 'system',
    model: 'N/A',
    inputs: ['requested profile list'],
    outputs: ['continue variant DAG or finish after Stage 4'],
    instruction: 'If no extra profiles are requested, the pipeline stops after Stage 4. If one or more non-base profiles are requested, the variant DAG starts.',
    requestExample: { requested_profiles: ['male:kid:black'] },
  },
  variant_inventory_check: {
    apiCall: 'inventory lookup',
    provider: 'system + inventory table',
    model: 'N/A',
    inputs: ['word/part_of_sentence/category entry', 'requested profile key', 'override_existing_variants flag'],
    outputs: ['reuse exact requested profile or continue dependency planning'],
    instruction: 'Check word_inventory for the exact requested profile. If it already exists and override is false, reuse it and skip generation for that profile.',
    requestExample: { requested_profile: 'male:kid:black', override_existing_variants: false },
  },
  variant_family_router: {
    apiCall: 'dependency planner',
    provider: 'system',
    model: 'N/A',
    inputs: ['requested profile'],
    outputs: ['one of the profile-family branches'],
    instruction: 'Route the requested profile into the correct branch: white male kid base, male age branch, white female kid seed, female age branch, or race branch from a matching white age+gender baseline.',
    requestExample: { requested_profile: 'female:teenager:brown' },
  },
  variant_dependency_check: {
    apiCall: 'inventory lookup + recursive planner',
    provider: 'system + inventory table',
    model: 'N/A',
    inputs: ['dependency profile key', 'inventory row'],
    outputs: ['reuse dependency or create it first'],
    instruction: 'If the dependency profile already exists in inventory, reuse it. Otherwise create the dependency profile first, then continue into the requested target profile.',
    requestExample: { dependency_profile: 'female:kid:white', action: 'create first if missing' },
  },
  variant_reuse_complete: {
    apiCall: 'decision result',
    provider: 'system',
    model: 'N/A',
    inputs: ['existing exact inventory profile'],
    outputs: ['completed path by reuse'],
    instruction: 'No new image generation is needed for this profile. Reuse the inventory assets and continue to completion/export readiness.',
    requestExample: { reused_profile: 'male:kid:black' },
  },
}

function promptEngineerModeLabel(config) {
  if (config?.prompt_engineer_mode !== 'responses_api') return 'OpenAI Assistant'
  const model = String(config?.responses_prompt_engineer_model || '').toLowerCase()
  return model.startsWith('gemini-') ? 'Direct Model API' : 'Responses API + Vector Store'
}

export default function AlgorithmStaticMap({ assistantName = '', config = null, mode = 'csv_dag' }) {
  const [selectedNodeId, setSelectedNodeId] = useState(mode === 'legacy' ? 'stage4_variant_generate' : 'stage3_prompt_upgrade')
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

  useEffect(() => {
    setSelectedNodeId(mode === 'legacy' ? 'stage4_variant_generate' : 'stage3_prompt_upgrade')
  }, [mode])

  const csvDagNodes = useMemo(
    () => [
      { id: 'stage1_prompt', label: 'Stage 1 Prompt Generation', subtitle: `${promptEngineerLabel} + initial person guess`, status: 'queued', x: 40, y: 72 },
      { id: 'stage2_draft', label: 'Stage 2 Draft Image', subtitle: 'flux-schnell', status: 'queued', x: 340, y: 72 },
      { id: 'stage3_critique', label: 'Stage 3.1 Vision Critique', subtitle: 'OpenAI/Gemini + person/animal validation', status: 'queued', x: 640, y: 72 },
      { id: 'stage3_anatomy_critique', label: 'Stage 3.15 Anatomy Critique', subtitle: 'limbs + body integrity check', status: 'queued', x: 940, y: 72 },
      { id: 'stage3_accessibility_critique', label: 'Stage 3.16 Simplicity Critique (disabled)', subtitle: 'kept for compatibility, skipped for new runs', status: 'queued', x: 1240, y: 72 },
      { id: 'stage3_prompt_upgrade', label: 'Stage 3.2 Prompt Upgrade', subtitle: `${promptEngineerLabel} + resolved style`, status: 'queued', x: 1540, y: 72 },
      { id: 'stage3_generate', label: 'Stage 3.3 Upgraded Image', subtitle: 'selected model', status: 'queued', x: 1840, y: 72 },
      { id: 'quality_gate', label: 'Quality Gate', subtitle: 'OpenAI/Gemini score', status: 'queued', x: 2140, y: 72 },
      { id: 'stage3_post_quality_accessibility_critique', label: 'Post-quality AAC Critique', subtitle: 'minor readability review only', status: 'queued', x: 2440, y: 72 },
      { id: 'stage3_post_quality_accessibility_generate', label: 'Post-quality AAC Soften Image', subtitle: 'optional minor soften pass', status: 'queued', x: 2740, y: 72 },
      { id: 'stage4_background', label: 'Stage 4 White Background', subtitle: 'use softened image when present', status: 'queued', x: 3040, y: 72 },
      {
        id: 'variant_requested',
        label: 'IF more profiles are requested',
        subtitle: 'No -> finish after Stage 4. Yes -> open the variant DAG.',
        kind: 'decision',
        status: 'queued',
        x: 3340,
        y: 72,
      },
      {
        id: 'variant_inventory_check',
        label: 'THEN check exact profile in inventory',
        subtitle: 'Reuse exact requested profile if it already exists and override is off.',
        kind: 'decision',
        status: 'queued',
        x: 3640,
        y: 72,
      },
      {
        id: 'variant_reuse_complete',
        label: 'Reuse Existing Inventory Variant',
        subtitle: 'No new generation for that exact profile.',
        kind: 'decision',
        status: 'ok',
        x: 3940,
        y: 72,
      },
      {
        id: 'variant_family_router',
        label: 'THEN choose profile path',
        subtitle: 'male age / female seed / female age / race branch',
        kind: 'decision',
        status: 'queued',
        x: 4240,
        y: 72,
      },
      {
        id: 'variant_dependency_check',
        label: 'IF dependency baseline is missing',
        subtitle: 'Reuse it from inventory when present; otherwise create it first.',
        kind: 'decision',
        status: 'queued',
        x: 4540,
        y: 72,
      },
      {
        id: 'stage5_male_age',
        label: 'Step 5 Male Age Expansion',
        subtitle: 'White male kid -> requested male ages for the same race.',
        badge: 'backend: stage4_variant_generate',
        meta: [
          'Input: matching male kid baseline',
          'Output: requested male tween/teen baselines',
        ],
        status: 'queued',
        x: 2740,
        y: 356,
      },
      {
        id: 'stage6_female_seed',
        label: 'Step 6 Female Seed',
        subtitle: 'White male kid -> white female kid seed.',
        badge: 'backend: stage4_variant_generate',
        meta: [
          'Input: white male kid baseline',
          'Output: white female kid baseline',
        ],
        status: 'queued',
        x: 3040,
        y: 356,
      },
      {
        id: 'stage7_female_age',
        label: 'Step 7 Female Age Expansion',
        subtitle: 'White female kid -> requested female ages for the same race.',
        badge: 'backend: stage4_variant_generate',
        meta: [
          'Input: matching female kid baseline',
          'Output: requested female tween/teen baselines',
        ],
        status: 'queued',
        x: 3340,
        y: 356,
      },
      {
        id: 'stage8_race_expand',
        label: 'Step 8 Race Expansion',
        subtitle: 'Use matching white age+gender baseline -> requested race.',
        badge: 'backend: stage4_variant_generate',
        meta: [
          'Example: white male kid -> black/asian/brown male kid',
          'Example: white female teenager -> black/asian/brown female teenager',
        ],
        status: 'queued',
        x: 3640,
        y: 356,
      },
      {
        id: 'stage81_variant_critique',
        label: 'Step 8.1 Variant Critique',
        subtitle: 'Review clothing/styling for age or gender changes.',
        badge: 'backend: stage4_variant_critique',
        status: 'queued',
        x: 3940,
        y: 356,
      },
      {
        id: 'stage82_variant_correction',
        label: 'Step 8.2 Variant Correction',
        subtitle: 'One minimal correction pass only when needed.',
        badge: 'backend: stage4_variant_correction',
        status: 'queued',
        x: 4240,
        y: 356,
      },
      { id: 'stage9_variant_white_bg', label: 'Step 9 Variant White BG', subtitle: 'white background for every final variant', badge: 'backend: stage5_variant_white_bg', status: 'queued', x: 4540, y: 356 },
      { id: 'completed_pass', label: 'Completed Pass', subtitle: 'ready for export', status: 'ok', x: 4840, y: 356 },
      { id: 'completed_fail', label: 'Completed Fail', subtitle: 'below threshold', status: 'error', x: 2140, y: 204 },
    ],
    [promptEngineerLabel],
  )

  const csvDagEdges = useMemo(
    () => [
      { from: 'stage1_prompt', to: 'stage2_draft', label: 'prompt 1 + initial style hypothesis', fromPort: 'right', toPort: 'left' },
      { from: 'stage2_draft', to: 'stage3_critique', label: 'start attempt 1', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_critique', to: 'stage3_anatomy_critique', label: 'if person or animal is present', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_critique', to: 'stage3_accessibility_critique', label: 'record disabled legacy step', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_anatomy_critique', to: 'stage3_accessibility_critique', label: 'skip legacy step', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_accessibility_critique', to: 'stage3_prompt_upgrade', label: 'continue without simplicity edits', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_prompt_upgrade', to: 'stage3_generate', label: 'upgraded prompt', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_generate', to: 'quality_gate', label: 'candidate image', fromPort: 'right', toPort: 'left' },
      { from: 'quality_gate', to: 'stage3_critique', label: 'fail + attempts remain', type: 'loop', fromPort: 'left', toPort: 'top' },
      { from: 'quality_gate', to: 'stage3_post_quality_accessibility_critique', label: 'winner selected', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_post_quality_accessibility_critique', to: 'stage3_post_quality_accessibility_generate', label: 'only if minor softening is needed', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_post_quality_accessibility_critique', to: 'stage4_background', label: 'already AAC-friendly', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_post_quality_accessibility_generate', to: 'stage4_background', label: 'use softened image', fromPort: 'right', toPort: 'left' },
      { from: 'quality_gate', to: 'completed_fail', label: 'fail after max attempts', type: 'branch', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage4_background', to: 'variant_requested', label: 'base winner is ready', fromPort: 'right', toPort: 'left' },
      { from: 'variant_requested', to: 'completed_pass', label: 'no extra profiles requested', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_requested', to: 'variant_inventory_check', label: 'yes -> inspect requested profile(s)', fromPort: 'right', toPort: 'left' },
      { from: 'variant_inventory_check', to: 'variant_reuse_complete', label: 'exact profile already exists + override off', fromPort: 'right', toPort: 'left' },
      { from: 'variant_reuse_complete', to: 'completed_pass', label: 'reuse existing assets', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_inventory_check', to: 'variant_family_router', label: 'exact profile missing or override on', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_family_router', to: 'variant_dependency_check', label: 'look up dependency baseline', fromPort: 'right', toPort: 'left' },
      { from: 'variant_dependency_check', to: 'stage5_male_age', label: 'male age path', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_dependency_check', to: 'stage6_female_seed', label: 'white female kid seed path', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_dependency_check', to: 'stage7_female_age', label: 'female age path', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_dependency_check', to: 'stage8_race_expand', label: 'race path from matching white baseline', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage5_male_age', to: 'stage81_variant_critique', label: 'gender same, age changed', fromPort: 'right', toPort: 'left' },
      { from: 'stage6_female_seed', to: 'stage81_variant_critique', label: 'gender changed', fromPort: 'right', toPort: 'left' },
      { from: 'stage7_female_age', to: 'stage81_variant_critique', label: 'gender/age path complete', fromPort: 'right', toPort: 'left' },
      { from: 'stage8_race_expand', to: 'stage81_variant_critique', label: 'review clothing/styling', fromPort: 'right', toPort: 'left' },
      { from: 'stage81_variant_critique', to: 'stage82_variant_correction', label: 'only if fixes are needed', fromPort: 'right', toPort: 'left' },
      { from: 'stage81_variant_critique', to: 'stage9_variant_white_bg', label: 'skip correction when clean', fromPort: 'right', toPort: 'left' },
      { from: 'stage82_variant_correction', to: 'stage9_variant_white_bg', label: 'corrected final -> white BG', fromPort: 'right', toPort: 'left' },
      { from: 'stage9_variant_white_bg', to: 'completed_pass', label: 'done', fromPort: 'right', toPort: 'left' },
    ],
    [],
  )

  const legacyNodes = useMemo(
    () => [
      { id: 'stage1_prompt', label: 'Stage 1 Prompt Generation', subtitle: `${promptEngineerLabel} + initial person guess`, status: 'queued', x: 40, y: 72 },
      { id: 'stage2_draft', label: 'Stage 2 Draft Image', subtitle: 'flux-schnell', status: 'queued', x: 340, y: 72 },
      { id: 'stage3_critique', label: 'Stage 3.1 Vision Critique', subtitle: 'OpenAI/Gemini + person/animal validation', status: 'queued', x: 640, y: 72 },
      { id: 'stage3_anatomy_critique', label: 'Stage 3.15 Anatomy Critique', subtitle: 'limbs + body integrity check', status: 'queued', x: 940, y: 72 },
      { id: 'stage3_accessibility_critique', label: 'Stage 3.16 Simplicity Critique (disabled)', subtitle: 'kept for compatibility, skipped for new runs', status: 'queued', x: 1240, y: 72 },
      { id: 'stage3_prompt_upgrade', label: 'Stage 3.2 Prompt Upgrade', subtitle: `${promptEngineerLabel} + resolved style`, status: 'queued', x: 1540, y: 72 },
      { id: 'stage3_generate', label: 'Stage 3.3 Upgraded Image', subtitle: 'selected model', status: 'queued', x: 1840, y: 72 },
      { id: 'quality_gate', label: 'Quality Gate', subtitle: 'OpenAI/Gemini score', status: 'queued', x: 2140, y: 72 },
      { id: 'stage3_post_quality_accessibility_critique', label: 'Post-quality AAC Critique', subtitle: 'minor readability review only', status: 'queued', x: 2440, y: 72 },
      { id: 'stage3_post_quality_accessibility_generate', label: 'Post-quality AAC Soften Image', subtitle: 'optional minor soften pass', status: 'queued', x: 2740, y: 72 },
      { id: 'stage4_background', label: 'Stage 4 White Background', subtitle: 'use softened image when present', status: 'queued', x: 3040, y: 72 },
      {
        id: 'variant_requested',
        label: 'IF person variants were selected on Submit',
        subtitle: 'No -> finish the run. Yes -> create optional variants for this same run.',
        kind: 'decision',
        status: 'queued',
        x: 3340,
        y: 72,
      },
      {
        id: 'stage4_variant_generate',
        label: 'Stage 5-8 Variant Finals',
        subtitle: 'Generate the requested profile variants directly inside the run.',
        badge: 'backend: stage4_variant_generate',
        meta: [
          'No inventory dependency planner here',
          'Uses the current run winner image as the source',
        ],
        status: 'queued',
        x: 3640,
        y: 72,
      },
      {
        id: 'stage81_variant_critique',
        label: 'Step 8.1 Variant Critique',
        subtitle: 'Review clothing/styling for age or gender changes.',
        badge: 'backend: stage4_variant_critique',
        status: 'queued',
        x: 3940,
        y: 72,
      },
      {
        id: 'stage82_variant_correction',
        label: 'Step 8.2 Variant Correction',
        subtitle: 'One minimal correction pass only when needed.',
        badge: 'backend: stage4_variant_correction',
        status: 'queued',
        x: 4240,
        y: 72,
      },
      { id: 'stage5_variant_white_bg', label: 'Stage 9 Variant White BG', subtitle: 'white background for every final variant', badge: 'backend: stage5_variant_white_bg', status: 'queued', x: 4540, y: 72 },
      { id: 'completed_pass', label: 'Completed Pass', subtitle: 'ready for export', status: 'ok', x: 4840, y: 72 },
      { id: 'completed_fail', label: 'Completed Fail', subtitle: 'below threshold or technical failure', status: 'error', x: 2140, y: 220 },
    ],
    [promptEngineerLabel],
  )

  const legacyEdges = useMemo(
    () => [
      { from: 'stage1_prompt', to: 'stage2_draft', label: 'prompt 1 + initial style hypothesis', fromPort: 'right', toPort: 'left' },
      { from: 'stage2_draft', to: 'stage3_critique', label: 'start attempt 1', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_critique', to: 'stage3_anatomy_critique', label: 'if person or animal is present', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_critique', to: 'stage3_accessibility_critique', label: 'record disabled legacy step', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_anatomy_critique', to: 'stage3_accessibility_critique', label: 'skip legacy step', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_accessibility_critique', to: 'stage3_prompt_upgrade', label: 'continue without simplicity edits', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_prompt_upgrade', to: 'stage3_generate', label: 'upgraded prompt', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_generate', to: 'quality_gate', label: 'candidate image', fromPort: 'right', toPort: 'left' },
      { from: 'quality_gate', to: 'stage3_critique', label: 'fail + attempts remain', type: 'loop', fromPort: 'left', toPort: 'top' },
      { from: 'quality_gate', to: 'stage3_post_quality_accessibility_critique', label: 'winner selected', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_post_quality_accessibility_critique', to: 'stage3_post_quality_accessibility_generate', label: 'only if minor softening is needed', fromPort: 'right', toPort: 'left' },
      { from: 'stage3_post_quality_accessibility_critique', to: 'stage4_background', label: 'already AAC-friendly', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage3_post_quality_accessibility_generate', to: 'stage4_background', label: 'use softened image', fromPort: 'right', toPort: 'left' },
      { from: 'quality_gate', to: 'completed_fail', label: 'fail after max attempts', type: 'branch', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage4_background', to: 'variant_requested', label: 'base winner is ready', fromPort: 'right', toPort: 'left' },
      { from: 'variant_requested', to: 'completed_pass', label: 'no variants selected', fromPort: 'bottom', toPort: 'top' },
      { from: 'variant_requested', to: 'stage4_variant_generate', label: 'yes -> create selected variants', fromPort: 'right', toPort: 'left' },
      { from: 'stage4_variant_generate', to: 'stage81_variant_critique', label: 'review clothing/styling', fromPort: 'right', toPort: 'left' },
      { from: 'stage81_variant_critique', to: 'stage82_variant_correction', label: 'only if fixes are needed', fromPort: 'right', toPort: 'left' },
      { from: 'stage81_variant_critique', to: 'stage5_variant_white_bg', label: 'skip correction when clean', fromPort: 'bottom', toPort: 'top' },
      { from: 'stage82_variant_correction', to: 'stage5_variant_white_bg', label: 'corrected final -> white BG', fromPort: 'right', toPort: 'left' },
      { from: 'stage5_variant_white_bg', to: 'completed_pass', label: 'done', fromPort: 'right', toPort: 'left' },
    ],
    [],
  )

  const nodes = mode === 'legacy' ? legacyNodes : csvDagNodes
  const edges = mode === 'legacy' ? legacyEdges : csvDagEdges
  const canvasWidth = mode === 'legacy' ? 4260 : 4560
  const canvasHeight = mode === 'legacy' ? 360 : 560

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
        <strong>Mode:</strong> {mode === 'legacy' ? 'Legacy fallback runs' : 'Parallel CSV DAG'}
      </p>
      {mode === 'legacy' ? (
        <>
          <p className="algo-assistant-name">
            <strong>How to read this:</strong> this tab shows the simpler per-run fallback flow. Each imported row becomes its own legacy run, and the run works left-to-right without the CSV inventory dependency planner.
          </p>
          <p className="algo-assistant-name">
            <strong>Base track:</strong> Stage 1 decides the first prompt -> Stage 2 makes the draft -> Stage 3.1 critiques concept clarity -> Stage 3.15 checks anatomy only for person/animal scenes -> Stage 3.2 upgrades the prompt -> Stage 3.3 regenerates the image -> Quality Gate loops back until pass or max attempts -> Stage 4 creates the base white-background winner.
          </p>
          <p className="algo-assistant-name">
            <strong>Legacy variant track:</strong> after Stage 4, the run optionally creates the selected person variants directly from the current run winner, then runs Step 8.1 critique, optional Step 8.2 correction, and Stage 9 white background.
          </p>
          <p className="algo-assistant-name">
            <strong>What is different here:</strong> there is no per-word inventory reuse planner or dependency chain lookup on this path. The work stays inside the current legacy run.
          </p>
        </>
      ) : (
        <>
          <p className="algo-assistant-name">
            <strong>How to read this:</strong> follow the top row from left to right for the base image pipeline. After Stage 4, the top-row decision nodes explain whether we reuse inventory, which dependency baseline is needed, and which profile-specific branch runs next.
          </p>
          <p className="algo-assistant-name">
            <strong>Base track:</strong> Stage 1 decides the first prompt -> Stage 2 makes the draft -> Stage 3.1 critiques concept clarity -> Stage 3.15 checks anatomy only for person/animal scenes -> Stage 3.2 upgrades the prompt -> Stage 3.3 regenerates the image -> Quality Gate loops back until pass or max attempts -> Stage 4 creates the base white-background winner.
          </p>
          <p className="algo-assistant-name">
            <strong>Variant track:</strong> After Stage 4, the system first checks whether the exact requested profile already exists in inventory. If yes and override is off, it reuses it. Otherwise it follows the dependency table: white male kid base -> male age expansion, white female kid seed, female age expansion, and race expansion from the matching white age+gender baseline. Then it runs Step 8.1 critique, optional Step 8.2 correction, and Step 9 white background.
          </p>
          <p className="algo-assistant-name">
            <strong>Dependency examples:</strong> requesting <code>male:kid:asian</code> reuses <code>male:kid:asian</code> if it already exists; otherwise it looks for <code>male:kid:white</code> and creates that dependency first if needed. Requesting <code>female:teenager:brown</code> can require the chain <code>male:kid:white -&gt; female:kid:white -&gt; female:teenager:white -&gt; female:teenager:brown</code>.
          </p>
          <p className="algo-assistant-name">
            <strong>Variant staging in code:</strong> Steps 5-8 run inside backend stage <code>stage4_variant_generate</code>, Step 8.1 is recorded as <code>stage4_variant_critique</code>, Step 8.2 is recorded as <code>stage4_variant_correction</code>, and Step 9 runs inside backend stage <code>stage5_variant_white_bg</code>. If no extra variants are selected, or if the exact profile is reused from inventory, the run completes without new variant generation.
          </p>
        </>
      )}

      <WorkflowCanvas
        nodes={nodes}
        edges={edges}
        width={canvasWidth}
        height={canvasHeight}
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
