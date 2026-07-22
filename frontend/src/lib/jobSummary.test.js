import assert from 'node:assert/strict'
import test from 'node:test'

import { formatDurationSeconds, jobElapsedSeconds } from './jobSummary.js'

test('running elapsed time advances from browser time without an API response', () => {
  const startedAt = '2026-07-22T00:00:00Z'
  const first = jobElapsedSeconds({ startedAt, isFinal: false, nowMs: Date.parse('2026-07-22T00:00:05Z') })
  const second = jobElapsedSeconds({ startedAt, isFinal: false, nowMs: Date.parse('2026-07-22T00:00:06Z') })
  assert.equal(first, 5)
  assert.equal(second, 6)
})

test('completed elapsed time remains fixed', () => {
  assert.equal(jobElapsedSeconds({ startedAt: '2026-07-22T00:00:00Z', isFinal: true, finalSeconds: 42, nowMs: Date.now() }), 42)
  assert.equal(formatDurationSeconds(3661), '1h 1m 1s')
})
