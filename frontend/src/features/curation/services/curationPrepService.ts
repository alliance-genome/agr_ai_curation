import { LIBRARY_REMOVE_TEXT } from '@/components/weaviate/paperFileReasonText'

import {
  RESULT_QUOTES_REMOVED_FILE_ERROR_CODE,
  readCurationApiError,
  readCurationApiErrorWithCode,
} from './api'
import { CurationWorkspaceRequestError } from './curationWorkspaceService'

export interface CurationPrepPreview {
  ready: boolean
  summary_text: string
  candidate_count: number
  unscoped_candidate_count: number
  preparable_candidate_count: number
  extraction_result_count: number
  conversation_message_count: number
  adapter_keys: string[]
  discussed_adapter_keys: string[]
  blocking_reasons: string[]
}

export interface CurationPrepRunRequest {
  session_id: string
  adapter_keys: string[]
}

export interface CurationPrepRunResponse {
  summary_text: string
  document_id: string
  candidate_count: number
  warnings: string[]
  processing_notes: string[]
  adapter_keys: string[]
  prepared_sessions: Array<{
    session_id: string
    adapter_key: string
    created: boolean
  }>
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
    }),
  })

  if (!response.ok) {
    const { message, code } = await readCurationApiErrorWithCode(response)
    if (response.status === 409 && code === RESULT_QUOTES_REMOVED_FILE_ERROR_CODE) {
      throw new CurationWorkspaceRequestError(
        409,
        LIBRARY_REMOVE_TEXT.reviewAndCurateAfterRemoval,
        RESULT_QUOTES_REMOVED_FILE_ERROR_CODE,
      )
    }
    throw new Error(message)
  }

  return response.json() as Promise<CurationPrepRunResponse>
}
