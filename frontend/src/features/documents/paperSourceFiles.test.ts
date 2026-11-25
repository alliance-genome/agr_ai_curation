import { afterEach, describe, expect, it, vi } from 'vitest'

import fileListingFixture from '@/test/fixtures/supplements/file_listing.json'

import {
  PaperSourceFilesContractError,
  fetchPaperSourceFiles,
  parseIdentifierSourceListing,
  parsePaperSourceListing,
} from './paperSourceFiles'
import { PAPER_LISTING } from './paperSourceFilesFixtures'

function recognized<T extends { recognized: boolean }>(
  file: T | undefined,
): Extract<T, { recognized: true }> {
  if (!file?.recognized) {
    throw new Error('expected a recognised file')
  }
  return file as Extract<T, { recognized: true }>
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('paperSourceFiles', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads the per-paper listing in the shape the backend serves', () => {
    const listing = parsePaperSourceListing(PAPER_LISTING)

    expect(listing.files.map(recognized).map((file) => [file.provider_file_id, file.in_this_paper, file.tickable, file.not_tickable_reason]))
      .toEqual([
        ['10', true, false, 'already_in_paper'],
        ['20', false, true, null],
        ['30', false, false, 'check_not_passed'],
        ['40', false, false, 'provider_not_converted_yet'],
        ['50', false, false, 'large_data_file'],
      ])
    expect(recognized(listing.files[3]).last_provider_observation?.code).toBe('not_converted_in_time')
    expect(listing).toMatchObject({ not_shown_count: 2, duplicate_copies_count: 1 })
  })

  it('keeps a file with a code it does not know as an unrecognised row, rather than guessing', () => {
    const changed = { ...PAPER_LISTING, files: [{ ...PAPER_LISTING.files[4], not_tickable_reason: 'something_new' }] }

    expect(parsePaperSourceListing(changed).files).toEqual([
      { recognized: false, provider_file_id: '50', display_name: 'Made_up_S50', in_this_paper: false },
    ])
  })

  it('refuses a file that is neither tickable nor gives a reason, or both', () => {
    for (const shape of [
      { tickable: false, not_tickable_reason: null },
      { tickable: true, not_tickable_reason: 'large_data_file' },
    ]) {
      expect(() => parsePaperSourceListing({ ...PAPER_LISTING, files: [{ ...PAPER_LISTING.files[1], ...shape }] }))
        .toThrow(PaperSourceFilesContractError)
    }
  })

  it('refuses a malformed file or code as a contract error', () => {
    expect(() => parsePaperSourceListing({ ...PAPER_LISTING, files: [{ ...PAPER_LISTING.files[1], not_tickable_reason: 7 }] }))
      .toThrow(PaperSourceFilesContractError)
    expect(() => parsePaperSourceListing({ ...PAPER_LISTING, files: [{ ...PAPER_LISTING.files[1], status: 'something_new' }] }))
      .toThrow(PaperSourceFilesContractError)
  })

  it('returns a refusal of the whole listing as its code', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: { error: 'paper_reference_mismatch' } }, 409)))

    await expect(fetchPaperSourceFiles('doc-1')).resolves.toEqual({ kind: 'refused', code: 'paper_reference_mismatch', httpStatus: 409 })
  })

  it('preserves md5 and converted_md5 from backend-generated file_listing fixtures', () => {
    const listing = parsePaperSourceListing(fileListingFixture)
    const tickable = recognized(listing.files[1])
    expect(tickable.tickable).toBe(true)
    expect(tickable.md5).toBe('original-md5-20')
    expect(tickable.converted_md5).toBe('converted-md5-20')

    const nullMd5Listing = parsePaperSourceListing({
      ...fileListingFixture,
      files: [{ ...fileListingFixture.files[1], md5: null }],
    })
    expect(recognized(nullMd5Listing.files[0]).md5).toBeNull()
  })

  it('refuses a tickable supplement with an empty converted_md5', () => {
    expect(() => parsePaperSourceListing({
      ...fileListingFixture,
      files: [{ ...fileListingFixture.files[1], converted_md5: '' }],
    })).toThrow(PaperSourceFilesContractError)
  })

  it('fails loudly on any other failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: { error: 'file_listing_failed' } }, 500)))

    await expect(fetchPaperSourceFiles('doc-1')).rejects.toThrow('Failed to list the paper\'s files in the provider (500)')
  })

  it('parses identifier-route listings with tickable main files, tickable unconverted supplements, md5, and existing_document_id', () => {
    const identifierPayload = {
      ...fileListingFixture,
      files: [
        {
          ...fileListingFixture.files[0],
          in_this_paper: false,
          converted_text_id: null,
          converted_md5: null,
          tickable: true,
          not_tickable_reason: null,
          check: { ...fileListingFixture.files[0]!.check, status: 'not_checked' },
        },
        fileListingFixture.files[1],
        {
          ...fileListingFixture.files[3],
          tickable: true,
          not_tickable_reason: null,
          last_provider_observation: null,
        },
      ],
    }

    const parsed = parseIdentifierSourceListing(identifierPayload)
    expect(parsed.main_status).toBe('ready')
    expect(parsed.existing_document_id).toBeNull()
    expect(parsed.not_shown_count).toBe(2)
    expect(parsed.duplicate_copies_count).toBe(1)
    expect(parsed.files.map(recognized).map((file) => ({
      id: file.provider_file_id,
      role: file.file_role,
      md5: file.md5,
      converted_md5: file.converted_md5,
      tickable: file.tickable,
      status: file.status,
    }))).toEqual([
      { id: '10', role: 'main', md5: 'main-md5', converted_md5: null, tickable: true, status: 'ready' },
      { id: '20', role: 'supplement', md5: 'original-md5-20', converted_md5: 'converted-md5-20', tickable: true, status: 'ready' },
      { id: '40', role: null, md5: 'original-md5-40', converted_md5: null, tickable: true, status: 'no_converted_text' },
    ])
  })

  it('rejects invalid tickable supplements on identifier-route listings', () => {
    expect(() => parseIdentifierSourceListing({
      ...fileListingFixture,
      files: [{ ...fileListingFixture.files[1], converted_md5: null }],
    })).toThrow(PaperSourceFilesContractError)
    expect(() => parseIdentifierSourceListing({
      ...fileListingFixture,
      files: [{ ...fileListingFixture.files[4], tickable: true, not_tickable_reason: null }],
    })).toThrow(PaperSourceFilesContractError)
    expect(() => parseIdentifierSourceListing({
      ...fileListingFixture,
      main_status: '',
    })).toThrow(PaperSourceFilesContractError)
  })
})
