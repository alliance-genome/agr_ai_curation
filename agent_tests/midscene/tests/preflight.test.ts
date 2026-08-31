import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { appendStderrPreview, openAiModelPreflight } from '../src/preflight.js'

describe('OpenAI model preflight', () => {
  it('performs an authenticated metadata lookup without inference', async () => {
    const result = await openAiModelPreflight({
      baseUrl: 'https://api.openai.test/v1',
      model: 'gpt-5.6-sol',
      apiKey: 'test-project-key',
      timeoutMs: 1_000,
    }, async (input, init) => {
      assert.equal(String(input), 'https://api.openai.test/v1/models/gpt-5.6-sol')
      assert.equal(init?.method, 'GET')
      assert.deepEqual(init?.headers, { Authorization: 'Bearer test-project-key' })
      assert.equal('body' in (init ?? {}), false)
      return Response.json({ id: 'gpt-5.6-sol', object: 'model' })
    })
    assert.deepEqual(result, { id: 'gpt-5.6-sol' })
  })

  it('reports only status and model when lookup fails', async () => {
    await assert.rejects(
      openAiModelPreflight({
        baseUrl: 'https://api.openai.test/v1',
        model: 'missing-model',
        apiKey: 'secret-that-must-not-appear',
        timeoutMs: 1_000,
      }, async () => new Response('provider-secret-body', { status: 404 })),
      (error: unknown) => {
        assert.match(String(error), /missing-model.*HTTP 404/)
        assert.doesNotMatch(String(error), /secret-that-must-not-appear|provider-secret-body/)
        return true
      },
    )
  })

  it('bounds and redacts Codex stderr with the configured evidence limit', () => {
    const evidencePreviewChars = 120
    const stderr = appendStderrPreview('', `${'x'.repeat(1_000)} Bearer do-not-leak`, evidencePreviewChars)
    assert.ok(stderr.length <= evidencePreviewChars)
    assert.doesNotMatch(stderr, /do-not-leak/)
  })
})
