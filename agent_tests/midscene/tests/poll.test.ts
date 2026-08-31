import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { pollUntil } from '../src/poll.js'

describe('polling', () => {
  it('returns the first accepted value', async () => {
    const result = await pollUntil(async (attempt) => attempt, (value) => value === 3, {
      label: 'counter', intervalMs: 1, limit: 3, evidencePreviewChars: 120,
    })
    assert.equal(result, 3)
  })

  it('fails with a bounded timeout message', async () => {
    const evidencePreviewChars = 120
    await assert.rejects(
      pollUntil(async (attempt) => ({ attempt, detail: 'x'.repeat(1_000) }), () => false, {
        label: 'document processing', intervalMs: 1, limit: 2, evidencePreviewChars,
      }),
      (error: unknown) => {
        const prefix = 'document processing did not succeed after 2 attempts; last value: '
        assert.match(String(error), /document processing did not succeed after 2 attempts/)
        assert.ok((error as Error).message.length <= prefix.length + evidencePreviewChars)
        return true
      },
    )
  })
})
