import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { summarizeModelUsage } from '../src/model-usage.js'
import { buildRedactedVerdict } from '../src/verdict.js'

describe('smoke verdict boundary', () => {
  it('retains safe numeric usage while redacting the rest of the verdict', async () => {
    const modelUsage = await summarizeModelUsage('/missing/midscene-report', 'openai', 'gpt-5.6-sol', 5)
    modelUsage.input_tokens = 123
    modelUsage.cached_input_tokens = 45
    modelUsage.output_tokens = 6
    const verdict = buildRedactedVerdict({
      app_secret: 'must-not-survive',
      failure: 'Authorization: Bearer opaque-value',
    }, modelUsage)

    assert.equal(verdict.app_secret, '[REDACTED]')
    assert.doesNotMatch(String(verdict.failure), /opaque-value/)
    assert.deepEqual(verdict.model_usage, modelUsage)
    assert.equal((verdict.model_usage as typeof modelUsage).input_tokens, 123)
    assert.equal((verdict.model_usage as typeof modelUsage).cached_input_tokens, 45)
    assert.equal((verdict.model_usage as typeof modelUsage).output_tokens, 6)
  })
})
