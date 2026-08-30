import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { pollUntil } from '../src/poll.js'

describe('polling', () => {
  it('returns the first accepted value', async () => {
    const result = await pollUntil(async (attempt) => attempt, (value) => value === 3, {
      label: 'counter', intervalMs: 1, limit: 3,
    })
    assert.equal(result, 3)
  })

  it('fails with a bounded timeout message', async () => {
    await assert.rejects(
      pollUntil(async (attempt) => ({ attempt }), () => false, {
        label: 'document processing', intervalMs: 1, limit: 2,
      }),
      /document processing did not succeed after 2 attempts/,
    )
  })
})
