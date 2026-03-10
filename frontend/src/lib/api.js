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
  return fetchJson(`${API_BASE}/runs?${query.toString()}`, {}, 1)
}

export async function getRun(runId) {
  return fetchJson(`${API_BASE}/runs/${runId}`, {}, 1)
}

export async function retryRun(runId) {
  const response = await fetch(`${API_BASE}/runs/${runId}/retry`, { method: 'POST' })
  return parseResponse(response)
}

export async function createExport(payload) {
  const response = await fetch(`${API_BASE}/exports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(response)
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
