export type ChatRouteMode = 'automatic' | 'agent' | 'flow'

export interface ChatRouteTarget {
  id: string
  kind: Exclude<ChatRouteMode, 'automatic'>
  display_name: string
  description: string | null
  category: string | null
  available: boolean
}

export interface ChatRoutePreference {
  mode: ChatRouteMode
  agent_id: string | null
  flow_id: string | null
  status: 'available' | 'unavailable'
  target: ChatRouteTarget | null
}

export interface ChatRouteTargetsResponse {
  targets: ChatRouteTarget[]
}

export type ChatRoutePreferenceUpdate =
  | { mode: 'agent'; agent_id: string; flow_id: null }
  | { mode: 'flow'; agent_id: null; flow_id: string }

const PREFERENCE_URL = '/api/users/me/chat-route-preference'
const TARGETS_URL = '/api/users/me/chat-route-targets'

export class ChatRoutePreferenceApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = 'ChatRoutePreferenceApiError'
  }
}

async function readResponse<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) {
    throw new ChatRoutePreferenceApiError(message, response.status)
  }
  return response.json()
}

export async function fetchChatRoutePreference(): Promise<ChatRoutePreference> {
  const response = await fetch(PREFERENCE_URL, { credentials: 'include' })
  return readResponse(response, 'Could not load your chat default.')
}

export async function fetchChatRouteTargets(): Promise<ChatRouteTarget[]> {
  const response = await fetch(TARGETS_URL, { credentials: 'include' })
  const result = await readResponse<ChatRouteTargetsResponse>(
    response,
    'Could not load chat default choices.',
  )
  return result.targets
}

export async function saveChatRoutePreference(
  update: ChatRoutePreferenceUpdate,
): Promise<ChatRoutePreference> {
  const response = await fetch(PREFERENCE_URL, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  })
  return readResponse(response, "We couldn't save your chat default.")
}

export async function clearChatRoutePreference(): Promise<ChatRoutePreference> {
  const response = await fetch(PREFERENCE_URL, {
    method: 'DELETE',
    credentials: 'include',
  })
  return readResponse(response, "We couldn't clear your chat default.")
}
