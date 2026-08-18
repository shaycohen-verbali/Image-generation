import assert from 'node:assert/strict'
import test from 'node:test'

import { nextPollDelay, SUMMARY_POLL_MAX_MS, SUMMARY_POLL_RETRY_MS, SUMMARY_POLL_START_MS } from './polling.js'

test('summary polling starts at thirty seconds and resets after success', () => {
  assert.equal(nextPollDelay(), SUMMARY_POLL_START_MS)
  assert.equal(nextPollDelay(SUMMARY_POLL_MAX_MS, true), SUMMARY_POLL_START_MS)
})

test('summary polling backs off to one and two minutes after request failures', () => {
  assert.equal(nextPollDelay(SUMMARY_POLL_START_MS, false), SUMMARY_POLL_RETRY_MS)
  assert.equal(nextPollDelay(SUMMARY_POLL_RETRY_MS, false), SUMMARY_POLL_MAX_MS)
  assert.equal(nextPollDelay(SUMMARY_POLL_MAX_MS, false), SUMMARY_POLL_MAX_MS)
})
