import { readCurationApiError } from './api'

export interface CurationPrepPreview {
  ready: boolean
  summary_text: string
  candidate_count: number
  extraction_result_count: number
  conversation_message_count: number
  adapter_keys: string[]
  profile_keys: string[]
  domain_keys: string[]
  blocking_reasons: string[]
}

export interface CurationPrepRunRequest {
  session_id: string
  adapter_keys: string[]
  profile_keys: string[]
  domain_keys: string[]
}

export interface CurationPrepRunResponse {
  summary_text: string
  candidate_count: number
  warnings: string[]
  processing_notes: string[]
  adapter_keys: string[]
  profile_keys: string[]
  domain_keys: string[]
}

export async function fetchCurationPrepPreview(sessionId: string): Promise<CurationPrepPreview> {
  const response = await fetch(
    `/api/curation-workspace/prep/preview?session_id=${encodeURIComponent(sessionId)}`,
    {
      credentials: 'include',
    }
  )

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  return response.json() as Promise<CurationPrepPreview>
}

export async function runCurationPrep(request: CurationPrepRunRequest): Promise<CurationPrepRunResponse> {
  const response = await fetch('/api/curation-workspace/prep', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      session_id: request.session_id,
      adapter_keys: request.adapter_keys,
      profile_keys: request.profile_keys,
      domain_keys: request.domain_keys,
    }),
  })

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  return response.json() as Promise<CurationPrepRunResponse>
}
