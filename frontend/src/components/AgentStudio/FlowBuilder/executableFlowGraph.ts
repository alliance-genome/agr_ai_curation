import type { FlowEdgeDefinition, FlowNodeDefinition } from './types'

export interface ExecutableFlowIssue {
  code: string
  message: string
  node_ids: string[]
  edge_ids: string[]
}

export interface ExecutableValidationSidecar {
  edge_id: string
  source_node_id: string
  validator_node_id: string
  binding_id: string
  replaces_attachment_id?: string
}

export interface ExecutableFlowGraph {
  valid: boolean
  control_node_ids: string[]
  ordered_control_node_ids: string[]
  ordered_executable_node_ids: string[]
  entry_node_ids: string[]
  exit_node_ids: string[]
  terminal_node_ids: string[]
  validation_sidecars: ExecutableValidationSidecar[]
  issues: ExecutableFlowIssue[]
}

type ProjectableNode = Pick<FlowNodeDefinition, 'id' | 'type' | 'data'>
type ProjectableEdge = Pick<
  FlowEdgeDefinition,
  'id' | 'source' | 'target' | 'role' | 'satisfies_binding_id' | 'replaces_attachment_id'
>

const issue = (
  code: string,
  message: string,
  nodeIds: string[] = [],
  edgeIds: string[] = [],
): ExecutableFlowIssue => ({ code, message, node_ids: nodeIds, edge_ids: edgeIds })

/** Frontend mirror of the backend's canonical sequential topology contract. */
export const projectExecutableFlowGraph = (
  nodes: ProjectableNode[],
  edges: ProjectableEdge[],
  declaredEntry: string,
): ExecutableFlowGraph => {
  const attachmentEdges = edges.filter(edge => edge.role === 'validation_attachment')
  const controlEdges = edges.filter(edge => (edge.role ?? 'control_flow') === 'control_flow')
  const sidecarTargets = new Set(attachmentEdges.map(edge => edge.target))
  const controlNodes = nodes.filter(node => !sidecarTargets.has(node.id))
  const controlIds = controlNodes.map(node => node.id)
  const controlSet = new Set(controlIds)
  const outgoing = new Map(controlIds.map(id => [id, [] as ProjectableEdge[]]))
  const incoming = new Map(controlIds.map(id => [id, [] as ProjectableEdge[]]))
  const issues: ExecutableFlowIssue[] = []

  controlEdges.forEach(edge => {
    if (!controlSet.has(edge.source) || !controlSet.has(edge.target)) {
      issues.push(issue(
        'sidecar_in_control_flow',
        'Validation sidecar nodes cannot participate in control_flow edges',
        [edge.source, edge.target].filter(id => sidecarTargets.has(id)),
        [edge.id],
      ))
      return
    }
    outgoing.get(edge.source)?.push(edge)
    incoming.get(edge.target)?.push(edge)
  })

  controlIds.forEach(nodeId => {
    const next = outgoing.get(nodeId) ?? []
    const previous = incoming.get(nodeId) ?? []
    if (next.length > 1) {
      issues.push(issue(
        'branch',
        `Control node '${nodeId}' has ${next.length} outgoing control_flow edges; sequential flows require at most one`,
        [nodeId],
        next.map(edge => edge.id),
      ))
    }
    if (previous.length > 1) {
      issues.push(issue(
        'join',
        `Control node '${nodeId}' has ${previous.length} incoming control_flow edges; sequential flows require at most one`,
        [nodeId],
        previous.map(edge => edge.id),
      ))
    }
  })

  const entries = controlIds.filter(id => (incoming.get(id)?.length ?? 0) === 0)
  const exits = controlIds.filter(id => (outgoing.get(id)?.length ?? 0) === 0)
  if (entries.length !== 1) {
    issues.push(issue(
      'ambiguous_entry',
      `Sequential control flow requires exactly one entry node; found ${entries.length}`,
      entries,
    ))
  }
  if (entries.length === 1 && declaredEntry !== entries[0]) {
    issues.push(issue(
      'entry_mismatch',
      `entry_node_id '${declaredEntry}' does not match the control-flow entry '${entries[0]}'`,
      [declaredEntry, entries[0]].filter(Boolean),
    ))
  }
  const taskInputIds = controlNodes
    .filter(node => node.type === 'task_input' || node.data.agent_id === 'task_input')
    .map(node => node.id)
  if (taskInputIds.length === 1 && (entries.length !== 1 || entries[0] !== taskInputIds[0])) {
    issues.push(issue(
      'task_input_not_entry',
      `Task Input node '${taskInputIds[0]}' must be the control-flow entry`,
      taskInputIds,
    ))
  }
  if (exits.length !== 1) {
    issues.push(issue(
      'ambiguous_terminal',
      `Sequential control flow requires exactly one terminal node; found ${exits.length}`,
      exits,
    ))
  }

  const ordered: string[] = []
  const seen = new Set<string>()
  let cursor = entries.length === 1 ? entries[0] : declaredEntry
  while (controlSet.has(cursor) && !seen.has(cursor)) {
    ordered.push(cursor)
    seen.add(cursor)
    const next = outgoing.get(cursor) ?? []
    if (next.length !== 1) break
    cursor = next[0].target
  }

  const globallySeen = new Set<string>()
  controlIds.forEach(start => {
    if (globallySeen.has(start)) return
    const localPositions = new Map<string, number>()
    const path: string[] = []
    let current = start
    while (controlSet.has(current) && !globallySeen.has(current)) {
      const cycleStart = localPositions.get(current)
      if (cycleStart !== undefined) {
        const cycleNodes = path.slice(cycleStart)
        issues.push(issue(
          'cycle',
          `Control flow contains a cycle through nodes ${cycleNodes.map(id => `'${id}'`).join(', ')}`,
          cycleNodes,
        ))
        break
      }
      localPositions.set(current, path.length)
      path.push(current)
      const next = outgoing.get(current) ?? []
      if (next.length !== 1) break
      current = next[0].target
    }
    path.forEach(id => globallySeen.add(id))
  })

  const disconnected = controlIds.filter(id => !seen.has(id))
  if (disconnected.length > 0) {
    issues.push(issue(
      'disconnected',
      `Executable control nodes are disconnected from the entry path: ${disconnected.map(id => `'${id}'`).join(', ')}`,
      disconnected,
    ))
  }

  const nodeById = new Map(nodes.map(node => [node.id, node]))
  const seenBindings = new Map<string, string>()
  const validationSidecars = attachmentEdges.map(edge => {
    const replacedAttachmentId = edge.replaces_attachment_id ?? ''
    const bindingId = edge.satisfies_binding_id
      ?? nodeById.get(edge.source)?.data.validation_attachments?.find(
        attachment => attachment.attachment_id === replacedAttachmentId,
      )?.validator_binding_id
      ?? replacedAttachmentId
    const key = `${edge.source}\u0000${bindingId}`
    const priorEdge = seenBindings.get(key)
    if (bindingId && priorEdge) {
      issues.push(issue(
        'duplicate_validation_binding',
        `Control node '${edge.source}' has multiple validation sidecars for binding '${bindingId}'`,
        [edge.source, edge.target],
        [priorEdge, edge.id],
      ))
    } else if (bindingId) {
      seenBindings.set(key, edge.id)
    }
    return {
      edge_id: edge.id,
      source_node_id: edge.source,
      validator_node_id: edge.target,
      binding_id: bindingId,
      replaces_attachment_id: edge.replaces_attachment_id,
    }
  })

  const executable = ordered.filter(nodeId => {
    const node = nodeById.get(nodeId)
    return node?.type !== 'task_input'
      && node?.data.agent_id !== 'task_input'
      && node?.data.agent_id !== 'supervisor'
  })

  return {
    valid: issues.length === 0,
    control_node_ids: controlIds,
    ordered_control_node_ids: ordered,
    ordered_executable_node_ids: executable,
    entry_node_ids: entries,
    exit_node_ids: exits,
    terminal_node_ids: exits,
    validation_sidecars: validationSidecars,
    issues,
  }
}
