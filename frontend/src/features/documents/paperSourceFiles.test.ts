import { afterEach, describe, expect, it, vi } from 'vitest'

import { PaperSourceFilesContractError, fetchPaperSourceFiles, parsePaperSourceListing } from './paperSourceFiles'
import { PAPER_LISTING } from './paperSourceFilesFixtures'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('paperSourceFiles', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads the per-paper listing in the shape the backend serves', () => {
    const listing = parsePaperSourceListing(PAPER_LISTING)

    expect(listing.files.map((file) => [file.provider_file_id, file.in_this_paper, file.tickable, file.not_tickable_reason]))
      .toEqual([
        ['10', true, false, 'already_in_paper'],
        ['20', false, true, null],
        ['30', false, false, 'check_not_passed'],
        ['40', false, false, 'provider_not_converted_yet'],
        ['50', false, false, 'large_data_file'],
      ])
    expect(listing.files[3]?.last_provider_observation?.code).toBe('not_converted_in_time')
    expect(listing).toMatchObject({ not_shown_count: 2, duplicate_copies_count: 1 })
  })

  it('refuses a code it does not know, rather than guessing', () => {
    const changed = { ...PAPER_LISTING, files: [{ ...PAPER_LISTING.files[1], not_tickable_reason: 'something_new' }] }

    expect(() => parsePaperSourceListing(changed)).toThrow(PaperSourceFilesContractError)
  })

  it('returns a refusal of the whole listing as its code', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: { error: 'paper_reference_mismatch' } }, 409)))

    await expect(fetchPaperSourceFiles('doc-1')).resolves.toEqual({ kind: 'refused', code: 'paper_reference_mismatch' })
  })

  it('fails loudly on any other failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: { error: 'file_listing_failed' } }, 500)))

    await expect(fetchPaperSourceFiles('doc-1')).rejects.toThrow('Failed to list the paper\'s files in the provider (500)')
  })
})
