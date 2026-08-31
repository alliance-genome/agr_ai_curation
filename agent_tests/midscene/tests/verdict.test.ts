import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { summarizeModelUsage } from '../src/model-usage.js'
import { buildRedactedVerdict, verdictFailure } from '../src/verdict.js'

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

  it('bounds verdict-facing failure messages with the configured evidence limit', () => {
    const evidencePreviewChars = 120
    const failure = verdictFailure(new Error(`Bearer do-not-leak ${'x'.repeat(1_000)}`), evidencePreviewChars)
    assert.equal((failure as { name: string }).name, 'Error')
    assert.ok((failure as { message: string }).message.length <= evidencePreviewChars)
    assert.doesNotMatch(JSON.stringify(failure), /do-not-leak/)
  })
})
