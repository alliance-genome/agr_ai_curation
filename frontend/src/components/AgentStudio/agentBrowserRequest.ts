/**
 * A request to open the Agent Browser on one agent, one tab, and optionally
 * one envelope field. Flow Builder's node panel and the Workshop send these;
 * the Agent Studio page turns them into a selection and a tab change.
 */

export type AgentBrowserTab = 'guide' | 'envelope' | 'prompts'

export interface AgentBrowserFocus {
  objectType: string
  fieldPath?: string
}

export interface AgentBrowserRequest {
  agentId: string
  tab: AgentBrowserTab
  focus?: AgentBrowserFocus
}

/** A request with an identity, so the same tab can be requested twice in a row. */
export interface AgentDetailsRequest extends AgentBrowserRequest {
  token: number
}
