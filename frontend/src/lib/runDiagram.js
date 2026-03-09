const ILLUSTRATION_STYLE_HINT =
  'Illustration style to use when a person is needed: follow the configured house illustration style block.'
const PHOTOREALISTIC_HINT =
  'Photorealistic style to use when a person is not needed: use a clean premium photorealistic AAC style and do not include a person.'

const STAGE_DEFINITIONS = [
  {
    id: 'stage1_prompt',
    label: 'Stage 1 Prompt',
    provider: 'Prompt Engineer',
    inputs: ['word', 'part_of_sentence', 'category', 'context', 'boy_or_girl'],
    expected: ['first prompt', 'need a person', 'initial render style hypothesis'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage2_draft',
    label: 'Stage 2 Draft',
    provider: 'Replicate: flux-schnell',
    inputs: ['prompt 1'],
    expected: ['prediction status', 'output URL', 'draft image'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage3_critique',
    label: 'Stage 3.1 Critique',
    provider: 'OpenAI Vision',
    inputs: ['previous image', 'word/POS/category'],
    expected: ['challenges', 'recommendations', 'person_needed_for_clarity', 'person_presence_problem'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage3_prompt_upgrade',
    label: 'Stage 3.2 Prompt Upgrade',
    provider: 'Prompt Engineer',
    inputs: ['old prompt', 'critique', 'previous score feedback'],
    expected: ['upgraded prompt', 'resolved person/style decision'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage3_generate',
    label: 'Stage 3.3 Image Generate',
    provider: 'Replicate: selected generation model',
    inputs: ['upgraded prompt'],
    expected: ['upgraded image'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'quality_gate',
    label: 'Quality Gate',
    provider: 'OpenAI Vision',
    inputs: ['stage3 image', 'word/POS/category', 'threshold'],
    expected: ['score', 'explanation', 'failure_tags', 'pass_fail'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage4_background',
    label: 'Stage 4 White Background',
    provider: 'Replicate: nano-banana-2',
    inputs: ['passing stage3 image'],
    expected: ['white background image'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'completed',
    label: 'Completed',
    provider: 'System',
    inputs: ['final run state'],
    expected: ['completed_pass | completed_fail_threshold | failed_technical'],
    retryPolicy: 'N/A',
  },
]

const FLOW_EDGES = [
  { from: 'stage1_prompt', to: 'stage2_draft', label: 'prompt + initial style guess', fromPort: 'right', toPort: 'left' },
  { from: 'stage2_draft', to: 'stage3_critique', label: 'start attempt 1', fromPort: 'right', toPort: 'left' },
  { from: 'stage3_critique', to: 'stage3_prompt_upgrade', label: 'critique + person validation', fromPort: 'bottom', toPort: 'top' },
  { from: 'stage3_prompt_upgrade', to: 'stage3_generate', label: 'upgraded prompt + resolved style', fromPort: 'bottom', toPort: 'top' },
  { from: 'stage3_generate', to: 'quality_gate', label: 'image', fromPort: 'right', toPort: 'left' },
  { from: 'quality_gate', to: 'stage3_critique', label: 'loop retry', type: 'loop', fromPort: 'left', toPort: 'top' },
  { from: 'quality_gate', to: 'stage4_background', label: 'winner selected', fromPort: 'top', toPort: 'left' },
  { from: 'stage4_background', to: 'completed', label: 'final', fromPort: 'right', toPort: 'left' },
]

const STATUS_LABELS = {
  queued: 'Queued',
  running: 'Running',
  ok: 'OK',
  error: 'Error',
  skipped: 'Skipped',
}

function safeObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value
}

function safeText(value) {
  if (value === undefined || value === null) return ''
  return String(value)
}

function promptEngineerLabel(mode, responsesModel) {
  const normalizedMode = safeText(mode).toLowerCase()
  if (normalizedMode === 'responses_api') {
    const normalizedModel = safeText(responsesModel).toLowerCase()
    if (normalizedModel.startsWith('gemini-')) {
      return responsesModel ? `Direct Model (${responsesModel})` : 'Direct Model'
    }
    return responsesModel ? `Responses API (${responsesModel})` : 'Responses API'
  }
  return 'OpenAI Assistant'
}

function asStageStatus(value) {
  const status = String(value || '').toLowerCase()
  if (status === 'ok' || status === 'completed' || status === 'succeeded') return 'ok'
  if (status === 'running' || status === 'in_progress') return 'running'
  if (status === 'skipped') return 'skipped'
  if (status.includes('error') || status.includes('fail') || status === 'cancelled' || status === 'canceled') return 'error'
  return 'queued'
}

function makeKey(stageName, attempt) {
  return `${stageName}:${attempt}`
}

function mapByAttemptAndStage(items, stageField, attemptField) {
  const index = new Map()
  items.forEach((item) => {
    const stageName = String(item[stageField] || '')
    const attempt = Number(item[attemptField] || 0)
    index.set(makeKey(stageName, attempt), item)
  })
  return index
}

function mapScoresByAttempt(scores) {
  const index = new Map()
  scores.forEach((score) => {
    index.set(Number(score.attempt || 0), score)
  })
  return index
}

function stageDef(stageId) {
  return STAGE_DEFINITIONS.find((stage) => stage.id === stageId)
}

function buildAttemptSummaries(detail, stageIndex, scoreIndex) {
  const run = safeObject(detail.run)
  const winnerAttempt = Number(run.optimization_attempt || 0)
  const completed = String(run.status || '').startsWith('completed_')
  return getAvailableAttempts(detail).map((attempt) => {
    const stage3 = stageIndex.get(makeKey('stage3_upgrade', attempt))
    const quality = stageIndex.get(makeKey('quality_gate', attempt))
    const stage4 = stageIndex.get(makeKey('stage4_background', attempt))
    const score = scoreIndex.get(attempt)
    let stage4Status = asStageStatus(stage4?.status)
    if (completed && !stage4 && winnerAttempt > 0 && attempt !== winnerAttempt) {
      stage4Status = 'skipped'
    }
    return {
      attempt,
      stage3Status: asStageStatus(stage3?.status),
      qualityStatus: asStageStatus(quality?.status),
      stage4Status,
      score: score?.score_0_100 ?? null,
      passFail: typeof score?.pass_fail === 'boolean' ? score.pass_fail : null,
    }
  })
}

function nodeStatus({ stageId, stageResult, run, attempt, score }) {
  const currentAttempt = Math.max(1, Number(run.optimization_attempt || 1))
  const isCurrentBase = run.status === 'running' && run.current_stage === stageId
  const isCurrentAttemptStage = run.status === 'running' && run.current_stage === stageId && attempt === currentAttempt

  if (stageId === 'stage1_prompt' || stageId === 'stage2_draft') {
    if (isCurrentBase) return 'running'
    return asStageStatus(stageResult?.status)
  }

  if (stageId === 'stage3_critique' || stageId === 'stage3_prompt_upgrade' || stageId === 'stage3_generate') {
    if (run.status === 'running' && run.current_stage === 'stage3_upgrade' && attempt === currentAttempt && !stageResult) {
      return 'running'
    }
    return asStageStatus(stageResult?.status)
  }

  if (stageId === 'stage4_background') {
    const winnerAttempt = Number(run.optimization_attempt || 0)
    const isCompleted = String(run.status || '').startsWith('completed_')
    if (isCompleted && winnerAttempt > 0 && attempt !== winnerAttempt && !stageResult) return 'skipped'
    if (isCurrentAttemptStage) return 'running'
    if (stageResult) return asStageStatus(stageResult.status)
    if (score && score.pass_fail === false) return 'skipped'
    return 'queued'
  }

  if (stageId === 'completed') {
    if (run.status === 'completed_pass' && attempt === Number(run.optimization_attempt || 0)) return 'ok'
    if ((run.status === 'completed_fail_threshold' || run.status === 'failed_technical') && attempt === Number(run.optimization_attempt || 0)) return 'error'
    return 'queued'
  }

  if (isCurrentAttemptStage) return 'running'
  return asStageStatus(stageResult?.status)
}

function nodeSubtitle(stageId, run, score, attempt) {
  if (stageId === 'completed') {
    if (run.status === 'completed_pass' && attempt === Number(run.optimization_attempt || 0)) return 'Pass'
    if (run.status === 'completed_fail_threshold' && attempt === Number(run.optimization_attempt || 0)) return 'Fail threshold'
    if (run.status === 'failed_technical' && attempt === Number(run.optimization_attempt || 0)) return 'Technical failure'
    return 'Pending'
  }
  if (stageId === 'quality_gate' && score) {
    return `Score ${score.score_0_100}${score.pass_fail ? ' (pass)' : ' (fail)'}`
  }
  return ''
}

function parseStage1Context(stage1Instruction, run) {
  const lines = safeText(stage1Instruction)
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

  function after(prefix) {
    const line = lines.find((value) => value.toLowerCase().startsWith(prefix.toLowerCase()))
    if (!line) return ''
    return line.slice(prefix.length).trim()
  }

  return {
    context: after('Context:') || '',
    word: after('Word:') || safeText(run.word),
    partOfSentence: after('Part of speech:') || safeText(run.part_of_sentence),
    category: after('Category:') || safeText(run.category),
    boyOrGirl: after('If a person is present, use a:') || '',
  }
}

function stage1Template(ctx) {
  return [
    'Task: Create the first image prompt for the given word and decide if the prompt needs a person.',
    'Return STRICT JSON with keys exactly:',
    '{ "first prompt": "<string>", "need a person": "yes" | "no" }',
    '',
    `Context: ${ctx.context}`,
    `Word: ${ctx.word}`,
    `Part of speech: ${ctx.partOfSentence}`,
    `Category: ${ctx.category}`,
    `If a person is present, use a: ${ctx.boyOrGirl}`,
    '',
    'Decision rule:',
    '- If a person is needed for AAC clarity, the prompt should use an illustration and make the person central.',
    '- If a person is not needed for AAC clarity, the prompt should be photorealistic and should not include a person.',
    '',
    ILLUSTRATION_STYLE_HINT,
    PHOTOREALISTIC_HINT,
  ].join('\n')
}

function critiqueTemplate(ctx) {
  return (
    'You are an expert AAC visual designer for children. ' +
    'Analyze the image for concept clarity. Return STRICT JSON with keys ' +
    '{"challenges":"...", "recommendations":"...", "person_needed_for_clarity":"yes|no", "person_presence_problem":"missing_person|unnecessary_person|none"}. ' +
    `Concept word: ${ctx.word}. Part of sentence: ${ctx.partOfSentence}. Category: ${ctx.category}.`
  )
}

function qualityTemplate(ctx, threshold, expectedRenderStyleMode) {
  return (
    'Score the AAC concept image quality for a child user. Return STRICT JSON with fields: ' +
    '{"score":0-100, "explanation":"...", "failure_tags":["ambiguity","clutter","wrong_concept","text_in_image","distracting_details"]}. ' +
    `Word: ${ctx.word}. Part of sentence: ${ctx.partOfSentence}. Category: ${ctx.category}. ` +
    `Pass threshold is ${threshold}. Expected render style is ${expectedRenderStyleMode || 'not specified'}.`
  )
}

function whiteBgTemplate(word) {
  return (
    'remove the background - keep only the important elements of the image and make the background white. ' +
    `The image's main message is to represent the concept "${word}". ` +
    'Do not add text in the image.'
  )
}

function aiInstructionForStage({
  stageId,
  run,
  stage1Context,
  stage1Result,
  stage2Result,
  stage3Result,
  stage1Prompt,
  stage3Prompt,
  stage3UpgradeRequest,
}) {
  if (stageId === 'stage1_prompt') {
    const stored = safeText(safeObject(stage1Result?.request_json).prompt)
    return {
      text: stored || stage1Template(stage1Context),
      source: stored ? 'stored request_json.prompt' : 'backend prompt template',
    }
  }

  if (stageId === 'stage2_draft') {
    const storedPrompt = safeText(safeObject(stage2Result?.request_json).prompt) || safeText(stage1Prompt?.prompt_text)
    return {
      text: storedPrompt
        ? JSON.stringify({ input: { prompt: storedPrompt, output_format: 'jpg' } }, null, 2)
        : JSON.stringify({ input: { prompt: '<stage1 first prompt>', output_format: 'jpg' } }, null, 2),
      source: storedPrompt ? 'stored request_json.prompt' : 'derived from stage prompt lineage',
    }
  }

  if (stageId === 'stage3_critique') {
    return {
      text: critiqueTemplate(stage1Context),
      source: 'backend prompt template (OpenAIClient.analyze_image)',
    }
  }

  if (stageId === 'stage3_prompt_upgrade') {
    return {
      text: safeText(stage3UpgradeRequest),
      source: stage3UpgradeRequest ? 'stored request_json.upgrade_prompt_request' : 'missing from payload',
    }
  }

  if (stageId === 'stage3_generate') {
    const prompt = safeText(stage3Prompt?.prompt_text)
    const payload = {
      selected_model: safeText(safeObject(stage3Result?.request_json).generation_model_selected) || '<selected stage3 model>',
      primary_model: 'runtime dependent',
      primary_input: {
        prompt: prompt || '<stage3 upgraded prompt>',
        aspect_ratio: '4:3',
        output_format: 'jpg',
        output_quality: 80,
        prompt_upsampling: false,
        safety_tolerance: 2,
        seed: 10000,
      },
      fallback_model: 'google/imagen-3-fast (only when enabled and flux-1.1-pro is selected)',
      fallback_input: {
        prompt: prompt || '<stage3 upgraded prompt>',
        num_outputs: 1,
        aspect_ratio: '4:3',
        output_format: 'jpg',
        output_quality: 80,
        prompt_upsampling: true,
        safety_tolerance: 2,
      },
    }
    return {
      text: JSON.stringify(payload, null, 2),
      source: prompt ? 'stored upgraded prompt + backend model payloads' : 'backend model payload templates',
    }
  }

  if (stageId === 'quality_gate') {
    const expectedRenderStyle = safeText(safeObject(stage3Result?.response_json).decision?.render_style_mode)
    return {
      text: qualityTemplate(stage1Context, Number(run.quality_threshold || 95), expectedRenderStyle),
      source: 'backend prompt template (OpenAIClient.score_image)',
    }
  }

  if (stageId === 'stage4_background') {
    const prompt = whiteBgTemplate(stage1Context.word || safeText(run.word))
    const payload = {
      input: {
        prompt,
        image_input: ['<stage3 upgraded image as data URI>'],
        aspect_ratio: 'match_input_image',
        output_format: 'jpg',
      },
    }
    return {
      text: JSON.stringify(payload, null, 2),
      source: 'backend prompt template (ReplicateClient.nano_banana_white_bg -> nano-banana-2)',
    }
  }

  return { text: 'No AI instruction for this system-only block.', source: 'system transition' }
}

export function getAvailableAttempts(detail) {
  if (!detail) return [1]
  const attempts = new Set()
  detail.stages.forEach((stage) => {
    const attempt = Number(stage.attempt || 0)
    if (attempt > 0) attempts.add(attempt)
  })
  detail.prompts.forEach((prompt) => {
    if (prompt.stage_name !== 'stage3_upgrade') return
    const attempt = Number(prompt.attempt || 0)
    if (attempt > 0) attempts.add(attempt)
  })
  detail.scores.forEach((score) => {
    const attempt = Number(score.attempt || 0)
    if (attempt > 0) attempts.add(attempt)
  })
  detail.assets.forEach((asset) => {
    if (asset.stage_name !== 'stage3_upgraded' && asset.stage_name !== 'stage4_white_bg') return
    const attempt = Number(asset.attempt || 0)
    if (attempt > 0) attempts.add(attempt)
  })
  const currentAttempt = Number(detail.run?.optimization_attempt || 0)
  if (currentAttempt > 0) attempts.add(currentAttempt)
  if (attempts.size === 0) return [1]
  return Array.from(attempts).sort((left, right) => left - right)
}

export function buildRunDiagram(detail, selectedAttempt) {
  if (!detail) {
    return {
      nodes: [],
      edges: FLOW_EDGES,
      attemptSummaries: [],
    }
  }

  const attempt = Number(selectedAttempt || 1)
  const run = safeObject(detail.run)
  const stages = Array.isArray(detail.stages) ? detail.stages : []
  const prompts = Array.isArray(detail.prompts) ? detail.prompts : []
  const assets = Array.isArray(detail.assets) ? detail.assets : []
  const scores = Array.isArray(detail.scores) ? detail.scores : []

  const stageIndex = mapByAttemptAndStage(stages, 'stage_name', 'attempt')
  const promptIndex = mapByAttemptAndStage(prompts, 'stage_name', 'attempt')
  const assetIndex = mapByAttemptAndStage(assets, 'stage_name', 'attempt')
  const scoreIndex = mapScoresByAttempt(scores)

  const stage1Result = stageIndex.get(makeKey('stage1_prompt', 0))
  const stage2Result = stageIndex.get(makeKey('stage2_draft', 0))
  const stage3Result = stageIndex.get(makeKey('stage3_upgrade', attempt))
  const qualityResult = stageIndex.get(makeKey('quality_gate', attempt))
  const stage4Result = stageIndex.get(makeKey('stage4_background', attempt))

  const stage1Prompt = promptIndex.get(makeKey('stage1_prompt', 0))
  const stage3Prompt = promptIndex.get(makeKey('stage3_upgrade', attempt))

  const stage2Asset = assetIndex.get(makeKey('stage2_draft', 0))
  const stage3Asset = assetIndex.get(makeKey('stage3_upgraded', attempt))
  const stage4Asset = assetIndex.get(makeKey('stage4_white_bg', attempt))

  const score = scoreIndex.get(attempt)
  const stage3Response = safeObject(stage3Result?.response_json)
  const stage3Request = safeObject(stage3Result?.request_json)
  const stage3Analysis = safeObject(stage3Response.analysis)
  const stage3PromptEngineer = safeObject(stage3Response.prompt_engineer)
  const stage3Assistant = Object.keys(stage3PromptEngineer).length > 0 ? stage3PromptEngineer : safeObject(stage3Response.assistant)
  const stage3Generation = safeObject(stage3Response.generation)
  const stage3UpgradeRequest = safeText(stage3Request.upgrade_prompt_request)
  const stage3GenerationModel = safeText(stage3Response.generation_model)
  const stage1Request = safeObject(stage1Result?.request_json)
  const stage1PromptEngineerMode = safeText(stage1Request.prompt_engineer_mode) || safeText(safeObject(stage1Prompt?.raw_response_json).prompt_engineer_mode) || 'assistant'
  const stage3PromptEngineerMode = safeText(stage3Request.prompt_engineer_mode) || safeText(stage3Assistant.mode) || safeText(safeObject(stage3Prompt?.raw_response_json).prompt_engineer_mode) || 'assistant'
  const stage1ResponsesModel = safeText(stage1Request.responses_model)
  const stage3ResponsesModel = safeText(stage3Request.responses_model)

  const stage1Instruction = safeText(stage1Request.prompt)
  const stage1Context = parseStage1Context(stage1Instruction, run)

  const nodeData = [
    {
      id: 'stage1_prompt',
      stageResult: stage1Result,
      promptRecord: stage1Prompt || null,
      asset: null,
      model: promptEngineerLabel(stage1PromptEngineerMode, stage1ResponsesModel),
      score: null,
      attempt: 0,
      requestPayload: stage1Request,
      responsePayload: safeObject(stage1Result?.response_json),
    },
    {
      id: 'stage2_draft',
      stageResult: stage2Result,
      promptRecord: stage1Prompt || null,
      asset: stage2Asset || null,
      model: stage2Asset?.model_name || '',
      score: null,
      attempt: 0,
      requestPayload: safeObject(stage2Result?.request_json),
      responsePayload: safeObject(stage2Result?.response_json),
    },
    {
      id: 'stage3_critique',
      stageResult: stage3Result,
      promptRecord: stage3Prompt || null,
      requestPayload: {},
      responsePayload: stage3Analysis,
      asset: stage2Asset || null,
      model: safeText(safeObject(stage3Result?.request_json).critique_model_selected) || 'gpt-4o-mini',
      score: null,
      attempt,
    },
    {
      id: 'stage3_prompt_upgrade',
      stageResult: stage3Result,
      promptRecord: stage3Prompt || null,
      requestPayload: { upgrade_prompt_request: stage3UpgradeRequest },
      responsePayload: stage3Assistant,
      asset: null,
      model: promptEngineerLabel(stage3PromptEngineerMode, stage3ResponsesModel),
      score: null,
      attempt,
    },
    {
      id: 'stage3_generate',
      stageResult: stage3Result,
      promptRecord: stage3Prompt || null,
      requestPayload: { prompt: safeText(stage3Prompt?.prompt_text), model: stage3GenerationModel },
      responsePayload: stage3Generation,
      asset: stage3Asset || null,
      model: stage3Asset?.model_name || stage3GenerationModel,
      score: null,
      attempt,
    },
    {
      id: 'quality_gate',
      stageResult: qualityResult,
      promptRecord: stage3Prompt || null,
      requestPayload: safeObject(qualityResult?.request_json),
      responsePayload: safeObject(qualityResult?.response_json),
      asset: stage3Asset || null,
      model: safeText(safeObject(qualityResult?.request_json).quality_model_selected) || 'gpt-4o-mini',
      score: score || null,
      attempt,
    },
    {
      id: 'stage4_background',
      stageResult: stage4Result,
      promptRecord: stage3Prompt || null,
      requestPayload: safeObject(stage4Result?.request_json),
      responsePayload: safeObject(stage4Result?.response_json),
      asset: stage4Asset || null,
      model: stage4Asset?.model_name || '',
      score: score || null,
      attempt,
    },
    {
      id: 'completed',
      stageResult: null,
      promptRecord: null,
      requestPayload: {},
      responsePayload: {},
      asset: stage4Asset || stage3Asset || null,
      model: '',
      score: score || null,
      attempt,
    },
  ]

  const nodes = nodeData.map((item) => {
    const contract = stageDef(item.id) || stageDef('completed')
    const status = nodeStatus({
      stageId: item.id,
      stageResult: item.stageResult,
      run,
      attempt,
      score: item.score,
    })

    const aiInstruction = aiInstructionForStage({
      stageId: item.id,
      run,
      stage1Context,
      stage1Result,
      stage2Result,
      stage3Result,
      stage1Prompt,
      stage3Prompt,
      stage3UpgradeRequest,
    })

    return {
      id: item.id,
      label: contract.label,
      status,
      statusLabel: STATUS_LABELS[status] || 'Queued',
      subtitle: nodeSubtitle(item.id, run, item.score, attempt),
      provider: contract.provider,
      inputs: contract.inputs,
      expected: contract.expected,
      retryPolicy: contract.retryPolicy,
      attempt: item.attempt,
      stageStatus: safeText(item.stageResult?.status),
      stageCreatedAt: safeText(item.stageResult?.created_at),
      stageErrorDetail: safeText(item.stageResult?.error_detail),
      promptText: safeText(item.promptRecord?.prompt_text),
      promptSource: safeText(item.promptRecord?.source),
      promptNeedsPerson: safeText(item.promptRecord?.needs_person),
      promptCreatedAt: safeText(item.promptRecord?.created_at),
      promptRaw: safeObject(item.promptRecord?.raw_response_json),
      model: item.model,
      asset: item.asset,
      score: item.score,
      scoreRubric: safeObject(item.score?.rubric_json),
      aiInstruction: safeText(aiInstruction.text),
      aiInstructionSource: safeText(aiInstruction.source),
      requestJson: safeObject(item.requestPayload || item.stageResult?.request_json),
      responseJson: safeObject(item.responsePayload || item.stageResult?.response_json),
      requestKeys: Object.keys(safeObject(item.requestPayload || item.stageResult?.request_json)),
      responseKeys: Object.keys(safeObject(item.responsePayload || item.stageResult?.response_json)),
    }
  })

  return {
    nodes,
    edges: FLOW_EDGES,
    attemptSummaries: buildAttemptSummaries(detail, stageIndex, scoreIndex),
  }
}

export { FLOW_EDGES, STAGE_DEFINITIONS }
