export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api/v1'
const API_ROOT = API_BASE.endsWith('/api/v1') ? API_BASE.slice(0, -7) : API_BASE

export function buildApiUrl(path) {
  if (!path) return ''
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  if (path.startsWith('/')) return `${API_ROOT}${path}`
  return `${API_ROOT}/${path}`
}

export function buildAssetContentUrl(assetOrId) {
  const assetId =
    typeof assetOrId === 'string'
      ? assetOrId
      : assetOrId && typeof assetOrId === 'object'
        ? assetOrId.id
        : ''
  if (!assetId) return ''
  return buildApiUrl(`/api/v1/assets/${assetId}/content`)
}

async function parseResponse(response) {
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `HTTP ${response.status}`)
  }
  return response.json()
}

async function fetchJson(url, options = {}, retries = 0) {
  try {
    const response = await fetch(url, options)
    return await parseResponse(response)
  } catch (error) {
    if (retries > 0 && error instanceof TypeError) {
      await new Promise((resolve) => window.setTimeout(resolve, 350))
      return fetchJson(url, options, retries - 1)
    }
    throw error
  }
}

export async function createEntry(payload) {
  const response = await fetch(`${API_BASE}/entries`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function importCsv(file) {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}/entries/import-csv`, {
    method: 'POST',
    body: form,
  })
  return parseResponse(response)
}

export async function importCsvJob(file, payload = {}) {
  const form = new FormData()
  form.append('file', file)
  form.append('execution_mode', payload.execution_mode || 'csv_dag')
  form.append('person_gender_options', JSON.stringify(payload.person_gender_options || []))
  form.append('person_age_options', JSON.stringify(payload.person_age_options || []))
  form.append('person_skin_color_options', JSON.stringify(payload.person_skin_color_options || []))
  form.append('override_existing_variants', payload.override_existing_variants ? 'true' : 'false')
  const response = await fetch(`${API_BASE}/csv-jobs/import`, {
    method: 'POST',
    body: form,
  })
  return parseResponse(response)
}

export async function listWordSources() {
  return fetchJson(`${API_BASE}/word-sources`, {}, 1)
}

export async function listWordSourceRows(tableName, filters = {}) {
  const query = new URLSearchParams()
  if (filters.search) query.set('search', filters.search)
  if (filters.selection_mode) query.set('selection_mode', filters.selection_mode)
  if (filters.row_id) query.set('row_id', filters.row_id)
  if (filters.range_start) query.set('range_start', String(filters.range_start))
  if (filters.range_end) query.set('range_end', String(filters.range_end))
  for (const partOfSpeech of filters.parts_of_speech || []) {
    query.append('parts_of_speech', partOfSpeech)
  }
  query.set('limit', String(filters.limit || 200))
  query.set('offset', String(filters.offset || 0))
  return fetchJson(`${API_BASE}/word-sources/${encodeURIComponent(tableName)}/rows?${query.toString()}`, {}, 1)
}

export async function importWordSourceRows(tableName, payload) {
  const response = await fetch(`${API_BASE}/word-sources/${encodeURIComponent(tableName)}/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function exportWordSourceRows(tableName, payload) {
  const response = await fetch(`${API_BASE}/word-sources/${encodeURIComponent(tableName)}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function getCloudflareConfig() {
  return fetchJson(`${API_BASE}/word-sources/cloudflare/config`, {}, 1)
}

export async function getCloudflareUploadStatus(batchId) {
  return fetchJson(`${API_BASE}/word-sources/cloud-uploads/${encodeURIComponent(batchId)}`, {}, 1)
}

export async function downloadWordSourceReport(tableName, params = {}) {
  const query = new URLSearchParams()
  if (params.selection_mode) query.set('selection_mode', params.selection_mode)
  if (params.row_id) query.set('row_id', params.row_id)
  if (params.range_start) query.set('range_start', String(params.range_start))
  if (params.range_end) query.set('range_end', String(params.range_end))
  return `${API_BASE}/word-sources/${encodeURIComponent(tableName)}/report.csv?${query.toString()}`
}

export async function listCsvJobs() {
  return fetchJson(`${API_BASE}/csv-jobs`, {}, 2)
}

export async function getCsvJob(jobId) {
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}`, {}, 2)
}

export async function getCsvJobOverview(jobId) {
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}/overview`, {}, 2)
}

export async function getCsvJobMetadata(jobId) {
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}/metadata`, {}, 1)
}

export async function getCsvJobSummary(jobId) {
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}/summary`, {}, 1)
}

export async function getCsvJobSummaryDetails(jobId) {
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}/summary/details`, {}, 1)
}

export async function getCsvJobItems(jobId, { offset = 0, limit = 50 } = {}) {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}/items?${query.toString()}`, {}, 1)
}

export async function getCsvJobItemDetail(jobId, itemId) {
  return fetchJson(`${API_BASE}/csv-jobs/${jobId}/items/${itemId}`, {}, 1)
}

export async function startCsvJob(jobId) {
  const response = await fetch(`${API_BASE}/csv-jobs/${jobId}/start`, { method: 'POST' })
  return parseResponse(response)
}

export async function continueCsvJob(jobId, payload) {
  const response = await fetch(`${API_BASE}/csv-jobs/${jobId}/continue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function retryCsvJobFailures(jobId) {
  const response = await fetch(`${API_BASE}/csv-jobs/${jobId}/retry-failures`, { method: 'POST' })
  return parseResponse(response)
}

export async function cancelCsvJob(jobId) {
  const response = await fetch(`${API_BASE}/csv-jobs/${jobId}/cancel`, { method: 'POST' })
  return parseResponse(response)
}

export async function clearTerminalCsvJobs() {
  const response = await fetch(`${API_BASE}/csv-jobs`, { method: 'DELETE' })
  return parseResponse(response)
}

export async function exportCsvJob(jobId, payload = {}) {
  const response = await fetch(`${API_BASE}/csv-jobs/${jobId}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function applyEntryProfileOptions(payload) {
  const response = await fetch(`${API_BASE}/entries/apply-profile-options`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function listEntries(filters = {}) {
  const query = new URLSearchParams(filters)
  return fetchJson(`${API_BASE}/entries?${query.toString()}`, {}, 1)
}

export async function createRuns(payload) {
  const response = await fetch(`${API_BASE}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function listRuns(filters = {}) {
  const query = new URLSearchParams(filters)
  return fetchJson(`${API_BASE}/runs?${query.toString()}`, {}, 2)
}

export async function getRun(runId, options = {}) {
  const query = new URLSearchParams()
  if (options.includeDebug) query.set('include_debug', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson(`${API_BASE}/runs/${runId}${suffix}`, {}, 2)
}

export async function retryRun(runId) {
  const response = await fetch(`${API_BASE}/runs/${runId}/retry`, { method: 'POST' })
  return parseResponse(response)
}

export async function stopRun(runId) {
  const response = await fetch(`${API_BASE}/runs/${runId}/stop`, { method: 'POST' })
  return parseResponse(response)
}

export async function deleteRun(runId) {
  const response = await fetch(`${API_BASE}/runs/${runId}`, { method: 'DELETE' })
  return parseResponse(response)
}

export async function clearTerminalRuns(options = {}) {
  const query = new URLSearchParams()
  query.set('terminal_only', 'true')
  if (options.batchId) query.set('batch_id', options.batchId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  const response = await fetch(`${API_BASE}/runs${suffix}`, { method: 'DELETE' })
  return parseResponse(response)
}

export async function getBatchReport(batchId) {
  return fetchJson(`${API_BASE}/runs/batches/${batchId}/report`, {}, 1)
}

export async function createExport(payload) {
  const response = await fetch(`${API_BASE}/exports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}

export async function listExports() {
  return fetchJson(`${API_BASE}/exports`, {}, 1)
}

export async function getExport(exportId) {
  const response = await fetch(`${API_BASE}/exports/${exportId}`)
  return parseResponse(response)
}

export async function getConfig() {
  return fetchJson(`${API_BASE}/config`, {}, 1)
}

export async function updateConfig(payload) {
  const response = await fetch(`${API_BASE}/config`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
}
