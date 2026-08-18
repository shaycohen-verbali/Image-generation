const SUMMARY_POLL_START_MS = 30000
const SUMMARY_POLL_RETRY_MS = 60000
const SUMMARY_POLL_MAX_MS = 120000

export function nextPollDelay(currentMs = SUMMARY_POLL_START_MS, succeeded = true) {
  if (succeeded) return SUMMARY_POLL_START_MS
  const current = Number(currentMs) || SUMMARY_POLL_START_MS
  if (current < SUMMARY_POLL_RETRY_MS) return SUMMARY_POLL_RETRY_MS
  return Math.min(SUMMARY_POLL_MAX_MS, Math.max(SUMMARY_POLL_RETRY_MS, current * 2))
}

export { SUMMARY_POLL_START_MS, SUMMARY_POLL_RETRY_MS, SUMMARY_POLL_MAX_MS }
