import { describe, expect, it } from 'vitest'
import { isFlowRecovery, isWorkshopRecovery } from './draftRecoveryValidation'

const fields = { name: '', description: '', customPrompt: '', groupPromptOverrides: {}, includeGroupRules: true,
  visibility: 'private', allowedGroupIds: [], modelId: '', modelReasoning: '', toolIds: [], icon: '🔧',
  outputDraft: { mode: 'profile_bound_generic', schemaKey: '', profilePin: null,
    profileContract: { name: '', semantic_class: '', fields: [{ key: 'source', value_schema: { kind: 'object', fields: [
      { key: 'name', value_schema: { kind: 'string' } },
    ] } }] } } }
const agent = { fields, baseline: null, mode: 'scratch', parentId: '', customId: '', cloneId: '' }
const flow = { currentFlowId: null, draft: { name: '', description: '', definition: { version: '1.1', entry_node_id: 'node_0', edges: [],
  nodes: [{ id: 'node_0', type: 'task_input', position: { x: 0, y: 0 }, data: { agent_id: 'task_input', agent_display_name: 'Task', output_key: '', task_instructions: 'Unfinished instructions' } }] } } }

describe('browser draft validation', () => {
  it('accepts incomplete authored fields and nested parts', () => {
    expect(isWorkshopRecovery(agent)).toBe(true)
    expect(isFlowRecovery(flow)).toBe(true)
  })
  it('rejects malformed nested fields before editor hydration', () => {
    const broken = structuredClone(agent)
    Object.assign(broken.fields.outputDraft.profileContract.fields[0].value_schema, { fields: [{ key: 'bad', value_schema: null }] })
    expect(isWorkshopRecovery(broken)).toBe(false)
    expect(isWorkshopRecovery({ ...agent, fields: { ...fields, toolIds: [null] } })).toBe(false)
  })
  it('rejects malformed node text and coordinates before rendering the canvas', () => {
    const broken = structuredClone(flow)
    Object.assign(broken.draft.definition.nodes[0].data, { task_instructions: {} })
    expect(isFlowRecovery(broken)).toBe(false)
    Object.assign(broken.draft.definition.nodes[0], { position: null })
    expect(isFlowRecovery(broken)).toBe(false)
  })
})
