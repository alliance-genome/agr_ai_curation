import type {
  CurationCandidateDeleteRequest,
  CurationCandidateDeleteResponse,
  CurationCandidateDecisionRequest,
  CurationCandidateDecisionResponse,
  CurationCandidateValidationRequest,
  CurationCandidateValidationResponse,
  CurationManualCandidateCreateRequest,
  CurationManualCandidateCreateResponse,
  CurationCandidateDraftUpdateRequest,
  CurationCandidateDraftUpdateResponse,
  DomainEnvelopeReviewRowsResponse,
  CurationEnvelopeFieldPatchRequest,
  CurationEnvelopeFieldPatchResponse,
  CurationValidationFindingWaiveRequest,
  CurationValidationFindingWaiveResponse,
  CurationSessionValidationRequest,
  CurationSessionValidationResponse,
  CurationSubmissionExecuteRequest,
  CurationSubmissionExecuteResponse,
  CurationSessionUpdateRequest,
  CurationSessionUpdateResponse,
  CurationSubmissionPreviewRequest,
  CurationSubmissionPreviewResponse,
  CurationWorkspace,
  CurationWorkspaceResponse,
} from '@/features/curation/types'
import { readCurationApiError } from './api'

interface CurationWorkspaceRequestOptions {
  keepalive?: boolean
  signal?: AbortSignal
}

export class CurationWorkspaceRequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'CurationWorkspaceRequestError'
    this.status = status
  }
}

export interface DomainEnvelopeReviewRowsRequest {
  envelope_id: string
  envelope_revision?: number | null
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
    throw new CurationWorkspaceRequestError(
      response.status,
      await readCurationApiError(response),
    )
  }

  return response.json() as Promise<T>
}

export function buildDomainEnvelopeReviewRowsPath(
  request: DomainEnvelopeReviewRowsRequest,
): string {
  const query = new URLSearchParams()
  if (typeof request.envelope_revision === 'number') {
    query.set('revision', String(request.envelope_revision))
  }

  const encodedEnvelopeId = encodeURIComponent(request.envelope_id)
  const queryString = query.toString()
  return `/api/curation-workspace/domain-envelopes/${encodedEnvelopeId}/review-rows${
    queryString ? `?${queryString}` : ''
  }`
}

export function buildCurationWorkspaceEnvelopeReviewRowsRequests(
  workspace: CurationWorkspace,
): DomainEnvelopeReviewRowsRequest[] {
  const requestsByKey = new Map<string, DomainEnvelopeReviewRowsRequest>()

  for (const candidate of workspace.candidates) {
    const projectionRef = candidate.projection_ref
    if (!projectionRef) {
      continue
    }

    const key = `${projectionRef.envelope_id}:${projectionRef.envelope_revision}`
    if (!requestsByKey.has(key)) {
      requestsByKey.set(key, {
        envelope_id: projectionRef.envelope_id,
        envelope_revision: projectionRef.envelope_revision,
      })
    }
  }

  return Array.from(requestsByKey.values())
}

export async function fetchDomainEnvelopeReviewRows(
  request: DomainEnvelopeReviewRowsRequest,
): Promise<DomainEnvelopeReviewRowsResponse> {
  return fetchCurationWorkspaceJson<DomainEnvelopeReviewRowsResponse>(
    buildDomainEnvelopeReviewRowsPath(request),
  )
}

export async function fetchCurationWorkspaceEnvelopeReviewRows(
  workspace: CurationWorkspace,
): Promise<DomainEnvelopeReviewRowsResponse[]> {
  const requests = buildCurationWorkspaceEnvelopeReviewRowsRequests(workspace)
  return Promise.all(requests.map((request) => fetchDomainEnvelopeReviewRows(request)))
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
      signal: options.signal,
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

export async function patchCurationEnvelopeField(
  request: CurationEnvelopeFieldPatchRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationEnvelopeFieldPatchResponse> {
  return fetchCurationWorkspaceJson<CurationEnvelopeFieldPatchResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/envelopes/${
      encodeURIComponent(request.envelope_id)
    }/field`,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function waiveCurationValidationFinding(
  request: CurationValidationFindingWaiveRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationValidationFindingWaiveResponse> {
  return fetchCurationWorkspaceJson<CurationValidationFindingWaiveResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/envelopes/${
      encodeURIComponent(request.envelope_id)
    }/validation-findings/${encodeURIComponent(request.finding_id)}/waive`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function deleteCurationCandidate(
  request: CurationCandidateDeleteRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationCandidateDeleteResponse> {
  return fetchCurationWorkspaceJson<CurationCandidateDeleteResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/candidates/${
      encodeURIComponent(request.candidate_id)
    }`,
    {
      method: 'DELETE',
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

export async function fetchSubmissionPreview(
  request: CurationSubmissionPreviewRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationSubmissionPreviewResponse> {
  return fetchCurationWorkspaceJson<CurationSubmissionPreviewResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/submission-preview`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}

export async function executeCurationSubmission(
  request: CurationSubmissionExecuteRequest,
  options: CurationWorkspaceRequestOptions = {},
): Promise<CurationSubmissionExecuteResponse> {
  return fetchCurationWorkspaceJson<CurationSubmissionExecuteResponse>(
    `/api/curation-workspace/sessions/${encodeURIComponent(request.session_id)}/submit`,
    {
      method: 'POST',
      body: JSON.stringify(request),
      keepalive: options.keepalive,
    },
  )
}
