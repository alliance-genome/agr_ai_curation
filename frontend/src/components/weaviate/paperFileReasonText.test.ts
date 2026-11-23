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
  duplicateCopiesText,
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
})
