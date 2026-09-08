/**
 * Step numbering for the node panel header ("Step 2 of 4").
 *
 * Steps follow the order the backend would run: control-flow nodes from the
 * entry node onward, then the outputs they feed. Nodes outside that order (a
 * disconnected node, a validator sidecar) keep their canvas order after the
 * connected ones, so every node has a number while it is edited.
 */

import type { AgentNode, FlowEdge } from '../types'
import { projectExecutableFlowGraph } from '../executableFlowGraph'

export function stepOrder(nodes: AgentNode[], edges: FlowEdge[], entryNodeId: string | undefined): string[] {
  const projectable = nodes.map((node) => ({ id: node.id, type: node.type ?? 'agent', data: node.data }))
  const projectableEdges = edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    role: edge.data?.role,
    satisfies_binding_id: edge.data?.satisfies_binding_id,
    replaces_attachment_id: edge.data?.replaces_attachment_id,
  }))
  const graph = projectExecutableFlowGraph(projectable, projectableEdges, entryNodeId ?? '')
  // Control nodes first (the task input is step 1), then the outputs they feed.
  const ordered: string[] = []
  const seen = new Set<string>()
  const push = (id: string) => {
    if (seen.has(id) || !nodes.some((node) => node.id === id)) return
    seen.add(id)
    ordered.push(id)
  }
  graph.ordered_control_node_ids.forEach(push)
  graph.output_attachments.forEach((attachment) => push(attachment.output_node_id))
  nodes.forEach((node) => push(node.id))
  return ordered
}
