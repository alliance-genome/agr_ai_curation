import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { ApiClient, ApiError } from '../src/api-client.js'

describe('API client', () => {
  it('sends no application credential in dev-mode', async () => {
    const client = new ApiClient({
      baseUrl: 'http://localhost:3002',
      authMode: 'dev-mode',
      secret: '',
      timeoutMs: 100,
      fetchImpl: async (_input, init) => {
        const headers = new Headers(init?.headers)
        assert.equal(headers.has('X-API-Key'), false)
        assert.equal(headers.has('Cookie'), false)
        return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
      },
    })
    await client.get('/api/users/me')
  })

  it('throws a structured redacted error for non-success responses', async () => {
    const evidence: import('../src/api-client.js').ApiEvidence[] = []
    const client = new ApiClient({
      baseUrl: 'http://app.test',
      authMode: 'api-key',
      secret: 'local-secret',
      timeoutMs: 100,
      evidence,
      fetchImpl: async (_input, init) => {
        assert.equal(new Headers(init?.headers).get('X-API-Key'), 'local-secret')
        return new Response(JSON.stringify({ detail: 'Bearer do-not-leak' }), {
          status: 503,
          headers: { 'content-type': 'application/json' },
        })
      },
    })
    await assert.rejects(client.get('/api/fail'), (error: unknown) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 503)
      assert.equal(error.message.includes('do-not-leak'), false)
      return true
    })
    assert.equal(JSON.stringify(evidence).includes('local-secret'), false)
    assert.equal(JSON.stringify(evidence).includes('do-not-leak'), false)
  })

  it('bounds failed response bodies and messages with the configured evidence limit', async () => {
    const evidencePreviewChars = 120
    const client = new ApiClient({
      baseUrl: 'http://app.test',
      authMode: 'api-key',
      secret: 'local-secret',
      timeoutMs: 100,
      evidencePreviewChars,
      fetchImpl: async () => Response.json({ detail: `Bearer do-not-leak ${'x'.repeat(1_000)}` }, { status: 503 }),
    })

    await assert.rejects(client.get('/api/fail'), (error: unknown) => {
      assert.ok(error instanceof ApiError)
      assert.ok(JSON.stringify(error.body).length <= evidencePreviewChars)
      assert.ok(error.message.length <= `GET /api/fail failed with 503: `.length + evidencePreviewChars)
      assert.doesNotMatch(`${error.message}${JSON.stringify(error.body)}`, /do-not-leak/)
      return true
    })
  })

  it('uses the configured evidence limit for failed form posts and downloads', async () => {
    const evidencePreviewChars = 120
    for (const invoke of [
      (client: ApiClient) => client.postForm('/api/upload', new FormData(), { filename: 'input.pdf' }),
      (client: ApiClient) => client.download('/api/files/output'),
    ]) {
      const client = new ApiClient({
        baseUrl: 'http://app.test',
        authMode: 'api-key',
        secret: 'local-secret',
        timeoutMs: 100,
        evidencePreviewChars,
        fetchImpl: async () => new Response(`Bearer do-not-leak ${'x'.repeat(1_000)}`, { status: 503 }),
      })
      await assert.rejects(invoke(client), (error: unknown) => {
        assert.ok(error instanceof ApiError)
        assert.ok(JSON.stringify(error.body).length <= evidencePreviewChars)
        assert.doesNotMatch(`${error.message}${JSON.stringify(error.body)}`, /do-not-leak/)
        return true
      })
    }
  })
})
