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
  FILE_REMOVAL_REFUSAL_CODES,
  SUPPLEMENT_OUTCOME_REASON_CODES,
  SUPPLEMENT_STATES,
} from '@/features/documents/supplementProgress'
import { normalizeDocumentSourceProvenance } from '@/services/weaviate'

import {
  ADD_LITERATURE_TEXT,
  CHECK_REASON_TEXT,
  CHECK_STATUS_TEXT,
  DEFAULT_PROVIDER_LABEL,
  FILE_REMOVAL_REFUSAL_TEXT,
  LIBRARY_ADD_TEXT,
  LIBRARY_FILES_COLUMN_TEXT,
  LIBRARY_REMOVE_TEXT,
  LISTING_REFUSAL_TEXT,
  NOT_TICKABLE_REASON_TEXT,
  OBSERVATION_TEXT,
  QUEUED_AFTER_MAIN_READY_TEXT,
  SUPPLEMENT_OUTCOME_REASON_TEXT,
  SUPPLEMENT_STATE_TEXT,
  TRY_AGAIN_BUTTON_TEXT,
  UNRECOGNIZED_FILE_TEXT,
  addFilesButtonText,
  addFilesRefusalText,
  duplicateCopiesText,
  fewCharactersNote,
  fileNoLongerChoosableText,
  fileRemovalHttpErrorText,
  fileRemovalRefusalText,
  filesCountAriaLabel,
  formatByteLimit,
  formatSupplementWaitEndTime,
  importWithSupplementsButtonText,
  listingRefusalText,
  notShownText,
  paperFileLimitRefusalText,
  quotedInReviewSessionsText,
  removeFileAriaLabel,
  removeFileDialogTitle,
  resolveProviderShortLabel,
  resolveSupplementProviderShortLabel,
  sourceFileStatusText,
  supplementOutcomeReasonText,
  supplementStateText,
  supplementWaitEndsText,
  unconvertedTickableSupplementText,
  uploadedPaperFilesText,
} from './paperFileReasonText'

describe('paperFileReasonText: one table for every code', () => {
  it('words every code the listing, job outcomes, and removal checks can send', () => {
    expect(Object.keys(NOT_TICKABLE_REASON_TEXT).sort()).toEqual([...NOT_TICKABLE_REASON_CODES].sort())
    expect(Object.keys(CHECK_REASON_TEXT).sort()).toEqual([...CHECK_REASON_CODES].sort())
    expect(Object.keys(CHECK_STATUS_TEXT).sort()).toEqual(CHECK_STATUSES.filter((status) => status !== 'passed' && status !== 'refused').sort())
    expect(Object.keys(OBSERVATION_TEXT).sort()).toEqual([...OBSERVATION_CODES].sort())
    expect(Object.keys(LISTING_REFUSAL_TEXT).sort()).toEqual([...LISTING_REFUSAL_CODES].sort())
    expect(Object.keys(SUPPLEMENT_STATE_TEXT).sort()).toEqual([...SUPPLEMENT_STATES].sort())
    expect(Object.keys(SUPPLEMENT_OUTCOME_REASON_TEXT).sort()).toEqual([...SUPPLEMENT_OUTCOME_REASON_CODES].sort())
    expect(Object.keys(FILE_REMOVAL_REFUSAL_TEXT).sort()).toEqual([...FILE_REMOVAL_REFUSAL_CODES].sort())
  })

  it('retires pdf_supplement_not_supported_yet from listings (ALL-1738 item 1) and keeps N17 for historical jobs', () => {
    expect(NOT_TICKABLE_REASON_CODES).not.toContain('pdf_supplement_not_supported_yet')
    expect(Object.keys(NOT_TICKABLE_REASON_TEXT)).not.toContain('pdf_supplement_not_supported_yet')
    expect(JSON.stringify(NOT_TICKABLE_REASON_TEXT)).not.toContain('PDF supplements cannot be added yet.')

    const [retiredListingFile] = parsePaperSourceListing({
      ...PAPER_LISTING,
      files: [{ ...PAPER_LISTING.files[1], format: 'pdf', tickable: false, not_tickable_reason: 'pdf_supplement_not_supported_yet' }],
    }).files
    expect(retiredListingFile?.recognized).toBe(false)
    expect(sourceFileStatusText(retiredListingFile!, 'ABC Literature')).toBe(UNRECOGNIZED_FILE_TEXT)

    expect(supplementOutcomeReasonText('pdf_supplement_not_supported_yet')).toBe(
      'Not added: PDF supplements could not be added when this import ran.',
    )
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

  it('words listing refusals including Card 34 for uploaded papers and sign-in causes', () => {
    expect(listingRefusalText('not_imported_from_provider', 409, 'ABC')).toBe(
      'Files can be added only to papers imported from ABC.',
    )
    expect(listingRefusalText('not_imported_from_provider', 409, 'Genome Archive')).toBe(
      'Files can be added only to papers imported from Genome Archive.',
    )
    expect(listingRefusalText('document_source_curator_token_unavailable', 409, 'P')).toBe('Sign in again to see this paper\'s files in P.')
    expect(listingRefusalText('document_source_curator_token_unavailable', 503, 'P')).toBe('This server cannot reach P just now, so this paper\'s files there cannot be listed.')
    expect(listingRefusalText('document_source_import_disabled', 503, 'P')).toBeNull()
  })

  it('tells a file not checked yet to refresh, since a reopen soon after checks nothing', () => {
    const [file] = parsePaperSourceListing({
      ...PAPER_LISTING,
      files: [{ ...PAPER_LISTING.files[2], check: { ...PAPER_LISTING.files[2]!.check, status: 'check_limit_reached', reason: null } }],
    }).files

    expect(sourceFileStatusText(file!, 'P')).toBe('Not checked yet. Refresh this paper\'s details to check it.')
  })

  it('states the little-text note as a count, not a guess about images', () => {
    expect(fewCharactersNote(1200)).toBe('Little text: about 1,200 characters')
  })
})

describe('approved supplement screen texts (N1–N29, N33–N44, N46–N47, Cards 2/3/19/20/34/38/39/40/41)', () => {
  it('matches Add Literature strings N1–N6 character-for-character', () => {
    expect(ADD_LITERATURE_TEXT.chooseSupplements).toBe('Choose supplements')
    expect(ADD_LITERATURE_TEXT.mainPaperAlwaysImported).toBe('Main paper (always imported)')
    expect(unconvertedTickableSupplementText('ABC Literature')).toBe(
      'Not converted by ABC Literature yet. If you choose it, the import asks ABC Literature to convert it; the main paper does not wait for it.',
    )
    expect(unconvertedTickableSupplementText('Genome Archive')).toBe(
      'Not converted by Genome Archive yet. If you choose it, the import asks Genome Archive to convert it; the main paper does not wait for it.',
    )
    expect(importWithSupplementsButtonText(1)).toBe('Import with 1 supplement')
    expect(importWithSupplementsButtonText(4)).toBe('Import with 4 supplements')
    expect(ADD_LITERATURE_TEXT.openInLibraryToAddFiles).toBe('Open it in the Library to add its files.')
    expect(ADD_LITERATURE_TEXT.listingFailedImportMainOnly).toBe(
      'Could not list this paper\'s files. You can import the main paper now and add supplements later from the Library.',
    )
  })

  it('matches job state labels (N7, post-main queued, Card 39, N8, N9) and strict wait-end timestamps (N10)', () => {
    expect(QUEUED_AFTER_MAIN_READY_TEXT).toBe('Waiting to be added')
    expect(supplementStateText('queued')).toBe('Waiting for the main paper')
    expect(supplementStateText('queued', { mainReady: false })).toBe('Waiting for the main paper')
    expect(supplementStateText('queued', { mainReady: true })).toBe('Waiting to be added')
    expect(supplementStateText('waiting', { providerShortLabel: 'ABC' })).toBe('Waiting for ABC')
    expect(supplementStateText('waiting', { providerShortLabel: 'Genome Archive' })).toBe('Waiting for Genome Archive')
    expect(supplementStateText('waiting')).toBe(`Waiting for ${DEFAULT_PROVIDER_LABEL}`)
    expect(supplementStateText('adding')).toBe('Being added')
    expect(supplementStateText('added')).toBe('Added')
    expect(supplementStateText('left_out')).toBe('Not added')

    expect(supplementWaitEndsText('3:30 PM', 'ABC')).toBe('ABC has until 3:30 PM to convert them.')
    expect(supplementWaitEndsText('3:30 PM', 'Genome Archive')).toBe(
      'Genome Archive has until 3:30 PM to convert them.',
    )
    expect(supplementWaitEndsText('3:30 PM')).toBe(
      'The literature source has until 3:30 PM to convert them.',
    )

    expect(formatSupplementWaitEndTime('2026-11-24T15:30:00+00:00')).toBe(
      new Date('2026-11-24T15:30:00+00:00').toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }),
    )
    expect(formatSupplementWaitEndTime('0')).toBeNull()
    expect(formatSupplementWaitEndTime('123')).toBeNull()
    expect(formatSupplementWaitEndTime('2026-11-24')).toBeNull()
    expect(formatSupplementWaitEndTime('2026-11-24T15:30:00')).toBeNull()
    expect(formatSupplementWaitEndTime('2026-02-30T15:30:00Z')).toBeNull()
    expect(formatSupplementWaitEndTime('not-a-timestamp')).toBeNull()
  })

  it('formats byte limits exactly without rounding up arbitrary thresholds (Card 20 & N26)', () => {
    expect(formatByteLimit(20_000_000)).toBe('20 MB')
    expect(formatByteLimit(2_000_000)).toBe('2 MB')
    expect(formatByteLimit(1_500_000)).toBe('1.5 MB')
    expect(formatByteLimit(1_060_000)).toBe('1,060,000 bytes')
    expect(formatByteLimit(2_500_000_000)).toBe('2.5 GB')
    expect(formatByteLimit(1_250_000_000)).toBe('1,250,000,000 bytes')
    expect(formatByteLimit(500_000)).toBe('500 KB')
    expect(formatByteLimit(1_060)).toBe('1,060 bytes')
    expect(formatByteLimit(512)).toBe('512 bytes')
    expect(formatByteLimit(1)).toBe('1 byte')
  })

  it('matches every per-file outcome code N11–N29, Card 20, Card 40, and Card 8 checks (with Alliance & non-ABC providers)', () => {
    const abc = { providerShortLabel: 'ABC' }
    const other = { providerShortLabel: 'Genome Archive' }

    // N11–N28 with Alliance short label
    expect(supplementOutcomeReasonText('login_expired', abc)).toBe(
      'Not added: your sign-in expired before it could be added.',
    )
    expect(supplementOutcomeReasonText('wait_limit_reached', abc)).toBe(
      'Not added: the import\'s time for supplements ran out before this file could be added.',
    )
    expect(supplementOutcomeReasonText('import_cancelled', abc)).toBe('Not added: the import was cancelled.')
    expect(supplementOutcomeReasonText('supplement_stage_failed', abc)).toBe(
      'Not added: adding this paper\'s supplements failed.',
    )
    expect(supplementOutcomeReasonText('provider_answer_unknown', abc)).toBe(
      'Not added: ABC\'s answer about this file could not be read.',
    )
    expect(supplementOutcomeReasonText('provider_answer_unknown', other)).toBe(
      'Not added: Genome Archive\'s answer about this file could not be read.',
    )
    expect(supplementOutcomeReasonText('paper_busy_when_ready', abc)).toBe(
      'Not added: the paper was being changed when its main paper became ready.',
    )
    expect(supplementOutcomeReasonText('pdf_supplement_not_supported_yet', abc)).toBe(
      'Not added: PDF supplements could not be added when this import ran.',
    )
    expect(supplementOutcomeReasonText('file_not_in_reference', abc)).toBe(
      'Not added: ABC no longer lists this file for the paper.',
    )
    expect(supplementOutcomeReasonText('file_not_in_reference', other)).toBe(
      'Not added: Genome Archive no longer lists this file for the paper.',
    )
    expect(supplementOutcomeReasonText('file_not_tickable', abc)).toBe(
      'Not added: ABC has no converted text for this file.',
    )
    expect(supplementOutcomeReasonText('file_changed_since_listed', abc)).toBe(
      'Not added: the file changed in ABC after it was listed. Try again.',
    )
    expect(supplementOutcomeReasonText('source_file_missing', abc)).toBe(
      'Not added: its converted text is missing in ABC.',
    )
    expect(supplementOutcomeReasonText('source_unavailable', abc)).toBe(
      'Not added: ABC could not provide its converted text just now. Try again.',
    )
    expect(supplementOutcomeReasonText('source_download_failed', abc)).toBe(
      'Not added: its converted text could not be downloaded. Try again.',
    )
    expect(supplementOutcomeReasonText('access_denied', abc)).toBe(
      'Not added: ABC refused access to its converted text.',
    )
    expect(supplementOutcomeReasonText('markdown_invalid', abc)).toBe(
      'Not added: its converted text could not be read.',
    )
    expect(supplementOutcomeReasonText('paper_text_limit', { limitValue: 2_000_000 })).toBe(
      'Not added: the paper would hold more text than AI Curation reads (2 MB).',
    )
    expect(supplementOutcomeReasonText('paper_text_limit', { limitValue: 1_060_000 })).toBe(
      'Not added: the paper would hold more text than AI Curation reads (1,060,000 bytes).',
    )
    expect(supplementOutcomeReasonText('paper_file_limit', { limitValue: 20 })).toBe(
      'Not added: the paper already has the most files allowed (20).',
    )
    expect(supplementOutcomeReasonText('already_in_paper', abc)).toBe('Already in this paper')

    // Missing or invalid required limits fall back to N29 (no unsigned fallback copy)
    for (const code of ['paper_text_limit', 'paper_file_limit', 'pdf_too_large'] as const) {
      expect(supplementOutcomeReasonText(code)).toBe(UNRECOGNIZED_FILE_TEXT)
      expect(supplementOutcomeReasonText(code, { limitValue: null })).toBe(UNRECOGNIZED_FILE_TEXT)
      expect(supplementOutcomeReasonText(code, { limitValue: 0 })).toBe(UNRECOGNIZED_FILE_TEXT)
      expect(supplementOutcomeReasonText(code, { limitValue: -1 })).toBe(UNRECOGNIZED_FILE_TEXT)
    }

    // Card 20 PDF supplement outcomes
    expect(supplementOutcomeReasonText('pdf_too_large', { limitValue: 20_000_000 })).toBe(
      'This PDF is larger than 20 MB, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_too_large', { limitValue: 1_060_000 })).toBe(
      'This PDF is larger than 1,060,000 bytes, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_invalid')).toBe(
      'This PDF could not be opened, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_access_denied', abc)).toBe(
      'You can read this file\'s text in ABC but not its PDF, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_access_denied', other)).toBe(
      'You can read this file\'s text in Genome Archive but not its PDF, so it was not added.',
    )

    // Card 40 conversion wait outcomes (second sentence restored in Part 4 when Library Add ships)
    expect(supplementOutcomeReasonText('provider_not_converted', abc)).toBe(
      'ABC has not converted this file. You can add it later from the Library once ABC shows it as converted.',
    )
    expect(supplementOutcomeReasonText('provider_conversion_failed', abc)).toBe(
      'ABC tried and could not convert this file.',
    )
    expect(supplementOutcomeReasonText('not_converted_in_time', abc)).toBe(
      'ABC had not converted this file when the import stopped waiting. You can add it later from the Library once ABC shows it as converted.',
    )
    expect(supplementOutcomeReasonText('provider_not_converted', other)).toBe(
      'Genome Archive has not converted this file. You can add it later from the Library once Genome Archive shows it as converted.',
    )
    expect(supplementOutcomeReasonText('not_converted_in_time', other)).toBe(
      'Genome Archive had not converted this file when the import stopped waiting. You can add it later from the Library once Genome Archive shows it as converted.',
    )
    expect(supplementOutcomeReasonText('provider_not_converted')).toBe(
      'The literature source has not converted this file. You can add it later from the Library once the literature source shows it as converted.',
    )
    expect(supplementOutcomeReasonText('not_converted_in_time')).toBe(
      'The literature source had not converted this file when the import stopped waiting. You can add it later from the Library once the literature source shows it as converted.',
    )
    expect(supplementOutcomeReasonText('provider_conversion_failed', abc)).not.toContain(
      'add it later from the Library',
    )

    // Card 8 check refusals emitted during add
    expect(supplementOutcomeReasonText('too_large')).toBe('This file is too large to read reliably')
    expect(supplementOutcomeReasonText('invalid_text')).toBe(
      'Its converted text contains characters that cannot be read',
    )
    expect(supplementOutcomeReasonText('table_cells_mismatch')).toBe(
      'A table in its converted text has rows that do not match its header',
    )
    expect(supplementOutcomeReasonText('table_row_too_wide')).toBe(
      'A table row is too long to keep whole',
    )
    expect(supplementOutcomeReasonText('table_stray_separator')).toBe(
      'A table in its converted text is laid out in a way that cannot be read reliably',
    )
    expect(supplementOutcomeReasonText('title_count')).toBe(
      'Its converted text does not have one clear title, so it cannot be read reliably',
    )

    // N29 fallback for unknown or missing codes
    expect(supplementOutcomeReasonText('something_unknown', abc)).toBe(
      'This screen cannot describe this file yet.',
    )
    expect(supplementOutcomeReasonText(null, abc)).toBe('This screen cannot describe this file yet.')
    expect(supplementOutcomeReasonText(undefined, abc)).toBe('This screen cannot describe this file yet.')
  })

  it('matches Library Add strings N33–N37, Card 34, Card 41, and 409 vs 503 refusals', () => {
    expect(addFilesButtonText(0)).toBe('Add files')
    expect(addFilesButtonText(-1)).toBe('Add files')
    expect(addFilesButtonText(Number.NaN)).toBe('Add files')
    expect(addFilesButtonText(Number.POSITIVE_INFINITY)).toBe('Add files')
    expect(addFilesButtonText(1)).toBe('Add 1 file')
    expect(addFilesButtonText(3)).toBe('Add 3 files')
    expect(LIBRARY_ADD_TEXT.importStillRunning).toBe(
      'This paper\'s import is still running. You can add or remove files when it has finished.',
    )
    expect(LIBRARY_ADD_TEXT.fileRemovalUnfinished).toBe(
      'A file of this paper is still being removed. Try again in a moment.',
    )
    expect(LIBRARY_ADD_TEXT.addingFilesHeading).toBe('Adding files')
    expect(uploadedPaperFilesText('ABC')).toBe('Files can be added only to papers imported from ABC.')
    expect(uploadedPaperFilesText('Genome Archive')).toBe(
      'Files can be added only to papers imported from Genome Archive.',
    )
    expect(fileNoLongerChoosableText('ABC')).toBe(
      'This file can no longer be chosen: ABC has not converted it and this import will not ask ABC to.',
    )
    expect(paperFileLimitRefusalText(20)).toBe(
      'Too many files for one paper: at most 20, including the main paper.',
    )
    expect(paperFileLimitRefusalText(0)).toBe(UNRECOGNIZED_FILE_TEXT)

    expect(addFilesRefusalText('import_in_progress')).toBe(LIBRARY_ADD_TEXT.importStillRunning)
    expect(addFilesRefusalText('file_removal_unfinished')).toBe(LIBRARY_ADD_TEXT.fileRemovalUnfinished)
    expect(addFilesRefusalText('file_not_tickable', { providerShortLabel: 'ABC' })).toBe(
      'This file can no longer be chosen: ABC has not converted it and this import will not ask ABC to.',
    )
    expect(addFilesRefusalText('file_not_tickable', { provider: 'Genome Archive' })).toBe(
      'This file can no longer be chosen: Genome Archive has not converted it and this import will not ask Genome Archive to.',
    )
    expect(addFilesRefusalText('paper_file_limit', { limitValue: 20 })).toBe(
      'Too many files for one paper: at most 20, including the main paper.',
    )
    // Missing or invalid limit_value must return N29 (no unsigned fallback copy)
    expect(addFilesRefusalText('paper_file_limit')).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(addFilesRefusalText('paper_file_limit', { limitValue: null })).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(addFilesRefusalText('paper_file_limit', { limitValue: 0 })).toBe(UNRECOGNIZED_FILE_TEXT)

    // 409 vs 503 disambiguation with a non-ABC provider label, and strict N29 when status or provider is missing/other
    expect(
      addFilesRefusalText('document_source_curator_token_unavailable', {
        httpStatus: 409,
        provider: 'Genome Archive',
      }),
    ).toBe('Sign in again to see this paper\'s files in Genome Archive.')
    expect(
      addFilesRefusalText('document_source_curator_token_unavailable', {
        httpStatus: 503,
        provider: 'Genome Archive',
      }),
    ).toBe('This server cannot reach Genome Archive just now, so this paper\'s files there cannot be listed.')
    expect(
      addFilesRefusalText('document_source_curator_token_unavailable', {
        provider: 'Genome Archive',
      }),
    ).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(
      addFilesRefusalText('document_source_curator_token_unavailable', {
        httpStatus: 502,
        provider: 'Genome Archive',
      }),
    ).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(addFilesRefusalText('document_source_curator_token_unavailable', { httpStatus: 409 })).toBe(
      UNRECOGNIZED_FILE_TEXT,
    )
    expect(
      addFilesRefusalText('document_source_curator_token_unavailable', { httpStatus: 409, provider: '   ' }),
    ).toBe(UNRECOGNIZED_FILE_TEXT)

    expect(addFilesRefusalText('document_source_import_disabled')).toBeNull()
    expect(addFilesRefusalText('unknown_refusal')).toBe(UNRECOGNIZED_FILE_TEXT)
  })

  it('matches Library Remove strings N38–N44, Card 2 (W-D1), Card 3 (W-D2), and Card 19', () => {
    expect(LIBRARY_REMOVE_TEXT.buttonLabel).toBe('Remove')
    expect(removeFileAriaLabel('Table_S1.xlsx')).toBe('Remove Table_S1.xlsx')
    expect(removeFileDialogTitle('Table_S1.xlsx')).toBe('Remove Table_S1.xlsx?')
    expect(LIBRARY_REMOVE_TEXT.confirmBody).toBe(
      'AI Curation will no longer read this file or show it in the viewer. Evidence already saved stays as it is.',
    )
    expect(LIBRARY_REMOVE_TEXT.confirmButton).toBe('Remove file')
    expect(LIBRARY_REMOVE_TEXT.cancelButton).toBe('Cancel')
    expect(LIBRARY_REMOVE_TEXT.newestResultQuotesFileWarning).toBe(
      'This paper\'s newest result quotes this file, so Review & Curate will ask you to run the extraction again.',
    )
    expect(LIBRARY_REMOVE_TEXT.operationInProgress).toBe(
      'Another operation on this paper is in progress; try again in a moment.',
    )
    expect(LIBRARY_REMOVE_TEXT.serverBusy).toBe(
      'The server is busy right now; try removing the file again in a moment.',
    )
    expect(LIBRARY_REMOVE_TEXT.reviewAndCurateAfterRemoval).toBe(
      'This paper\'s newest result quotes a file that was removed from the paper. Run the extraction again to review it.',
    )

    // Card 2 (W-D1): drops {names}, singular vs plural, and never fabricates "1" when missing/0
    expect(quotedInReviewSessionsText(1)).toBe(
      'This file is quoted in 1 review session. To remove it, delete the paper.',
    )
    expect(quotedInReviewSessionsText(3)).toBe(
      'This file is quoted in 3 review sessions. To remove it, delete the paper.',
    )
    expect(quotedInReviewSessionsText(0)).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(fileRemovalRefusalText('file_quoted_in_review', { sessionCount: 1 })).toBe(
      'This file is quoted in 1 review session. To remove it, delete the paper.',
    )
    expect(fileRemovalRefusalText('file_quoted_in_review', { sessionCount: 2 })).toBe(
      'This file is quoted in 2 review sessions. To remove it, delete the paper.',
    )
    expect(fileRemovalRefusalText('file_quoted_in_review')).toBe(UNRECOGNIZED_FILE_TEXT)
    expect(fileRemovalRefusalText('file_quoted_in_review', { sessionCount: 0 })).toBe(
      UNRECOGNIZED_FILE_TEXT,
    )
    expect(fileRemovalRefusalText('file_quoted_in_review', { sessionCount: -2 })).toBe(
      UNRECOGNIZED_FILE_TEXT,
    )

    // N41–N44 + N34/N35 + N29 fallback
    expect(fileRemovalRefusalText('main_file_not_removable')).toBe(
      'The main paper cannot be removed. To remove it, delete the paper.',
    )
    expect(fileRemovalRefusalText('last_file_not_removable')).toBe(
      'A paper\'s only file cannot be removed. To remove it, delete the paper.',
    )
    expect(fileRemovalRefusalText('paper_not_completed')).toBe(
      'Files can be removed once the paper has finished processing.',
    )
    expect(fileRemovalRefusalText('file_not_in_paper')).toBe('This file is no longer in the paper.')
    expect(fileRemovalRefusalText('import_in_progress')).toBe(
      'This paper\'s import is still running. You can add or remove files when it has finished.',
    )
    expect(fileRemovalRefusalText('unknown_code')).toBe(UNRECOGNIZED_FILE_TEXT)

    // Card 3 (W-D2): no N45 cleanup_finished message in UI
    expect(JSON.stringify(LIBRARY_REMOVE_TEXT)).not.toContain('Clearing its stored copies')

    // HTTP refusal mapping strictly by structured code or status (never server detail text)
    expect(
      fileRemovalHttpErrorText({
        httpStatus: 409,
        code: 'file_quoted_in_review',
        sessionCount: 2,
        fallbackText: 'Fallback.',
      }),
    ).toEqual({
      text: 'This file is quoted in 2 review sessions. To remove it, delete the paper.',
      retryable: false,
      code: 'file_quoted_in_review',
    })
    expect(
      fileRemovalHttpErrorText({
        httpStatus: 409,
        code: 'unknown_future_code',
        fallbackText: 'Fallback.',
      }),
    ).toEqual({
      text: UNRECOGNIZED_FILE_TEXT,
      retryable: false,
      code: 'unknown_future_code',
    })
    expect(
      fileRemovalHttpErrorText({
        httpStatus: 409,
        code: null,
        fallbackText: 'Fallback.',
      }),
    ).toEqual({
      text: LIBRARY_REMOVE_TEXT.operationInProgress,
      retryable: true,
      code: null,
    })
    expect(
      fileRemovalHttpErrorText({
        httpStatus: 503,
        code: null,
        fallbackText: 'Fallback.',
      }),
    ).toEqual({
      text: LIBRARY_REMOVE_TEXT.serverBusy,
      retryable: true,
      code: null,
    })
    expect(
      fileRemovalHttpErrorText({
        httpStatus: 500,
        code: null,
        fallbackText: 'Fallback.',
      }),
    ).toEqual({
      text: 'Fallback.',
      retryable: true,
      code: null,
    })
  })

  it('resolves provider short labels from presentation metadata cleanly without Alliance defaults', () => {
    expect(resolveProviderShortLabel({ providerShortLabel: 'ABC', providerLabel: 'ABC Literature' })).toBe('ABC')
    expect(resolveProviderShortLabel({ providerLabel: 'Genome Archive' })).toBe('Genome Archive')
    expect(resolveProviderShortLabel()).toBe(DEFAULT_PROVIDER_LABEL)
    expect(
      resolveSupplementProviderShortLabel({
        providerMetadata: { shortLabel: 'GA', displayLabel: 'Genome Archive' },
      }),
    ).toBe('GA')
    expect(
      resolveSupplementProviderShortLabel({
        providerMetadata: { displayLabel: 'Genome Archive' },
      }),
    ).toBe('Genome Archive')
    expect(resolveSupplementProviderShortLabel(null)).toBeNull()

    // End-to-end Alliance provider presentation wire shape -> exact approved wording
    const provenance = normalizeDocumentSourceProvenance({
      provider: 'abc_literature',
      provider_metadata: {
        display_label: 'ABC Literature',
        short_label: 'ABC',
      },
    })
    const shortLabel = resolveSupplementProviderShortLabel(provenance)
    expect(shortLabel).toBe('ABC')
    expect(supplementStateText('waiting', { providerShortLabel: shortLabel })).toBe('Waiting for ABC')
    expect(supplementWaitEndsText('3:30 PM', shortLabel)).toBe('ABC has until 3:30 PM to convert them.')
    expect(supplementOutcomeReasonText('provider_answer_unknown', { providerShortLabel: shortLabel })).toBe(
      'Not added: ABC\'s answer about this file could not be read.',
    )
    expect(supplementOutcomeReasonText('file_not_in_reference', { providerShortLabel: shortLabel })).toBe(
      'Not added: ABC no longer lists this file for the paper.',
    )
    expect(supplementOutcomeReasonText('pdf_access_denied', { providerShortLabel: shortLabel })).toBe(
      'You can read this file\'s text in ABC but not its PDF, so it was not added.',
    )
    expect(supplementOutcomeReasonText('not_converted_in_time', { providerShortLabel: shortLabel })).toBe(
      'ABC had not converted this file when the import stopped waiting. You can add it later from the Library once ABC shows it as converted.',
    )
  })

  it('matches Library Files column N46–N47 and Card 38 Try again', () => {
    expect(LIBRARY_FILES_COLUMN_TEXT.header).toBe('Files')
    expect(filesCountAriaLabel(1)).toBe('1 file')
    expect(filesCountAriaLabel(5)).toBe('5 files')
    expect(TRY_AGAIN_BUTTON_TEXT).toBe('Try again')
  })
})
