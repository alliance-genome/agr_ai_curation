import { z } from 'zod'

const nodeSchema = z.object({
  id: z.string().min(1),
  type: z.string().min(1),
  data: z.record(z.unknown()).default({}),
}).passthrough()

const edgeSchema = z.object({
  source: z.string().min(1),
  target: z.string().min(1),
  role: z.string().optional(),
  data: z.object({ role: z.string().optional() }).passthrough().optional(),
}).passthrough()

const graphSchema = z.object({
  entry_node_id: z.string().min(1),
  nodes: z.array(nodeSchema),
  edges: z.array(edgeSchema),
}).passthrough()

export interface ExpectedGraph {
  taskInstructions: string
  formatter: 'json_formatter' | 'csv_formatter'
}

function agentId(node: z.infer<typeof nodeSchema>): string {
  const id = node.data.agent_id
  return typeof id === 'string' ? id : node.type
}

function edgeRole(edge: z.infer<typeof edgeSchema>): string {
  return edge.role ?? edge.data?.role ?? 'control_flow'
}

export function assertExactSmokeGraph(raw: unknown, expected: ExpectedGraph): void {
  const graph = graphSchema.parse(raw)
  if (graph.nodes.length !== 3) throw new Error(`expected exactly 3 nodes, received ${graph.nodes.length}`)
  if (graph.edges.length !== 2) throw new Error(`expected exactly 2 edges, received ${graph.edges.length}`)

  const byAgent = new Map(graph.nodes.map((node) => [agentId(node), node]))
  const task = byAgent.get('task_input')
  const gene = byAgent.get('gene_extractor')
  const formatter = byAgent.get(expected.formatter)
  if (!task || !gene || !formatter || byAgent.size !== 3) {
    throw new Error(`expected task_input, gene_extractor, and ${expected.formatter}; received ${[...byAgent.keys()].join(', ')}`)
  }
  if (graph.entry_node_id !== task.id) throw new Error('task_input must be the entry node')
  if (task.data.task_instructions !== expected.taskInstructions) {
    throw new Error(`task instructions mismatch: ${JSON.stringify(task.data.task_instructions)}`)
  }

  const edges = graph.edges.map((edge) => `${edge.source}->${edge.target}:${edgeRole(edge)}`)
  const expectedEdges = [
    `${task.id}->${gene.id}:control_flow`,
    `${gene.id}->${formatter.id}:output_attachment`,
  ]
  for (const edge of expectedEdges) {
    if (!edges.includes(edge)) throw new Error(`missing graph edge ${edge}; received ${edges.join(', ')}`)
  }
}

export function buildSmokeFlowDefinition(options: {
  instructions: string
  formatter: 'json_formatter' | 'csv_formatter'
}): Record<string, unknown> {
  const formatterLabel = options.formatter === 'json_formatter' ? 'JSON Formatter' : 'CSV Formatter'
  return {
    version: '1.1',
    entry_node_id: 'task-input',
    nodes: [
      {
        id: 'task-input',
        type: 'task_input',
        position: { x: 80, y: 160 },
        data: {
          agent_id: 'task_input',
          agent_display_name: 'Initial Instructions',
          task_instructions: options.instructions,
          output_key: 'task_input_text',
        },
      },
      {
        id: 'gene-extractor',
        type: 'agent',
        position: { x: 400, y: 160 },
        data: {
          agent_id: 'gene_extractor',
          agent_display_name: 'Gene Extractor',
          output_key: 'gene_extraction',
          step_goal: 'Extract only crb/Crumbs with one verified evidence record from the active publication.',
        },
      },
      {
        id: 'formatter',
        type: 'output',
        position: { x: 720, y: 160 },
        data: {
          agent_id: options.formatter,
          agent_display_name: formatterLabel,
          output_key: 'final_output',
          step_goal: `Save the extracted crb/Crumbs gene and its evidence record as ${formatterLabel.replace(' Formatter', '')}.`,
          projection_plan: {
            format: options.formatter === 'json_formatter' ? 'json' : 'csv',
            row_source: 'object',
            json_shape: 'rows',
            filters: [{ field_ref: 'object.evidence_record_ids', op: 'is_not_empty' }],
            columns: [
              { key: 'gene', header: 'Gene', field_ref: 'object.label' },
              { key: 'evidence_record_ids', header: 'Evidence Record IDs', field_ref: 'object.evidence_record_ids' },
            ],
          },
        },
      },
    ],
    edges: [
      { id: 'control-task-gene', source: 'task-input', target: 'gene-extractor', role: 'control_flow' },
      { id: 'output-gene-formatter', source: 'gene-extractor', target: 'formatter', role: 'output_attachment' },
    ],
  }
}
