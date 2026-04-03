import { describe, expect, it } from 'vitest'

import { flowResponseToSummary, upsertFlowSummary } from './flowListSummaries'
import type { FlowResponse, FlowSummaryResponse } from '@/components/AgentStudio/FlowBuilder/types'

function buildFlowResponse(overrides: Partial<FlowResponse> = {}): FlowResponse {
  return {
    id: 'flow-1',
    user_id: 7,
    name: 'Evidence Flow',
    description: 'Collects evidence',
    flow_definition: {
      version: '1.0',
      entry_node_id: 'node_0',
      nodes: [
        {
          id: 'node_0',
          type: 'task_input',
          position: { x: 0, y: 0 },
          data: {
            agent_id: 'task_input',
            agent_display_name: 'Initial Instructions',
            input_source: 'user_query',
            output_key: 'task_input',
          },
        },
        {
          id: 'node_1',
          type: 'agent',
          position: { x: 100, y: 0 },
          data: {
            agent_id: 'gene',
            agent_display_name: 'Gene',
            input_source: 'previous_output',
            output_key: 'gene_output',
          },
        },
      ],
      edges: [],
    },
    execution_count: 0,
    last_executed_at: null,
    created_at: '2026-04-02T00:00:00Z',
    updated_at: '2026-04-03T00:00:00Z',
    ...overrides,
  }
}

function buildFlowSummary(overrides: Partial<FlowSummaryResponse> = {}): FlowSummaryResponse {
  return {
    id: 'flow-0',
    user_id: 7,
    name: 'Older Flow',
    description: 'Older flow',
    step_count: 1,
    execution_count: 0,
    last_executed_at: null,
    created_at: '2026-04-01T00:00:00Z',
    updated_at: '2026-04-01T00:00:00Z',
    ...overrides,
  }
}

describe('flowListSummaries', () => {
  it('builds a flow summary from the saved flow response', () => {
    const summary = flowResponseToSummary(buildFlowResponse())

    expect(summary).toEqual({
      id: 'flow-1',
      user_id: 7,
      name: 'Evidence Flow',
      description: 'Collects evidence',
      step_count: 2,
      execution_count: 0,
      last_executed_at: null,
      created_at: '2026-04-02T00:00:00Z',
      updated_at: '2026-04-03T00:00:00Z',
    })
  })

  it('upserts a freshly saved flow at the front of the current list', () => {
    const flows = [
      buildFlowSummary({
        id: 'flow-older',
        updated_at: '2026-04-01T00:00:00Z',
      }),
    ]

    const updatedFlows = upsertFlowSummary(flows, buildFlowResponse(), 20)

    expect(updatedFlows.map((flow) => flow.id)).toEqual(['flow-1', 'flow-older'])
    expect(updatedFlows[0].step_count).toBe(2)
  })

  it('replaces an existing flow summary without duplicating it', () => {
    const flows = [
      buildFlowSummary({
        id: 'flow-1',
        name: 'Old Name',
        updated_at: '2026-04-01T00:00:00Z',
      }),
      buildFlowSummary({
        id: 'flow-2',
        updated_at: '2026-03-31T00:00:00Z',
      }),
    ]

    const updatedFlows = upsertFlowSummary(
      flows,
      buildFlowResponse({
        name: 'Renamed Flow',
        updated_at: '2026-04-03T02:00:00Z',
      }),
      20
    )

    expect(updatedFlows).toHaveLength(2)
    expect(updatedFlows[0].name).toBe('Renamed Flow')
    expect(updatedFlows.map((flow) => flow.id)).toEqual(['flow-1', 'flow-2'])
  })
})
