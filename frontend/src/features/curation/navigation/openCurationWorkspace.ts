import { fetchCurationSessionList } from '@/features/curation/inventory/curationInventoryService'
import { readCurationApiError } from '@/features/curation/services/api'
import type {
  CurationDocumentBootstrapAvailabilityResponse,
  CurationDocumentBootstrapResponse,
} from '@/features/curation/types'

export interface CurationWorkspaceLaunchTarget {
  sessionId?: string | null
  documentId?: string | null
  flowRunId?: string | null
  originSessionId?: string | null
  adapterKeys?: string[]
  profileKeys?: string[]
  domainKeys?: string[]
}

export interface OpenCurationWorkspaceOptions extends CurationWorkspaceLaunchTarget {
  navigate: (path: string) => void
}

export interface CurationWorkspaceLaunchAvailability {
  existingSessionId: string | null
  canBootstrap: boolean
}

function normalizeScopeValues(values?: string[] | null): string[] {
  return [...new Set((values ?? []).map((value) => value.trim()).filter(Boolean))]
}

function resolveSingleScopeValue(values?: string[] | null): string | null {
  const normalizedValues = normalizeScopeValues(values)
  return normalizedValues.length === 1 ? normalizedValues[0] : null
}

function assertWorkspaceTarget(target: CurationWorkspaceLaunchTarget) {
  if (target.sessionId || target.documentId) {
    return
  }

  throw new Error('Opening the curation workspace requires a session or document target.')
}

function buildBootstrapRequest(target: CurationWorkspaceLaunchTarget) {
  const adapterKey = resolveSingleScopeValue(target.adapterKeys)
  const profileKey = resolveSingleScopeValue(target.profileKeys)
  const domainKey = resolveSingleScopeValue(target.domainKeys)

  return {
    ...(adapterKey ? { adapter_key: adapterKey } : {}),
    ...(profileKey ? { profile_key: profileKey } : {}),
    ...(domainKey ? { domain_key: domainKey } : {}),
    ...(target.flowRunId ? { flow_run_id: target.flowRunId } : {}),
    ...(target.originSessionId ? { origin_session_id: target.originSessionId } : {}),
  }
}

async function canBootstrapCurationSession(
  target: CurationWorkspaceLaunchTarget
): Promise<boolean> {
  assertWorkspaceTarget(target)

  if (!target.documentId) {
    return false
  }

  const params = new URLSearchParams()
  const request = buildBootstrapRequest(target)
  Object.entries(request).forEach(([key, value]) => {
    if (value) {
      params.set(key, value)
    }
  })

  const query = params.toString()
  const response = await fetch(
    `/api/curation-workspace/documents/${encodeURIComponent(target.documentId)}/bootstrap-availability${query ? `?${query}` : ''}`,
    {
      credentials: 'include',
    }
  )

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  const payload = await response.json() as CurationDocumentBootstrapAvailabilityResponse
  return payload.eligible
}

export async function findExistingCurationSessionId(
  target: CurationWorkspaceLaunchTarget
): Promise<string | null> {
  if (target.sessionId) {
    return target.sessionId
  }

  assertWorkspaceTarget(target)

  const response = await fetchCurationSessionList({
    filters: {
      document_id: target.documentId ?? null,
      flow_run_id: target.flowRunId ?? null,
      origin_session_id: target.originSessionId ?? null,
      adapter_keys: normalizeScopeValues(target.adapterKeys),
      profile_keys: normalizeScopeValues(target.profileKeys),
      domain_keys: normalizeScopeValues(target.domainKeys),
    },
    sort_by: 'prepared_at',
    sort_direction: 'desc',
    page: 1,
    page_size: 1,
  })

  return response.sessions[0]?.session_id ?? null
}

export async function getCurationWorkspaceLaunchAvailability(
  target: CurationWorkspaceLaunchTarget
): Promise<CurationWorkspaceLaunchAvailability> {
  const existingSessionId = await findExistingCurationSessionId(target)
  if (existingSessionId) {
    return {
      existingSessionId,
      canBootstrap: true,
    }
  }

  return {
    existingSessionId: null,
    canBootstrap: await canBootstrapCurationSession(target),
  }
}

async function bootstrapCurationSession(
  target: CurationWorkspaceLaunchTarget
): Promise<string> {
  assertWorkspaceTarget(target)

  if (!target.documentId) {
    throw new Error('Bootstrapping a curation workspace requires a document target.')
  }

  const response = await fetch(
    `/api/curation-workspace/documents/${encodeURIComponent(target.documentId)}/bootstrap`,
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(buildBootstrapRequest(target)),
    }
  )

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  const payload = await response.json() as CurationDocumentBootstrapResponse
  return payload.session.session_id
}

export async function resolveCurationWorkspaceSessionId(
  target: CurationWorkspaceLaunchTarget
): Promise<string> {
  if (target.sessionId) {
    return target.sessionId
  }

  const existingSessionId = await findExistingCurationSessionId(target)
  if (existingSessionId) {
    return existingSessionId
  }

  return bootstrapCurationSession(target)
}

export async function openCurationWorkspace(
  options: OpenCurationWorkspaceOptions
): Promise<string> {
  const { navigate, ...target } = options
  const sessionId = await resolveCurationWorkspaceSessionId(target)
  navigate(`/curation/${sessionId}`)
  return sessionId
}
