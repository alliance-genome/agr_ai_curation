export interface FlowShortcuts { flow_ids: string[] | null; revision: number }
const url = '/api/users/me/flow-shortcuts'
export async function getFlowShortcuts(): Promise<FlowShortcuts> {
  const response = await fetch(url, { credentials: 'include' })
  if (!response.ok) throw new Error('Your flow list could not be loaded. Please try again.')
  return response.json()
}
export async function saveFlowShortcuts(flow_ids: string[], revision: number): Promise<FlowShortcuts> {
  const response = await fetch(url, { method: 'PUT', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ flow_ids, revision }) })
  if (!response.ok) throw new Error(response.status === 409 ? 'Your flow list changed in another tab. Refresh it and try again.' : 'Your flow list could not be saved. Your previous list is unchanged.')
  return response.json()
}
