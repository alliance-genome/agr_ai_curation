import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { originScopedApiKeyHeaders } from '../src/setup.js'

describe('browser application authentication', () => {
  it('adds the API key only to the configured application origin', () => {
    const sameOrigin = originScopedApiKeyHeaders(
      'http://localhost:3002', 'http://localhost:3002/api/users/me', { accept: 'application/json' }, 'local-secret',
    )
    assert.equal(sameOrigin['X-API-Key'], 'local-secret')
    const external = originScopedApiKeyHeaders(
      'http://localhost:3002', 'https://cdn.example.org/app.js', { 'x-api-key': 'stale', accept: '*/*' }, 'local-secret',
    )
    assert.equal(Object.keys(external).some((name) => name.toLowerCase() === 'x-api-key'), false)
  })
})
