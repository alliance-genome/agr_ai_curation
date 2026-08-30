import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { CASE_NAMES } from '../src/config.js'
import { canonicalCaseStatuses, executedCanonicalCases } from '../src/run-results.js'

const canonicalResult = {
  cases: CASE_NAMES.map((name) => ({ sourcePath: `/repo/cases/${name}.yaml`, status: 'success' })),
}

describe('run result accounting', () => {
  it('maps exactly one result for every canonical case', () => {
    assert.deepEqual(
      canonicalCaseStatuses(canonicalResult),
      Object.fromEntries(CASE_NAMES.map((name) => [name, 'success'])),
    )
  })

  it('records missing or duplicate canonical cases as not run', () => {
    const partial = { cases: [canonicalResult.cases[0]!, canonicalResult.cases[0]!] }
    const statuses = canonicalCaseStatuses(partial)
    assert.equal(statuses['create-connect-save'], 'not-run')
    assert.equal(statuses['edit-rewire'], 'not-run')
  })

  it('derives cleanup scope from cases actually present after tag filtering', () => {
    assert.deepEqual(executedCanonicalCases({ cases: [canonicalResult.cases[3]!] }), ['run-saved-flow'])
    assert.deepEqual(executedCanonicalCases(undefined), [])
  })
})
