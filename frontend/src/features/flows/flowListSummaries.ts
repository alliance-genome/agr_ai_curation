import type {
  FlowResponse,
  FlowSummaryResponse,
} from '@/components/AgentStudio/FlowBuilder/types'

export function flowResponseToSummary(flow: FlowResponse): FlowSummaryResponse {
  return {
    id: flow.id,
    user_id: flow.user_id,
    name: flow.name,
    description: flow.description,
    step_count: flow.flow_definition.nodes.length,
    execution_count: flow.execution_count,
    last_executed_at: flow.last_executed_at,
    created_at: flow.created_at,
    updated_at: flow.updated_at,
  }
}

export function upsertFlowSummary(
  flows: FlowSummaryResponse[],
  flow: FlowResponse,
  limit?: number
): FlowSummaryResponse[] {
  const flowSummary = flowResponseToSummary(flow)
  const nextFlows = [
    ...flows.filter((existingFlow) => existingFlow.id !== flowSummary.id),
    flowSummary,
  ].sort((left, right) => right.updated_at.localeCompare(left.updated_at))

  if (typeof limit === 'number') {
    return nextFlows.slice(0, limit)
  }

  return nextFlows
}
