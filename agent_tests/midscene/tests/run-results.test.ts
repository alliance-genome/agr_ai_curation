import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { CASE_NAMES } from '../src/config.js'
import { acceptanceCases, canonicalCaseStatuses, executedCanonicalCases, runAcceptancePassed, selectedCasesSucceeded } from '../src/run-results.js'

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

  it('requires every selected canonical case to succeed', () => {
    const statuses = canonicalCaseStatuses(canonicalResult)
    assert.equal(selectedCasesSucceeded(statuses, ['create-connect-save', 'edit-rewire']), true)
    assert.equal(selectedCasesSucceeded({ ...statuses, 'edit-rewire': 'not-run' }, ['create-connect-save', 'edit-rewire']), false)
    assert.equal(selectedCasesSucceeded({ ...statuses, 'edit-rewire': 'failed' }, ['create-connect-save', 'edit-rewire']), false)
  })

  it('requires identified model usage before accepting a successful AI run', () => {
    const statuses = canonicalCaseStatuses(canonicalResult)
    assert.equal(runAcceptancePassed(statuses, ['create-connect-save'], 1), true)
    assert.equal(runAcceptancePassed(statuses, ['create-connect-save'], 0), false)
    assert.equal(runAcceptancePassed({ ...statuses, 'create-connect-save': 'not-run' }, ['create-connect-save'], 1), false)
    assert.equal(runAcceptancePassed(statuses, [], 1), false)
  })

  it('evaluates configured cases normally and only executed cases under tag filtering', () => {
    const configured = ['create-connect-save', 'edit-rewire', 'upload-ask'] as const
    assert.deepEqual(acceptanceCases(canonicalResult, configured, []), configured)
    assert.deepEqual(
      acceptanceCases({ cases: [canonicalResult.cases[2]!] }, configured, ['chat']),
      ['upload-ask'],
    )
    assert.deepEqual(acceptanceCases({ cases: [] }, configured, ['missing-tag']), [])
  })
})
