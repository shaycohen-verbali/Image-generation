export function formatDurationSeconds(value) {
  if (value === undefined || value === null || value === '') return '-'
  const seconds = Math.max(0, Math.round(Number(value)))
  if (Number.isNaN(seconds)) return '-'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = seconds % 60
  return [hours ? `${hours}h` : '', minutes || hours ? `${minutes}m` : '', `${remaining}s`].filter(Boolean).join(' ')
}

export function jobElapsedSeconds({ startedAt, finalSeconds, isFinal, nowMs }) {
  if (isFinal && finalSeconds !== undefined && finalSeconds !== null) return Number(finalSeconds)
  if (!startedAt) return null
  const raw = String(startedAt)
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : `${raw}Z`
  const started = new Date(normalized).getTime()
  if (Number.isNaN(started)) return null
  return Math.max(0, (Number(nowMs) - started) / 1000)
}
