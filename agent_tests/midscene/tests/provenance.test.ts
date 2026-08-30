import assert from 'node:assert/strict'
import path from 'node:path'
import { describe, it } from 'node:test'

import { readRunnerProvenance } from '../src/provenance.js'

describe('runner provenance', () => {
  it('binds evidence to the current repository commit and host', async () => {
    const provenance = await readRunnerProvenance(path.resolve(process.cwd(), '../..'), 5_000)
    assert.match(provenance.git_sha, /^[0-9a-f]{40}$/)
    assert.ok(provenance.hostname.length > 0)
  })
})
