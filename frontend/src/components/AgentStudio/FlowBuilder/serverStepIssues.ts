/**
 * Step problems the server named when a flow was opened (for example a validator
 * saved as an ordinary step), keyed by node ID.
 *
 * A mark belongs to the step as it was loaded: the agent and pinned revision the
 * server judged. When the curator removes the step, or changes it in place (picks
 * another agent or another saved revision), the mark no longer applies and the
 * server's own save answer decides.
 */

export interface ServerStepIssue {
  message: string
  agentId: string
  agentRevisionId: string | null
}

export type ServerStepIssues = ReadonlyMap<string, ServerStepIssue>

interface WarningWithNode {
  message: string
  node_id?: string | null
}

interface NodeIdentity {
  id: string
  data: { agent_id: string; agent_revision_id?: string | null }
}

export function serverStepIssuesFromLoad(
  warnings: readonly WarningWithNode[] | undefined,
  nodes: readonly NodeIdentity[],
): Map<string, ServerStepIssue> {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const issues = new Map<string, ServerStepIssue>()
  for (const warning of warnings ?? []) {
    const node = warning.node_id ? byId.get(warning.node_id) : undefined
    if (!node) continue
    issues.set(node.id, {
      message: warning.message,
      agentId: node.data.agent_id,
      agentRevisionId: node.data.agent_revision_id ?? null,
    })
  }
  return issues
}

/** The server's message for this node, while it is still the step that was loaded. */
export function currentServerStepIssue(node: NodeIdentity, issues: ServerStepIssues): string | undefined {
  const issue = issues.get(node.id)
  if (!issue) return undefined
  if (issue.agentId !== node.data.agent_id) return undefined
  if (issue.agentRevisionId !== (node.data.agent_revision_id ?? null)) return undefined
  return issue.message
}
