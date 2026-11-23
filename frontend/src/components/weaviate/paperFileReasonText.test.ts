import { describe, expect, it } from 'vitest'

import {
  CHECK_REASON_CODES,
  CHECK_STATUSES,
  LISTING_REFUSAL_CODES,
  NOT_TICKABLE_REASON_CODES,
  OBSERVATION_CODES,
  parsePaperSourceListing,
} from '@/features/documents/paperSourceFiles'
import { PAPER_LISTING } from '@/features/documents/paperSourceFilesFixtures'

import {
  CHECK_REASON_TEXT,
  CHECK_STATUS_TEXT,
  LISTING_REFUSAL_TEXT,
  NOT_TICKABLE_REASON_TEXT,
  OBSERVATION_TEXT,
  UNRECOGNIZED_FILE_TEXT,
  duplicateCopiesText,
  fewCharactersNote,
  listingRefusalText,
  notShownText,
  sourceFileStatusText,
} from './paperFileReasonText'

describe('paperFileReasonText: one table for every code', () => {
  it('words every code the listing can send', () => {
    expect(Object.keys(NOT_TICKABLE_REASON_TEXT).sort()).toEqual([...NOT_TICKABLE_REASON_CODES].sort())
    expect(Object.keys(CHECK_REASON_TEXT).sort()).toEqual([...CHECK_REASON_CODES].sort())
    expect(Object.keys(CHECK_STATUS_TEXT).sort()).toEqual(CHECK_STATUSES.filter((status) => status !== 'passed' && status !== 'refused').sort())
    expect(Object.keys(OBSERVATION_TEXT).sort()).toEqual([...OBSERVATION_CODES].sort())
    expect(Object.keys(LISTING_REFUSAL_TEXT).sort()).toEqual([...LISTING_REFUSAL_CODES].sort())
  })

  it('names the provider by its configured label, never a fixed name', () => {
    const files = parsePaperSourceListing(PAPER_LISTING).files

    expect(sourceFileStatusText(files[3]!, 'Made-up Provider')).toBe('Not converted by Made-up Provider yet')
    expect(sourceFileStatusText(files[1]!, 'Made-up Provider')).toBe('Converted by Made-up Provider; not added to this paper')
  })

  it('shows the check\'s own reason for a file whose check did not pass', () => {
    const files = parsePaperSourceListing(PAPER_LISTING).files

    expect(sourceFileStatusText(files[2]!, 'P')).toBe('A table in its converted text has rows that do not match its header')
  })

  it('counts in the singular and the plural', () => {
    expect(notShownText(1, 'P')).toBe('1 more file in P is not listed because you cannot open it.')
    expect(notShownText(3, 'P')).toBe('3 more files in P are not listed because you cannot open them.')
    expect(duplicateCopiesText(1)).toBe('1 further copy of a listed file is not listed again.')
    expect(duplicateCopiesText(2)).toBe('2 further copies of listed files are not listed again.')
  })

  it('shows what stopped a check that could not run', () => {
    const [file] = parsePaperSourceListing({
      ...PAPER_LISTING,
      files: [{ ...PAPER_LISTING.files[2], check: { ...PAPER_LISTING.files[2]!.check, status: 'text_unavailable', reason: null } }],
    }).files

    expect(sourceFileStatusText(file!, 'P')).toBe('P could not provide its converted text just now')
  })

  it('words a different main file from its code, with or without converted text', () => {
    const main = { ...PAPER_LISTING.files[0], in_this_paper: false, tickable: false, not_tickable_reason: 'main_differs_from_paper' }
    for (const converted of [{}, { converted_text_id: null, converted_md5: null }]) {
      const [file] = parsePaperSourceListing({ ...PAPER_LISTING, files: [{ ...main, ...converted }] }).files

      expect(sourceFileStatusText(file!, 'P')).toBe('P now has a different main file for this paper than the one in the Library')
    }
  })

  it('refuses a main file offered for adding', () => {
    expect(() => parsePaperSourceListing({
      ...PAPER_LISTING,
      files: [{ ...PAPER_LISTING.files[0], in_this_paper: false, tickable: true, not_tickable_reason: null }],
    })).toThrow('The per-paper listing never offers the main file for adding')
  })

  it('refuses a supplement offered for adding without converted text', () => {
    expect(() => parsePaperSourceListing({
      ...PAPER_LISTING,
      files: [{ ...PAPER_LISTING.files[1], converted_text_id: null, converted_md5: null }],
    })).toThrow('A supplement that can be added must have converted text')
  })

  it('keeps a file with an unknown code as its own row, never showing the code', () => {
    const files = parsePaperSourceListing({
      ...PAPER_LISTING,
      files: [
        { ...PAPER_LISTING.files[2], check: { ...PAPER_LISTING.files[2]!.check, reason: 'a_new_check' } },
        { ...PAPER_LISTING.files[3], last_provider_observation: { code: 'a_new_observation', observed_at: '2026-11-22T14:05:00+00:00' } },
        PAPER_LISTING.files[1],
      ],
    }).files

    expect(files.map((file) => file.recognized)).toEqual([false, false, true])
    expect(sourceFileStatusText(files[0]!, 'P')).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(UNRECOGNIZED_FILE_TEXT).not.toMatch(/a_new/)
  })

  it('words the sign-in code by its cause', () => {
    expect(listingRefusalText('document_source_curator_token_unavailable', 409, 'P')).toBe('Sign in again to see this paper\'s files in P.')
    expect(listingRefusalText('document_source_curator_token_unavailable', 503, 'P')).toBe('This server cannot reach P just now, so this paper\'s files there cannot be listed.')
    expect(listingRefusalText('document_source_import_disabled', 503, 'P')).toBeNull()
  })

  it('states the little-text note as a count, not a guess about images', () => {
    expect(fewCharactersNote(1200)).toBe('Little text: about 1,200 characters')
  })
})
