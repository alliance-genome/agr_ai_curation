import { beforeEach, describe, expect, it, vi } from 'vitest'

import { LIBRARY_REMOVE_TEXT } from '@/components/weaviate/paperFileReasonText'
import { RESULT_QUOTES_REMOVED_FILE_ERROR_CODE } from './api'
import { CurationWorkspaceRequestError } from './curationWorkspaceService'
import { runCurationPrep } from './curationPrepService'

describe('curationPrepService', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset()
  })

  it('maps 409 detail.error result_quotes_removed_file from POST /prep to approved Card 19 wording', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { error: 'result_quotes_removed_file' },
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )

    let caught: unknown = null
    try {
      await runCurationPrep({ session_id: 'chat-session-1', adapter_keys: ['disease'] })
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(CurationWorkspaceRequestError)
    expect(caught).toMatchObject({
      status: 409,
      code: RESULT_QUOTES_REMOVED_FILE_ERROR_CODE,
      message: LIBRARY_REMOVE_TEXT.reviewAndCurateAfterRemoval,
    })
  })

  it('does not match result_quotes_removed_file from unstructured detail strings or non-409 statuses', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: 'Plain message with result_quotes_removed_file',
          }),
          {
            status: 409,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: { error: 'result_quotes_removed_file' },
          }),
          {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )

    await expect(
      runCurationPrep({ session_id: 'chat-session-1', adapter_keys: ['disease'] }),
    ).rejects.toThrow('Plain message with result_quotes_removed_file')

    await expect(
      runCurationPrep({ session_id: 'chat-session-1', adapter_keys: ['disease'] }),
    ).rejects.toThrow('Request failed')
  })
})
