import type {
  CurationWorkspace,
  CurationWorkspaceResponse,
} from '@/features/curation/types'

async function readApiError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string; message?: string }
    return payload.detail || payload.message || 'Request failed'
  } catch {
    return 'Request failed'
  }
}

export async function fetchCurationWorkspace(sessionId: string): Promise<CurationWorkspace> {
  const response = await fetch(
    `/api/curation-workspace/sessions/${encodeURIComponent(sessionId)}?include_workspace=true`,
    {
      credentials: 'include',
    },
  )

  if (!response.ok) {
    throw new Error(await readApiError(response))
  }

  const payload = await response.json() as CurationWorkspaceResponse
  return payload.workspace
}
