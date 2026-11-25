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

  it('does not match result_quotes_removed_file from unstructured detail strings or non-409 statuses and hides raw server detail', async () => {
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

    let unmapped409: unknown = null
    try {
      await runCurationPrep({ session_id: 'chat-session-1', adapter_keys: ['disease'] })
    } catch (error) {
      unmapped409 = error
    }
    expect(unmapped409).toBeInstanceOf(CurationWorkspaceRequestError)
    expect((unmapped409 as Error).message).toBe('Failed to prepare curation scope.')
    expect((unmapped409 as Error).message).not.toContain('Plain message')
    expect((unmapped409 as Error).message).not.toBe(LIBRARY_REMOVE_TEXT.reviewAndCurateAfterRemoval)

    let non409Error: unknown = null
    try {
      await runCurationPrep({ session_id: 'chat-session-1', adapter_keys: ['disease'] })
    } catch (error) {
      non409Error = error
    }
    expect(non409Error).toBeInstanceOf(CurationWorkspaceRequestError)
    expect((non409Error as Error).message).toBe('Failed to prepare curation scope.')
    expect((non409Error as Error).message).not.toContain('result_quotes_removed_file')
  })
})
