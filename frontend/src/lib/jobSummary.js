export function formatDurationSeconds(value) {
  if (value === undefined || value === null || value === '') return '-'
  const seconds = Math.max(0, Math.round(Number(value)))
  if (Number.isNaN(seconds)) return '-'
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remaining = seconds % 60
  return [hours ? `${hours}h` : '', minutes || hours ? `${minutes}m` : '', `${remaining}s`].filter(Boolean).join(' ')
}

export function jobElapsedSeconds({ startedAt, finishedAt, finalSeconds, isFinal, nowMs }) {
  if (isFinal && finalSeconds !== undefined && finalSeconds !== null) return Number(finalSeconds)
  if (!startedAt) return null
  const raw = String(startedAt)
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw) ? raw : `${raw}Z`
  const started = new Date(normalized).getTime()
  if (Number.isNaN(started)) return null
  let endMs = Number(nowMs)
  if (isFinal) {
    if (!finishedAt) return null
    const rawEnd = String(finishedAt)
    endMs = new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(rawEnd) ? rawEnd : `${rawEnd}Z`).getTime()
  }
  if (!Number.isFinite(endMs)) return null
  return Math.max(0, (endMs - started) / 1000)
}
