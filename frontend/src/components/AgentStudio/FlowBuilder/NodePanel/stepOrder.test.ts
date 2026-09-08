import { describe, expect, it } from 'vitest'

import type { AgentNode, FlowEdge } from '../types'
import { stepOrder } from './stepOrder'

const node = (id: string, agentId: string, type: AgentNode['type'] = 'agent'): AgentNode => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { agent_id: agentId, agent_display_name: agentId, output_key: `${agentId}_output` },
})

describe('stepOrder', () => {
  it('numbers steps in executable order and keeps unconnected nodes after them', () => {
    const nodes = [
      node('loose', 'extractor_b'),
      node('out', 'csv_formatter', 'output'),
      node('extract', 'extractor_a'),
      node('input', 'task_input', 'task_input'),
    ]
    const edges: FlowEdge[] = [
      { id: 'e1', source: 'input', target: 'extract', data: { role: 'control_flow' } },
      { id: 'e2', source: 'extract', target: 'out', data: { role: 'output_attachment' } },
    ]

    expect(stepOrder(nodes, edges, 'input')).toEqual(['input', 'extract', 'out', 'loose'])
  })

  it('uses canvas order when there is no entry node yet', () => {
    const nodes = [node('a', 'extractor_a'), node('b', 'extractor_b')]
    expect(stepOrder(nodes, [], undefined)).toEqual(['a', 'b'])
  })
})
