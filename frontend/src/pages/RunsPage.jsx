import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildApiUrl,
  buildAssetContentUrl,
  cancelCsvJob,
  clearTerminalCsvJobs,
  clearTerminalRuns,
  continueCsvJob,
  deleteRun,
  exportCsvJob,
  getConfig,
  getCsvJobMetadata,
  getCsvJobItems,
  getCsvJobItemDetail,
  getCsvJobSummary,
  getRun,
  listCsvJobs,
  listRuns,
  retryCsvJobFailures,
  retryRun,
  startCsvJob,
  stopRun,
  updateConfig,
} from '../lib/api'
import PageErrorBoundary from '../components/PageErrorBoundary'
import RunExecutionDiagram from '../components/RunExecutionDiagram'
import DeferredAssetImage from '../components/DeferredAssetImage'

const SELECTED_RUN_STORAGE_KEY = 'aac:selectedRunId'
const RUNS_POLL_MS = 30000
const DETAIL_POLL_RUNNING_MS = 12000
const DETAIL_POLL_WAITING_MS = 20000
const CSV_LIST_POLL_MS = 15000
const CSV_LIST_POLL_FAST_MS = 5000
const CSV_DETAIL_POLL_MS = 15000
const CSV_DETAIL_POLL_FAST_MS = 5000

function isTerminalRunStatus(status) {
  const value = String(status || '').toLowerCase()
  return ['completed_pass', 'completed_fail_threshold', 'failed_technical', 'canceled'].includes(value)
}

function canDeleteRunStatus(status) {
  const value = String(status || '').toLowerCase()
  return ['completed_pass', 'completed_fail_threshold', 'failed_technical', 'canceled', 'cancel_requested'].includes(value)
}

function isWaitingRunStatus(status) {
  const value = String(status || '').toLowerCase()
  return ['queued', 'retry_queued'].includes(value)
}

function canStopRun(status) {
  const value = String(status || '').toLowerCase()
  return ['queued', 'retry_queued', 'running', 'cancel_requested'].includes(value)
}

function isTerminalCsvJobStatus(status) {
  const value = String(status || '').toLowerCase()
  return ['completed', 'failed', 'partial_failed', 'canceled'].includes(value)
}

function csvItemTaskSummary(tasks, itemId) {
  const relevant = (Array.isArray(tasks) ? tasks : []).filter((task) => task.csv_job_item_id === itemId)
  const counts = { pending: 0, queued: 0, running: 0, completed: 0, failed: 0, canceled: 0 }
  relevant.forEach((task) => {
    const key = String(task.status || '').toLowerCase()
    if (Object.prototype.hasOwnProperty.call(counts, key)) {
      counts[key] += 1
    }
  })
  return counts
}

const CSV_STEP_LABELS = {
  step1_base: 'Base images',
  step2_male_age: 'Male age variant',
  step3_female_white: 'Female white variant',
  step4_race_variant: 'Race variant',
}

function csvStepLabel(stepName) {
  return CSV_STEP_LABELS[String(stepName || '').trim()] || String(stepName || 'Unknown step')
}

const CSV_BASE_STAGE_PROGRESS = {
  stage1_prompt: {
    currentStep: 'Prompt generation',
    subStatus: 'Creating the AAC image prompt',
  },
  stage2_draft: {
    currentStep: 'Draft image',
    subStatus: 'Creating the first draft image with Replicate',
  },
  stage3_upgrade: {
    currentStep: 'Image optimization',
    subStatus: 'Improving the image for AAC clarity',
  },
  quality_gate: {
    currentStep: 'Quality check',
    subStatus: 'Checking image quality and concept clarity',
  },
  stage3_post_quality_accessibility_critique: {
    currentStep: 'AAC accessibility review',
    subStatus: 'Checking whether the winning image needs a clarity adjustment',
  },
  stage3_post_quality_accessibility_generate: {
    currentStep: 'AAC clarity adjustment',
    subStatus: 'Applying the final AAC clarity adjustment',
  },
  stage4_background: {
    currentStep: 'White-background image',
    subStatus: 'Creating the white-background version',
  },
  completed_base_assets: {
    currentStep: 'Base images ready',
    subStatus: 'The quality and white-background base images are ready',
  },
}

function csvItemLiveProgress(item) {
  const fallback = {
    currentStep: String(item?.current_step || ''),
    subStatus: String(item?.sub_status || ''),
    isBaseSubstage: false,
  }
  if (!item || String(item.current_step || '') !== CSV_STEP_LABELS.step1_base) return fallback

  const shadowStage = String(item.shadow_run_current_stage || '').trim()
  const stageProgress = CSV_BASE_STAGE_PROGRESS[shadowStage]
  if (!stageProgress) return fallback

  const attempt = Number(item.optimization_attempt || 0)
  const attemptSuffix = attempt > 0 && ['stage3_upgrade', 'quality_gate'].includes(shadowStage)
    ? ` · attempt ${attempt}`
    : ''
  const scoreSuffix = shadowStage === 'stage3_upgrade' && item.quality_score !== undefined && item.quality_score !== null
    ? ` after a ${formatQualityScore(item.quality_score)}/100 quality score`
    : ''

  return {
    currentStep: `${stageProgress.currentStep}${attemptSuffix}`,
    subStatus: `${stageProgress.subStatus}${scoreSuffix}`,
    isBaseSubstage: true,
  }
}

function csvIsVariantJob(job) {
  return Boolean(String(job?.continued_from_job_id || '').trim())
}

function csvHasFollowUpJob(jobs, jobId) {
  if (!jobId) return false
  return (Array.isArray(jobs) ? jobs : []).some((job) => String(job?.continued_from_job_id || '').trim() === String(jobId))
}

function csvJobMainStatus(jobOrStatus) {
  if (jobOrStatus && typeof jobOrStatus === 'object') {
    const displayStatus = String(jobOrStatus.display_status || '').trim()
    const displaySubStatus = String(jobOrStatus.display_sub_status || '').trim()
    if (displayStatus || displaySubStatus) {
      return {
        main: displayStatus || 'running',
        sub: displaySubStatus || 'Work is in progress',
      }
    }
  }
  const value = String(
    jobOrStatus && typeof jobOrStatus === 'object'
      ? jobOrStatus.status
      : jobOrStatus,
  ).toLowerCase()
  if (value === 'completed') return { main: 'completed', sub: 'All rows finished' }
  if (value === 'partial_failed') return { main: 'failure', sub: 'Some rows failed and some completed' }
  if (value === 'failed') return { main: 'failure', sub: 'One or more rows failed' }
  if (value === 'canceled') return { main: 'failure', sub: 'Canceled' }
  if (value === 'cancel_requested') return { main: 'running', sub: 'Stopping after active work finishes' }
  if (value === 'imported') return { main: 'pending', sub: 'Imported and not started yet' }
  if (['queued', 'retry_queued'].includes(value)) return { main: 'running', sub: 'Queued under load' }
  return { main: 'running', sub: 'Work is in progress' }
}

function csvPrettyStatus(status) {
  const value = String(status || '').trim()
  if (!value) return '-'
  if (value === 'failure') return 'Failure'
  if (value === 'partial_failed') return 'Partially failed'
  const normalized = value.replaceAll('_', ' ')
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function formatQualityScore(score) {
  if (score === undefined || score === null || score === '') return '-'
  const numeric = Number(score)
  if (Number.isNaN(numeric)) return '-'
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1)
}

function formatUsd(value) {
  if (value === undefined || value === null || value === '') return '-'
  const numeric = Number(value)
  if (Number.isNaN(numeric)) return '-'
  return `$${numeric.toFixed(4)}`
}

function normalizeProviderBreakdown(breakdown) {
  const source = breakdown && typeof breakdown === 'object' ? breakdown : {}
  return {
    google: Number(source.google || 0),
    replicate: Number(source.replicate || 0),
    openai: Number(source.openai || 0),
  }
}

function personAttentionLabel(needsAttention) {
  return needsAttention ? 'Needs review' : 'No'
}

function csvProfileSummary(profileKey) {
  const [gender, age, skinColor] = String(profileKey || '').split(':')
  return [age, gender, skinColor].filter(Boolean).join(' ')
}

function normalizeProfileOptionLabel(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function csvProfileDisplay(profileKey) {
  const [gender, age, skinColor] = String(profileKey || '').split(':')
  return [skinColor, age, gender].filter(Boolean).map(normalizeProfileOptionLabel).join(' + ')
}

function formatLocalDateTime(value) {
  if (!value) return '-'
  const raw = String(value).trim()
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : `${raw}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function elapsedSeconds(startedAt, finishedAt) {
  if (!startedAt) return 0
  const start = new Date(startedAt).getTime()
  if (Number.isNaN(start)) return 0
  if (!finishedAt) return 0
  const end = new Date(finishedAt).getTime()
  if (Number.isNaN(end)) return 0
  return Math.max(0, Math.round((end - start) / 1000))
}

function csvJobDurationLabel(job) {
  if (!job?.started_at || !job?.finished_at) {
    return '-'
  }
  return `${elapsedSeconds(job.started_at, job.finished_at)}s`
}

function csvTaskProgressSummary(tasks, itemId) {
  const relevant = (Array.isArray(tasks) ? tasks : [])
    .filter((task) => task.csv_job_item_id === itemId)
    .sort((left, right) => String(left.created_at || '').localeCompare(String(right.created_at || '')))
  const counts = csvItemTaskSummary(relevant, itemId)
  const total = relevant.length
  const completed = counts.completed
  const runningTask = relevant.find((task) => String(task.status || '').toLowerCase() === 'running')
  const waitingTask = relevant.find((task) => ['queued', 'pending'].includes(String(task.status || '').toLowerCase()))
  const failedTask = relevant.find((task) => String(task.status || '').toLowerCase() === 'failed')
  const allCanceled = total > 0 && relevant.every((task) => String(task.status || '').toLowerCase() === 'canceled')

  let mainStatus = 'pending'
  let subStatus = 'Waiting to be picked up'
  let currentStep = waitingTask ? csvStepLabel(waitingTask.step_name) : ''

  if (failedTask || allCanceled) {
    mainStatus = 'failure'
    subStatus = allCanceled ? 'Canceled' : failedTask?.error_summary || `${csvStepLabel(failedTask?.step_name)} failed`
    currentStep = failedTask ? csvStepLabel(failedTask.step_name) : currentStep
  } else if (total > 0 && completed === total) {
    mainStatus = 'completed'
    subStatus = 'All requested images are ready'
    currentStep = ''
  } else if (runningTask) {
    mainStatus = 'running'
    subStatus = `Creating ${csvStepLabel(runningTask.step_name)}`
    currentStep = csvStepLabel(runningTask.step_name)
  } else if (completed > 0) {
    mainStatus = 'running'
    subStatus = waitingTask ? `Waiting for ${csvStepLabel(waitingTask.step_name)}` : 'Preparing next step'
    currentStep = waitingTask ? csvStepLabel(waitingTask.step_name) : ''
  }

  return {
    counts,
    total,
    completed,
    failed: counts.failed,
    canceled: counts.canceled,
    waiting: counts.queued + counts.pending,
    mainStatus,
    subStatus,
    currentStep,
  }
}

function csvJobWordSummary(items, tasks) {
  const counts = { pending: 0, running: 0, completed: 0, failure: 0, previously_done: 0 }
  ;(Array.isArray(items) ? items : []).forEach((item) => {
    const backendStatus = String(item?.main_status || '').toLowerCase()
    const state = backendStatus || csvTaskProgressSummary(tasks, item.id).mainStatus
    if (Object.prototype.hasOwnProperty.call(counts, state)) counts[state] += 1
  })
  return counts
}

function csvItemImages(item, tasks, { includeBaseOutputs = true } = {}) {
  const images = []
  const seen = new Set()
  const addImage = (payload) => {
    const key = `${payload.kind}:${payload.id || payload.label}`
    if ((!payload.id && !payload.missing) || seen.has(key)) return
    seen.add(key)
    images.push(payload)
  }
  if (includeBaseOutputs) {
    addImage({
      id: item?.base_regular_asset_id || '',
      label: 'Quality image',
      kind: 'quality',
      missing: !item?.base_regular_asset_id,
    })
    addImage({
      id: item?.base_soften_asset_id || '',
      label: 'Soften image',
      kind: 'soften',
      missing: !item?.base_soften_asset_id,
    })
    addImage({
      id: item?.base_white_bg_asset_id || '',
      label: 'White background image',
      kind: 'white_bg',
      missing: !item?.base_white_bg_asset_id,
    })
  }
  ;(Array.isArray(tasks) ? tasks : []).forEach((task) => {
    const profile = csvProfileSummary(task.profile_key)
    const baseLabel = `${profile || csvStepLabel(task.step_name)}`
    if (task.regular_asset_id) {
      addImage({
        id: task.regular_asset_id,
        label: `${baseLabel} regular`,
        kind: 'regular',
        stepLabel: csvStepLabel(task.step_name),
      })
    }
    if (task.white_bg_asset_id) {
      addImage({
        id: task.white_bg_asset_id,
        label: `${baseLabel} white background`,
        kind: 'white_bg',
        stepLabel: csvStepLabel(task.step_name),
      })
    }
  })
  return images
}

function csvAvailableProfiles(item) {
  return Array.isArray(item?.available_profiles) ? item.available_profiles : []
}

function csvItemProfileColumnText(item) {
  if (item?.current_profile_key) {
    return csvProfileDisplay(item.current_profile_key)
  }
  const available = csvAvailableProfiles(item)
  if (available.length) {
    return available.map((profile) => csvProfileDisplay(profile.profile_key)).join(', ')
  }
  if (Array.isArray(item?.requested_profile_keys) && item.requested_profile_keys.length) {
    return item.requested_profile_keys.map(csvProfileDisplay).join(', ')
  }
  return '-'
}

function csvCombinedImages(item, tasks, options = {}) {
  const images = csvItemImages(item, tasks, options)
  const seen = new Set(images.map((image) => `${image.id}:${image.kind}`))
  csvAvailableProfiles(item).forEach((profile) => {
    if (profile.regular_asset_id) {
      const key = `${profile.regular_asset_id}:regular`
      if (!seen.has(key)) {
        seen.add(key)
        images.push({
          id: profile.regular_asset_id,
          label: `${csvProfileDisplay(profile.profile_key)} regular`,
          kind: 'regular',
        })
      }
    }
    if (profile.white_bg_asset_id) {
      const key = `${profile.white_bg_asset_id}:white_bg`
      if (!seen.has(key)) {
        seen.add(key)
        images.push({
          id: profile.white_bg_asset_id,
          label: `${csvProfileDisplay(profile.profile_key)} white background`,
          kind: 'white_bg',
        })
      }
    }
  })
  return images
}

function csvInventoryImages(item) {
  const images = []
  const seen = new Set()
  csvAvailableProfiles(item).forEach((profile) => {
    if (profile.regular_asset_id) {
      const key = `${profile.regular_asset_id}:regular`
      if (!seen.has(key)) {
        seen.add(key)
        images.push({
          id: profile.regular_asset_id,
          label: `${csvProfileDisplay(profile.profile_key)} regular`,
          kind: 'regular',
        })
      }
    }
    if (profile.white_bg_asset_id) {
      const key = `${profile.white_bg_asset_id}:white_bg`
      if (!seen.has(key)) {
        seen.add(key)
        images.push({
          id: profile.white_bg_asset_id,
          label: `${csvProfileDisplay(profile.profile_key)} white background`,
          kind: 'white_bg',
        })
      }
    }
  })
  return images
}

function csvTaskDiagnostics(tasks, selectedId) {
  const relevant = (Array.isArray(tasks) ? tasks : []).filter((task) => task.csv_job_item_id === selectedId)
  const taskById = new Map(relevant.map((task) => [task.id, task]))
  return relevant.map((task) => {
    const waitingOn = (Array.isArray(task.dependency_task_ids) ? task.dependency_task_ids : [])
      .map((id) => taskById.get(id))
      .filter(Boolean)
    const blocking = waitingOn.find((dep) => ['failed', 'canceled'].includes(String(dep.status || '').toLowerCase()))
    return {
      ...task,
      stepLabel: csvStepLabel(task.step_name),
      profileLabel: csvProfileSummary(task.profile_key),
      waitingOnLabel:
        blocking
          ? `${csvStepLabel(blocking.step_name)} ${csvPrettyStatus(blocking.status)}`
          : waitingOn.length
            ? waitingOn.map((dep) => csvStepLabel(dep.step_name)).join(', ')
            : '',
    }
  })
}

function csvAssetAvailabilityLabel(assetId) {
  return assetId ? 'Created' : 'Not created'
}

function shouldPollRuns(runs) {
  return (Array.isArray(runs) ? runs : []).some((run) => !isTerminalRunStatus(run?.status))
}

function getStoredRunId() {
  try {
    if (typeof window === 'undefined') return ''
    return window.sessionStorage.getItem(SELECTED_RUN_STORAGE_KEY) || ''
  } catch (_error) {
    return ''
  }
}

function setStoredRunId(runId) {
  try {
    if (typeof window === 'undefined') return
    if (runId) {
      window.sessionStorage.setItem(SELECTED_RUN_STORAGE_KEY, runId)
    } else {
      window.sessionStorage.removeItem(SELECTED_RUN_STORAGE_KEY)
    }
  } catch (_error) {
    // Ignore storage failures and keep the page usable.
  }
}

function dedupeById(items) {
  const map = new Map()
  ;(Array.isArray(items) ? items : []).forEach((item) => {
    if (!item || !item.id) return
    map.set(item.id, item)
  })
  return Array.from(map.values())
}

function dedupeCostRows(items) {
  const map = new Map()
  ;(Array.isArray(items) ? items : []).forEach((item) => {
    if (!item) return
    const key = `${item.stage_name || 'stage'}:${item.attempt || 0}:${item.model || ''}:${item.estimate_basis || ''}`
    map.set(key, item)
  })
  return Array.from(map.values())
}

function mergeRunDetail(previous, next) {
  if (!next) return previous
  if (!previous) return next
  if (!previous.run || !next.run || previous.run.id !== next.run.id) return next
  return {
    ...previous,
    ...next,
    run: { ...previous.run, ...next.run },
    stages: dedupeById([...(previous.stages || []), ...(next.stages || [])]),
    prompts: dedupeById([...(previous.prompts || []), ...(next.prompts || [])]),
    assets: dedupeById([...(previous.assets || []), ...(next.assets || [])]),
    scores: dedupeById([...(previous.scores || []), ...(next.scores || [])]),
    cost_summary: {
      ...(previous.cost_summary || {}),
      ...(next.cost_summary || {}),
      stage_costs: dedupeCostRows([
        ...((previous.cost_summary || {}).stage_costs || []),
        ...((next.cost_summary || {}).stage_costs || []),
      ]),
    },
  }
}

function stageTitle(stageName) {
  if (stageName === 'stage1_prompt') return 'Stage 1 Prompt'
  if (stageName === 'stage3_upgrade') return 'Stage 3 Prompt Upgrade'
  if (stageName === 'stage3_accessibility_critique') return 'Stage 3.16 Simplicity Critique (disabled)'
  if (stageName === 'stage3_post_quality_accessibility_critique') return 'Post-quality AAC Critique'
  if (stageName === 'stage3_post_quality_accessibility_generate') return 'Post-quality AAC Soften Image'
  if (stageName === 'stage2_draft') return 'Stage 2 Draft'
  if (stageName === 'stage3_upgraded') return 'Stage 3 Upgraded'
  if (stageName === 'stage4_white_bg') return 'Stage 4 White Background'
  if (stageName === 'stage4_background') return 'Stage 4 White Background Prompt'
  if (stageName === 'stage4_variant_correction') return 'Step 8.2 Variant Correction Prompt'
  if (stageName === 'stage4_variant_generate') return 'Character Variant Final'
  if (stageName === 'stage5_variant_white_bg') return 'Character Variant White Background'
  return stageName
}

const stagePriority = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage3_post_quality_accessibility_generate: 3,
  stage4_white_bg: 4,
  stage4_variant_generate: 5,
  stage5_variant_white_bg: 6,
}

const CSV_GENDER_OPTIONS = ['male', 'female']
const CSV_AGE_OPTIONS = ['toddler', 'kid', 'tween', 'teenager']
const CSV_SKIN_OPTIONS = ['white', 'black', 'asian', 'brown']

export default function RunsPage() {
  const algoDiagramEnabled = import.meta.env.VITE_ALGO_DIAGRAM_ENABLED !== 'false'
  const [filters, setFilters] = useState({ status: '', word: '', part_of_sentence: '', category: '' })
  const [runs, setRuns] = useState([])
  const [message, setMessage] = useState('')
  const [selectedRunId, setSelectedRunId] = useState(() => getStoredRunId())
  const [detail, setDetail] = useState(null)
  const [assistantName, setAssistantName] = useState('')
  const [promptEngineerMode, setPromptEngineerMode] = useState('responses_api')
  const [responsesPromptEngineerModel, setResponsesPromptEngineerModel] = useState('gpt-5.4')
  const [responsesVectorStoreId, setResponsesVectorStoreId] = useState('vs_683f3d36223481919f59fc5623286253')
  const [visualStyleId, setVisualStyleId] = useState('warm_watercolor_storybook_kids_v3')
  const [visualStyleName, setVisualStyleName] = useState('Warm Watercolor Storybook Kids Style v3')
  const [visualStylePromptBlock, setVisualStylePromptBlock] = useState('')
  const [stage1PromptTemplate, setStage1PromptTemplate] = useState('')
  const [stage3PromptTemplate, setStage3PromptTemplate] = useState('')
  const [imageAspectRatio, setImageAspectRatio] = useState('4:3')
  const [imageResolution, setImageResolution] = useState('1K')
  const [selectedDetailTab, setSelectedDetailTab] = useState('overview')
  const [csvJobs, setCsvJobs] = useState([])
  const [selectedCsvJobId, setSelectedCsvJobId] = useState('')
  const [csvJobOverview, setCsvJobOverview] = useState(null)
  const [csvItemsPage, setCsvItemsPage] = useState({ items: [], tasks: [], total: 0, offset: 0, limit: 50 })
  const [csvItemDetail, setCsvItemDetail] = useState(null)
  const [selectedCsvItemId, setSelectedCsvItemId] = useState('')
  const [csvShadowRunDetail, setCsvShadowRunDetail] = useState(null)
  const [selectedCsvStatusFilter, setSelectedCsvStatusFilter] = useState('')
  const [showCsvScoreHistory, setShowCsvScoreHistory] = useState(false)
  const [showCsvPromptHistory, setShowCsvPromptHistory] = useState(false)
  const [continuingCsvJobId, setContinuingCsvJobId] = useState('')
  const [continueSelections, setContinueSelections] = useState({
    person_gender_options: [],
    person_age_options: [],
    person_skin_color_options: [],
    override_existing_variants: false,
  })
  const [pageVisible, setPageVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })
  const selectedRunIdRef = useRef('')
  const runsRef = useRef([])
  const detailStateRef = useRef(null)
  const detailRef = useRef(null)
  const runsRequestInFlightRef = useRef(false)
  const runDetailRequestInFlightRef = useRef(false)
  const selectedCsvJobIdRef = useRef('')
  const csvListRequestInFlightRef = useRef(false)
  const csvOverviewRequestInFlightRef = useRef(false)
  const csvSummaryRequestInFlightRef = useRef(false)
  const csvItemsRequestInFlightRef = useRef(false)
  const csvItemDetailRequestInFlightRef = useRef(false)

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
    setStoredRunId(selectedRunId)
  }, [selectedRunId])

  useEffect(() => {
    selectedCsvJobIdRef.current = selectedCsvJobId
  }, [selectedCsvJobId])

  useEffect(() => {
    setContinueSelections({
      person_gender_options: [],
      person_age_options: [],
      person_skin_color_options: [],
      override_existing_variants: false,
    })
  }, [selectedCsvJobId])

  useEffect(() => {
    runsRef.current = runs
  }, [runs])

  useEffect(() => {
    detailStateRef.current = detail
  }, [detail])

  useEffect(() => {
    if (typeof document === 'undefined') return undefined
    const handleVisibilityChange = () => {
      setPageVisible(document.visibilityState !== 'hidden')
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  const query = useMemo(() => {
    const next = {}
    if (filters.status) next.status = filters.status
    return next
  }, [filters.status])

  const filteredRuns = useMemo(() => {
    return runs.filter((run) => {
      if (filters.word && !String(run.word || '').toLowerCase().includes(filters.word.toLowerCase())) return false
      if (
        filters.part_of_sentence &&
        !String(run.part_of_sentence || '').toLowerCase().includes(filters.part_of_sentence.toLowerCase())
      ) return false
      if (filters.category && !String(run.category || '').toLowerCase().includes(filters.category.toLowerCase())) return false
      return true
    })
  }, [runs, filters.word, filters.part_of_sentence, filters.category])
  const showingCsvWords = Boolean(selectedCsvJobId && csvJobOverview)

  const sortedAssets = detail?.assets
    ? [...detail.assets].sort((left, right) => {
        const leftOrder = stagePriority[left.stage_name] || 99
        const rightOrder = stagePriority[right.stage_name] || 99
        if (leftOrder !== rightOrder) return leftOrder - rightOrder
        return (left.attempt || 0) - (right.attempt || 0)
      })
    : []

  const finalAsset = sortedAssets.reduce((latest, asset) => {
    if (asset.stage_name !== 'stage4_white_bg') return latest
    if (!latest) return asset
    return (asset.attempt || 0) >= (latest.attempt || 0) ? asset : latest
  }, null)

  const csvJobPollKey = useMemo(
    () => csvJobs.map((job) => `${job.id}:${job.status}:${job.updated_at || ''}`).join('|'),
    [csvJobs]
  )
  const csvJobItems = Array.isArray(csvItemsPage?.items) ? csvItemsPage.items : []
  const csvJobTasks = Array.isArray(csvItemsPage?.tasks) ? csvItemsPage.tasks : []
  const requestedProfileHistory = Array.isArray(csvJobOverview?.requested_profile_history)
    ? csvJobOverview.requested_profile_history
    : []
  const currentRequestedProfiles = Array.isArray(csvJobOverview?.job?.requested_profiles)
    ? csvJobOverview.job.requested_profiles
    : []
  const visibleCsvJobs = useMemo(() => {
    if (!Array.isArray(csvJobs) || csvJobs.length === 0) return []
    if (selectedCsvJobId) {
      const selected = csvJobs.find((job) => job.id === selectedCsvJobId)
      if (selected) {
        if (csvJobOverview?.job?.id === selected.id) {
          return [{ ...selected, ...csvJobOverview.job }]
        }
        return [selected]
      }
    }
    if (csvJobOverview?.job?.id && (!csvJobs.length || csvJobs[0]?.id === csvJobOverview.job.id)) {
      return [{ ...(csvJobs[0] || {}), ...csvJobOverview.job }]
    }
    return [csvJobs[0]]
  }, [csvJobs, selectedCsvJobId, csvJobOverview])
  const csvJobLiveCounts = useMemo(
    () => csvJobOverview?.word_counts || csvJobWordSummary(csvJobItems, csvJobTasks),
    [csvJobOverview?.word_counts, csvJobItems, csvJobTasks],
  )
  const filteredCsvJobItems = useMemo(() => {
    if (!selectedCsvStatusFilter) return csvJobItems
    return csvJobItems.filter((item) => String(item.main_status || '').toLowerCase() === selectedCsvStatusFilter)
  }, [csvJobItems, selectedCsvStatusFilter])
  const selectedCsvListItem = useMemo(
    () => filteredCsvJobItems.find((item) => item.id === selectedCsvItemId) || filteredCsvJobItems[0] || null,
    [filteredCsvJobItems, selectedCsvItemId]
  )
  const hasMatchingCsvItemDetail = Boolean(
    csvItemDetail?.item?.id && selectedCsvListItem?.id && csvItemDetail.item.id === selectedCsvListItem.id,
  )
  const selectedCsvItem = hasMatchingCsvItemDetail
    ? { ...selectedCsvListItem, ...csvItemDetail.item }
    : selectedCsvListItem
  const selectedCsvItemTasks = useMemo(
    () => hasMatchingCsvItemDetail
      ? (csvItemDetail.tasks || [])
      : csvJobTasks.filter((task) => task.csv_job_item_id === selectedCsvItem?.id),
    [hasMatchingCsvItemDetail, csvItemDetail, csvJobTasks, selectedCsvItem?.id]
  )
  const csvScoreHistory = useMemo(
    () => [...(Array.isArray(csvShadowRunDetail?.scores) ? csvShadowRunDetail.scores : [])].sort((left, right) => (left.attempt || 0) - (right.attempt || 0)),
    [csvShadowRunDetail]
  )
  const csvPromptHistory = useMemo(
    () => [...(Array.isArray(csvShadowRunDetail?.prompts) ? csvShadowRunDetail.prompts : [])]
      .filter((prompt) => ['stage1_prompt', 'stage3_upgrade', 'stage4_background', 'stage4_variant_correction'].includes(String(prompt.stage_name || '')))
      .sort((left, right) => {
        const leftAttempt = Number(left.attempt || 0)
        const rightAttempt = Number(right.attempt || 0)
        if (leftAttempt !== rightAttempt) return leftAttempt - rightAttempt
        return String(left.stage_name || '').localeCompare(String(right.stage_name || ''))
      }),
    [csvShadowRunDetail]
  )
  const csvJobProviderBreakdown = useMemo(
    () => normalizeProviderBreakdown(csvJobOverview?.provider_breakdown),
    [csvJobOverview]
  )
  const csvJobHasProviderCost = csvJobOverview?.estimated_total_cost_usd != null
  const csvSelectedItemProviderBreakdown = useMemo(
    () => normalizeProviderBreakdown(csvShadowRunDetail?.cost_summary?.provider_breakdown || selectedCsvItem?.provider_breakdown),
    [csvShadowRunDetail, selectedCsvItem]
  )
  const csvSelectedItemHasProviderCost =
    selectedCsvItem?.estimated_total_cost_usd != null || csvShadowRunDetail?.cost_summary?.estimated_total_cost_usd != null
  const selectedCsvItemProgress = selectedCsvItem || null
  const selectedCsvItemLiveProgress = useMemo(
    () => csvItemLiveProgress(selectedCsvItem),
    [selectedCsvItem],
  )
  const selectedCsvJob = csvJobOverview?.job || csvJobs.find((job) => job.id === selectedCsvJobId) || null
  const showBaseCsvOutputs = !csvIsVariantJob(selectedCsvJob)
  const selectedCsvItemImages = useMemo(
    () => {
      if (showBaseCsvOutputs) {
        return csvItemImages(selectedCsvItem, selectedCsvItemTasks, { includeBaseOutputs: true })
      }
      return csvCombinedImages(selectedCsvItem, selectedCsvItemTasks, { includeBaseOutputs: false })
    },
    [selectedCsvItem, selectedCsvItemTasks, showBaseCsvOutputs]
  )
  const selectedCsvTaskDiagnostics = useMemo(
    () => csvTaskDiagnostics(csvJobTasks, selectedCsvItem?.id),
    [csvJobTasks, selectedCsvItem?.id]
  )
  const canContinueSelectedCsvJob = Boolean(
    selectedCsvJobId &&
    csvJobOverview?.job &&
    isTerminalCsvJobStatus(csvJobOverview.job.status)
  )
  const selectedCsvJobAlreadyContinued = csvHasFollowUpJob(csvJobs, selectedCsvJobId)
  const shouldFastPollCsv =
    Boolean(selectedCsvJobId) &&
    !!selectedCsvJob &&
    ['imported', 'queued', 'retry_queued', 'pending'].includes(String(selectedCsvJob.status || '').toLowerCase())

  async function loadRunDetail(runId, { isPolling = false, includeDebug = false } = {}) {
    if (!runId) return
    if (runDetailRequestInFlightRef.current) return
    runDetailRequestInFlightRef.current = true
    try {
      const data = await getRun(runId, { includeDebug })
      if (selectedRunIdRef.current && selectedRunIdRef.current !== runId) {
        return
      }
      setDetail((previous) => mergeRunDetail(previous, data))
    } catch (error) {
      if (isPolling && detailStateRef.current?.run?.id === runId) {
        return
      }
      setMessage(`Error loading detail: ${error.message}`)
    } finally {
      runDetailRequestInFlightRef.current = false
    }
  }

  function scrollToDetail() {
    window.requestAnimationFrame(() => {
      detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function selectRun(runId, options = {}) {
    if (detailStateRef.current?.run?.id !== runId) {
      setDetail(null)
    }
    setSelectedRunId(runId)
    selectedRunIdRef.current = runId
    if (options.scrollToDetail) {
      scrollToDetail()
    }
  }

  async function refreshRuns({ isPolling = false } = {}) {
    if (runsRequestInFlightRef.current) return
    runsRequestInFlightRef.current = true
    try {
      const data = await listRuns(query)
      setRuns(data)

      const activeRunId = selectedRunIdRef.current
      if (!activeRunId && data.length > 0) {
        setSelectedRunId(data[0].id)
        selectedRunIdRef.current = data[0].id
        setStoredRunId(data[0].id)
      } else if (activeRunId) {
        const exists = data.some((run) => run.id === activeRunId)
        if (!exists && data.length > 0) {
          setSelectedRunId(data[0].id)
          selectedRunIdRef.current = data[0].id
          setStoredRunId(data[0].id)
        } else if (!exists) {
          setSelectedRunId('')
          selectedRunIdRef.current = ''
          setStoredRunId('')
          setDetail(null)
        }
      }
    } catch (error) {
      if (selectedCsvJobIdRef.current) {
        return
      }
      if (isPolling && (runsRef.current.length > 0 || detailStateRef.current?.run)) {
        return
      }
      setMessage(`Error loading runs: ${error.message}`)
    } finally {
      runsRequestInFlightRef.current = false
    }
  }

  async function refreshCsvJobs({ isPolling = false } = {}) {
    if (csvListRequestInFlightRef.current) return
    csvListRequestInFlightRef.current = true
    try {
      const data = await listCsvJobs()
      setCsvJobs(data)
      const activeCsvJobId = selectedCsvJobIdRef.current
      if (!activeCsvJobId && data.length > 0) {
        selectedCsvJobIdRef.current = data[0].id
        setSelectedCsvJobId(data[0].id)
      } else if (activeCsvJobId && !data.some((job) => job.id === activeCsvJobId)) {
        selectedCsvJobIdRef.current = data[0]?.id || ''
        setSelectedCsvJobId(data[0]?.id || '')
        setCsvJobOverview(null)
      }
    } catch (error) {
      if (!isPolling) {
        setMessage(`Error loading CSV jobs: ${error.message}`)
      }
    } finally {
      csvListRequestInFlightRef.current = false
    }
  }

  async function loadCsvJobDetail(jobId, { isPolling = false } = {}) {
    if (!jobId) return
    if (csvOverviewRequestInFlightRef.current) return
    csvOverviewRequestInFlightRef.current = true
    try {
      const data = await getCsvJobMetadata(jobId)
      if (selectedCsvJobIdRef.current && selectedCsvJobIdRef.current !== jobId) {
        return
      }
      setCsvJobOverview(data)
    } catch (error) {
      if (!isPolling) {
        setMessage(`Error loading CSV job detail: ${error.message}`)
      }
    } finally {
      csvOverviewRequestInFlightRef.current = false
    }
  }

  async function loadCsvJobSummary(jobId, { isPolling = false } = {}) {
    if (!jobId) return
    if (csvSummaryRequestInFlightRef.current) return
    csvSummaryRequestInFlightRef.current = true
    try {
      const data = await getCsvJobSummary(jobId)
      if (selectedCsvJobIdRef.current && selectedCsvJobIdRef.current !== jobId) {
        return
      }
      setCsvJobs((previous) => previous.map((job) => (job.id === jobId ? { ...job, ...data.job } : job)))
      setCsvJobOverview((previous) => {
        if (!previous || previous.job?.id !== jobId) return previous
        return {
          ...previous,
          job: data.job,
          word_counts: data.word_counts,
          step_counts: data.step_counts,
          last_progress_at: data.last_progress_at,
          stale_seconds: data.stale_seconds,
          is_stale: data.is_stale,
          export_ready: data.export_ready,
        }
      })
    } catch (error) {
      if (!isPolling) {
        setMessage(`Error loading CSV job summary: ${error.message}`)
      }
    } finally {
      csvSummaryRequestInFlightRef.current = false
    }
  }

  async function loadCsvJobItems(jobId, offset = 0) {
    if (!jobId || csvItemsRequestInFlightRef.current) return
    csvItemsRequestInFlightRef.current = true
    try {
      const data = await getCsvJobItems(jobId, { offset, limit: csvItemsPage.limit || 50 })
      if (selectedCsvJobIdRef.current !== jobId) return
      setCsvItemsPage(data)
    } catch (error) {
      setMessage(`Error loading CSV words: ${error.message}`)
    } finally {
      csvItemsRequestInFlightRef.current = false
    }
  }

  async function loadCsvItemDetail(jobId, itemId) {
    if (!jobId || !itemId || csvItemDetailRequestInFlightRef.current) return
    csvItemDetailRequestInFlightRef.current = true
    try {
      const data = await getCsvJobItemDetail(jobId, itemId)
      if (selectedCsvJobIdRef.current === jobId) setCsvItemDetail(data)
    } catch (error) {
      setMessage(`Error loading CSV word detail: ${error.message}`)
    } finally {
      csvItemDetailRequestInFlightRef.current = false
    }
  }

  useEffect(() => {
    let mounted = true
    const loadConfig = async () => {
      try {
        const config = await getConfig()
        if (mounted && config?.openai_assistant_name) {
          setAssistantName(config.openai_assistant_name)
        }
        if (mounted && config?.prompt_engineer_mode) {
          setPromptEngineerMode(config.prompt_engineer_mode)
        }
        if (mounted && config?.responses_prompt_engineer_model) {
          setResponsesPromptEngineerModel(config.responses_prompt_engineer_model)
        }
        if (mounted && config?.responses_vector_store_id) {
          setResponsesVectorStoreId(config.responses_vector_store_id)
        }
        if (mounted && config?.visual_style_id) {
          setVisualStyleId(config.visual_style_id)
        }
        if (mounted && config?.visual_style_name) {
          setVisualStyleName(config.visual_style_name)
        }
        if (mounted && typeof config?.visual_style_prompt_block === 'string') {
          setVisualStylePromptBlock(config.visual_style_prompt_block)
        }
        if (mounted && typeof config?.stage1_prompt_template === 'string') {
          setStage1PromptTemplate(config.stage1_prompt_template)
        }
        if (mounted && typeof config?.stage3_prompt_template === 'string') {
          setStage3PromptTemplate(config.stage3_prompt_template)
        }
        if (mounted && config?.image_aspect_ratio) {
          setImageAspectRatio(config.image_aspect_ratio)
        }
        if (mounted && config?.image_resolution) {
          setImageResolution(config.image_resolution)
        }
      } catch (_error) {
        // Keep fallback value.
      }
    }
    loadConfig()
    return () => {
      mounted = false
    }
  }, [])

  useEffect(() => {
    if (!showingCsvWords) {
      refreshRuns()
    }
    refreshCsvJobs()
    const timer = setInterval(() => {
      if (!pageVisible) return
      if (selectedCsvJobIdRef.current) return
      if (!shouldPollRuns(runsRef.current)) return
      refreshRuns({ isPolling: true })
    }, RUNS_POLL_MS)
    return () => clearInterval(timer)
  }, [query, pageVisible, showingCsvWords])

  useEffect(() => {
    refreshCsvJobs()
    const timer = setInterval(() => {
      if (!pageVisible) return
      refreshCsvJobs({ isPolling: true })
    }, shouldFastPollCsv ? CSV_LIST_POLL_FAST_MS : CSV_LIST_POLL_MS)
    return () => clearInterval(timer)
  }, [pageVisible, shouldFastPollCsv])

  useEffect(() => {
    if (!selectedCsvJobId || !pageVisible) return
    loadCsvJobSummary(selectedCsvJobId, { isPolling: true })
  }, [selectedCsvJobId, csvJobPollKey, pageVisible])

  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null)
      return undefined
    }
    if (showingCsvWords) {
      return undefined
    }
    const includeDebug = selectedDetailTab === 'debug'
    const currentStatus = detailStateRef.current?.run?.status
    const pollMs = includeDebug
      ? DETAIL_POLL_WAITING_MS
      : isWaitingRunStatus(currentStatus)
        ? DETAIL_POLL_WAITING_MS
        : DETAIL_POLL_RUNNING_MS
    loadRunDetail(selectedRunId, { includeDebug })
    const timer = setInterval(() => {
      if (!pageVisible) return
      const activeRunId = selectedRunIdRef.current
      const activeDetail = detailStateRef.current
      const activeStatus = activeDetail?.run?.status
      if (!activeRunId) return
      if (isTerminalRunStatus(activeStatus)) return
      loadRunDetail(activeRunId, { isPolling: true, includeDebug })
    }, pollMs)
    return () => clearInterval(timer)
  }, [selectedRunId, selectedDetailTab, pageVisible, detail?.run?.status, showingCsvWords])

  useEffect(() => {
    if (!selectedCsvJobId) {
      setCsvJobOverview(null)
      setCsvItemsPage({ items: [], tasks: [], total: 0, offset: 0, limit: 50 })
      setSelectedCsvItemId('')
      setSelectedCsvStatusFilter('')
      return undefined
    }
    loadCsvJobDetail(selectedCsvJobId)
    loadCsvJobItems(selectedCsvJobId, 0)
    const timer = setInterval(() => {
      if (!pageVisible) return
      if (!selectedCsvJobId) return
      if (isTerminalCsvJobStatus(csvJobOverview?.job?.status)) return
      loadCsvJobSummary(selectedCsvJobId, { isPolling: true })
      loadCsvJobItems(selectedCsvJobId, csvItemsPage.offset || 0)
    }, shouldFastPollCsv ? CSV_DETAIL_POLL_FAST_MS : CSV_DETAIL_POLL_MS)
    return () => clearInterval(timer)
  }, [selectedCsvJobId, pageVisible, csvJobOverview?.job?.status, shouldFastPollCsv, csvItemsPage.offset])

  useEffect(() => {
    if (!filteredCsvJobItems.length) {
      setSelectedCsvItemId('')
      return
    }
    if (!selectedCsvItemId || !filteredCsvJobItems.some((item) => item.id === selectedCsvItemId)) {
      setSelectedCsvItemId(filteredCsvJobItems[0].id)
    }
  }, [filteredCsvJobItems, selectedCsvItemId])

  useEffect(() => {
    setCsvShadowRunDetail(null)
    setCsvItemDetail(null)
    setShowCsvScoreHistory(false)
    setShowCsvPromptHistory(false)
  }, [selectedCsvItem?.id])

  useEffect(() => {
    if (selectedCsvJobId && selectedCsvListItem?.id) {
      loadCsvItemDetail(selectedCsvJobId, selectedCsvListItem.id)
    }
  }, [selectedCsvJobId, selectedCsvListItem?.id])

  useEffect(() => {
    const shadowRunId = String(selectedCsvItem?.shadow_run_id || '').trim()
    if (!shadowRunId) {
      setCsvShadowRunDetail(null)
      return undefined
    }
    let canceled = false
    let requestInFlight = false
    const loadShadowRunDetail = async ({ isPolling = false } = {}) => {
      if (requestInFlight) return
      requestInFlight = true
      try {
        const data = await getRun(shadowRunId)
        if (!canceled && String(selectedCsvItem?.shadow_run_id || '').trim() === shadowRunId) {
          setCsvShadowRunDetail(data)
        }
      } catch (_error) {
        if (!isPolling && !canceled) {
          setCsvShadowRunDetail(null)
        }
      } finally {
        requestInFlight = false
      }
    }
    loadShadowRunDetail()
    const timer = window.setInterval(() => {
      if (!pageVisible) return
      if (isTerminalRunStatus(selectedCsvItem?.shadow_run_status)) return
      loadShadowRunDetail({ isPolling: true })
    }, CSV_DETAIL_POLL_MS)
    return () => {
      canceled = true
      window.clearInterval(timer)
    }
  }, [selectedCsvItem?.shadow_run_id, selectedCsvItem?.shadow_run_status, pageVisible])

  useEffect(() => {
    if (!pageVisible) return undefined
    const handleFocus = () => {
      if (!selectedCsvJobIdRef.current) {
        refreshRuns({ isPolling: true })
      }
      if (!selectedCsvJobIdRef.current && selectedRunIdRef.current) {
        const includeDebug = selectedDetailTab === 'debug'
        loadRunDetail(selectedRunIdRef.current, { isPolling: true, includeDebug })
      }
      if (selectedCsvJobIdRef.current) {
        refreshCsvJobs({ isPolling: true })
        loadCsvJobSummary(selectedCsvJobIdRef.current, { isPolling: true })
      }
    }
    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [pageVisible, selectedDetailTab])

  const onRetry = async (runId) => {
    try {
      await retryRun(runId)
      setMessage(`Run ${runId} queued for retry`)
      refreshRuns()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onStop = async (runId) => {
    try {
      const result = await stopRun(runId)
      setMessage(result.message || `Run ${runId} stop requested`)
      refreshRuns()
      if (selectedRunIdRef.current === runId) {
        loadRunDetail(runId, { includeDebug: selectedDetailTab === 'debug' })
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onDeleteRun = async (runId) => {
    try {
      const result = await deleteRun(runId)
      setMessage(`Deleted ${result.deleted_run_count} run`)
      if (selectedRunIdRef.current === runId) {
        setSelectedRunId('')
        selectedRunIdRef.current = ''
        setStoredRunId('')
        setDetail(null)
      }
      refreshRuns()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onClearTerminalHistory = async () => {
    try {
      const result = await clearTerminalRuns()
      setMessage(`Cleared ${result.deleted_run_count} terminal runs`)
      setSelectedRunId('')
      selectedRunIdRef.current = ''
      setStoredRunId('')
      setDetail(null)
      refreshRuns()
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onCancelCsvJob = async (jobId) => {
    try {
      const result = await cancelCsvJob(jobId)
      setMessage(`CSV job ${jobId} status: ${result.status}`)
      refreshCsvJobs()
      loadCsvJobDetail(jobId)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onRetryCsvJob = async (jobId) => {
    try {
      const result = await retryCsvJobFailures(jobId)
      setMessage(`Requeued ${result.requeued_task_count} failed CSV tasks`)
      refreshCsvJobs()
      loadCsvJobDetail(jobId)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onStartCsvJob = async (jobId) => {
    try {
      const result = await startCsvJob(jobId)
      selectedCsvJobIdRef.current = result.job_id || jobId
      setSelectedCsvJobId(result.job_id || jobId)
      setSelectedCsvItemId('')
      setSelectedCsvStatusFilter('')
      setCsvJobOverview(null)
      setMessage(`Started CSV job ${jobId}`)
      refreshCsvJobs()
      loadCsvJobDetail(result.job_id || jobId)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onExportCsvJob = async (jobId) => {
    try {
      const result = await exportCsvJob(jobId)
      window.open(buildApiUrl(result.download_url), '_blank', 'noopener,noreferrer')
      setMessage(`Prepared export for CSV job ${jobId}`)
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onClearCsvHistory = async () => {
    try {
      const result = await clearTerminalCsvJobs()
      setMessage(`Cleared ${result.deleted_job_count} terminal CSV jobs`)
      refreshCsvJobs()
      if (selectedCsvJobId && isTerminalCsvJobStatus(csvJobOverview?.job?.status)) {
        setSelectedCsvJobId('')
        setSelectedCsvItemId('')
        setCsvJobOverview(null)
      }
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  const onToggleContinueSelection = (field, option) => {
    setContinueSelections((current) => {
      const currentValues = Array.isArray(current[field]) ? current[field] : []
      const nextValues = currentValues.includes(option)
        ? currentValues.filter((value) => value !== option)
        : [...currentValues, option]
      return {
        ...current,
        [field]: nextValues,
      }
    })
  }

  const onContinueCsvJob = async () => {
    if (!selectedCsvJobId) {
      setMessage('Select a completed CSV job first')
      return
    }
    if (csvHasFollowUpJob(csvJobs, selectedCsvJobId)) {
      setMessage('This CSV job already has a follow-up variants run in progress or saved in history')
      return
    }
    if (
      !continueSelections.person_gender_options.length ||
      !continueSelections.person_age_options.length ||
      !continueSelections.person_skin_color_options.length
    ) {
      setMessage('Choose at least one gender, one age, and one skin color for the next variants')
      return
    }
    const sourceJobId = selectedCsvJobId
    setContinuingCsvJobId(sourceJobId)
    try {
      const result = await continueCsvJob(sourceJobId, continueSelections)
      setMessage(`Started follow-up CSV job ${result.batch_id}`)
      selectedCsvJobIdRef.current = result.job_id
      setSelectedCsvJobId(result.job_id)
      setSelectedCsvItemId('')
      setSelectedCsvStatusFilter('')
      setCsvJobOverview(null)
      refreshCsvJobs()
      loadCsvJobDetail(result.job_id)
    } catch (error) {
      setContinuingCsvJobId((current) => (current === sourceJobId ? '' : current))
      setMessage(`Error: ${error.message}`)
    }
  }

  const onSavePromptEngineerConfig = async () => {
    try {
      const updated = await updateConfig({
        prompt_engineer_mode: promptEngineerMode,
        responses_prompt_engineer_model: responsesPromptEngineerModel,
        responses_vector_store_id: responsesVectorStoreId,
        visual_style_id: visualStyleId,
        visual_style_name: visualStyleName,
        visual_style_prompt_block: visualStylePromptBlock,
        stage1_prompt_template: stage1PromptTemplate,
        stage3_prompt_template: stage3PromptTemplate,
        image_aspect_ratio: imageAspectRatio,
        image_resolution: imageResolution,
      })
      setPromptEngineerMode(updated.prompt_engineer_mode)
      setResponsesPromptEngineerModel(updated.responses_prompt_engineer_model)
      setResponsesVectorStoreId(updated.responses_vector_store_id)
      setVisualStyleId(updated.visual_style_id)
      setVisualStyleName(updated.visual_style_name)
      setVisualStylePromptBlock(updated.visual_style_prompt_block)
      setStage1PromptTemplate(updated.stage1_prompt_template)
      setStage3PromptTemplate(updated.stage3_prompt_template)
      setImageAspectRatio(updated.image_aspect_ratio)
      setImageResolution(updated.image_resolution)
      setMessage('Saved prompt engineer, visual style, and image output configuration')
    } catch (error) {
      setMessage(`Error: ${error.message}`)
    }
  }

  return (
    <section className="runs-page-stack">
      <section className="runs-layout runs-layout-stacked">
        <article className="card runs-floor-card">
          <div className="runs-floor-head">
            <div>
              <p className="detail-eyebrow">First Floor</p>
              <h2>{showingCsvWords ? 'CSV Job Words' : 'Runs'}</h2>
              <p className="runs-floor-copy">
                {showingCsvWords
                  ? 'The selected CSV job controls this table. Click a word here to inspect its details below.'
                  : 'Choose a legacy run here, then inspect it in full width below.'}
              </p>
            </div>
            <div className="runs-floor-summary">
              <span>{showingCsvWords ? filteredCsvJobItems.length : filteredRuns.length} shown</span>
              <span>{showingCsvWords ? csvItemsPage.total : runs.length} total</span>
              {showingCsvWords ? (
                <div className="csv-page-controls">
                  <button
                    type="button"
                    disabled={csvItemsPage.offset <= 0}
                    onClick={() => loadCsvJobItems(selectedCsvJobId, Math.max(0, csvItemsPage.offset - csvItemsPage.limit))}
                  >
                    Previous
                  </button>
                  <span>
                    {csvItemsPage.total ? csvItemsPage.offset + 1 : 0}–{Math.min(csvItemsPage.total, csvItemsPage.offset + csvItemsPage.items.length)}
                  </span>
                  <button
                    type="button"
                    disabled={csvItemsPage.offset + csvItemsPage.limit >= csvItemsPage.total}
                    onClick={() => loadCsvJobItems(selectedCsvJobId, csvItemsPage.offset + csvItemsPage.limit)}
                  >
                    Next
                  </button>
                </div>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  if (showingCsvWords && selectedCsvJobId) {
                    refreshCsvJobs()
                    loadCsvJobDetail(selectedCsvJobId)
                  } else {
                    refreshRuns()
                  }
                }}
                className="button-secondary"
              >
                Refresh
              </button>
              <button
                type="button"
                onClick={showingCsvWords ? onClearCsvHistory : onClearTerminalHistory}
                className="button-secondary"
              >
                {showingCsvWords ? 'Clear CSV History' : 'Clear Terminal History'}
              </button>
            </div>
          </div>

          {!showingCsvWords ? (
            <div className="inline-fields">
              <label>
                Status
                <input value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} />
              </label>
              <label>
                Word
                <input value={filters.word} onChange={(e) => setFilters({ ...filters, word: e.target.value })} />
              </label>
              <label>
                POS
                <input
                  value={filters.part_of_sentence}
                  onChange={(e) => setFilters({ ...filters, part_of_sentence: e.target.value })}
                />
              </label>
              <label>
                Category
                <input value={filters.category} onChange={(e) => setFilters({ ...filters, category: e.target.value })} />
              </label>
            </div>
          ) : (
            <p className="config-help-text">
              {selectedCsvStatusFilter
                ? `Filtered to ${csvPrettyStatus(selectedCsvStatusFilter)} words from ${csvJobOverview?.job?.batch_id || selectedCsvJobId}.`
                : `Showing all words from ${csvJobOverview?.job?.batch_id || selectedCsvJobId}.`}
            </p>
          )}

          <div className="table-wrap runs-table-wrap">
            <table>
              {showingCsvWords ? (
                <>
                  <thead>
                    <tr>
                      <th>Word</th>
                      <th>POS</th>
                      <th>Category</th>
                      <th>Profile</th>
                      <th>Status</th>
                      <th>Score</th>
                      <th>Iterations</th>
                      <th>Est. cost</th>
                      <th>Progress</th>
                      <th>Current step</th>
                      <th>Needs person attention?</th>
                      <th>Person?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCsvJobItems.map((item) => {
                      const liveProgress = csvItemLiveProgress(item)
                      return (
                        <tr
                          key={item.id}
                          className={item.id === selectedCsvItem?.id ? 'selected-row' : 'clickable-row'}
                          onClick={() => {
                            setSelectedCsvItemId(item.id)
                            scrollToDetail()
                          }}
                        >
                          <td>{item.word || '-'}</td>
                          <td>{item.part_of_sentence || '-'}</td>
                          <td>{item.category || '-'}</td>
                          <td>{csvItemProfileColumnText(item)}</td>
                          <td>
                            <div className="status-stack">
                              <strong>{csvPrettyStatus(item.main_status)}</strong>
                              <span>{liveProgress.subStatus || '-'}</span>
                            </div>
                          </td>
                          <td>{formatQualityScore(item.quality_score)}</td>
                          <td>{item.optimization_attempt || item.optimization_loop_count || '-'}</td>
                          <td>{formatUsd(item.estimated_total_cost_usd)}</td>
                          <td>{item.progress?.completed || 0}/{item.progress?.total || 0}</td>
                          <td>{liveProgress.currentStep || '-'}</td>
                          <td>{personAttentionLabel(Boolean(item.needs_person_attention))}</td>
                          <td>{item.has_person === 'yes' ? 'Yes' : item.has_person === 'no' ? 'No' : '-'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </>
              ) : (
                <>
                  <thead>
                    <tr>
                      <th>Word</th>
                      <th>POS</th>
                      <th>Category</th>
                      <th>Status</th>
                      <th>Score</th>
                      <th>Needs person attention?</th>
                      <th>Attempt</th>
                      <th>Est. cost</th>
                      <th>Est. avg / image</th>
                      <th>Stop</th>
                      <th>Retry</th>
                      <th>Delete</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRuns.map((run) => (
                      <tr
                        key={run.id}
                        className={run.id === selectedRunId ? 'selected-row' : 'clickable-row'}
                        onClick={() => selectRun(run.id, { scrollToDetail: true })}
                      >
                        <td>{run.word || '-'}</td>
                        <td>{run.part_of_sentence || '-'}</td>
                        <td>{run.category || '-'}</td>
                        <td>{run.status}</td>
                        <td>{formatQualityScore(run.quality_score)}</td>
                        <td>{personAttentionLabel(Boolean(run.needs_person_attention))}</td>
                        <td>{run.optimization_attempt}</td>
                        <td>{typeof run.estimated_total_cost_usd === 'number' ? `$${Number(run.estimated_total_cost_usd).toFixed(4)}` : '-'}</td>
                        <td>{run.estimated_cost_per_image_usd != null ? `$${Number(run.estimated_cost_per_image_usd).toFixed(4)}` : '-'}</td>
                        <td>
                          <button
                            onClick={(event) => {
                              event.stopPropagation()
                              onStop(run.id)
                            }}
                            disabled={!canStopRun(run.status)}
                          >
                            {String(run.status || '').toLowerCase() === 'cancel_requested' ? 'Stopping…' : 'Stop'}
                          </button>
                        </td>
                        <td>
                          <button
                            onClick={(event) => {
                              event.stopPropagation()
                              onRetry(run.id)
                            }}
                            disabled={!run.status.startsWith('failed')}
                          >
                            Retry
                          </button>
                        </td>
                        <td>
                          <button
                            onClick={(event) => {
                              event.stopPropagation()
                              onDeleteRun(run.id)
                            }}
                            disabled={!canDeleteRunStatus(run.status)}
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </>
              )}
            </table>
          </div>
          <p>{message}</p>
        </article>

        <article className="card runs-floor-card">
          <div className="runs-floor-head">
            <div>
              <p className="detail-eyebrow">Second Floor</p>
              <h2>CSV Stats</h2>
              <p className="runs-floor-copy">The current CSV DAG job and its summary live here, separately from legacy runs.</p>
            </div>
            <div className="runs-floor-summary">
              <span>{visibleCsvJobs.length} shown</span>
              <button type="button" onClick={() => refreshCsvJobs()} className="button-secondary">Refresh</button>
              <button type="button" onClick={onClearCsvHistory} className="button-secondary">Clear CSV History</button>
            </div>
          </div>

          <div className="csv-section-block">
            <div className="csv-section-head">
              <h3>Current CSV Job</h3>
              <p>The selected job stays pinned here while you inspect it.</p>
            </div>

            <div className="table-wrap runs-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Batch</th>
                    <th>Status</th>
                    <th>Rows</th>
                    <th>Duration</th>
                    <th>Started</th>
                    <th>Finished</th>
                    <th>Start</th>
                    <th>Retry</th>
                    <th>Cancel</th>
                    <th>Export</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleCsvJobs.map((job) => (
                    <tr
                      key={job.id}
                      className={job.id === selectedCsvJobId ? 'selected-row' : 'clickable-row'}
                      onClick={() => {
                        setCsvJobOverview(null)
                        selectedCsvJobIdRef.current = job.id
                        setSelectedCsvJobId(job.id)
                        setSelectedCsvItemId('')
                        setSelectedCsvStatusFilter('')
                      }}
                    >
                      <td>{job.batch_id}</td>
                      <td>
                        <div className="status-stack">
                          <strong>{csvPrettyStatus(csvJobMainStatus(job).main)}</strong>
                          <span>{csvJobMainStatus(job).sub}</span>
                        </div>
                      </td>
                      <td>{job.total_row_count}</td>
                      <td>{csvJobDurationLabel(job)}</td>
                      <td>{formatLocalDateTime(job.started_at)}</td>
                      <td>{formatLocalDateTime(job.finished_at)}</td>
                      <td>
                        <button
                          onClick={(event) => {
                            event.stopPropagation()
                            onStartCsvJob(job.id)
                          }}
                          disabled={job.status !== 'imported'}
                        >
                          Start
                        </button>
                      </td>
                      <td>
                        <button
                          onClick={(event) => {
                            event.stopPropagation()
                            onRetryCsvJob(job.id)
                          }}
                          disabled={!['failed', 'partial_failed'].includes(String(job.status || '').toLowerCase())}
                        >
                          Retry
                        </button>
                      </td>
                      <td>
                        <button
                          onClick={(event) => {
                            event.stopPropagation()
                            onCancelCsvJob(job.id)
                          }}
                          disabled={isTerminalCsvJobStatus(job.status) || job.status === 'cancel_requested'}
                        >
                          {job.status === 'cancel_requested' ? 'Stopping…' : 'Cancel'}
                        </button>
                      </td>
                      <td>
                        <button
                          onClick={(event) => {
                            event.stopPropagation()
                            onExportCsvJob(job.id)
                          }}
                          disabled={!isTerminalCsvJobStatus(job.status)}
                        >
                          Export
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {csvJobOverview ? (
            <div className="card csv-job-overview-card" style={{ marginTop: 16 }}>
              <h3>CSV Job Overview</h3>
              <div className="csv-job-stat-grid">
                <div>
                  <strong>Job</strong>
                  <p>{csvJobOverview.job.batch_id}</p>
                </div>
                <div>
                  <strong>Status</strong>
                  <p>{csvPrettyStatus(csvJobMainStatus(csvJobOverview.job).main)}</p>
                  <small>{csvJobMainStatus(csvJobOverview.job).sub}</small>
                </div>
                <div>
                    <strong>Duration</strong>
                    <p>{csvJobDurationLabel(csvJobOverview.job)}</p>
                </div>
                <div>
                  <strong>Rows</strong>
                  <p>{csvJobOverview.job.total_row_count}</p>
                </div>
                <div>
                  <strong>Total cost</strong>
                  <p>{formatUsd(csvJobOverview.estimated_total_cost_usd)}</p>
                </div>
                <div>
                  <strong>Started</strong>
                  <p>{formatLocalDateTime(csvJobOverview.job.started_at)}</p>
                </div>
                <div>
                  <strong>Finished</strong>
                  <p>{formatLocalDateTime(csvJobOverview.job.finished_at)}</p>
                </div>
              </div>
              <div className="csv-job-live-strip">
                {[
                  ['pending', csvJobLiveCounts.pending],
                  ['running', csvJobLiveCounts.running],
                  ['completed', csvJobLiveCounts.completed],
                  ['failure', csvJobLiveCounts.failure],
                  ['previously_done', csvJobLiveCounts.previously_done],
                ].map(([statusKey, count]) => (
                  <button
                    key={statusKey}
                    type="button"
                    className={selectedCsvStatusFilter === statusKey ? 'csv-status-chip active' : 'csv-status-chip'}
                    onClick={() => {
                      setSelectedCsvStatusFilter((current) => (current === statusKey ? '' : statusKey))
                      scrollToDetail()
                    }}
                  >
                    {csvPrettyStatus(statusKey)} {count}
                  </button>
                ))}
              </div>
              {csvJobOverview.is_stale ? (
                <p className="config-help-text" role="status">
                  No recorded progress for {Math.max(3, Math.floor((csvJobOverview.stale_seconds || 0) / 60))} minutes. The job may be waiting on an external model or a worker recovery.
                </p>
              ) : null}
              <p className="config-help-text">
                Click a status chip to filter the word list on the first floor.
              </p>
              <div className="table-wrap runs-table-wrap" style={{ marginBottom: 16 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Estimated cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Google</td>
                      <td>{csvJobHasProviderCost ? formatUsd(csvJobProviderBreakdown.google) : '-'}</td>
                    </tr>
                    <tr>
                      <td>Replicate</td>
                      <td>{csvJobHasProviderCost ? formatUsd(csvJobProviderBreakdown.replicate) : '-'}</td>
                    </tr>
                    <tr>
                      <td>OpenAI</td>
                      <td>{csvJobHasProviderCost ? formatUsd(csvJobProviderBreakdown.openai) : '-'}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="csv-request-history">
                <div>
                  <strong>Current requested variants</strong>
                  <div className="csv-request-chip-row">
                    {currentRequestedProfiles.length ? (
                      currentRequestedProfiles.map((profileKey) => (
                        <span key={`current:${profileKey}`} className="csv-status-chip">
                          {csvProfileSummary(profileKey)}
                        </span>
                      ))
                    ) : (
                      <span className="config-help-text">No requested variants recorded.</span>
                    )}
                  </div>
                </div>
                {requestedProfileHistory.length > 0 ? (
                  <div>
                    <strong>Requested variant history</strong>
                    <div className="csv-request-history-list">
                      {requestedProfileHistory.map((historyItem) => (
                        <div key={historyItem.job_id} className="csv-request-history-card">
                          <div className="status-stack">
                            <strong>
                              {historyItem.is_current ? 'Current job' : 'Previous job'} · {historyItem.batch_id}
                            </strong>
                            <span>
                              {formatLocalDateTime(historyItem.created_at)} · {csvPrettyStatus(historyItem.status)}
                            </span>
                          </div>
                          <div className="csv-request-chip-row">
                            {(Array.isArray(historyItem.requested_profiles) ? historyItem.requested_profiles : []).map((profileKey) => (
                              <span key={`${historyItem.job_id}:${profileKey}`} className="csv-status-chip">
                                {csvProfileSummary(profileKey)}
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
              {canContinueSelectedCsvJob ? (
                <div className="csv-continue-panel">
                  <div className="csv-section-head">
                    <h3>Continue With More Variants</h3>
                    <p>Pick the next variants for the same words. We will create a new CSV DAG job and start it automatically.</p>
                  </div>
                  <div className="csv-continue-grid">
                    <fieldset className="checkbox-group">
                      <legend>Gender</legend>
                      {CSV_GENDER_OPTIONS.map((option) => (
                        <label key={`continue-gender:${option}`} className="checkbox-option">
                          <input
                            type="checkbox"
                            checked={continueSelections.person_gender_options.includes(option)}
                            onChange={() => onToggleContinueSelection('person_gender_options', option)}
                          />
                          <span>{normalizeProfileOptionLabel(option)}</span>
                        </label>
                      ))}
                    </fieldset>
                    <fieldset className="checkbox-group">
                      <legend>Age</legend>
                      {CSV_AGE_OPTIONS.map((option) => (
                        <label key={`continue-age:${option}`} className="checkbox-option">
                          <input
                            type="checkbox"
                            checked={continueSelections.person_age_options.includes(option)}
                            onChange={() => onToggleContinueSelection('person_age_options', option)}
                          />
                          <span>{normalizeProfileOptionLabel(option)}</span>
                        </label>
                      ))}
                    </fieldset>
                    <fieldset className="checkbox-group">
                      <legend>Skin color</legend>
                      {CSV_SKIN_OPTIONS.map((option) => (
                        <label key={`continue-skin:${option}`} className="checkbox-option">
                          <input
                            type="checkbox"
                            checked={continueSelections.person_skin_color_options.includes(option)}
                            onChange={() => onToggleContinueSelection('person_skin_color_options', option)}
                          />
                          <span>{normalizeProfileOptionLabel(option)}</span>
                        </label>
                      ))}
                    </fieldset>
                  </div>
                  <label className="checkbox-option">
                    <input
                      type="checkbox"
                      checked={continueSelections.override_existing_variants}
                      onChange={(event) =>
                        setContinueSelections((current) => ({
                          ...current,
                          override_existing_variants: event.target.checked,
                        }))
                      }
                    />
                    <span>Override existing images for the requested variants</span>
                  </label>
                  <button
                    type="button"
                    className="button-primary"
                    onClick={onContinueCsvJob}
                    disabled={continuingCsvJobId === selectedCsvJobId || selectedCsvJobAlreadyContinued}
                  >
                    {continuingCsvJobId === selectedCsvJobId || selectedCsvJobAlreadyContinued
                      ? 'Continue Process Started'
                      : 'Continue Process'}
                  </button>
                </div>
              ) : null}
            </div>
          ) : (
            <p style={{ marginTop: 16 }}>Select a CSV job to see its overview.</p>
          )}
        </article>

        <article ref={detailRef} className="card run-detail-floor-card">
          <div className="runs-floor-head">
            <div>
              <p className="detail-eyebrow">Third Floor</p>
              <h2>{showingCsvWords ? 'CSV Word Detail' : 'Run Detail'}</h2>
              <p className="runs-floor-copy">
                {showingCsvWords
                  ? 'The selected word shows its current CSV DAG state, images, and dependency blockers here.'
                  : 'The selected run gets the full page width so the story, images, and process are easier to read.'}
              </p>
            </div>
          </div>

          {showingCsvWords ? (
            selectedCsvItem ? (
              <div className="csv-word-detail">
                <div className="csv-word-detail-head">
                  <div>
                    <h4>{selectedCsvItem.word || 'Selected word'}</h4>
                    <p>
                      Row {selectedCsvItem.row_index} · {selectedCsvItem.part_of_sentence || 'POS n/a'} · {selectedCsvItem.category || 'Category n/a'}
                    </p>
                  </div>
                  <div className="status-stack">
                    <strong>{selectedCsvItemProgress ? csvPrettyStatus(selectedCsvItemProgress.main_status) : '-'}</strong>
                    <span>{selectedCsvItemLiveProgress.subStatus || '-'}</span>
                  </div>
                </div>
                <div className="csv-word-meta-grid">
                  <div>
                    <strong>Progress</strong>
                    <p>{selectedCsvItemProgress ? `${selectedCsvItemProgress.progress?.completed || 0}/${selectedCsvItemProgress.progress?.total || 0} steps finished` : '-'}</p>
                    {selectedCsvItemLiveProgress.isBaseSubstage ? (
                      <small>Base images finish as one step after all quality and background stages complete.</small>
                    ) : null}
                  </div>
                  <div>
                    <strong>Score</strong>
                    <p>{formatQualityScore(selectedCsvItem.quality_score)}</p>
                  </div>
                  <div>
                    <strong>Iterations</strong>
                    <p>{selectedCsvItem.optimization_attempt || selectedCsvItem.optimization_loop_count || '-'}</p>
                  </div>
                  <div>
                    <strong>Current step</strong>
                    <p>{selectedCsvItemLiveProgress.currentStep || '-'}</p>
                  </div>
                  <div>
                    <strong>Live activity</strong>
                    <p>{selectedCsvItem.blocking_reason || selectedCsvItemLiveProgress.subStatus || '-'}</p>
                  </div>
                  <div>
                    <strong>Shadow run</strong>
                    <p>{selectedCsvItem.shadow_run_id || '-'}</p>
                  </div>
                  <div>
                    <strong>Shadow run status</strong>
                    <p>
                      {selectedCsvItem.shadow_run_status || '-'}
                      {selectedCsvItem.shadow_run_current_stage ? ` · ${selectedCsvItem.shadow_run_current_stage}` : ''}
                    </p>
                  </div>
                  <div>
                    <strong>Error</strong>
                    <p>{selectedCsvItem.error_detail || selectedCsvItem.shadow_run_error_detail || '-'}</p>
                  </div>
                </div>
                <div className="inline-fields" style={{ marginBottom: 16 }}>
                  <button type="button" className="button-secondary" onClick={() => setShowCsvScoreHistory((value) => !value)}>
                    {showCsvScoreHistory ? 'Hide score history' : 'View score history'}
                  </button>
                  <button type="button" className="button-secondary" onClick={() => setShowCsvPromptHistory((value) => !value)}>
                    {showCsvPromptHistory ? 'Hide image prompts' : 'View image prompts'}
                  </button>
                </div>
                <div className="csv-word-image-grid">
                  {selectedCsvItemImages.length ? (
                    selectedCsvItemImages.map((image) => (
                      <article key={`${selectedCsvItem.id}:${image.id}:${image.label}`} className="csv-word-image-card">
                        {image.id ? (
                          <DeferredAssetImage
                            asset={image.id}
                            alt={image.label}
                            buttonLabel={`Load ${image.label}`}
                            className="asset-image"
                          />
                        ) : (
                          <div className="asset-image asset-image-empty">
                            <span>Not created</span>
                          </div>
                        )}
                        <div className="csv-word-image-meta">
                          <strong>{image.label}</strong>
                          {image.id ? (
                            <div className="csv-word-image-link-wrap">
                              <a href={buildAssetContentUrl(image.id)} target="_blank" rel="noreferrer">
                                Preview image
                              </a>
                              <div className="csv-word-image-hover-preview">
                                <img src={buildAssetContentUrl(image.id)} alt={image.label} loading="lazy" decoding="async" />
                              </div>
                            </div>
                          ) : (
                            <span>Not created</span>
                          )}
                        </div>
                      </article>
                    ))
                  ) : (
                    <p>No images have been created for this word yet.</p>
                  )}
                </div>
                {showCsvScoreHistory ? (
                  <div className="table-wrap runs-table-wrap" style={{ marginBottom: 16 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>Attempt</th>
                          <th>Score</th>
                          <th>Pass</th>
                          <th>Stage</th>
                        </tr>
                      </thead>
                      <tbody>
                        {csvScoreHistory.length ? (
                          csvScoreHistory.map((scoreRow) => (
                            <tr key={scoreRow.id}>
                              <td>{scoreRow.attempt || '-'}</td>
                              <td>{formatQualityScore(scoreRow.score_0_100)}</td>
                              <td>{scoreRow.pass_fail ? 'Yes' : 'No'}</td>
                              <td>{scoreRow.stage_name || 'quality_gate'}</td>
                            </tr>
                          ))
                        ) : (
                          <tr>
                            <td colSpan={4}>No score history recorded for this word yet.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                {showCsvPromptHistory ? (
                  <div style={{ marginBottom: 16 }}>
                    {csvPromptHistory.length ? (
                      csvPromptHistory.map((promptRow) => (
                        <article key={promptRow.id} className="csv-word-image-card" style={{ marginBottom: 12 }}>
                          <div className="csv-word-image-meta">
                            <strong>{stageTitle(promptRow.stage_name)}</strong>
                            <span>Attempt {promptRow.attempt || '-'}</span>
                          </div>
                          <pre className="prompt-doc-box">{promptRow.prompt_text || 'No prompt text recorded.'}</pre>
                        </article>
                      ))
                    ) : (
                      <p>No image-generation prompts recorded for this word yet.</p>
                    )}
                  </div>
                ) : null}
                {showBaseCsvOutputs ? (
                  <div className="table-wrap runs-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Output</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>Quality image</td>
                          <td>{csvAssetAvailabilityLabel(selectedCsvItem.base_regular_asset_id)}</td>
                        </tr>
                        <tr>
                          <td>Soften image</td>
                          <td>{csvAssetAvailabilityLabel(selectedCsvItem.base_soften_asset_id)}</td>
                        </tr>
                        <tr>
                          <td>White background image</td>
                          <td>{csvAssetAvailabilityLabel(selectedCsvItem.base_white_bg_asset_id)}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <div className="table-wrap runs-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Provider</th>
                        <th>Estimated cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>Google</td>
                        <td>{csvSelectedItemHasProviderCost ? formatUsd(csvSelectedItemProviderBreakdown.google) : '-'}</td>
                      </tr>
                      <tr>
                        <td>Replicate</td>
                        <td>{csvSelectedItemHasProviderCost ? formatUsd(csvSelectedItemProviderBreakdown.replicate) : '-'}</td>
                      </tr>
                      <tr>
                        <td>OpenAI</td>
                        <td>{csvSelectedItemHasProviderCost ? formatUsd(csvSelectedItemProviderBreakdown.openai) : '-'}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div className="table-wrap runs-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Created profile</th>
                        <th>Regular</th>
                        <th>White background</th>
                      </tr>
                    </thead>
                    <tbody>
                      {csvAvailableProfiles(selectedCsvItem).length ? (
                        csvAvailableProfiles(selectedCsvItem).map((profile) => (
                          <tr key={`${selectedCsvItem.id}:${profile.profile_key}`}>
                            <td>{csvProfileDisplay(profile.profile_key)}</td>
                            <td>{profile.regular_asset_id ? 'Created' : 'Not created'}</td>
                            <td>{profile.white_bg_asset_id ? 'Created' : 'Not created'}</td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={3}>No created profiles recorded yet for this word.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="table-wrap runs-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Step</th>
                        <th>Profile</th>
                        <th>Status</th>
                        <th>Regular image</th>
                        <th>White bg image</th>
                        <th>Waiting on</th>
                        <th>Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedCsvTaskDiagnostics.map((task) => (
                        <tr key={task.id}>
                          <td>{task.stepLabel}</td>
                          <td>{task.profileLabel || '-'}</td>
                          <td>{csvPrettyStatus(task.status)}</td>
                          <td>{csvAssetAvailabilityLabel(task.regular_asset_id)}</td>
                          <td>{csvAssetAvailabilityLabel(task.white_bg_asset_id)}</td>
                          <td>{task.waitingOnLabel || '-'}</td>
                          <td>{task.error_summary || '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              <p>Select a word row to see CSV DAG details.</p>
            )
          ) : !detail ? (
            <p>Select a run row to see details.</p>
          ) : algoDiagramEnabled ? (
            <PageErrorBoundary resetKey={`${detail?.run?.id || ''}:${detail?.run?.updated_at || ''}`}>
              <RunExecutionDiagram
                detail={detail}
                assistantName={assistantName}
                onActiveTabChange={setSelectedDetailTab}
                promptEngineerConfig={{
                  promptEngineerMode,
                  setPromptEngineerMode,
                  responsesPromptEngineerModel,
                  setResponsesPromptEngineerModel,
                  responsesVectorStoreId,
                  setResponsesVectorStoreId,
                  visualStyleId,
                  setVisualStyleId,
                  visualStyleName,
                  setVisualStyleName,
                  visualStylePromptBlock,
                  setVisualStylePromptBlock,
                  stage1PromptTemplate,
                  setStage1PromptTemplate,
                  stage3PromptTemplate,
                  setStage3PromptTemplate,
                  imageAspectRatio,
                  setImageAspectRatio,
                  imageResolution,
                  setImageResolution,
                }}
                onSavePromptEngineerConfig={onSavePromptEngineerConfig}
                onStopRun={onStop}
              />
            </PageErrorBoundary>
          ) : (
            <>
              <h3>Run</h3>
              <pre>{JSON.stringify(detail.run, null, 2)}</pre>

              <h3>Final Image</h3>
              {finalAsset?.id ? (
                <div className="asset-card">
                  <img className="asset-image" src={buildAssetContentUrl(finalAsset)} alt="Final white background output" loading="lazy" decoding="async" />
                  <div className="asset-meta">
                    <p>{finalAsset.file_name}</p>
                    <a href={buildAssetContentUrl(finalAsset)} target="_blank" rel="noreferrer">
                      Open Full Image
                    </a>
                  </div>
                </div>
              ) : (
                <p>No final image yet.</p>
              )}

              <h3>Image History</h3>
              {sortedAssets.length > 0 ? (
                <div className="asset-grid">
                  {sortedAssets.map((asset) => (
                    <div key={asset.id} className="asset-card">
                      <h4>{stageTitle(asset.stage_name)}</h4>
                      {asset.id ? (
                        <DeferredAssetImage asset={asset} alt={`${asset.stage_name} attempt ${asset.attempt}`} />
                      ) : (
                        <p>Image URL unavailable.</p>
                      )}
                      <div className="asset-meta">
                        <p>Attempt: {asset.attempt}</p>
                        <p>Model: {asset.model_name}</p>
                        {asset.id ? (
                          <a href={buildAssetContentUrl(asset)} target="_blank" rel="noreferrer">
                            Open Image
                          </a>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p>No images generated yet.</p>
              )}

              <h3>Prompts</h3>
              <pre>{JSON.stringify(detail.prompts, null, 2)}</pre>

              <h3>Scores</h3>
              <pre>{JSON.stringify(detail.scores, null, 2)}</pre>
            </>
          )}
        </article>
      </section>
    </section>
  )
}
