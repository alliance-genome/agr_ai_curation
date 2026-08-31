import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { compactDiagnostic, compactEvidence, redactSecrets, redactText, sanitizeHeaders } from '../src/redaction.js'

describe('secret redaction', () => {
  it('redacts secret-bearing keys recursively', () => {
    assert.deepEqual(redactSecrets({
      api_key: 'abc',
      nested: [{ Authorization: 'Bearer value', safe: 'visible' }],
    }), {
      api_key: '[REDACTED]',
      nested: [{ Authorization: '[REDACTED]', safe: 'visible' }],
    })
  })

  it('redacts bearer tokens, OpenAI-style keys, and cookie values in text', () => {
    const result = redactText('Authorization: Bearer abc.def-123 key sk-abcdefghijklmnop session=abcdefghijklmnop')
    assert.equal(result.includes('abc.def-123'), false)
    assert.equal(result.includes('sk-abcdefghijklmnop'), false)
    assert.equal(result.includes('abcdefghijklmnop'), false)
  })

  it('redacts auth headers while retaining harmless headers', () => {
    assert.deepEqual(sanitizeHeaders({ Cookie: 'session=secret', Accept: 'application/json', 'X-API-Key': 'secret' }), {
      Cookie: '[REDACTED]',
      Accept: 'application/json',
      'X-API-Key': '[REDACTED]',
    })
  })

  it('keeps compact evidence and diagnostics within a nondefault serialized bound', () => {
    const maxChars = 120
    const value = { detail: `Bearer do-not-leak ${'x'.repeat(1_000)}` }
    const evidence = compactEvidence(value, maxChars)
    const diagnostic = compactDiagnostic(value, maxChars)
    assert.ok(JSON.stringify(evidence).length <= maxChars)
    assert.ok(diagnostic.length <= maxChars)
    assert.doesNotMatch(`${JSON.stringify(evidence)}${diagnostic}`, /do-not-leak/)
  })
})
