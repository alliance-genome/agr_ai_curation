import { describe, expect, it } from 'vitest'

import { projectExecutableFlowGraph } from './executableFlowGraph'
import type { FlowEdgeDefinition, FlowNodeDefinition } from './types'

const node = (
  id: string,
  agentId: string,
  type: FlowNodeDefinition['type'] = 'agent',
): FlowNodeDefinition => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: {
    agent_id: agentId,
    agent_display_name: agentId,
    output_key: `${id}_output`,
  },
})

const edge = (
  id: string,
  source: string,
  target: string,
  extra: Partial<FlowEdgeDefinition> = {},
): FlowEdgeDefinition => ({ id, source, target, ...extra })

const multiSidecarFlow = (): {
  nodes: FlowNodeDefinition[]
  edges: FlowEdgeDefinition[]
} => ({
  nodes: [
    node('task', 'task_input', 'task_input'),
    node('extract', 'gene_extractor'),
    node('validator_symbol', 'custom_validator_symbol'),
    node('validator_identifier', 'custom_validator_identifier'),
    node('output', 'csv_formatter'),
  ],
  edges: [
    edge('control_1', 'task', 'extract'),
    edge('sidecar_1', 'extract', 'validator_symbol', {
      role: 'validation_attachment',
      satisfies_binding_id: 'symbol',
    }),
    edge('sidecar_2', 'extract', 'validator_identifier', {
      role: 'validation_attachment',
      satisfies_binding_id: 'identifier',
    }),
    edge('control_2', 'extract', 'output'),
  ],
})

describe('projectExecutableFlowGraph', () => {
  it('keeps distinct validator sidecars outside the sequential control path', () => {
    const flow = multiSidecarFlow()
    const graph = projectExecutableFlowGraph(flow.nodes, flow.edges, 'task')

    expect(graph.valid).toBe(true)
    expect(graph.ordered_control_node_ids).toEqual(['task', 'extract', 'output'])
    expect(graph.ordered_executable_node_ids).toEqual(['extract', 'output'])
    expect(graph.entry_node_ids).toEqual(['task'])
    expect(graph.terminal_node_ids).toEqual(['output'])
    expect(graph.validation_sidecars.map(sidecar => sidecar.binding_id)).toEqual([
      'symbol',
      'identifier',
    ])
  })

  it('reports the same stable branch, terminal, and disconnected reasons', () => {
    const flow = multiSidecarFlow()
    flow.nodes.push(node('other_output', 'json_formatter'))
    flow.edges.push(edge('branch', 'extract', 'other_output'))

    const graph = projectExecutableFlowGraph(flow.nodes, flow.edges, 'task')
    expect(graph.valid).toBe(false)
    expect(graph.issues.map(issue => issue.code)).toEqual(expect.arrayContaining([
      'branch',
      'ambiguous_terminal',
      'disconnected',
    ]))
  })

  it('rejects joins, cycles, orphans, and duplicate sidecar bindings', () => {
    const joined = multiSidecarFlow()
    joined.nodes.push(node('other', 'gene'))
    joined.edges.push(edge('other_entry', 'task', 'other'))
    joined.edges.push(edge('join', 'other', 'output'))
    expect(projectExecutableFlowGraph(joined.nodes, joined.edges, 'task').issues)
      .toEqual(expect.arrayContaining([expect.objectContaining({ code: 'join' })]))

    const cycled = multiSidecarFlow()
    cycled.edges.push(edge('cycle', 'output', 'extract'))
    expect(projectExecutableFlowGraph(cycled.nodes, cycled.edges, 'task').issues)
      .toEqual(expect.arrayContaining([expect.objectContaining({ code: 'cycle' })]))

    const orphaned = multiSidecarFlow()
    orphaned.nodes.push(node('orphan', 'gene'))
    expect(projectExecutableFlowGraph(orphaned.nodes, orphaned.edges, 'task').issues)
      .toEqual(expect.arrayContaining([expect.objectContaining({ code: 'disconnected' })]))

    const duplicate = multiSidecarFlow()
    duplicate.edges[2].satisfies_binding_id = 'symbol'
    const duplicateIssues = projectExecutableFlowGraph(
      duplicate.nodes,
      duplicate.edges,
      'task',
    ).issues
    expect(duplicateIssues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'duplicate_validation_binding' }),
    ]))
    expect(duplicateIssues).not.toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'branch' }),
    ]))

    const replacements = multiSidecarFlow()
    replacements.nodes[1].data.validation_attachments = [
      { attachment_id: 'attachment-a', validator_binding_id: 'symbol' },
      { attachment_id: 'attachment-b', validator_binding_id: 'symbol' },
    ] as unknown as FlowNodeDefinition['data']['validation_attachments']
    replacements.edges[1].satisfies_binding_id = undefined
    replacements.edges[1].replaces_attachment_id = 'attachment-a'
    replacements.edges[2].satisfies_binding_id = undefined
    replacements.edges[2].replaces_attachment_id = 'attachment-b'
    expect(projectExecutableFlowGraph(
      replacements.nodes,
      replacements.edges,
      'task',
    ).issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'duplicate_validation_binding' }),
    ]))
  })
})
