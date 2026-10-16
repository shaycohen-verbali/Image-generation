export function isCloudUploadFinished(status) {
  return ['completed', 'completed_with_errors', 'failed'].includes(status)
}

// Keep following the server job for its full lifetime. Each request has a
// timeout, but neither slow uploads nor temporary network failures end polling.
export function monitorCloudUpload({
  fetchStatus, onStatus, onError,
  schedule = setTimeout, cancel = clearTimeout,
  intervalMs = 1000, retryMs = 5000, requestTimeoutMs = 30000,
}) {
  let stopped = false
  let nextTimer
  let requestTimer
  let controller

  async function poll() {
    controller = new AbortController()
    requestTimer = schedule(() => controller.abort(), requestTimeoutMs)
    let delay = intervalMs
    try {
      const status = await fetchStatus(controller.signal)
      if (stopped) return
      onStatus(status)
      if (isCloudUploadFinished(status.status)) {
        stopped = true
        return
      }
    } catch (error) {
      if (stopped) return
      onError(error)
      delay = retryMs
    } finally {
      cancel(requestTimer)
    }
    if (!stopped) nextTimer = schedule(poll, delay)
  }

  nextTimer = schedule(poll, 500)
  return () => {
    stopped = true
    cancel(nextTimer)
    cancel(requestTimer)
    controller?.abort()
  }
}
