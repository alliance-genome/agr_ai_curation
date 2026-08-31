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
})
