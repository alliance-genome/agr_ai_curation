import { webcrypto } from 'node:crypto'
import { beforeAll, describe, expect, it } from 'vitest'

import type { ChatContext } from '@/types/promptExplorer'
import {
  fingerprintAuthoringContext,
  fingerprintFlowDraft,
  fingerprintWorkshopDraft,
} from './authoringContext'

const flowContext = (): ChatContext => ({
  active_tab: 'flows',
  flow_id: 'flow-1',
  flow_name: 'Test Flow',
  flow_description: 'Exact draft',
  flow_updated_at: '2026-09-04T00:00:00Z',
  flow_definition: {
    version: '1.1',
    entry_node_id: 'task',
    nodes: [{
      id: 'task',
      node_type: 'task_input',
      position: { x: 250, y: 100 },
      agent_id: 'task_input',
      agent_display_name: 'Initial Instructions',
      task_instructions: 'Extract genes',
      output_key: 'task_input',
      validation_attachments: [],
      validation_groups: [],
    }],
    edges: [],
  },
})

const adversarialFlowContext = (): ChatContext => ({
  active_tab: 'flows',
  flow_id: 'flow-é-A',
  flow_name: 'Unicode β flow',
  flow_description: 'Case-sensitive IDs A/a',
  flow_updated_at: '2026-09-04T01:02:03.456Z',
  flow_definition: {
    version: '1.1',
    entry_node_id: 'A',
    nodes: [
      {
        id: 'a',
        node_type: 'agent',
        position: { x: 1e20, y: -0 },
        agent_id: 'unicode_β',
        agent_display_name: 'β extractor',
        output_key: 'out-a',
        projection_plan: { é: 1.25, A: 1e-7, a: 1e20 },
      },
      {
        id: 'A',
        node_type: 'task_input',
        position: { x: 1e-7, y: 1.25 },
        agent_id: 'task_input',
        agent_display_name: 'Initial Instructions',
        task_instructions: 'Exact 🧬 task',
        output_key: 'task_input',
      },
    ],
    edges: [{
      id: 'é-edge',
      source: 'A',
      target: 'a',
      role: 'control_flow',
      condition: { type: 'contains', value: 'β' },
    }],
  },
})

const adversarialWorkshopContext = () => ({
  getting_started_mode: 'clone' as const,
  template_source: 'source-é',
  custom_agent_id: 'ca-Aa',
  custom_agent_updated_at: '2026-09-04T01:02:03.456Z',
  draft_name: 'β agent',
  draft_description: 'Unicode 🧬 description',
  draft_icon: 'science',
  draft_visibility: 'project' as const,
  draft_allowed_group_ids: ['é', 'a', 'A'],
  prompt_draft: 'Exact main prompt',
  group_prompt_overrides: { é: 'accent', a: 'lower', A: 'upper' },
  include_group_rules: true,
  draft_model_id: 'gpt-5.6-sol',
  draft_model_reasoning: 'high',
  draft_tool_ids: ['é-tool', 'a-tool', 'A-tool'],
  draft_output_schema_key: 'gene',
})

describe('Agent Studio authoring context fingerprints', () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, 'crypto', {
      configurable: true,
      value: webcrypto,
    })
  })

  it('matches the backend canonical hash fixture', async () => {
    await expect(fingerprintFlowDraft(flowContext())).resolves.toBe(
      'sha256:d78fb31bafaeef99c20bbebb07af22debf58dbe0315a1caa327a03e89f7586d7'
    )
  })

  it('matches backend fixtures for UTF-8 ordering and IEEE-754 numbers', async () => {
    const inMemory = adversarialFlowContext()
    const transported = JSON.parse(JSON.stringify(inMemory)) as ChatContext
    expect(Object.is(inMemory.flow_definition!.nodes[0].position.y, -0)).toBe(true)
    expect(Object.is(transported.flow_definition!.nodes[0].position.y, -0)).toBe(false)
    await expect(fingerprintFlowDraft(inMemory)).resolves.toBe(
      await fingerprintFlowDraft(transported)
    )
    await expect(fingerprintFlowDraft(transported)).resolves.toBe(
      'sha256:f9f8664ca18901527a106d90c077ae0b52f2733a592531c7cd1110795a558b92'
    )
    await expect(fingerprintWorkshopDraft(adversarialWorkshopContext())).resolves.toBe(
      'sha256:63fee43c366577e95a0eb9382622d0d345f4bd98b6d467f2ed5be2c155780443'
    )
  })

  it('normalizes set-like ordering without hiding authorable changes', async () => {
    const first = flowContext()
    first.flow_definition!.nodes.push({
      id: 'extract',
      node_type: 'agent',
      position: { x: 400, y: 100 },
      agent_id: 'gene_extractor',
      agent_display_name: 'Gene Extractor',
      output_key: 'genes',
      validation_attachments: [
        { attachment_id: 'b' },
        { attachment_id: 'a' },
      ],
    })
    first.flow_definition!.edges.push({
      id: 'edge-1', source: 'task', target: 'extract', role: 'control_flow',
    })

    const reordered = structuredClone(first)
    reordered.flow_definition!.nodes.reverse()
    reordered.flow_definition!.nodes[0].validation_attachments!.reverse()
    await expect(fingerprintFlowDraft(reordered)).resolves.toBe(
      await fingerprintFlowDraft(first)
    )

    reordered.flow_definition!.nodes[0].position.x += 1
    await expect(fingerprintFlowDraft(reordered)).resolves.not.toBe(
      await fingerprintFlowDraft(first)
    )
  })

  it('adds independent flow and Workshop fingerprints without mutating input', async () => {
    const context = flowContext()
    context.agent_workshop = {
      getting_started_mode: 'scratch',
      draft_name: 'Draft agent',
      draft_description: 'Description',
      draft_icon: 'science',
      draft_visibility: 'private',
      draft_allowed_group_ids: ['TEAM_B', 'TEAM_A'],
      inherited_allowed_group_ids: [],
      prompt_draft: 'Use exact evidence.',
      group_prompt_overrides: { TEAM_B: 'Use Team B conventions.' },
      include_group_rules: true,
      draft_tool_ids: ['search_document', 'read_chunk'],
      draft_output_schema_key: 'gene',
    }

    const captured = await fingerprintAuthoringContext(context)

    expect(captured.flow_draft_fingerprint).toMatch(/^sha256:[0-9a-f]{64}$/)
    expect(captured.agent_workshop?.draft_fingerprint).toMatch(/^sha256:[0-9a-f]{64}$/)
    expect(captured.agent_workshop?.draft_allowed_group_ids).toEqual(['TEAM_A', 'TEAM_B'])
    expect(context.agent_workshop.draft_allowed_group_ids).toEqual(['TEAM_B', 'TEAM_A'])
  })
})
