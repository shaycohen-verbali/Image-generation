import assert from 'node:assert/strict'
import test from 'node:test'
import { monitorCloudUpload } from './cloudUploadPolling.js'

function harness(fetchStatus) {
  const timers = new Map()
  const statuses = []
  const errors = []
  let id = 0
  const stop = monitorCloudUpload({
    fetchStatus,
    onStatus: (status) => statuses.push(status),
    onError: (error) => errors.push(error),
    schedule: (fn, delay) => { timers.set(++id, { fn, delay }); return id },
    cancel: (key) => timers.delete(key),
  })
  return { timers, statuses, errors, stop, async tick() {
    const [key, timer] = timers.entries().next().value
    timers.delete(key)
    await timer.fn()
  } }
}

test('uploads continue beyond 120 progress checks and publish final download links', async () => {
  let calls = 0
  const h = harness(async () => ++calls <= 150
    ? { status: 'running', uploaded: calls }
    : { status: 'completed', uploaded: 151, csv_package_download_url: '/package.zip' })
  for (let n = 0; n < 151; n++) await h.tick()
  assert.equal(h.statuses.length, 151)
  assert.equal(h.statuses.at(-1).csv_package_download_url, '/package.zip')
  assert.equal(h.timers.size, 0)
})

test('temporary failures back off and recover without losing the batch', async () => {
  let calls = 0
  const h = harness(async () => {
    if (++calls === 1) throw new TypeError('Offline')
    return { status: 'completed' }
  })
  await h.tick()
  assert.equal(h.errors.length, 1)
  assert.equal([...h.timers.values()][0].delay, 5000)
  await h.tick()
  assert.equal(h.statuses.at(-1).status, 'completed')
  assert.equal(h.timers.size, 0)
})

for (const status of ['completed_with_errors', 'failed']) {
  test(`polling stops when the server reports ${status}`, async () => {
    const h = harness(async () => ({ status }))
    await h.tick()
    assert.equal(h.statuses.at(-1).status, status)
    assert.equal(h.timers.size, 0)
  })
}

test('cleanup aborts the pending request and ignores late responses', async () => {
  let resolveRequest
  let signal
  const h = harness((requestSignal) => {
    signal = requestSignal
    return new Promise((resolve) => { resolveRequest = resolve })
  })
  const pending = h.tick()
  h.stop()
  assert.equal(signal.aborted, true)
  resolveRequest({ status: 'completed' })
  await pending
  assert.equal(h.statuses.length, 0)
  assert.equal(h.timers.size, 0)
})

test('a timed-out request reports a connection problem and retries', async () => {
  const h = harness((signal) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new Error('Timed out')), { once: true })
  }))
  const pending = h.tick()
  await h.tick()
  await pending
  assert.equal(h.errors.length, 1)
  assert.equal([...h.timers.values()][0].delay, 5000)
  h.stop()
})
