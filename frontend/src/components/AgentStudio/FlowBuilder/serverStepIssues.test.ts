import { describe, expect, it } from 'vitest'

import { currentServerStepIssue, serverStepIssuesFromLoad } from './serverStepIssues'

const copy = { id: 'node_1', data: { agent_id: 'ca_copy', agent_revision_id: 'rev-1' } }
const task = { id: 'node_0', data: { agent_id: 'task_input' } }
const message = 'My allele check is your copy of a validator, and a copy of a validator cannot run in a flow.'

describe('server step marks', () => {
  const issues = serverStepIssuesFromLoad(
    [{ message, node_id: 'node_1' }, { message: 'Flow-wide note.' }, { message: 'Gone.', node_id: 'node_9' }],
    [task, copy],
  )

  it('marks only the step a warning names, as it was loaded', () => {
    expect(currentServerStepIssue(copy, issues)).toBe(message)
    expect(currentServerStepIssue(task, issues)).toBeUndefined()
    expect(issues.size).toBe(1)
  })

  it('clears the mark when the step is re-pinned to another revision or given another agent', () => {
    expect(currentServerStepIssue({ id: 'node_1', data: { agent_id: 'ca_copy', agent_revision_id: 'rev-2' } }, issues))
      .toBeUndefined()
    expect(currentServerStepIssue({ id: 'node_1', data: { agent_id: 'gene_extractor' } }, issues)).toBeUndefined()
  })

  it('treats a missing and a null revision alike', () => {
    const validator = { id: 'node_2', data: { agent_id: 'gene_validation' } }
    const loaded = serverStepIssuesFromLoad([{ message, node_id: 'node_2' }], [validator])
    expect(currentServerStepIssue({ id: 'node_2', data: { agent_id: 'gene_validation', agent_revision_id: null } }, loaded))
      .toBe(message)
  })
})
