import React, { useEffect, useMemo, useState } from 'react'
import RunNodeDetailCard from './RunNodeDetailCard'
import WorkflowCanvas from './WorkflowCanvas'
import DeferredAssetImage from './DeferredAssetImage'
import { buildRunDiagram, getAvailableAttempts } from '../lib/runDiagram'
import { buildAssetContentUrl } from '../lib/api'

function runDetailStateKey(runId) {
  return `aac:run-detail:${runId || 'unknown'}`
}

function loadRunDetailState(runId) {
  try {
    if (typeof window === 'undefined' || !runId) return null
    const raw = window.sessionStorage.getItem(runDetailStateKey(runId))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch (_error) {
    return null
  }
}

function saveRunDetailState(runId, value) {
  try {
    if (typeof window === 'undefined' || !runId) return
    window.sessionStorage.setItem(runDetailStateKey(runId), JSON.stringify(value))
  } catch (_error) {
    // Ignore storage failures and keep UI usable.
  }
}

function humanStatus(status) {
  const value = String(status || '').toLowerCase()
  if (value === 'ok') return 'Done'
  if (value === 'running') return 'In progress'
  if (value === 'queued') return 'Waiting'
  if (value === 'skipped') return 'Skipped'
  if (value === 'error') return 'Failed'
  return status || '-'
}

function prettyStage(stage) {
  if (stage === 'queued') return 'Waiting to start'
  if (stage === 'stage1_prompt') return 'Stage 1: First prompt'
  if (stage === 'stage2_draft') return 'Stage 2: Draft image'
  if (stage === 'stage3_upgrade') return 'Stage 3: Improve + generate'
  if (stage === 'quality_gate') return 'Quality check'
  if (stage === 'stage4_background') return 'Stage 4: White background'
  if (stage === 'stage4_variant_generate') return 'Stage 5-8: Variant finals'
  if (stage === 'stage5_variant_white_bg') return 'Stage 9: Variant white background'
  if (stage === 'completed') return 'Completed'
  return stage || '-'
}

function prettyRunStatus(status) {
  if (status === 'completed_pass') return 'Completed (Pass)'
  if (status === 'completed_fail_threshold') return 'Completed (Below threshold)'
  if (status === 'failed_technical') return 'Failed (Technical)'
  if (status === 'running') return 'Running'
  if (status === 'queued') return 'Queued'
  if (status === 'retry_queued') return 'Queued for retry'
  return status || '-'
}

function stage4StatusText({ run, selectedSummary, winnerStage4Attempt }) {
  if (typeof winnerStage4Attempt === 'number' && winnerStage4Attempt > 0) {
    return `Background removal completed on winner attempt ${winnerStage4Attempt}.`
  }
  const stage4 = selectedSummary?.stage4Status
  if (stage4 === 'ok') {
    return `Background removal completed for this attempt.`
  }
  if (run.status === 'running' && run.current_stage !== 'stage4_background' && run.current_stage !== 'completed') {
    return `Background removal has not started yet. It runs after scoring selects the winner attempt.`
  }
  if (run.status === 'failed_technical' && run.current_stage === 'stage4_background') {
    return `Background removal failed technically on this run.`
  }
  if (stage4 === 'skipped' || stage4 === 'queued') {
    return `Background removal is waiting for winner selection.`
  }
  if (stage4 === 'error') {
    return `Background removal failed for this attempt.`
  }
  return `Background removal runs after the winner attempt is selected.`
}

const assetStageOrder = {
  stage2_draft: 1,
  stage3_upgraded: 2,
  stage4_white_bg: 3,
  stage4_variant_generate: 4,
  stage5_variant_white_bg: 5,
}

const IMAGE_FILTER = {
  DRAFT: 'draft',
  ATTEMPT: 'attempt',
  REMOVE_BACKGROUND: 'remove_background',
}

const DETAIL_TABS = {
  OVERVIEW: 'overview',
  IMAGES: 'images',
  PROCESS: 'process',
  SETTINGS: 'settings',
  DEBUG: 'debug',
}

const IMAGE_ASPECT_RATIO_OPTIONS = ['1:1', '2:3', '3:2', '3:4', '4:3', '9:16', '16:9', '21:9']
const IMAGE_RESOLUTION_OPTIONS = ['1K', '2K', '4K']

function stageImageLabel(stageName) {
  if (stageName === 'stage2_draft') return 'Stage 2 Draft'
  if (stageName === 'stage3_upgraded') return 'Stage 3 Upgraded'
  if (stageName === 'stage4_white_bg') return 'Stage 4 White Background'
  if (stageName === 'stage4_variant_generate') return 'Character Variant Final'
  if (stageName === 'stage5_variant_white_bg') return 'Character Variant White Background'
  return stageName || 'Image'
}

function attemptLabel(asset) {
  if (asset.stage_name === 'stage2_draft') return 'Base draft'
  if (typeof asset.attempt === 'number' && asset.attempt > 0) return `Attempt ${asset.attempt}`
  return 'Attempt -'
}

function defaultAttempt(detail, attempts) {
  const current = Number(detail?.run?.optimization_attempt || 0)
  if (current > 0 && attempts.includes(current)) return current
  return attempts[attempts.length - 1] || 1
}

function currentNodeId(detail) {
  const stage = String(detail?.run?.current_stage || '')
  if (stage === 'stage1_prompt') return 'stage1_prompt'
  if (stage === 'stage2_draft') return 'stage2_draft'
  if (stage === 'stage3_upgrade') return 'stage3_generate'
  if (stage === 'quality_gate') return 'quality_gate'
  if (stage === 'stage4_background') return 'stage4_background'
  if (stage === 'stage4_variant_generate') return 'stage4_variant_generate'
  if (stage === 'stage5_variant_white_bg') return 'stage5_variant_white_bg'
  if (stage === 'completed') return 'completed'
  return ''
}

function statusTone(status) {
  const value = String(status || '').toLowerCase()
  if (value.includes('completed_pass')) return 'ok'
  if (value.includes('running')) return 'running'
  if (value.includes('queued')) return 'queued'
  if (value.includes('fail')) return 'error'
  return 'queued'
}

function compactDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function formatUsd(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  return `$${Number(value).toFixed(4)}`
}

function humanEstimateBasis(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'official token pricing') return 'Official token pricing'
  if (normalized === 'provider image-price estimate') return 'Image price estimate'
  return value || '-'
}

function humanAttemptLabel(value) {
  const attempt = Number(value || 0)
  if (attempt <= 0) return 'Base'
  return `Attempt ${attempt}`
}

function safeArray(value) {
  return Array.isArray(value) ? value.filter(Boolean) : []
}

function safeObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
}

function firstNonEmptyObject(...values) {
  for (const value of values) {
    const candidate = safeObject(value)
    if (Object.keys(candidate).length > 0) {
      return candidate
    }
  }
  return {}
}

function profileLabel(item) {
  const profile = safeObject(item?.profile)
  const parts = [humanGender(profile.gender), humanAge(profile.age), humanSkinColor(profile.skin_color)].filter(Boolean)
  const branchRole = item?.branch_role ? ` (${item.branch_role})` : ''
  return `${parts.join(' / ') || item?.profile_description || 'profile'}${branchRole}`
}

function humanGender(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'female') return 'Female'
  if (normalized === 'male') return 'Male'
  return value || ''
}

function humanAge(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'toddler') return 'Toddler (2-4)'
  if (normalized === 'kid') return 'Kid (5-9)'
  if (normalized === 'tween') return 'Tween (10-14)'
  if (normalized === 'teenager') return 'Teenager (15-18)'
  return value || ''
}

function humanSkinColor(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'white') return 'White'
  if (normalized === 'black') return 'Black'
  if (normalized === 'asian') return 'Asian'
  if (normalized === 'brown') return 'Brown (Indian origin)'
  return value || ''
}

function variantGroupLabel(profile) {
  const safeProfile = safeObject(profile)
  return [humanGender(safeProfile.gender), humanAge(safeProfile.age), humanSkinColor(safeProfile.skin_color)]
    .filter(Boolean)
    .join(' | ')
}

function variantProfileIndex(stages) {
  const index = new Map()
  safeArray(stages).forEach((stage) => {
    const response = safeObject(stage?.response_json)
    safeArray(response.variants).forEach((item) => {
      const asset = safeObject(item?.asset)
      if (!asset.id) return
      index.set(asset.id, {
        profile: safeObject(item?.profile),
        profile_description: item?.profile_description || '',
        branch_role: item?.branch_role || '',
        source_profile: safeObject(item?.source_profile),
      })
    })
  })
  return index
}

function inferredProfileFromFileName(fileName) {
  const text = String(fileName || '')
  const match = text.match(/_(male|female)_(toddler|kid|tween|teenager)_(white|black|asian|brown)_attempt_/i)
  if (!match) return {}
  return {
    gender: String(match[1] || '').toLowerCase(),
    age: String(match[2] || '').toLowerCase(),
    skin_color: String(match[3] || '').toLowerCase(),
  }
}

function assetProfileMeta(asset, profileIndex) {
  const indexed = profileIndex.get(asset.id)
  if (indexed && Object.keys(safeObject(indexed.profile)).length > 0) {
    return indexed
  }
  const inferredProfile = inferredProfileFromFileName(asset.file_name)
  if (Object.keys(inferredProfile).length === 0) {
    return null
  }
  return {
    profile: inferredProfile,
    profile_description: '',
    branch_role: '',
    source_profile: {},
  }
}

function groupAssetsForQualityReview(assets, profileIndex) {
  const baseAssets = []
  const groupedVariantAssets = new Map()

  safeArray(assets).forEach((asset) => {
    if (!['stage4_variant_generate', 'stage5_variant_white_bg'].includes(asset.stage_name)) {
      baseAssets.push(asset)
      return
    }
    const profileMeta = assetProfileMeta(asset, profileIndex)
    if (!profileMeta) {
      baseAssets.push(asset)
      return
    }
    const groupKey = [
      String(profileMeta.profile.gender || ''),
      String(profileMeta.profile.age || ''),
      String(profileMeta.profile.skin_color || ''),
    ].join('|')
    const existing = groupedVariantAssets.get(groupKey) || {
      key: groupKey,
      label: variantGroupLabel(profileMeta.profile),
      profile: profileMeta.profile,
      assets: [],
    }
    existing.assets.push(asset)
    groupedVariantAssets.set(groupKey, existing)
  })

  const genderOrder = { male: 1, female: 2 }
  const ageOrder = { toddler: 1, kid: 2, tween: 3, teenager: 4 }
  const skinOrder = { white: 1, black: 2, asian: 3, brown: 4 }

  const variantGroups = [...groupedVariantAssets.values()].sort((left, right) => {
    const leftProfile = safeObject(left.profile)
    const rightProfile = safeObject(right.profile)
    const leftGender = genderOrder[String(leftProfile.gender || '').toLowerCase()] || 99
    const rightGender = genderOrder[String(rightProfile.gender || '').toLowerCase()] || 99
    if (leftGender !== rightGender) return leftGender - rightGender
    const leftAge = ageOrder[String(leftProfile.age || '').toLowerCase()] || 99
    const rightAge = ageOrder[String(rightProfile.age || '').toLowerCase()] || 99
    if (leftAge !== rightAge) return leftAge - rightAge
    const leftSkin = skinOrder[String(leftProfile.skin_color || '').toLowerCase()] || 99
    const rightSkin = skinOrder[String(rightProfile.skin_color || '').toLowerCase()] || 99
    if (leftSkin !== rightSkin) return leftSkin - rightSkin
    return left.label.localeCompare(right.label)
  })

  return { baseAssets, variantGroups }
}

function createdImageRows(assets, profileIndex) {
  return safeArray(assets).map((asset) => {
    const profileMeta = assetProfileMeta(asset, profileIndex)
    return {
      id: asset.id,
      stageLabel: stageImageLabel(asset.stage_name),
      attemptLabel: attemptLabel(asset),
      profileLabel: profileMeta ? variantGroupLabel(profileMeta.profile) : '',
      fileName: asset.file_name || '-',
      modelName: asset.model_name || '-',
      dimensions:
        asset.width && asset.height
          ? `${asset.width} x ${asset.height}`
          : '-',
      createdAt: compactDate(asset.created_at),
    }
  })
}

const MATRIX_GENDERS = ['male', 'female']
const MATRIX_AGES = ['toddler', 'kid', 'tween', 'teenager']
const MATRIX_SKINS = ['white', 'black', 'asian', 'brown']

function buildProfileCoverageData(stages, selectedAttempt, filteredAssets, profileIndex) {
  const profiles = new Map()
  const stageRows = safeArray(stages).filter(
    (stage) =>
      ['stage4_variant_generate', 'stage5_variant_white_bg'].includes(stage.stage_name) &&
      Number(stage.attempt || 0) === Number(selectedAttempt || 0),
  )

  stageRows.forEach((stage) => {
    const request = safeObject(stage.request_json)
    const branchPlan = safeObject(request.branch_plan)
    const baseProfile = safeObject(branchPlan.base_profile)
    if (Object.keys(baseProfile).length > 0) {
      profiles.set(profileKeyString(baseProfile), baseProfile)
    }
    safeArray(branchPlan.planned_profiles).forEach((profile) => {
      const safeProfile = safeObject(profile)
      if (Object.keys(safeProfile).length === 0) return
      profiles.set(profileKeyString(safeProfile), safeProfile)
    })
    safeArray(request.profiles).forEach((profile) => {
      const safeProfile = safeObject(profile)
      if (Object.keys(safeProfile).length === 0) return
      profiles.set(profileKeyString(safeProfile), safeProfile)
    })
  })

  safeArray(filteredAssets).forEach((asset) => {
    if (asset.stage_name === 'stage3_upgraded' || asset.stage_name === 'stage4_white_bg') {
      const baseProfile = { gender: 'male', age: 'kid', skin_color: 'white' }
      profiles.set(profileKeyString(baseProfile), baseProfile)
      return
    }
    const meta = assetProfileMeta(asset, profileIndex)
    if (!meta) return
    profiles.set(profileKeyString(meta.profile), safeObject(meta.profile))
  })

  const statusByProfile = new Map()
  profiles.forEach((profile, key) => {
    statusByProfile.set(key, {
      profile,
      regular: false,
      white: false,
      regularAsset: null,
      whiteAsset: null,
    })
  })

  safeArray(filteredAssets).forEach((asset) => {
    if (asset.stage_name === 'stage3_upgraded') {
      const key = profileKeyString({ gender: 'male', age: 'kid', skin_color: 'white' })
      const row = statusByProfile.get(key)
      if (row) {
        row.regular = true
        row.regularAsset = asset
      }
      return
    }
    if (asset.stage_name === 'stage4_white_bg') {
      const key = profileKeyString({ gender: 'male', age: 'kid', skin_color: 'white' })
      const row = statusByProfile.get(key)
      if (row) {
        row.white = true
        row.whiteAsset = asset
      }
      return
    }
    const meta = assetProfileMeta(asset, profileIndex)
    if (!meta) return
    const key = profileKeyString(meta.profile)
    const row = statusByProfile.get(key)
    if (!row) return
    if (asset.stage_name === 'stage4_variant_generate') {
      row.regular = true
      row.regularAsset = asset
    }
    if (asset.stage_name === 'stage5_variant_white_bg') {
      row.white = true
      row.whiteAsset = asset
    }
  })

  const sections = MATRIX_GENDERS.map((gender) => {
    const rowsForGender = [...statusByProfile.values()].filter(
      (item) => String(safeObject(item.profile).gender || '').toLowerCase() === gender,
    )
    if (rowsForGender.length === 0) return null
    const ageSet = new Set(rowsForGender.map((item) => String(safeObject(item.profile).age || '').toLowerCase()))
    const skinSet = new Set(rowsForGender.map((item) => String(safeObject(item.profile).skin_color || '').toLowerCase()))
    return {
      gender,
      ages: MATRIX_AGES.filter((age) => ageSet.has(age)),
      skins: MATRIX_SKINS.filter((skin) => skinSet.has(skin)),
    }
  }).filter(Boolean)

  return {
    matrix: statusByProfile,
    sections,
  }
}

function profileKeyString(profile) {
  const safeProfile = safeObject(profile)
  return [
    String(safeProfile.gender || ''),
    String(safeProfile.age || ''),
    String(safeProfile.skin_color || ''),
  ].join('|')
}

function matrixCellState(matrix, gender, age, skin) {
  return matrix.get([gender, age, skin].join('|')) || {
    regular: false,
    white: false,
    regularAsset: null,
    whiteAsset: null,
  }
}

function variantPanelData(node) {
  if (!node) return null
  const request = safeObject(node.requestJson)
  const response = safeObject(node.responseJson)
  const progress = safeObject(response.progress)
  const planned = safeArray(request.profiles)
  const submitted = safeArray(response.submitted_profiles)
  const completed = safeArray(response.completed_profiles)
  const failed = safeArray(response.failed_profiles)
  const submittedKeys = new Set(submitted.map((item) => JSON.stringify(safeObject(item.profile))))
  const completedKeys = new Set(completed.map((item) => JSON.stringify(safeObject(item.profile))))
  const failedKeys = new Set(failed.map((item) => JSON.stringify(safeObject(item.profile))))
  const inFlight = submitted.filter((item) => {
    const key = JSON.stringify(safeObject(item.profile))
    return !completedKeys.has(key) && !failedKeys.has(key)
  })
  const waiting = planned
    .filter((profile) => {
      const key = JSON.stringify(safeObject(profile))
      return !submittedKeys.has(key) && !completedKeys.has(key) && !failedKeys.has(key)
    })
    .map((profile) => ({ profile, branch_role: 'planned' }))

  return {
    nodeId: node.id,
    title: node.id === 'stage5_variant_white_bg' ? 'White-background variant progress' : 'Final-image variant progress',
    progress,
    completed,
    failed,
    inFlight,
    waiting,
    hasActivity:
      completed.length > 0 ||
      failed.length > 0 ||
      inFlight.length > 0 ||
      waiting.length > 0 ||
      Number(progress.completed_count || 0) > 0 ||
      Number(progress.in_flight_count || 0) > 0 ||
      Number(progress.remaining_count || 0) > 0 ||
      Number(progress.failed_count || 0) > 0,
  }
}

function runNarrative(detail, selectedSummary, threshold) {
  const run = detail.run || {}
  const score = selectedSummary?.score
  if (run.status === 'queued' || run.status === 'retry_queued') {
    return 'The run is waiting in the queue. No provider call is active yet.'
  }
  if (run.status === 'running') {
    return `The run is active. It is currently in ${prettyStage(run.current_stage)} and working on attempt ${Math.max(1, Number(run.optimization_attempt || 1))}.`
  }
  if (run.status === 'completed_pass') {
    return `The run completed successfully. Attempt ${run.optimization_attempt} met the quality threshold of ${threshold} and the winner image moved to white background processing.`
  }
  if (run.status === 'completed_fail_threshold') {
    return `The run completed without reaching the quality threshold of ${threshold}. The best scored attempt stayed below the acceptance rule.`
  }
  if (run.status === 'failed_technical') {
    return `The run stopped because of a technical failure during ${prettyStage(run.current_stage)}.`
  }
  if (score != null) {
    return `The latest visible score is ${score} against a threshold of ${threshold}.`
  }
  return 'Run details are available, but the final outcome is not complete yet.'
}

function renderOverviewSection({
  detail,
  selectedSummary,
  currentAttempt,
  maxAttempts,
  threshold,
  finalAsset,
  winnerStage4Asset,
  imageCreationFailed,
  scoreTooLow,
  selectedPromptEngineerMode,
  selectedResponsesModel,
  selectedVectorStoreId,
  usesGeminiPromptEngineer,
  selectedVisualStyleName,
  selectedVisualStyleId,
  resolvedRenderStyleMode,
  resolvedRenderStyleName,
  resolvedNeedPerson,
  resolvedPersonReason,
  stage1NeedPerson,
  critiqueNeedPerson,
  critiquePresenceProblem,
  critiqueReasoning,
  critiqueRecommendation,
  attempts,
  setSelectedAttempt,
  setImageFilter,
  selectedAttempt,
}) {
  const run = detail.run
  const latestScore = selectedSummary?.score ?? run.quality_score ?? null
  const estimatedTotalCost = Number(run.estimated_total_cost_usd || 0)
  const estimatedCostPerImage = run.estimated_cost_per_image_usd
  const imageCount = Number(run.image_count || 0)
  const meterPercent = Math.max(3, Math.min(100, Math.round((estimatedTotalCost / 1.0) * 100)))
  const finalImageUrl = buildAssetContentUrl(winnerStage4Asset || finalAsset)
  const costSummary = safeObject(detail.cost_summary)
  const costBreakdown = safeArray(costSummary.stage_costs)
  const estimateNote = String(costSummary.estimate_note || '').trim()

  return (
    <div className="run-detail-section-grid">
      <section className="run-snapshot-card">
        <div className="run-snapshot-head">
          <div>
            <p className="detail-eyebrow">Selected run</p>
            <h3>{run.word || '-'}</h3>
            <p className="run-snapshot-meta">
              {run.part_of_sentence || 'No POS'}
              {run.category ? ` | ${run.category}` : ' | No category'}
            </p>
          </div>
          <span className={`status-pill status-${statusTone(run.status)}`}>{prettyRunStatus(run.status)}</span>
        </div>
        <p className="run-snapshot-summary">{runNarrative(detail, selectedSummary, threshold)}</p>
        <div className="run-snapshot-metrics">
          <div className="snapshot-metric">
            <span>Current step</span>
            <strong>{prettyStage(run.current_stage)}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Attempts</span>
            <strong>{currentAttempt > 0 ? currentAttempt : 1} / {maxAttempts}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Score</span>
            <strong>{latestScore ?? '-'}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Threshold</span>
            <strong>{threshold}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Updated</span>
            <strong>{compactDate(run.updated_at)}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Run id</span>
            <strong>{run.id}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Estimated cost</span>
            <strong>{formatUsd(estimatedTotalCost)}</strong>
          </div>
          <div className="snapshot-metric">
            <span>Avg / image</span>
            <strong>{formatUsd(estimatedCostPerImage)}</strong>
          </div>
        </div>
      </section>

      <section className="run-kpi-grid">
        <article className="run-kpi-card run-cost-card">
          <p className="detail-eyebrow">Job price meter</p>
          <h4>{formatUsd(estimatedTotalCost)}</h4>
          <p>{imageCount} image{imageCount === 1 ? '' : 's'} in this run | avg {formatUsd(estimatedCostPerImage)} each</p>
          <div className="cost-meter" aria-label="Estimated job cost">
            <div className="cost-meter-fill" style={{ width: `${meterPercent}%` }} />
          </div>
          <p className="cost-meter-caption">{estimateNote || 'Estimate only. Based on stored token usage and mapped provider pricing, not provider invoice totals.'}</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Prompt engineer</p>
          <h4>{selectedPromptEngineerMode === 'responses_api' ? (usesGeminiPromptEngineer ? 'Direct model API' : 'Responses API') : 'OpenAI Assistant'}</h4>
          {selectedPromptEngineerMode === 'responses_api' ? (
            <p>{selectedResponsesModel || '-'}{!usesGeminiPromptEngineer ? ` | ${selectedVectorStoreId || 'No vector store'}` : ''}</p>
          ) : (
            <p>Assistant-based prompt generation</p>
          )}
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Resolved render style</p>
          <h4>{resolvedRenderStyleMode || '-'}</h4>
          <p>{resolvedRenderStyleName || `${selectedVisualStyleName || '-'}${selectedVisualStyleId ? ` (${selectedVisualStyleId})` : ''}`}</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Final image</p>
          <h4>{winnerStage4Asset ? 'Ready' : 'Not ready yet'}</h4>
          <p>{winnerStage4Asset?.file_name || 'No white-background winner image yet'}</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Person needed? (Stage 3.1)</p>
          <h4>{resolvedNeedPerson ? `Person ${resolvedNeedPerson === 'yes' ? 'required' : 'not needed'}` : 'Not resolved yet'}</h4>
          <p>{resolvedPersonReason || 'No critique correction recorded yet.'}</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Decision flow</p>
          <h4>Stage 1: {stage1NeedPerson ? (stage1NeedPerson === 'yes' ? 'person needed' : 'no person needed') : '-'}</h4>
          <p>
            Stage 3.1: {critiqueNeedPerson ? (critiqueNeedPerson === 'yes' ? 'person needed' : 'no person needed') : 'not decided yet'}
            {critiquePresenceProblem ? ` | ${critiquePresenceProblem}` : ''}
          </p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Applied to next step</p>
          <h4>{resolvedNeedPerson ? 'Yes' : 'Pending'}</h4>
          <p>Stage 3.2 prompt upgrade and Stage 3.3 image generation use the Stage 3.1 resolved person decision.</p>
        </article>
        <article className="run-kpi-card">
          <p className="detail-eyebrow">Attention</p>
          <h4>{imageCreationFailed || scoreTooLow ? 'Needs review' : 'No alert'}</h4>
          <p>
            {imageCreationFailed
              ? 'A provider stage failed in this run.'
              : scoreTooLow
                ? `Visible attempt score is below ${threshold}.`
                : 'No failure or threshold warning is active.'}
          </p>
        </article>
      </section>

      {(imageCreationFailed || scoreTooLow) ? (
        <div className="run-flag-row">
          {imageCreationFailed ? <span className="run-flag run-flag-error">Flag: image creation failed in this run</span> : null}
          {scoreTooLow ? <span className="run-flag run-flag-warn">Flag: score is below threshold ({threshold})</span> : null}
        </div>
      ) : null}

      {(stage1NeedPerson || critiqueNeedPerson || critiqueRecommendation) ? (
        <section className="run-overview-card">
          <div className="section-head-row">
            <div>
              <h4>Stage 3.1 Person Decision</h4>
              <p>This is the clearest place to see whether the upgraded image should include a person.</p>
            </div>
          </div>
          <div className="run-help-card">
            <p><strong>Stage 1 initial guess:</strong> {stage1NeedPerson ? (stage1NeedPerson === 'yes' ? 'person needed' : 'no person needed') : 'not available'}</p>
            <p><strong>Stage 3.1 critique:</strong> {critiqueNeedPerson ? (critiqueNeedPerson === 'yes' ? 'person needed' : 'no person needed') : 'not available'}</p>
            <p><strong>Presence issue found:</strong> {critiquePresenceProblem || 'none recorded'}</p>
            {critiqueReasoning ? <p><strong>Why:</strong> {critiqueReasoning}</p> : null}
            <p><strong>Result used in Stage 3.2/3.3:</strong> {resolvedNeedPerson ? (resolvedNeedPerson === 'yes' ? 'include a person' : 'do not include a person') : 'pending'}</p>
            {critiqueRecommendation ? <p><strong>Critique recommendation:</strong> {critiqueRecommendation}</p> : null}
          </div>
        </section>
      ) : null}

      <section className="run-overview-card">
        <div className="section-head-row">
          <div>
            <h4>Cost Breakdown</h4>
            <p>Estimated cost for each provider step in this run.</p>
          </div>
        </div>
        {costBreakdown.length > 0 ? (
          <div className="table-wrap">
            <table className="cost-breakdown-table">
              <thead>
                <tr>
                  <th>Step</th>
                  <th>Attempt</th>
                  <th>Units</th>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Basis</th>
                  <th>Estimated cost</th>
                </tr>
              </thead>
              <tbody>
                {costBreakdown.map((entry, index) => (
                  <tr key={`${entry.stage_name || 'stage'}-${entry.attempt || 0}-${index}`}>
                    <td>{entry.stage_label || entry.stage_name || '-'}</td>
                    <td>{humanAttemptLabel(entry.attempt)}</td>
                    <td>{Number(entry.unit_count || 1)}</td>
                    <td>{entry.provider || '-'}</td>
                    <td>{entry.model || '-'}</td>
                    <td>{humanEstimateBasis(entry.estimate_basis)}</td>
                    <td>{formatUsd(entry.estimated_cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No cost rows were recorded for this run yet.</p>
        )}
      </section>

      <section className="run-overview-split">
        <div className="run-overview-card">
          <div className="section-head-row">
            <div>
              <h4>Attempts</h4>
              <p>Choose an attempt to inspect its image, score, and process trace.</p>
            </div>
          </div>
          <div className="attempt-chip-row">
            {attempts.map((attempt) => (
              <button
                key={attempt}
                className={attempt === selectedAttempt ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => {
                  setSelectedAttempt(attempt)
                  setImageFilter(IMAGE_FILTER.ATTEMPT)
                }}
              >
                Attempt {attempt}
              </button>
            ))}
          </div>
          <div className="attempt-summary-row compact">
            {detail.diagramAttemptSummaries?.map((summary) => (
              <div key={summary.attempt} className={summary.attempt === selectedAttempt ? 'attempt-summary active' : 'attempt-summary'}>
                <p><strong>Attempt {summary.attempt}</strong></p>
                <p>Improve: {humanStatus(summary.stage3Status)}</p>
                <p>Score: {summary.score ?? '-'}</p>
                <p>Background: {humanStatus(summary.stage4Status)}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="run-overview-card">
          <div className="section-head-row">
            <div>
              <h4>Outcome Preview</h4>
              <p>The quickest answer to “what did the run produce?”</p>
            </div>
          </div>
          {finalImageUrl ? (
            <div className="run-hero-image-card">
              <img className="asset-image" src={finalImageUrl} alt="Final run output" loading="lazy" decoding="async" />
              <div className="asset-meta">
                <p><strong>{winnerStage4Asset ? 'Winner white-background image' : 'Latest visible image'}</strong></p>
                <p>{winnerStage4Asset?.file_name || finalAsset?.file_name || '-'}</p>
                {finalImageUrl ? (
                  <a href={finalImageUrl} target="_blank" rel="noreferrer">
                    Open Full Image
                  </a>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="empty-state-card">
              <p>No final image yet.</p>
              <p>If the run is still active, the image may appear after scoring chooses a winner and Stage 4 finishes.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}

export default function RunExecutionDiagram({
  detail,
  assistantName = '',
  promptEngineerConfig,
  onSavePromptEngineerConfig,
  onActiveTabChange,
}) {
  const attempts = useMemo(() => getAvailableAttempts(detail), [detail?.run?.id, detail?.run?.updated_at, detail])
  const initialState = loadRunDetailState(detail?.run?.id) || {}
  const [selectedAttempt, setSelectedAttempt] = useState(initialState.selectedAttempt || defaultAttempt(detail, attempts))
  const [imageFilter, setImageFilter] = useState(initialState.imageFilter || IMAGE_FILTER.ATTEMPT)
  const diagram = useMemo(() => buildRunDiagram(detail, selectedAttempt), [detail, selectedAttempt])
  const [selectedNodeId, setSelectedNodeId] = useState(initialState.selectedNodeId || currentNodeId(detail) || 'stage3_generate')
  const [showRunJson, setShowRunJson] = useState(false)
  const [showExecutionLog, setShowExecutionLog] = useState(false)
  const [showDetailedExecutionLog, setShowDetailedExecutionLog] = useState(false)
  const [copyMessage, setCopyMessage] = useState('')
  const [activeTab, setActiveTab] = useState(initialState.activeTab || DETAIL_TABS.OVERVIEW)
  const [matrixPreviewAsset, setMatrixPreviewAsset] = useState(null)
  const [matrixPreviewLabel, setMatrixPreviewLabel] = useState('')

  async function copyJson(label, value) {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2))
      setCopyMessage(`${label} copied`)
      window.setTimeout(() => setCopyMessage(''), 1800)
    } catch (_error) {
      setCopyMessage(`Could not copy ${label.toLowerCase()}`)
      window.setTimeout(() => setCopyMessage(''), 1800)
    }
  }

  async function copyText(label, value) {
    try {
      await navigator.clipboard.writeText(String(value || ''))
      setCopyMessage(`${label} copied`)
      window.setTimeout(() => setCopyMessage(''), 1800)
    } catch (_error) {
      setCopyMessage(`Could not copy ${label.toLowerCase()}`)
      window.setTimeout(() => setCopyMessage(''), 1800)
    }
  }

  useEffect(() => {
    const next = defaultAttempt(detail, attempts)
    setSelectedAttempt((current) => (attempts.includes(current) ? current : next))
  }, [detail?.run?.id, attempts])

  useEffect(() => {
    if (!diagram.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(diagram.nodes[0]?.id || '')
    }
  }, [diagram.nodes, selectedNodeId])

  useEffect(() => {
    const stored = loadRunDetailState(detail?.run?.id)
    if (stored) {
      if (stored.activeTab) setActiveTab(stored.activeTab)
      if (stored.imageFilter) setImageFilter(stored.imageFilter)
      if (stored.selectedNodeId) setSelectedNodeId(stored.selectedNodeId)
      if (stored.selectedAttempt && attempts.includes(Number(stored.selectedAttempt))) {
        setSelectedAttempt(Number(stored.selectedAttempt))
      } else {
        setSelectedAttempt(defaultAttempt(detail, attempts))
      }
      return
    }
    setSelectedAttempt(defaultAttempt(detail, attempts))
    setImageFilter(IMAGE_FILTER.ATTEMPT)
    setActiveTab(DETAIL_TABS.OVERVIEW)
    const nextNodeId = currentNodeId(detail)
    if (nextNodeId) {
      setSelectedNodeId(nextNodeId)
    }
  }, [detail?.run?.id])

  useEffect(() => {
    if (!detail?.run?.id) return
    saveRunDetailState(detail.run.id, {
      activeTab,
      imageFilter,
      selectedNodeId,
      selectedAttempt,
    })
  }, [detail?.run?.id, activeTab, imageFilter, selectedNodeId, selectedAttempt])

  useEffect(() => {
    if (typeof onActiveTabChange === 'function') {
      onActiveTabChange(activeTab)
    }
  }, [activeTab, onActiveTabChange])

  if (!detail) {
    return <p>Select a run row to see live execution.</p>
  }

  if (!detail.run) {
    return <p>Run detail payload is incomplete.</p>
  }

  const stages = safeArray(detail.stages)
  const assets = safeArray(detail.assets)

  const selectedNode = diagram.nodes.find((node) => node.id === selectedNodeId) || diagram.nodes[0] || null
  const currentAttempt = Number(detail.run.optimization_attempt || 0)
  const maxAttempts = Number(detail.run.max_optimization_attempts || 0) + 1
  const selectedSummary = diagram.attemptSummaries.find((summary) => summary.attempt === selectedAttempt)
  const stage1Request = safeObject(stages.find((stage) => stage.stage_name === 'stage1_prompt')?.request_json)
  const stage1Response = safeObject(stages.find((stage) => stage.stage_name === 'stage1_prompt')?.response_json)
  const latestStage3 =
    [...stages]
      .filter((stage) => stage.stage_name === 'stage3_upgrade')
      .sort((left, right) => Number(right.attempt || 0) - Number(left.attempt || 0))[0] || null
  const latestDecision = firstNonEmptyObject(safeObject(latestStage3?.response_json).decision, stage1Response.decision)
  const latestAnalysis = safeObject(safeObject(latestStage3?.response_json).analysis)
  const selectedFinalVariantNode = diagram.nodes.find((node) => node.id === 'stage4_variant_generate') || null
  const selectedWhiteVariantNode = diagram.nodes.find((node) => node.id === 'stage5_variant_white_bg') || null
  const finalVariantPanel = variantPanelData(selectedFinalVariantNode)
  const whiteVariantPanel = variantPanelData(selectedWhiteVariantNode)
  const selectedPromptEngineerMode = String(stage1Request.prompt_engineer_mode || 'responses_api')
  const selectedResponsesModel = String(stage1Request.responses_model || '')
  const selectedVectorStoreId = String(stage1Request.responses_vector_store_id || '')
  const usesGeminiPromptEngineer = selectedResponsesModel.toLowerCase().startsWith('gemini-')
  const selectedVisualStyleName = String(stage1Request.visual_style_name || '')
  const selectedVisualStyleId = String(stage1Request.visual_style_id || '')
  const resolvedRenderStyleMode = String(latestDecision.render_style_mode || '')
  const stage1Parsed = safeObject(stage1Response.parsed)
  const stage1NeedPerson = String(stage1Parsed['need a person'] || stage1Parsed.need_person || '')
  const critiqueNeedPerson = String(latestAnalysis.person_needed_for_clarity || latestDecision.person_needed_for_clarity || '')
  const critiquePresenceProblem = String(latestAnalysis.person_presence_problem || latestDecision.person_presence_problem || '')
  const critiqueReasoning = String(latestAnalysis.person_decision_reasoning || '')
  const critiqueRecommendation = String(latestAnalysis.recommendations || '')
  const resolvedNeedPerson = String(latestDecision.resolved_need_person || stage1Parsed['need a person'] || '')
  const resolvedRenderStyleName = String(latestDecision.render_style_name || '')
  const resolvedPersonReason = String(latestDecision.resolved_need_person_reasoning || '')
  const threshold = Number(detail.run.quality_threshold || 95)
  const selectedAttemptScore = selectedSummary?.score ?? null
  const imageCreationFailed = (() => {
    if (detail.run.status === 'failed_technical') return true
    return stages.some((stage) => {
      if (!['stage2_draft', 'stage3_upgrade', 'stage4_background', 'stage4_variant_generate', 'stage5_variant_white_bg'].includes(stage.stage_name)) return false
      const status = String(stage.status || '').toLowerCase()
      return status.includes('error') || status.includes('fail')
    })
  })()
  const scoreTooLow = (() => {
    if (detail.run.status === 'completed_fail_threshold') return true
    if (selectedAttemptScore == null) return false
    return Number(selectedAttemptScore) < threshold
  })()
  const allRunAssets = [...assets].sort((left, right) => {
    const leftAttempt = Number(left.attempt || 0)
    const rightAttempt = Number(right.attempt || 0)
    if (leftAttempt !== rightAttempt) return leftAttempt - rightAttempt
    const leftOrder = assetStageOrder[left.stage_name] || 99
    const rightOrder = assetStageOrder[right.stage_name] || 99
    if (leftOrder !== rightOrder) return leftOrder - rightOrder
    return String(left.created_at || '').localeCompare(String(right.created_at || ''))
  })
  const winnerStage4Asset =
    allRunAssets
      .filter((asset) => asset.stage_name === 'stage4_white_bg')
      .sort((left, right) => Number(right.attempt || 0) - Number(left.attempt || 0))[0] || null
  const finalAsset = winnerStage4Asset || [...allRunAssets].reverse().find((asset) => asset.id) || null
  const filteredRunAssets = (() => {
    if (imageFilter === IMAGE_FILTER.DRAFT) {
      return allRunAssets.filter((asset) => asset.stage_name === 'stage2_draft')
    }
    if (imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND) {
      return allRunAssets.filter((asset) => ['stage4_white_bg', 'stage5_variant_white_bg'].includes(asset.stage_name))
    }
    return allRunAssets.filter((asset) => {
      if (asset.stage_name === 'stage2_draft') return true
      if (asset.stage_name === 'stage4_variant_generate') return Number(asset.attempt || 0) === selectedAttempt
      return Number(asset.attempt || 0) === selectedAttempt
    })
  })()
  const profileIndex = variantProfileIndex(stages)
  const qualityReviewGroups = groupAssetsForQualityReview(filteredRunAssets, profileIndex)
  const imageRows = createdImageRows(filteredRunAssets, profileIndex)
  const profileCoverage = buildProfileCoverageData(stages, selectedAttempt, filteredRunAssets, profileIndex)
  const profileMatrix = profileCoverage.matrix
  const profileMatrixSections = profileCoverage.sections
  const visibleVariantPanels = (() => {
    if (imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND) {
      return whiteVariantPanel?.hasActivity ? [whiteVariantPanel] : []
    }
    if (imageFilter === IMAGE_FILTER.ATTEMPT) {
      return finalVariantPanel?.hasActivity ? [finalVariantPanel] : []
    }
    return []
  })()
  const canvasNodes = diagram.nodes.map((node) => {
    const position = {
      stage1_prompt: { x: 40, y: 235 },
      stage2_draft: { x: 380, y: 235 },
      stage3_critique: { x: 760, y: 45 },
      stage3_prompt_upgrade: { x: 760, y: 235 },
      stage3_generate: { x: 760, y: 425 },
      quality_gate: { x: 1160, y: 235 },
      stage4_background: { x: 1540, y: 120 },
      stage4_variant_generate: { x: 1910, y: 40 },
      stage5_variant_white_bg: { x: 1910, y: 280 },
      completed: { x: 2280, y: 160 },
    }[node.id] || { x: 40, y: 185 }

    const badge =
      node.id === 'stage3_critique' ||
      node.id === 'stage3_prompt_upgrade' ||
      node.id === 'stage3_generate' ||
      node.id === 'quality_gate' ||
      node.id === 'stage4_background' ||
      node.id === 'stage4_variant_generate' ||
      node.id === 'stage5_variant_white_bg'
        ? `Attempt ${selectedAttempt}`
        : ''

    return {
      ...node,
      ...position,
      badge,
    }
  })

  const detailWithSummaries = {
    ...detail,
    diagramAttemptSummaries: diagram.attemptSummaries,
  }

  return (
    <div className="run-diagram-root">
      <div className="run-diagram-head refined">
        <div>
          <p className="detail-eyebrow">Run + Details</p>
          <h3>Run Timeline</h3>
          <p className="run-head-summary">
            Start here for the current answer, then move to Images, Process, or Debug only if you need more detail.
          </p>
        </div>
        <div className="run-head-status-block">
          <span className={`status-pill status-${statusTone(detail.run.status)}`}>{prettyRunStatus(detail.run.status)}</span>
          <p>Current step: <strong>{prettyStage(detail.run.current_stage)}</strong></p>
          <p>Attempt: <strong>{currentAttempt > 0 ? currentAttempt : 1} / {maxAttempts}</strong></p>
        </div>
      </div>

      <div className="detail-subnav-row">
        <button className={activeTab === DETAIL_TABS.OVERVIEW ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.OVERVIEW)}>Overview</button>
        <button className={activeTab === DETAIL_TABS.IMAGES ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.IMAGES)}>Images</button>
        <button className={activeTab === DETAIL_TABS.PROCESS ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.PROCESS)}>Process</button>
        <button className={activeTab === DETAIL_TABS.SETTINGS ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.SETTINGS)}>Settings</button>
        <button className={activeTab === DETAIL_TABS.DEBUG ? 'tab active' : 'tab'} onClick={() => setActiveTab(DETAIL_TABS.DEBUG)}>Debug</button>
      </div>

      {activeTab === DETAIL_TABS.OVERVIEW ? renderOverviewSection({
        detail: detailWithSummaries,
        selectedSummary,
        currentAttempt,
        maxAttempts,
        threshold,
        finalAsset,
        winnerStage4Asset,
        imageCreationFailed,
        scoreTooLow,
        selectedPromptEngineerMode,
        selectedResponsesModel,
        selectedVectorStoreId,
        usesGeminiPromptEngineer,
        selectedVisualStyleName,
        selectedVisualStyleId,
        resolvedRenderStyleMode,
        resolvedRenderStyleName,
        resolvedNeedPerson,
        resolvedPersonReason,
        stage1NeedPerson,
        critiqueNeedPerson,
        critiquePresenceProblem,
        critiqueReasoning,
        critiqueRecommendation,
        attempts,
        setSelectedAttempt,
        setImageFilter,
        selectedAttempt,
      }) : null}

      {activeTab === DETAIL_TABS.IMAGES ? (
        <div className="run-detail-section-grid">
          <section className="run-overview-card">
            <div className="section-head-row">
              <div>
                <h4>Image Gallery</h4>
                <p>Filter by draft, selected attempt, or white-background outputs including any character variants.</p>
              </div>
            </div>
            <div className="attempt-chip-row">
              <button
                className={imageFilter === IMAGE_FILTER.DRAFT ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => setImageFilter(IMAGE_FILTER.DRAFT)}
              >
                Draft
              </button>
              {attempts.map((attempt) => (
                <button
                  key={attempt}
                  className={attempt === selectedAttempt && imageFilter === IMAGE_FILTER.ATTEMPT ? 'attempt-chip active' : 'attempt-chip'}
                  onClick={() => {
                    setSelectedAttempt(attempt)
                    setImageFilter(IMAGE_FILTER.ATTEMPT)
                  }}
                >
                  Attempt {attempt}
                </button>
              ))}
              <button
                className={imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => setImageFilter(IMAGE_FILTER.REMOVE_BACKGROUND)}
              >
                White Background
              </button>
            </div>
            <div className="run-help-card compact-help-card">
              {imageFilter === IMAGE_FILTER.DRAFT ? <p>Showing only Stage 2 draft images.</p> : null}
              {imageFilter === IMAGE_FILTER.ATTEMPT ? <p>Showing Attempt {selectedAttempt}, the original draft, and any extra character-profile finals for that winning attempt.</p> : null}
              {imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND ? <p>Showing the white-background winner plus any extra character-profile white-background variants.</p> : null}
              {imageFilter !== IMAGE_FILTER.DRAFT ? <p>Variant images below are grouped by Gender, Age, and Skin Color for quality review.</p> : null}
              {winnerStage4Asset ? <p>Winner attempt: <strong>{winnerStage4Asset.attempt}</strong></p> : null}
            </div>
          </section>

          <section className="run-all-images-section">
            {visibleVariantPanels.length > 0 ? (
              <div className="run-overview-card">
                {visibleVariantPanels.map((panel) => (
                  <div key={panel.nodeId} className="run-help-card compact-help-card">
                    <p><strong>{panel.title}</strong></p>
                    <p>
                      Completed: <strong>{panel.progress.completed_count ?? 0}</strong> | In flight: <strong>{panel.progress.in_flight_count ?? 0}</strong> | Remaining: <strong>{panel.progress.remaining_count ?? 0}</strong> | Failed: <strong>{panel.progress.failed_count ?? 0}</strong>
                    </p>
                    {panel.inFlight.length > 0 ? (
                      <p><strong>In flight:</strong> {panel.inFlight.map(profileLabel).join(', ')}</p>
                    ) : null}
                    {panel.waiting.length > 0 ? (
                      <p><strong>Waiting on branch or queue:</strong> {panel.waiting.map(profileLabel).join(', ')}</p>
                    ) : null}
                    {panel.failed.length > 0 ? (
                      <p><strong>Failed:</strong> {panel.failed.map((item) => `${profileLabel(item)}${item.error ? ` - ${item.error}` : ''}`).join(', ')}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}

            {imageFilter !== IMAGE_FILTER.DRAFT ? (
              <section className="run-overview-card">
                <div className="section-head-row">
                  <div>
                    <h4>Profile Coverage Matrix</h4>
                    <p>Show only the profiles planned for this run. `Regular` means the final variant image. `White BG` means the matching white-background image.</p>
                  </div>
                </div>
                <div className="matrix-legend-row">
                  <span><strong>Regular</strong> = final image</span>
                  <span><strong>White BG</strong> = white-background image</span>
                </div>
                <div className="profile-matrix-grid">
                  {profileMatrixSections.map((section) => (
                    <section key={section.gender} className="profile-matrix-card">
                      <h5>{humanGender(section.gender)}</h5>
                      <div className="table-wrap">
                        <table className="profile-matrix-table">
                          <thead>
                            <tr>
                              <th>Age \ Skin</th>
                              {section.skins.map((skin) => (
                                <th key={skin}>{humanSkinColor(skin)}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {section.ages.map((age) => (
                              <tr key={age}>
                                <th>{humanAge(age)}</th>
                                {section.skins.map((skin) => {
                                  const cell = matrixCellState(profileMatrix, section.gender, age, skin)
                                  return (
                                    <td key={`${section.gender}-${age}-${skin}`}>
                                      <label className="matrix-check">
                                        <input type="checkbox" checked={cell.regular} readOnly disabled />
                                        <span>Regular</span>
                                      </label>
                                      {cell.regularAsset ? (
                                        <button
                                          type="button"
                                          className="matrix-view-button"
                                          onClick={() => {
                                            setMatrixPreviewAsset(cell.regularAsset)
                                            setMatrixPreviewLabel(`${humanGender(section.gender)} | ${humanAge(age)} | ${humanSkinColor(skin)} | Regular`)
                                          }}
                                        >
                                          View
                                        </button>
                                      ) : null}
                                      <label className="matrix-check">
                                        <input type="checkbox" checked={cell.white} readOnly disabled />
                                        <span>White BG</span>
                                      </label>
                                      {cell.whiteAsset ? (
                                        <button
                                          type="button"
                                          className="matrix-view-button"
                                          onClick={() => {
                                            setMatrixPreviewAsset(cell.whiteAsset)
                                            setMatrixPreviewLabel(`${humanGender(section.gender)} | ${humanAge(age)} | ${humanSkinColor(skin)} | White BG`)
                                          }}
                                        >
                                          View
                                        </button>
                                      ) : null}
                                    </td>
                                  )
                                })}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </section>
                  ))}
                </div>
                {profileMatrixSections.length === 0 ? (
                  <p>No planned profile variants are recorded for this run yet.</p>
                ) : null}
              </section>
            ) : null}

            <section className="run-overview-card">
              <div className="section-head-row">
                <div>
                  <h4>Created Images</h4>
                  <p>Asset metadata for the current filter. This table does not load image previews.</p>
                </div>
              </div>
              {imageRows.length > 0 ? (
                <div className="table-wrap">
                  <table className="created-images-table">
                    <thead>
                      <tr>
                        <th>Stage</th>
                        <th>Attempt</th>
                        <th>Profile</th>
                        <th>File</th>
                        <th>Model</th>
                        <th>Size</th>
                        <th>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      {imageRows.map((row) => (
                        <tr key={row.id}>
                          <td>{row.stageLabel}</td>
                          <td>{row.attemptLabel}</td>
                          <td>{row.profileLabel || '-'}</td>
                          <td className="created-images-file-cell">{row.fileName}</td>
                          <td>{row.modelName}</td>
                          <td>{row.dimensions}</td>
                          <td>{row.createdAt}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p>No image assets were created for this filter yet.</p>
              )}
            </section>

            {filteredRunAssets.length === 0 ? (
              <div className="empty-state-card">
                <p>
                  {visibleVariantPanels.length > 0
                    ? 'Variant generation is active. Images will appear here as soon as each finished output is downloaded and saved.'
                    : imageFilter === IMAGE_FILTER.REMOVE_BACKGROUND
                      ? 'No remove-background image yet.'
                      : selectedSummary?.stage3Status === 'error'
                        ? 'No Stage 3 image is available for this attempt because the process failed after the draft image.'
                        : 'No images available for this filter yet.'}
                </p>
              </div>
            ) : (
              <>
                {qualityReviewGroups.baseAssets.length > 0 ? (
                  <div className="asset-grid">
                    {qualityReviewGroups.baseAssets.map((asset) => (
                      <div key={asset.id} className="asset-card run-asset-card">
                        <h4>{stageImageLabel(asset.stage_name)}</h4>
                        {asset.id ? (
                          <DeferredAssetImage asset={asset} alt={`${asset.stage_name} ${attemptLabel(asset)}`} />
                        ) : (
                          <p className="asset-meta-empty">Image URL unavailable.</p>
                        )}
                        <div className="asset-meta">
                          <p><strong>{attemptLabel(asset)}</strong></p>
                          <p>{asset.file_name || '-'}</p>
                          <p>{asset.model_name || '-'}</p>
                          {asset.id ? (
                            <a href={buildAssetContentUrl(asset)} target="_blank" rel="noreferrer">
                              Open Full Image
                            </a>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}

                {qualityReviewGroups.variantGroups.map((group) => (
                  <section key={group.key} className="variant-review-group">
                    <div className="variant-review-group-head">
                      <h4>{group.label || 'Variant group'}</h4>
                      <p>{group.assets.length} image{group.assets.length === 1 ? '' : 's'}</p>
                    </div>
                    <div className="asset-grid">
                      {group.assets.map((asset) => (
                        <div key={asset.id} className="asset-card run-asset-card">
                          <h4>{stageImageLabel(asset.stage_name)}</h4>
                          {asset.id ? (
                            <DeferredAssetImage asset={asset} alt={`${group.label} ${attemptLabel(asset)}`} />
                          ) : (
                            <p className="asset-meta-empty">Image URL unavailable.</p>
                          )}
                          <div className="asset-meta">
                            <p><strong>{attemptLabel(asset)}</strong></p>
                            <p>{group.label || '-'}</p>
                            <p>{asset.file_name || '-'}</p>
                            <p>{asset.model_name || '-'}</p>
                            {asset.id ? (
                              <a href={buildAssetContentUrl(asset)} target="_blank" rel="noreferrer">
                                Open Full Image
                              </a>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </>
            )}
          </section>
        </div>
      ) : null}

      {activeTab === DETAIL_TABS.PROCESS ? (
        <div className="run-detail-section-grid">
          <div className="run-help-card">
            <p><strong>How to read this:</strong> each attempt is one full try to improve the image and pass the quality score.</p>
            <p>Flow: Stage 1 prompt + initial person guess -> Stage 2 draft -> Stage 3 critique validates whether a person is actually needed -> Stage 3 prompt/image enforce the resolved style -> Quality Gate -> winner selection -> Stage 4 white background -> Stage 5 white male age expansion from the Stage 3 winner -> Stage 6 white female kid seed from the Stage 3 winner -> Stage 7 white female age expansion -> Stage 8 race expansion from matching white age/gender baselines -> Stage 9 white-background copies for every final variant.</p>
            <p>If quality fails and attempts remain, the system loops from Quality Gate back to Stage 3 for the next attempt.</p>
          </div>

          <div className="attempt-chip-row">
            {attempts.map((attempt) => (
              <button
                key={attempt}
                className={attempt === selectedAttempt ? 'attempt-chip active' : 'attempt-chip'}
                onClick={() => setSelectedAttempt(attempt)}
              >
                Attempt {attempt}
              </button>
            ))}
          </div>

          <WorkflowCanvas
            nodes={canvasNodes}
            edges={diagram.edges}
            width={2580}
            height={640}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
          />

          <RunNodeDetailCard node={selectedNode} assistantName={assistantName} />
        </div>
      ) : null}

      {activeTab === DETAIL_TABS.SETTINGS ? (
        <div className="run-detail-section-grid">
          <section className="run-debug-card">
            <div>
              <h4>Prompt Engineer Configuration</h4>
              <p>This affects new runs. It does not change historical payloads already stored for this selected run.</p>
            </div>
            <div className="form-grid">
              <label>
                Prompt engineer mode
                <select
                  value={promptEngineerConfig.promptEngineerMode}
                  onChange={(e) => promptEngineerConfig.setPromptEngineerMode(e.target.value)}
                >
                  <option value="responses_api">Option 2: Responses API / Direct Model</option>
                  <option value="assistant">Option 1: OpenAI Assistant</option>
                </select>
              </label>
              <label>
                Prompt engineer model
                <select
                  value={promptEngineerConfig.responsesPromptEngineerModel}
                  onChange={(e) => promptEngineerConfig.setResponsesPromptEngineerModel(e.target.value)}
                >
                  <option value="gpt-4o-mini">gpt-4o-mini</option>
                  <option value="gpt-4.1-mini">gpt-4.1-mini</option>
                  <option value="gpt-5.4">gpt-5.4</option>
                  <option value="gemini-3-flash">Gemini-3-flash</option>
                  <option value="gemini-3-pro">Gemini-3-pro</option>
                </select>
              </label>
              <label>
                Responses vector store id
                <input
                  value={promptEngineerConfig.responsesVectorStoreId}
                  onChange={(e) => promptEngineerConfig.setResponsesVectorStoreId(e.target.value)}
                />
              </label>
              <label>
                Output aspect ratio
                <select
                  value={promptEngineerConfig.imageAspectRatio}
                  onChange={(e) => promptEngineerConfig.setImageAspectRatio(e.target.value)}
                >
                  {IMAGE_ASPECT_RATIO_OPTIONS.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
              <label>
                Output resolution
                <select
                  value={promptEngineerConfig.imageResolution}
                  onChange={(e) => promptEngineerConfig.setImageResolution(e.target.value)}
                >
                  {IMAGE_RESOLUTION_OPTIONS.map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </select>
              </label>
              <label>
                Illustration style id
                <input value={promptEngineerConfig.visualStyleId} onChange={(e) => promptEngineerConfig.setVisualStyleId(e.target.value)} />
              </label>
              <label>
                Illustration style name
                <input value={promptEngineerConfig.visualStyleName} onChange={(e) => promptEngineerConfig.setVisualStyleName(e.target.value)} />
              </label>
              <label>
                Illustration style instructions
                <textarea rows="12" value={promptEngineerConfig.visualStylePromptBlock} onChange={(e) => promptEngineerConfig.setVisualStylePromptBlock(e.target.value)} />
              </label>
              <label>
                Stage 1 prompt engineer input
                <textarea rows="10" value={promptEngineerConfig.stage1PromptTemplate} onChange={(e) => promptEngineerConfig.setStage1PromptTemplate(e.target.value)} />
              </label>
              <label>
                Stage 3 prompt engineer input
                <textarea rows="10" value={promptEngineerConfig.stage3PromptTemplate} onChange={(e) => promptEngineerConfig.setStage3PromptTemplate(e.target.value)} />
              </label>
              <p className="config-help-text">
                Placeholders: {'{word}'}, {'{part_of_sentence}'}, {'{category}'}, {'{context}'}, {'{boy_or_girl}'}, {'{photorealistic_hint}'}, {'{visual_style_id}'}, {'{visual_style_name}'}, {'{visual_style_block}'}, {'{old_prompt}'}, {'{challenges}'}, {'{recommendations}'}.
              </p>
              <p className="config-help-text">
                OpenAI models use Responses API with the vector store. Gemini prompt engineer models use the direct Google API and do not use the vector store.
              </p>
              <p className="config-help-text">
                Google image output settings follow the documented API options. Default aspect ratio is 1:1. Default resolution is 1K.
              </p>
              <button type="button" onClick={onSavePromptEngineerConfig}>Save Prompt Engineer Settings</button>
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === DETAIL_TABS.DEBUG ? (
        <div className="run-detail-section-grid">
          <div className="run-help-card stage4-help-card">
            <p>
              <strong>Stage 4 (Background Removal):</strong>{' '}
              {stage4StatusText({
                run: detail.run,
                selectedSummary,
                winnerStage4Attempt: winnerStage4Asset ? Number(winnerStage4Asset.attempt || 0) : null,
              })}
            </p>
          </div>

          <div className="run-debug-card">
            <div>
              <h4>Execution Log</h4>
              <p>Copy this while the run is active. It gives a compact step-by-step view of stage progress, variant counts, saved assets, and scores.</p>
            </div>
            <div className="run-debug-actions">
              <button type="button" onClick={() => copyText('Execution log', detail.execution_log || '')}>
                Copy execution log
              </button>
              <button type="button" onClick={() => setShowExecutionLog((value) => !value)}>
                {showExecutionLog ? 'Hide execution log' : 'Show execution log'}
              </button>
            </div>
            {copyMessage ? <p className="run-debug-copy-message">{copyMessage}</p> : null}
            {showExecutionLog ? <pre>{detail.execution_log || 'No execution log available yet.'}</pre> : null}
          </div>

          <div className="run-debug-card">
            <div>
              <h4>Detailed Execution Log</h4>
              <p>Use this to trace the exact variant flow: source image, prompt, Replicate submission, provider responses, poll transitions, and asset save/failure events.</p>
            </div>
            <div className="run-debug-actions">
              <button type="button" onClick={() => copyText('Detailed execution log', detail.detailed_execution_log || '')}>
                Copy detailed execution log
              </button>
              <button type="button" onClick={() => setShowDetailedExecutionLog((value) => !value)}>
                {showDetailedExecutionLog ? 'Hide detailed execution log' : 'Show detailed execution log'}
              </button>
            </div>
            {copyMessage ? <p className="run-debug-copy-message">{copyMessage}</p> : null}
            {showDetailedExecutionLog ? <pre>{detail.detailed_execution_log || 'No detailed execution log available yet.'}</pre> : null}
          </div>

          <div className="run-debug-card">
            <div>
              <h4>Raw JSON</h4>
              <p>Use this when something fails. It includes the run, stages, prompts, assets, and scores exactly as stored.</p>
            </div>
            <div className="run-debug-actions">
              <button type="button" onClick={() => copyJson('Full run JSON', detail)}>
                Copy full run JSON
              </button>
              <button type="button" onClick={() => setShowRunJson((value) => !value)}>
                {showRunJson ? 'Hide full run JSON' : 'Show full run JSON'}
              </button>
            </div>
            {copyMessage ? <p className="run-debug-copy-message">{copyMessage}</p> : null}
            {showRunJson ? <pre>{JSON.stringify(detail, null, 2)}</pre> : null}
          </div>
        </div>
      ) : null}

      {matrixPreviewAsset ? (
        <div
          className="matrix-preview-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={matrixPreviewLabel || 'Image preview'}
          onClick={() => {
            setMatrixPreviewAsset(null)
            setMatrixPreviewLabel('')
          }}
        >
          <div className="matrix-preview-card" onClick={(event) => event.stopPropagation()}>
            <div className="matrix-preview-head">
              <div>
                <h4>{matrixPreviewLabel || 'Image preview'}</h4>
                <p>{matrixPreviewAsset.file_name || ''}</p>
              </div>
              <button
                type="button"
                className="matrix-preview-close"
                onClick={() => {
                  setMatrixPreviewAsset(null)
                  setMatrixPreviewLabel('')
                }}
              >
                Close
              </button>
            </div>
            <div className="matrix-preview-body">
              <img
                className="matrix-preview-image"
                src={buildAssetContentUrl(matrixPreviewAsset)}
                alt={matrixPreviewLabel || 'Image preview'}
              />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
