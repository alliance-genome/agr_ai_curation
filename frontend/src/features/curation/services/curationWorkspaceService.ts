import type {
  CurationCandidateDecisionRequest,
  CurationCandidateDecisionResponse,
  CurationCandidateValidationRequest,
  CurationCandidateValidationResponse,
  CurationManualCandidateCreateRequest,
  CurationManualCandidateCreateResponse,
  CurationCandidateDraftUpdateRequest,
  CurationCandidateDraftUpdateResponse,
  CurationSessionValidationRequest,
  CurationSessionValidationResponse,
  CurationSessionUpdateRequest,
  CurationSessionUpdateResponse,
  CurationWorkspace,
  CurationWorkspaceResponse,
} from '@/features/curation/types'
import { readCurationApiError } from './api'

interface CurationWorkspaceRequestOptions {
  keepalive?: boolean
}

async function fetchCurationWorkspaceJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    credentials: 'include',
    ...init,
    headers,
  })

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  return response.json() as Promise<T>
}

export async function fetchCurationWorkspace(sessionId: string): Promise<CurationWorkspace> {
  const payload = await fetchCurationWorkspaceJson<CurationWorkspaceResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(sessionId)}?include_workspace=true`,
  )

  return payload.workspace
}

export async function updateCurationSession(
  request: CurationSessionUpdateRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationSessionUpdateResponse> {
  return fetchCurationWorkspaceJson<CurationSessionUpdateResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function createManualCurationCandidate(
  request: CurationManualCandidateCreateRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationManualCandidateCreateResponse> {
  return fetchCurationWorkspaceJson<CurationManualCandidateCreateResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/candidates`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function autosaveCurationCandidateDraft(
  request: CurationCandidateDraftUpdateRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationCandidateDraftUpdateResponse> {
  return fetchCurationWorkspaceJson<CurationCandidateDraftUpdateResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/candidates/${
      encodeURIComponent(request.candidate_id)
    }/draft`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function submitCurationCandidateDecision(
  request: CurationCandidateDecisionRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationCandidateDecisionResponse> {
  return fetchCurationWorkspaceJson<CurationCandidateDecisionResponse>(
    `/api/curation-workspace/candidates/${encodeURIComponent(request.candidate_id)}/decision`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function validateCurationCandidate(
  request: CurationCandidateValidationRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationCandidateValidationResponse> {
  return fetchCurationWorkspaceJson<CurationCandidateValidationResponse>(
    `/api/curation-workspace/candidates/${encodeURIComponent(request.candidate_id)}/validate`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function validateAllCurationSessionCandidates(
  request: CurationSessionValidationRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationSessionValidationResponse> {
  return fetchCurationWorkspaceJson<CurationSessionValidationResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/validate-all`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}
