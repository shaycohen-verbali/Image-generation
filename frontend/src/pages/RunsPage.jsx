import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildApiUrl,
  buildAssetContentUrl,
  cancelCsvJob,
  clearTerminalCsvJobs,
  clearTerminalRuns,
  deleteRun,
  exportCsvJob,
  getConfig,
  getCsvJobOverview,
  getRun,
  listCsvJobs,
  listRuns,
  retryCsvJobFailures,
  retryRun,
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
  return ['completed', 'failed', 'canceled'].includes(value)
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

function csvJobMainStatus(rawStatus) {
  const value = String(rawStatus || '').toLowerCase()
  if (value === 'completed') return { main: 'completed', sub: 'All rows finished' }
  if (value === 'failed') return { main: 'failure', sub: 'One or more rows failed' }
  if (value === 'canceled') return { main: 'failure', sub: 'Canceled' }
  if (value === 'cancel_requested') return { main: 'running', sub: 'Stopping after active work finishes' }
  if (['queued', 'retry_queued', 'imported'].includes(value)) return { main: 'pending', sub: 'Waiting to be picked up' }
  return { main: 'running', sub: 'Work is in progress' }
}

function csvPrettyStatus(status) {
  const value = String(status || '').trim()
  if (!value) return '-'
  if (value === 'failure') return 'Failure'
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function csvProfileSummary(profileKey) {
  const [gender, age, skinColor] = String(profileKey || '').split(':')
  return [age, gender, skinColor].filter(Boolean).join(' ')
}

function formatLocalDateTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date)
}

function elapsedSeconds(startedAt, finishedAt, nowMs) {
  if (!startedAt) return 0
  const start = new Date(startedAt).getTime()
  if (Number.isNaN(start)) return 0
  const end = finishedAt ? new Date(finishedAt).getTime() : nowMs
  if (Number.isNaN(end)) return 0
  return Math.max(0, Math.round((end - start) / 1000))
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
  const counts = { pending: 0, running: 0, completed: 0, failure: 0 }
  ;(Array.isArray(items) ? items : []).forEach((item) => {
    const state = csvTaskProgressSummary(tasks, item.id)
    counts[state.mainStatus] += 1
  })
  return counts
}

function csvItemImages(item, tasks) {
  const images = []
  const seen = new Set()
  const addImage = (payload) => {
    const key = `${payload.id}:${payload.kind}`
    if (!payload.id || seen.has(key)) return
    seen.add(key)
    images.push(payload)
  }
  if (item?.base_regular_asset_id) {
    addImage({ id: item.base_regular_asset_id, label: 'Base regular', kind: 'regular' })
  }
  if (item?.base_white_bg_asset_id) {
    addImage({ id: item.base_white_bg_asset_id, label: 'Base white background', kind: 'white_bg' })
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
  if (stageName === 'stage2_draft') return 'Stage 2 Draft'
  if (stageName === 'stage3_upgraded') return 'Stage 3 Upgraded'
  if (stageName === 'stage4_white_bg') return 'Stage 4 White Background'
  if (stageName === 'stage4_variant_generate') return 'Character Variant Final'
  if (stageName === 'stage5_variant_white_bg') return 'Character Variant White Background'
  return stageName
}

const stagePriority = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage4_white_bg: 3,
  stage4_variant_generate: 4,
  stage5_variant_white_bg: 5,
}

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
  const [imageAspectRatio, setImageAspectRatio] = useState('1:1')
  const [imageResolution, setImageResolution] = useState('1K')
  const [selectedDetailTab, setSelectedDetailTab] = useState('overview')
  const [csvJobs, setCsvJobs] = useState([])
  const [selectedCsvJobId, setSelectedCsvJobId] = useState('')
  const [csvJobOverview, setCsvJobOverview] = useState(null)
  const [selectedCsvItemId, setSelectedCsvItemId] = useState('')
  const [selectedCsvStatusFilter, setSelectedCsvStatusFilter] = useState('')
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [pageVisible, setPageVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    return document.visibilityState !== 'hidden'
  })
  const selectedRunIdRef = useRef('')
  const runsRef = useRef([])
  const detailStateRef = useRef(null)
  const detailRef = useRef(null)

  useEffect(() => {
    selectedRunIdRef.current = selectedRunId
    setStoredRunId(selectedRunId)
  }, [selectedRunId])

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

  useEffect(() => {
    if (!pageVisible) return undefined
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [pageVisible])

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
  const csvJobItems = Array.isArray(csvJobOverview?.items) ? csvJobOverview.items : []
  const csvJobTasks = Array.isArray(csvJobOverview?.tasks) ? csvJobOverview.tasks : []
  const csvJobLiveCounts = useMemo(() => csvJobWordSummary(csvJobItems, csvJobTasks), [csvJobItems, csvJobTasks])
  const filteredCsvJobItems = useMemo(() => {
    if (!selectedCsvStatusFilter) return csvJobItems
    return csvJobItems.filter((item) => String(item.main_status || '').toLowerCase() === selectedCsvStatusFilter)
  }, [csvJobItems, selectedCsvStatusFilter])
  const selectedCsvItem = useMemo(
    () => filteredCsvJobItems.find((item) => item.id === selectedCsvItemId) || filteredCsvJobItems[0] || null,
    [filteredCsvJobItems, selectedCsvItemId]
  )
  const selectedCsvItemTasks = useMemo(
    () => csvJobTasks.filter((task) => task.csv_job_item_id === selectedCsvItem?.id),
    [csvJobTasks, selectedCsvItem?.id]
  )
  const selectedCsvItemProgress = selectedCsvItem || null
  const selectedCsvItemImages = useMemo(
    () => csvItemImages(selectedCsvItem, selectedCsvItemTasks),
    [selectedCsvItem, selectedCsvItemTasks]
  )
  const selectedCsvTaskDiagnostics = useMemo(
    () => csvTaskDiagnostics(csvJobTasks, selectedCsvItem?.id),
    [csvJobTasks, selectedCsvItem?.id]
  )

  async function loadRunDetail(runId, { isPolling = false, includeDebug = false } = {}) {
    if (!runId) return
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
      if (isPolling && (runsRef.current.length > 0 || detailStateRef.current?.run)) {
        return
      }
      setMessage(`Error loading runs: ${error.message}`)
    }
  }

  async function refreshCsvJobs({ isPolling = false } = {}) {
    try {
      const data = await listCsvJobs()
      setCsvJobs(data)
      if (!selectedCsvJobId && data.length > 0) {
        setSelectedCsvJobId(data[0].id)
      } else if (selectedCsvJobId && !data.some((job) => job.id === selectedCsvJobId)) {
        setSelectedCsvJobId(data[0]?.id || '')
        setCsvJobOverview(null)
      }
    } catch (error) {
      if (!isPolling) {
        setMessage(`Error loading CSV jobs: ${error.message}`)
      }
    }
  }

  async function loadCsvJobDetail(jobId, { isPolling = false } = {}) {
    if (!jobId) return
    try {
      const data = await getCsvJobOverview(jobId)
      setCsvJobOverview(data)
    } catch (error) {
      if (!isPolling) {
        setMessage(`Error loading CSV job detail: ${error.message}`)
      }
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
    refreshRuns()
    refreshCsvJobs()
    const timer = setInterval(() => {
      if (!pageVisible) return
      if (!shouldPollRuns(runsRef.current)) return
      refreshRuns({ isPolling: true })
    }, RUNS_POLL_MS)
    return () => clearInterval(timer)
  }, [query, pageVisible])

  useEffect(() => {
    refreshCsvJobs()
    const timer = setInterval(() => {
      if (!pageVisible) return
      if (!csvJobs.some((job) => !isTerminalCsvJobStatus(job.status))) return
      refreshCsvJobs({ isPolling: true })
    }, RUNS_POLL_MS)
    return () => clearInterval(timer)
  }, [pageVisible, csvJobPollKey])

  useEffect(() => {
    if (!selectedCsvJobId || !pageVisible) return
    loadCsvJobDetail(selectedCsvJobId, { isPolling: true })
  }, [selectedCsvJobId, csvJobPollKey, pageVisible])

  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null)
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
  }, [selectedRunId, selectedDetailTab, pageVisible, detail?.run?.status])

  useEffect(() => {
    if (!selectedCsvJobId) {
      setCsvJobOverview(null)
      setSelectedCsvItemId('')
      setSelectedCsvStatusFilter('')
      return undefined
    }
    loadCsvJobDetail(selectedCsvJobId)
    const timer = setInterval(() => {
      if (!pageVisible) return
      if (!selectedCsvJobId) return
      if (isTerminalCsvJobStatus(csvJobOverview?.job?.status)) return
      loadCsvJobDetail(selectedCsvJobId, { isPolling: true })
    }, DETAIL_POLL_WAITING_MS)
    return () => clearInterval(timer)
  }, [selectedCsvJobId, pageVisible, csvJobOverview?.job?.status])

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
    if (!pageVisible) return undefined
    const handleFocus = () => {
      refreshRuns({ isPolling: true })
      if (selectedRunIdRef.current) {
        const includeDebug = selectedDetailTab === 'debug'
        loadRunDetail(selectedRunIdRef.current, { isPolling: true, includeDebug })
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
              <span>{showingCsvWords ? csvJobItems.length : runs.length} total</span>
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
                      <th>Status</th>
                      <th>Progress</th>
                      <th>Current step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredCsvJobItems.map((item) => (
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
                        <td>
                          <div className="status-stack">
                            <strong>{csvPrettyStatus(item.main_status)}</strong>
                            <span>{item.sub_status || '-'}</span>
                          </div>
                        </td>
                        <td>{item.progress?.completed || 0}/{item.progress?.total || 0}</td>
                        <td>{item.current_step || '-'}</td>
                      </tr>
                    ))}
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
                        <td>{run.quality_score ?? '-'}</td>
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
              <p className="runs-floor-copy">CSV DAG history and the selected job summary live here, separately from legacy runs.</p>
            </div>
            <div className="runs-floor-summary">
              <span>{csvJobs.length} total</span>
              <button type="button" onClick={() => refreshCsvJobs()} className="button-secondary">Refresh</button>
              <button type="button" onClick={onClearCsvHistory} className="button-secondary">Clear CSV History</button>
            </div>
          </div>

          <div className="csv-section-block">
            <div className="csv-section-head">
              <h3>CSV Job History</h3>
              <p>Most recently created jobs are shown first.</p>
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
                    <th>Retry</th>
                    <th>Cancel</th>
                    <th>Export</th>
                  </tr>
                </thead>
                <tbody>
                  {csvJobs.map((job) => (
                    <tr
                      key={job.id}
                      className={job.id === selectedCsvJobId ? 'selected-row' : 'clickable-row'}
                      onClick={() => {
                        setCsvJobOverview(null)
                        setSelectedCsvJobId(job.id)
                        setSelectedCsvItemId('')
                        setSelectedCsvStatusFilter('')
                      }}
                    >
                      <td>{job.batch_id}</td>
                      <td>
                        <div className="status-stack">
                          <strong>{csvPrettyStatus(csvJobMainStatus(job.status).main)}</strong>
                          <span>{csvJobMainStatus(job.status).sub}</span>
                        </div>
                      </td>
                      <td>{job.total_row_count}</td>
                      <td>{job.started_at ? `${elapsedSeconds(job.started_at, job.finished_at, nowMs)}s` : '-'}</td>
                      <td>{formatLocalDateTime(job.started_at)}</td>
                      <td>
                        <button
                          onClick={(event) => {
                            event.stopPropagation()
                            onRetryCsvJob(job.id)
                          }}
                          disabled={job.status !== 'failed'}
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
                  <p>{csvPrettyStatus(csvJobMainStatus(csvJobOverview.job.status).main)}</p>
                  <small>{csvJobMainStatus(csvJobOverview.job.status).sub}</small>
                </div>
                <div>
                  <strong>Timer</strong>
                  <p>
                    {csvJobOverview.job.started_at
                      ? `${elapsedSeconds(csvJobOverview.job.started_at, csvJobOverview.job.finished_at, nowMs)}s`
                      : '-'}
                  </p>
                </div>
                <div>
                  <strong>Rows</strong>
                  <p>{csvJobOverview.job.total_row_count}</p>
                </div>
                <div>
                  <strong>Started</strong>
                  <p>{formatLocalDateTime(csvJobOverview.job.started_at)}</p>
                </div>
              </div>
              <div className="csv-job-live-strip">
                {[
                  ['pending', csvJobLiveCounts.pending],
                  ['running', csvJobLiveCounts.running],
                  ['completed', csvJobLiveCounts.completed],
                  ['failure', csvJobLiveCounts.failure],
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
              <p className="config-help-text">
                Click a status chip to filter the word list on the first floor.
              </p>
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
                    <span>{selectedCsvItemProgress?.sub_status || '-'}</span>
                  </div>
                </div>
                <div className="csv-word-meta-grid">
                  <div>
                    <strong>Progress</strong>
                    <p>{selectedCsvItemProgress ? `${selectedCsvItemProgress.progress?.completed || 0}/${selectedCsvItemProgress.progress?.total || 0} steps finished` : '-'}</p>
                  </div>
                  <div>
                    <strong>Current step</strong>
                    <p>{selectedCsvItem.current_step || '-'}</p>
                  </div>
                  <div>
                    <strong>Why it may be waiting</strong>
                    <p>{selectedCsvItem.blocking_reason || selectedCsvItem.sub_status || '-'}</p>
                  </div>
                  <div>
                    <strong>Shadow run</strong>
                    <p>{selectedCsvItem.shadow_run_id || '-'}</p>
                  </div>
                  <div>
                    <strong>Error</strong>
                    <p>{selectedCsvItem.error_detail || '-'}</p>
                  </div>
                </div>
                <div className="csv-word-image-grid">
                  {selectedCsvItemImages.length ? (
                    selectedCsvItemImages.map((image) => (
                      <article key={`${selectedCsvItem.id}:${image.id}:${image.label}`} className="csv-word-image-card">
                        <img src={buildAssetContentUrl(image.id)} alt={image.label} loading="lazy" decoding="async" />
                        <div className="csv-word-image-meta">
                          <strong>{image.label}</strong>
                          <a href={buildAssetContentUrl(image.id)} target="_blank" rel="noreferrer">
                            Open image
                          </a>
                        </div>
                      </article>
                    ))
                  ) : (
                    <p>No images have been created for this word yet.</p>
                  )}
                </div>
                <div className="table-wrap runs-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Step</th>
                        <th>Profile</th>
                        <th>Status</th>
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
