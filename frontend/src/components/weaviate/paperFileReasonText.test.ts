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

import {
  ADD_LITERATURE_TEXT,
  CHECK_REASON_TEXT,
  CHECK_STATUS_TEXT,
  FILE_REMOVAL_REFUSAL_TEXT,
  LIBRARY_ADD_TEXT,
  LIBRARY_FILES_COLUMN_TEXT,
  LIBRARY_REMOVE_TEXT,
  LISTING_REFUSAL_TEXT,
  NOT_TICKABLE_REASON_TEXT,
  OBSERVATION_TEXT,
  SUPPLEMENT_OUTCOME_REASON_TEXT,
  SUPPLEMENT_STATE_TEXT,
  TRY_AGAIN_BUTTON_TEXT,
  UNRECOGNIZED_FILE_TEXT,
  UPLOADED_PAPER_FILES_TEXT,
  addFilesButtonText,
  addFilesRefusalText,
  duplicateCopiesText,
  fewCharactersNote,
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
  sourceFileStatusText,
  supplementOutcomeReasonText,
  supplementWaitEndsText,
  unconvertedTickableSupplementText,
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
    expect(listingRefusalText('not_imported_from_provider', 409, 'ABC Literature')).toBe(
      'Files can be added only to papers imported from ABC.',
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
    expect(importWithSupplementsButtonText(1)).toBe('Import with 1 supplement')
    expect(importWithSupplementsButtonText(4)).toBe('Import with 4 supplements')
    expect(ADD_LITERATURE_TEXT.openInLibraryToAddFiles).toBe('Open it in the Library to add its files.')
    expect(ADD_LITERATURE_TEXT.listingFailedImportMainOnly).toBe(
      'Could not list this paper\'s files. You can import the main paper now and add supplements later from the Library.',
    )
  })

  it('matches job state labels (N7–N9, Card 39) and wait-end message (N10)', () => {
    expect(SUPPLEMENT_STATE_TEXT).toEqual({
      queued: 'Waiting for the main paper',
      waiting: 'Waiting for ABC',
      adding: 'Being added',
      added: 'Added',
      left_out: 'Not added',
    })
    expect(supplementWaitEndsText('3:30 PM')).toBe('ABC has until 3:30 PM to convert them.')
    expect(formatSupplementWaitEndTime('2026-11-24T15:30:00+00:00')).toBe(
      new Date('2026-11-24T15:30:00+00:00').toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }),
    )
    expect(formatSupplementWaitEndTime('not-a-timestamp')).toBeNull()
  })

  it('formats byte limits in SI decimal units matching backend defaults (Card 20 & N26)', () => {
    expect(formatByteLimit(20_000_000)).toBe('20 MB')
    expect(formatByteLimit(2_000_000)).toBe('2 MB')
    expect(formatByteLimit(1_500_000)).toBe('1.5 MB')
    expect(formatByteLimit(2_500_000_000)).toBe('2.5 GB')
    expect(formatByteLimit(500_000)).toBe('500 KB')
    expect(formatByteLimit(512)).toBe('512 bytes')
    expect(formatByteLimit(1)).toBe('1 byte')
  })

  it('matches every per-file outcome code N11–N29, Card 20, Card 40, and Card 8 checks', () => {
    // N11–N28
    expect(supplementOutcomeReasonText('login_expired')).toBe(
      'Not added: your sign-in expired before it could be added.',
    )
    expect(supplementOutcomeReasonText('wait_limit_reached')).toBe(
      'Not added: the import\'s time for supplements ran out before this file could be added.',
    )
    expect(supplementOutcomeReasonText('import_cancelled')).toBe('Not added: the import was cancelled.')
    expect(supplementOutcomeReasonText('supplement_stage_failed')).toBe(
      'Not added: adding this paper\'s supplements failed.',
    )
    expect(supplementOutcomeReasonText('provider_answer_unknown')).toBe(
      'Not added: ABC\'s answer about this file could not be read.',
    )
    expect(supplementOutcomeReasonText('paper_busy_when_ready')).toBe(
      'Not added: the paper was being changed when its main paper became ready.',
    )
    expect(supplementOutcomeReasonText('pdf_supplement_not_supported_yet')).toBe(
      'Not added: PDF supplements could not be added when this import ran.',
    )
    expect(supplementOutcomeReasonText('file_not_in_reference')).toBe(
      'Not added: ABC no longer lists this file for the paper.',
    )
    expect(supplementOutcomeReasonText('file_not_tickable')).toBe(
      'Not added: ABC has no converted text for this file.',
    )
    expect(supplementOutcomeReasonText('file_changed_since_listed')).toBe(
      'Not added: the file changed in ABC after it was listed. Try again.',
    )
    expect(supplementOutcomeReasonText('source_file_missing')).toBe(
      'Not added: its converted text is missing in ABC.',
    )
    expect(supplementOutcomeReasonText('source_unavailable')).toBe(
      'Not added: ABC could not provide its converted text just now. Try again.',
    )
    expect(supplementOutcomeReasonText('source_download_failed')).toBe(
      'Not added: its converted text could not be downloaded. Try again.',
    )
    expect(supplementOutcomeReasonText('access_denied')).toBe(
      'Not added: ABC refused access to its converted text.',
    )
    expect(supplementOutcomeReasonText('markdown_invalid')).toBe(
      'Not added: its converted text could not be read.',
    )
    expect(supplementOutcomeReasonText('paper_text_limit', { limitValue: 2_000_000 })).toBe(
      'Not added: the paper would hold more text than AI Curation reads (2 MB).',
    )
    expect(supplementOutcomeReasonText('paper_text_limit')).toBe(
      'Not added: the paper would hold more text than AI Curation reads.',
    )
    expect(supplementOutcomeReasonText('paper_file_limit', { limitValue: 20 })).toBe(
      'Not added: the paper already has the most files allowed (20).',
    )
    expect(supplementOutcomeReasonText('paper_file_limit')).toBe(
      'Not added: the paper already has the most files allowed.',
    )
    expect(supplementOutcomeReasonText('already_in_paper')).toBe('Already in this paper')

    // Card 20 PDF supplement outcomes
    expect(supplementOutcomeReasonText('pdf_too_large', { limitValue: 20_000_000 })).toBe(
      'This PDF is larger than 20 MB, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_too_large')).toBe(
      'This PDF is larger than the allowed size, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_invalid')).toBe(
      'This PDF could not be opened, so it was not added.',
    )
    expect(supplementOutcomeReasonText('pdf_access_denied')).toBe(
      'You can read this file\'s text in ABC but not its PDF, so it was not added.',
    )

    // Card 40 conversion wait outcomes (first sentence only until Part 4 ships)
    expect(supplementOutcomeReasonText('provider_not_converted')).toBe(
      'ABC has not converted this file.',
    )
    expect(supplementOutcomeReasonText('provider_conversion_failed')).toBe(
      'ABC tried and could not convert this file.',
    )
    expect(supplementOutcomeReasonText('not_converted_in_time')).toBe(
      'ABC had not converted this file when the import stopped waiting.',
    )
    for (const code of ['provider_not_converted', 'not_converted_in_time'] as const) {
      expect(supplementOutcomeReasonText(code)).not.toContain('add it later from the Library')
    }

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
    expect(supplementOutcomeReasonText('something_unknown')).toBe(
      'This screen cannot describe this file yet.',
    )
    expect(supplementOutcomeReasonText(null)).toBe('This screen cannot describe this file yet.')
    expect(supplementOutcomeReasonText(undefined)).toBe('This screen cannot describe this file yet.')
  })

  it('matches Library Add strings N33–N37, Card 34, and Card 41', () => {
    expect(addFilesButtonText(1)).toBe('Add 1 file')
    expect(addFilesButtonText(3)).toBe('Add 3 files')
    expect(LIBRARY_ADD_TEXT.importStillRunning).toBe(
      'This paper\'s import is still running. You can add or remove files when it has finished.',
    )
    expect(LIBRARY_ADD_TEXT.fileRemovalUnfinished).toBe(
      'A file of this paper is still being removed. Try again in a moment.',
    )
    expect(LIBRARY_ADD_TEXT.addingFilesHeading).toBe('Adding files')
    expect(UPLOADED_PAPER_FILES_TEXT).toBe('Files can be added only to papers imported from ABC.')
    expect(LIBRARY_ADD_TEXT.uploadedPaperFiles).toBe(UPLOADED_PAPER_FILES_TEXT)
    expect(LIBRARY_ADD_TEXT.fileNoLongerChoosable).toBe(
      'This file can no longer be chosen: ABC has not converted it and this import will not ask ABC to.',
    )
    expect(paperFileLimitRefusalText(20)).toBe(
      'Too many files for one paper: at most 20, including the main paper.',
    )

    expect(addFilesRefusalText('import_in_progress')).toBe(LIBRARY_ADD_TEXT.importStillRunning)
    expect(addFilesRefusalText('file_removal_unfinished')).toBe(LIBRARY_ADD_TEXT.fileRemovalUnfinished)
    expect(addFilesRefusalText('file_not_tickable')).toBe(LIBRARY_ADD_TEXT.fileNoLongerChoosable)
    expect(addFilesRefusalText('paper_file_limit', { limitValue: 20 })).toBe(
      'Too many files for one paper: at most 20, including the main paper.',
    )
    expect(addFilesRefusalText('paper_file_limit')).toBe('Too many files for one paper.')
    expect(
      addFilesRefusalText('document_source_curator_token_unavailable', { provider: 'ABC Literature' }),
    ).toBe('Sign in again to see this paper\'s files in ABC Literature.')
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

    // Card 2 (W-D1): drops {names}, singular vs plural
    expect(quotedInReviewSessionsText(1)).toBe(
      'This file is quoted in 1 review session. To remove it, delete the paper.',
    )
    expect(quotedInReviewSessionsText(3)).toBe(
      'This file is quoted in 3 review sessions. To remove it, delete the paper.',
    )
    expect(fileRemovalRefusalText('file_quoted_in_review', { sessionCount: 1 })).toBe(
      'This file is quoted in 1 review session. To remove it, delete the paper.',
    )
    expect(fileRemovalRefusalText('file_quoted_in_review', { sessionCount: 2 })).toBe(
      'This file is quoted in 2 review sessions. To remove it, delete the paper.',
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
  })

  it('matches Library Files column N46–N47 and Card 38 Try again', () => {
    expect(LIBRARY_FILES_COLUMN_TEXT.header).toBe('Files')
    expect(filesCountAriaLabel(1)).toBe('1 file')
    expect(filesCountAriaLabel(5)).toBe('5 files')
    expect(TRY_AGAIN_BUTTON_TEXT).toBe('Try again')
  })
})
