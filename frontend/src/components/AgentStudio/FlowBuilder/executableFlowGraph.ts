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

export interface ExecutableOutputAttachment {
  edge_id: string
  source_node_id: string
  output_node_id: string
}

export interface ExecutableFlowGraph {
  valid: boolean
  control_node_ids: string[]
  ordered_control_node_ids: string[]
  ordered_executable_node_ids: string[]
  entry_node_ids: string[]
  exit_node_ids: string[]
  terminal_node_ids: string[]
  output_attachments: ExecutableOutputAttachment[]
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
  flowVersion: '1.0' | '1.1' = '1.0',
): ExecutableFlowGraph => {
  const validationAttachmentEdges = edges.filter(edge => edge.role === 'validation_attachment')
  const outputAttachmentEdges = edges.filter(edge => edge.role === 'output_attachment')
  const controlEdges = edges.filter(edge => (edge.role ?? 'control_flow') === 'control_flow')
  const validationTargets = new Set(validationAttachmentEdges.map(edge => edge.target))
  const declaredOutputIds = new Set(
    nodes.filter(node => node.type === 'output').map(node => node.id),
  )
  const outputTargets = new Set(outputAttachmentEdges.map(edge => edge.target))
  const crossRoleTargets = [...validationTargets].filter(nodeId => outputTargets.has(nodeId))
  const detachedOutputIds = flowVersion === '1.1' ? declaredOutputIds : outputTargets
  const controlNodes = nodes.filter(
    node => !validationTargets.has(node.id) && !detachedOutputIds.has(node.id),
  )
  const controlIds = controlNodes.map(node => node.id)
  const controlSet = new Set(controlIds)
  const outgoing = new Map(controlIds.map(id => [id, [] as ProjectableEdge[]]))
  const incoming = new Map(controlIds.map(id => [id, [] as ProjectableEdge[]]))
  const issues: ExecutableFlowIssue[] = []

  crossRoleTargets.sort().forEach(nodeId => {
    issues.push(issue(
      'attachment_target_role_conflict',
      `Node '${nodeId}' cannot be both an output attachment and a validation attachment target`,
      [nodeId],
      [...outputAttachmentEdges, ...validationAttachmentEdges]
        .filter(edge => edge.target === nodeId)
        .map(edge => edge.id),
    ))
  })

  controlEdges.forEach(edge => {
    if (!controlSet.has(edge.source) || !controlSet.has(edge.target)) {
      const outputIds = [edge.source, edge.target].filter(id => detachedOutputIds.has(id))
      const validationIds = [edge.source, edge.target].filter(id => validationTargets.has(id))
      issues.push(issue(
        outputIds.length > 0 ? 'output_in_control_flow' : 'sidecar_in_control_flow',
        outputIds.length > 0
          ? 'Output nodes cannot participate in control_flow edges'
          : 'Validation sidecar nodes cannot participate in control_flow edges',
        outputIds.length > 0 ? outputIds : validationIds,
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
  const outputAttachments: ExecutableOutputAttachment[] = []
  const outputEdgeByTarget = new Map<string, string>()
  if (flowVersion !== '1.1' && outputAttachmentEdges.length > 0) {
    issues.push(issue(
      'output_attachment_requires_v1_1',
      "output_attachment edges require flow schema version '1.1'",
      [],
      outputAttachmentEdges.map(edge => edge.id),
    ))
  }
  outputAttachmentEdges.forEach(edge => {
    const sourceNode = nodeById.get(edge.source)
    const targetNode = nodeById.get(edge.target)
    if (!controlSet.has(edge.source)) {
      issues.push(issue(
        'invalid_output_source',
        `Output attachment '${edge.id}' must originate from a control-flow extraction node`,
        [edge.source, edge.target].filter(Boolean),
        [edge.id],
      ))
    }
    if (targetNode?.type !== 'output') {
      issues.push(issue(
        'invalid_output_target',
        `Output attachment '${edge.id}' must target a node with type 'output'`,
        [edge.target],
        [edge.id],
      ))
    }
    if (sourceNode?.type === 'output') {
      issues.push(issue(
        'output_from_output',
        'Output nodes cannot own other output attachments',
        [edge.source, edge.target],
        [edge.id],
      ))
    }
    const priorEdge = outputEdgeByTarget.get(edge.target)
    if (priorEdge) {
      issues.push(issue(
        'multiple_output_sources',
        `Output node '${edge.target}' must be attached to exactly one source`,
        [edge.target],
        [priorEdge, edge.id],
      ))
    } else {
      outputEdgeByTarget.set(edge.target, edge.id)
    }
    outputAttachments.push({
      edge_id: edge.id,
      source_node_id: edge.source,
      output_node_id: edge.target,
    })
  })
  if (flowVersion === '1.1') {
    declaredOutputIds.forEach(nodeId => {
      if (!outputEdgeByTarget.has(nodeId)) {
        issues.push(issue(
          'missing_output_binding',
          `Output node '${nodeId}' must be attached to exactly one control-flow extraction node`,
          [nodeId],
        ))
      }
    })
  }

  const seenBindings = new Map<string, string>()
  const validationSidecars = validationAttachmentEdges.map(edge => {
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

  const outputIdsBySource = new Map<string, string[]>()
  outputAttachments.forEach(attachment => {
    const outputIds = outputIdsBySource.get(attachment.source_node_id) ?? []
    outputIds.push(attachment.output_node_id)
    outputIdsBySource.set(attachment.source_node_id, outputIds)
  })
  const executable = ordered.flatMap(nodeId => {
    const node = nodeById.get(nodeId)
    const controlStep = node?.type !== 'task_input'
      && node?.data.agent_id !== 'task_input'
      && node?.data.agent_id !== 'supervisor'
      ? [nodeId]
      : []
    return [...controlStep, ...(outputIdsBySource.get(nodeId) ?? [])]
  })
  const terminals = Array.from(new Set([
    ...exits,
    ...outputAttachments.map(attachment => attachment.output_node_id),
  ]))

  return {
    valid: issues.length === 0,
    control_node_ids: controlIds,
    ordered_control_node_ids: ordered,
    ordered_executable_node_ids: executable,
    entry_node_ids: entries,
    exit_node_ids: exits,
    terminal_node_ids: terminals,
    output_attachments: outputAttachments,
    validation_sidecars: validationSidecars,
    issues,
  }
}
