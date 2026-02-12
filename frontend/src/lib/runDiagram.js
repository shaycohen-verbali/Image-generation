const STAGE_DEFINITIONS = [
  {
    id: 'stage1_prompt',
    label: 'Stage 1 Prompt',
    provider: 'OpenAI Assistant',
    inputs: ['word', 'part_of_sentence', 'category', 'context', 'boy_or_girl'],
    expected: ['first prompt', 'need a person'],
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
    expected: ['challenges', 'recommendations'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage3_prompt_upgrade',
    label: 'Stage 3.2 Prompt Upgrade',
    provider: 'OpenAI Assistant',
    inputs: ['old prompt', 'critique', 'previous score feedback'],
    expected: ['upgraded prompt'],
    retryPolicy: 'API retry + stage retry',
  },
  {
    id: 'stage3_generate',
    label: 'Stage 3.3 Image Generate',
    provider: 'Replicate: flux-pro / imagen fallback',
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
    provider: 'Replicate: nano-banana',
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
  { from: 'stage1_prompt', to: 'stage2_draft', label: 'prompt 1' },
  { from: 'stage2_draft', to: 'stage3_critique', label: 'start attempt A1' },
  { from: 'stage3_critique', to: 'stage3_prompt_upgrade', label: 'critique output' },
  { from: 'stage3_prompt_upgrade', to: 'stage3_generate', label: 'upgraded prompt' },
  { from: 'stage3_generate', to: 'quality_gate', label: 'score upgraded image' },
  { from: 'quality_gate', to: 'stage3_critique', label: 'fail + attempts remain', type: 'loop' },
  { from: 'quality_gate', to: 'stage4_background', label: 'pass' },
  { from: 'stage4_background', to: 'completed', label: 'final white-bg output' },
  { from: 'quality_gate', to: 'completed', label: 'fail + attempts exhausted', type: 'branch' },
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

function readRequestKeys(stageResult) {
  return Object.keys(safeObject(stageResult?.request_json))
}

function readResponseKeys(stageResult) {
  return Object.keys(safeObject(stageResult?.response_json))
}

function safeText(value) {
  if (value === undefined || value === null) return ''
  return String(value)
}

function buildAttemptSummaries(detail, stageIndex, scoreIndex) {
  return getAvailableAttempts(detail).map((attempt) => {
    const stage3 = stageIndex.get(makeKey('stage3_upgrade', attempt))
    const quality = stageIndex.get(makeKey('quality_gate', attempt))
    const stage4 = stageIndex.get(makeKey('stage4_background', attempt))
    const score = scoreIndex.get(attempt)
    return {
      attempt,
      stage3Status: asStageStatus(stage3?.status),
      qualityStatus: asStageStatus(quality?.status),
      stage4Status: asStageStatus(stage4?.status),
      score: score?.score_0_100 ?? null,
      passFail: typeof score?.pass_fail === 'boolean' ? score.pass_fail : null,
    }
  })
}

function nodeStatus({
  stageId,
  stageResult,
  run,
  attempt,
  score,
}) {
  const currentAttempt = Math.max(1, Number(run.optimization_attempt || 1))
  const isCurrentBase = run.status === 'running' && run.current_stage === stageId
  const isCurrentAttemptStage =
    run.status === 'running' &&
    run.current_stage === stageId &&
    attempt === currentAttempt

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
  const stage3Assistant = safeObject(stage3Response.assistant)
  const stage3Generation = safeObject(stage3Response.generation)
  const stage3UpgradeRequest = safeObject(stage3Request.upgrade_prompt_request)
  const stage3GenerationModel = safeText(stage3Response.generation_model)

  const nodeData = [
    {
      id: 'stage1_prompt',
      stageResult: stage1Result,
      promptRecord: stage1Prompt || null,
      asset: null,
      model: '',
      score: null,
      attempt: 0,
    },
    {
      id: 'stage2_draft',
      stageResult: stage2Result,
      promptRecord: stage1Prompt || null,
      asset: stage2Asset || null,
      model: stage2Asset?.model_name || '',
      score: null,
      attempt: 0,
    },
    {
      id: 'stage3_critique',
      stageResult: stage3Result,
      promptRecord: stage3Prompt || null,
      requestPayload: stage3UpgradeRequest,
      responsePayload: stage3Analysis,
      asset: stage2Asset || null,
      model: 'gpt-4o-mini',
      score: null,
      attempt,
    },
    {
      id: 'stage3_prompt_upgrade',
      stageResult: stage3Result,
      promptRecord: stage3Prompt || null,
      requestPayload: stage3UpgradeRequest,
      responsePayload: stage3Assistant,
      asset: null,
      model: 'OpenAI Assistant',
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
      asset: stage3Asset || null,
      model: 'gpt-4o-mini',
      score: score || null,
      attempt,
    },
    {
      id: 'stage4_background',
      stageResult: stage4Result,
      promptRecord: stage3Prompt || null,
      asset: stage4Asset || null,
      model: stage4Asset?.model_name || '',
      score: score || null,
      attempt,
    },
    {
      id: 'completed',
      stageResult: null,
      promptRecord: null,
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
