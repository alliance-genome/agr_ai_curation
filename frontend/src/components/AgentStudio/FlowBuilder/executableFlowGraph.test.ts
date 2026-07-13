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
    node('output', 'gene_summary'),
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

const multiOutputAttachmentFlow = (): {
  nodes: FlowNodeDefinition[]
  edges: FlowEdgeDefinition[]
} => ({
  nodes: [
    node('task', 'task_input', 'task_input'),
    node('general', 'pdf_extraction'),
    node('gene', 'gene_extractor'),
    node('allele', 'allele_extractor'),
    node('general_csv', 'csv_formatter', 'output'),
    node('allele_tsv', 'tsv_formatter', 'output'),
  ],
  edges: [
    edge('control_1', 'task', 'general'),
    edge('control_2', 'general', 'gene'),
    edge('control_3', 'gene', 'allele'),
    edge('output_1', 'general', 'general_csv', { role: 'output_attachment' }),
    edge('output_2', 'allele', 'allele_tsv', { role: 'output_attachment' }),
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

  it('projects formatter attachments as terminal leaves without control branching', () => {
    const flow = multiOutputAttachmentFlow()
    const graph = projectExecutableFlowGraph(flow.nodes, flow.edges, 'task')

    expect(graph.valid).toBe(true)
    expect(graph.ordered_control_node_ids).toEqual(['task', 'general', 'gene', 'allele'])
    expect(graph.ordered_executable_node_ids).toEqual([
      'general',
      'gene',
      'allele',
      'general_csv',
      'allele_tsv',
    ])
    expect(graph.exit_node_ids).toEqual(['allele'])
    expect(graph.terminal_node_ids).toEqual(['allele', 'general_csv', 'allele_tsv'])
    expect(graph.output_attachments).toEqual([
      {
        output_node_id: 'general_csv',
        sources: [{ edge_id: 'output_1', source_node_id: 'general' }],
        edge_id: 'output_1',
        source_node_id: 'general',
      },
      {
        output_node_id: 'allele_tsv',
        sources: [{ edge_id: 'output_2', source_node_id: 'allele' }],
        edge_id: 'output_2',
        source_node_id: 'allele',
      },
    ])
  })

  it('groups distinct sources for one formatter and schedules the formatter once', () => {
    const flow = multiOutputAttachmentFlow()
    flow.edges.push(edge(
      'output_3',
      'gene',
      'general_csv',
      { role: 'output_attachment' },
    ))

    const graph = projectExecutableFlowGraph(flow.nodes, flow.edges, 'task', '1.1')

    expect(graph.valid).toBe(true)
    expect(graph.ordered_executable_node_ids).toEqual([
      'general',
      'gene',
      'allele',
      'general_csv',
      'allele_tsv',
    ])
    expect(graph.output_attachments[0]).toEqual({
      output_node_id: 'general_csv',
      sources: [
        { edge_id: 'output_1', source_node_id: 'general' },
        { edge_id: 'output_3', source_node_id: 'gene' },
      ],
      edge_id: 'output_1',
      source_node_id: 'general',
    })
  })

  it('rejects formatter agents retained as ordinary control-flow steps', () => {
    const flow = multiSidecarFlow()
    const formatterNode = flow.nodes.find(candidate => candidate.id === 'output')
    if (!formatterNode) throw new Error('formatter fixture is missing')
    formatterNode.data.agent_id = 'csv_formatter'
    formatterNode.data.agent_display_name = 'CSV Formatter'

    expect(projectExecutableFlowGraph(flow.nodes, flow.edges, 'task', '1.1').issues)
      .toEqual(expect.arrayContaining([
        expect.objectContaining({
          code: 'formatter_in_control_flow',
          node_ids: ['output'],
          edge_ids: ['control_2'],
        }),
      ]))
  })

  it('reports missing, identical duplicate, and legacy output bindings', () => {
    const missing = multiOutputAttachmentFlow()
    missing.edges.pop()
    expect(projectExecutableFlowGraph(
      missing.nodes,
      missing.edges,
      'task',
      '1.1',
    ).issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'missing_output_binding' }),
    ]))

    const duplicate = multiOutputAttachmentFlow()
    duplicate.edges.push(edge(
      'output_duplicate',
      'general',
      'general_csv',
      { role: 'output_attachment' },
    ))
    expect(projectExecutableFlowGraph(
      duplicate.nodes,
      duplicate.edges,
      'task',
      '1.1',
    ).issues).toEqual(expect.arrayContaining([
      expect.objectContaining({
        code: 'duplicate_output_source',
        node_ids: ['general', 'general_csv'],
        edge_ids: ['output_1', 'output_duplicate'],
      }),
    ]))

    const crossRole = multiOutputAttachmentFlow()
    crossRole.edges.push(edge(
      'validation_output_collision',
      'general',
      'general_csv',
      { role: 'validation_attachment' },
    ))
    expect(projectExecutableFlowGraph(
      crossRole.nodes,
      crossRole.edges,
      'task',
      '1.1',
    ).issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'attachment_target_role_conflict' }),
    ]))
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
