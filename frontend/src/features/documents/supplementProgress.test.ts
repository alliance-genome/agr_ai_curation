import { describe, expect, it } from 'vitest'

import fileListingFixture from '@/test/fixtures/supplements/file_listing.json'
import removalCheckFixture from '@/test/fixtures/supplements/removal_check.json'
import supplementAddMetadataFixture from '@/test/fixtures/supplements/supplement_add_metadata.json'
import supplementWaitMetadataFixture from '@/test/fixtures/supplements/supplement_wait_metadata.json'

import { parsePaperSourceListing } from './paperSourceFiles'
import {
  ADDING_SUPPLEMENTS_STAGE,
  DocumentFileRemovalContractError,
  WAITING_FOR_SUPPLEMENTS_STAGE,
  getJobSupplementProgress,
  hasWaitingSupplements,
  isKnownSupplementOutcomeReasonCode,
  isPostMainSupplementPhase,
  isValidIsoTimestampWithTz,
  parseDocumentFileRemovalCheckResponse,
  parseDocumentFileRemovalResponse,
  parseSupplementJobProgress,
  type DocumentFileRemovalCheckResponse,
} from './supplementProgress'

describe('supplementProgress parser (interface I1)', () => {
  it('parses backend-generated real-shape supplement_wait and supplement_add fixtures', () => {
    const mixed = parseSupplementJobProgress(supplementWaitMetadataFixture.mixed_waiting, {
      currentStage: WAITING_FOR_SUPPLEMENTS_STAGE,
    })
    expect(mixed).not.toBeNull()
    expect(mixed?.job_kind).toBe('supplement_wait')
    expect(mixed?.current_stage).toBe(WAITING_FOR_SUPPLEMENTS_STAGE)
    expect(mixed?.supplement_wait_ends_at).toBe('2026-11-24T15:30:00+00:00')
    expect(mixed?.supplements).toHaveLength(2)
    expect(mixed?.supplements[0]).toMatchObject({
      recognized: true,
      provider_file_id: '20',
      display_name: 'Converted_S1.xlsx',
      state: 'queued',
    })
    expect(mixed?.supplements[1]).toMatchObject({
      recognized: true,
      provider_file_id: '21',
      display_name: 'Pending_S2.docx',
      state: 'waiting',
    })
    expect(hasWaitingSupplements(mixed)).toBe(true)
    expect(isPostMainSupplementPhase(mixed!)).toBe(true)

    const allWait = parseSupplementJobProgress(supplementWaitMetadataFixture.all_states_and_outcomes)
    expect(allWait?.supplements).toHaveLength(9)
    expect(allWait?.supplements.every((entry) => entry.recognized)).toBe(true)

    const paperBusy = parseSupplementJobProgress(supplementWaitMetadataFixture.paper_busy_when_ready)
    expect(paperBusy?.job_kind).toBeNull()
    expect(paperBusy?.supplement_wait_ends_at).toBeNull()
    expect(paperBusy?.supplements).toHaveLength(1)
    expect(paperBusy?.supplements[0]).toMatchObject({
      recognized: true,
      provider_file_id: '90',
      state: 'left_out',
      reason_code: 'paper_busy_when_ready',
    })

    const addProgress = parseSupplementJobProgress(supplementAddMetadataFixture)
    expect(addProgress?.job_kind).toBe('supplement_add')
    expect(addProgress?.supplements).toHaveLength(10)
    expect(addProgress?.supplements[1]).toMatchObject({
      recognized: true,
      provider_file_id: '101',
      display_name: null,
      state: 'left_out',
      reason_code: 'file_not_in_reference',
    })
    expect(hasWaitingSupplements(addProgress)).toBe(false)
    expect(isPostMainSupplementPhase(addProgress!)).toBe(true)
  })

  it('matches backend-generated file_listing and removal_check fixtures', () => {
    const listing = parsePaperSourceListing(fileListingFixture)
    expect(listing.files).toHaveLength(6)
    expect(listing.files.every((file) => file.recognized)).toBe(true)
    expect(listing.not_shown_count).toBe(2)
    expect(listing.duplicate_copies_count).toBe(1)

    const checks: Record<string, DocumentFileRemovalCheckResponse> = removalCheckFixture
    expect(checks.removable_plain?.removable).toBe(true)
    expect(checks.removable_with_review_warning?.review_and_curate_would_refuse).toBe(true)
    expect(checks.refused_file_quoted_in_review_single?.session_ids).toEqual(['session-1'])
    expect(checks.refused_file_quoted_in_review_multiple?.session_ids).toEqual(['session-1', 'session-2'])
    expect(checks.refused_main_file?.error).toBe('main_file_not_removable')
    expect(checks.refused_last_file?.error).toBe('last_file_not_removable')
    expect(checks.refused_import_in_progress?.error).toBe('import_in_progress')
    expect(checks.refused_paper_not_completed?.error).toBe('paper_not_completed')
    expect(checks.refused_file_not_in_paper?.error).toBe('file_not_in_paper')
  })

  it('returns null when metadata is absent, not an array, or empty', () => {
    for (const candidate of [
      null,
      undefined,
      'not-an-object',
      42,
      [],
      {},
      { supplements: null },
      { supplements: 'not-a-list' },
      { supplements: [] },
      { job_kind: 'supplement_wait', supplement_wait_ends_at: '2026-11-24T15:30:00+00:00', supplements: [] },
    ]) {
      expect(parseSupplementJobProgress(candidate)).toBeNull()
    }
    expect(getJobSupplementProgress(null)).toBeNull()
    expect(getJobSupplementProgress({ metadata: null })).toBeNull()
    expect(hasWaitingSupplements(null)).toBe(false)
  })

  it('preserves unrecognized or malformed entries as unrecognized rows instead of dropping chosen files', () => {
    const parsed = parseSupplementJobProgress({
      job_kind: 'unknown_kind',
      supplement_wait_ends_at: 'not-a-date',
      supplements: [
        null,
        { provider_file_id: 'bad-state', display_name: 'Mystery_S1.xlsx', state: 'future_state' },
        { display_name: 'MissingId_S2.xlsx', state: 'waiting' },
        {
          provider_file_id: '50',
          display_name: '   ',
          state: 'left_out',
          reason_code: 'future_backend_reason',
          observed_at: 999,
          file_id: '',
          measured_value: -5,
          limit_value: 12.5,
        },
        {
          provider_file_id: '60',
          display_name: 'Big.pdf',
          state: 'left_out',
          reason_code: 'pdf_too_large',
          observed_at: null,
          file_id: null,
          measured_value: Number.NaN,
          limit_value: 20_000_000,
        },
      ],
    })

    expect(parsed).toEqual({
      job_kind: null,
      current_stage: null,
      supplement_wait_ends_at: null,
      supplements: [
        {
          recognized: false,
          provider_file_id: null,
          display_name: null,
        },
        {
          recognized: false,
          provider_file_id: 'bad-state',
          display_name: 'Mystery_S1.xlsx',
        },
        {
          recognized: false,
          provider_file_id: null,
          display_name: 'MissingId_S2.xlsx',
        },
        {
          recognized: true,
          provider_file_id: '50',
          display_name: null,
          state: 'left_out',
          reason_code: 'future_backend_reason',
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
        {
          recognized: true,
          provider_file_id: '60',
          display_name: 'Big.pdf',
          state: 'left_out',
          reason_code: 'pdf_too_large',
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: 20_000_000,
        },
      ],
    })
    expect(hasWaitingSupplements(parsed)).toBe(false)
  })

  it('validates supplement_wait_ends_at strictly as an ISO-8601 timestamp with timezone', () => {
    for (const valid of [
      '2026-11-24T15:30:00+00:00',
      '2026-11-24T15:30:00.123456+00:00',
      '2026-11-24T15:30:00Z',
      '2026-11-24T11:30:00-04:00',
    ]) {
      expect(isValidIsoTimestampWithTz(valid)).toBe(true)
    }

    for (const invalid of [
      '0',
      '123',
      '2026-11-24',
      '2026-11-24T15:30:00',
      '2026-02-30T15:30:00Z',
      '2026-13-01T15:30:00Z',
      ' 2026-11-24T15:30:00Z ',
      'not-a-date',
      '',
      null,
      123,
    ]) {
      expect(isValidIsoTimestampWithTz(invalid)).toBe(false)
    }

    const parsed = parseSupplementJobProgress({
      supplements: [
        { provider_file_id: '20', display_name: 'S1.xlsx', state: 'waiting' },
      ],
      supplement_wait_ends_at: '0',
    })
    expect(parsed?.supplement_wait_ends_at).toBeNull()
  })

  it('distinguishes pre-main vs post-main supplement phases cleanly', () => {
    const preMain = getJobSupplementProgress({
      current_stage: 'extracting',
      metadata: {
        supplements: [
          { provider_file_id: '20', display_name: 'S1.xlsx', state: 'queued' },
        ],
      },
    })
    expect(preMain).not.toBeNull()
    expect(isPostMainSupplementPhase(preMain!)).toBe(false)
    expect(isPostMainSupplementPhase(preMain!, WAITING_FOR_SUPPLEMENTS_STAGE)).toBe(true)
    expect(isPostMainSupplementPhase(preMain!, ADDING_SUPPLEMENTS_STAGE)).toBe(true)

    const postMainByJobKind = getJobSupplementProgress({
      current_stage: 'pending',
      metadata: {
        job_kind: 'supplement_add',
        supplements: [
          { provider_file_id: '20', display_name: 'S1.xlsx', state: 'queued' },
        ],
      },
    })
    expect(isPostMainSupplementPhase(postMainByJobKind!)).toBe(true)
  })

  it('distinguishes known vs unknown supplement outcome reason codes', () => {
    expect(isKnownSupplementOutcomeReasonCode('import_cancelled')).toBe(true)
    expect(isKnownSupplementOutcomeReasonCode('pdf_supplement_not_supported_yet')).toBe(true)
    expect(isKnownSupplementOutcomeReasonCode('pdf_invalid')).toBe(true)
    expect(isKnownSupplementOutcomeReasonCode('not_converted_in_time')).toBe(true)
    expect(isKnownSupplementOutcomeReasonCode('brand_new_code')).toBe(false)
    expect(isKnownSupplementOutcomeReasonCode(null)).toBe(false)
    expect(isKnownSupplementOutcomeReasonCode(undefined)).toBe(false)
  })

  it('strictly validates removal-check and removal responses against backend fixtures', () => {
    for (const [caseName, fixture] of Object.entries(removalCheckFixture)) {
      expect(parseDocumentFileRemovalCheckResponse(fixture), caseName).toEqual(fixture)
    }

    // Future unknown refusal code is preserved so the UI can word it as N29 instead of crashing.
    expect(
      parseDocumentFileRemovalCheckResponse({
        ...removalCheckFixture.refused_main_file,
        error: 'future_refusal_code',
      }).error,
    ).toBe('future_refusal_code')

    for (const badCheck of [
      null,
      [],
      {},
      { ...removalCheckFixture.removable_plain, document_id: '' },
      { ...removalCheckFixture.removable_plain, file_id: '   ' },
      { ...removalCheckFixture.removable_plain, removable: 'true' },
      { ...removalCheckFixture.removable_plain, error: 'unexpected_error_when_removable' },
      { ...removalCheckFixture.refused_main_file, error: null },
      { ...removalCheckFixture.removable_plain, newest_result_quotes_file: 'no' },
      { ...removalCheckFixture.removable_plain, has_review_session: null },
      { ...removalCheckFixture.removable_plain, review_and_curate_would_refuse: 0 },
      { ...removalCheckFixture.refused_file_quoted_in_review_single, session_ids: 'session-1' },
      { ...removalCheckFixture.refused_file_quoted_in_review_single, session_ids: [''] },
      { ...removalCheckFixture.refused_file_quoted_in_review_single, session_ids: [123] },
    ]) {
      expect(() => parseDocumentFileRemovalCheckResponse(badCheck)).toThrow(
        DocumentFileRemovalContractError,
      )
    }

    expect(
      parseDocumentFileRemovalResponse({
        document_id: 'doc-1',
        file_id: 'file-2',
        removed: true,
        cleanup_finished: true,
        ready_file_count: 1,
      }),
    ).toEqual({
      document_id: 'doc-1',
      file_id: 'file-2',
      removed: true,
      cleanup_finished: true,
      ready_file_count: 1,
    })

    expect(
      parseDocumentFileRemovalResponse({
        document_id: 'doc-1',
        file_id: 'file-2',
        removed: true,
        cleanup_finished: false,
        ready_file_count: 2,
      }),
    ).toEqual({
      document_id: 'doc-1',
      file_id: 'file-2',
      removed: true,
      cleanup_finished: false,
      ready_file_count: 2,
    })

    for (const badRemoval of [
      null,
      {},
      { document_id: 'doc-1', file_id: 'file-2', removed: false, cleanup_finished: true, ready_file_count: 1 },
      { document_id: 'doc-1', file_id: 'file-2', removed: true, cleanup_finished: 'yes', ready_file_count: 1 },
      { document_id: 'doc-1', file_id: 'file-2', removed: true, cleanup_finished: true, ready_file_count: -1 },
      { document_id: 'doc-1', file_id: 'file-2', removed: true, cleanup_finished: true, ready_file_count: 1.5 },
      { document_id: '', file_id: 'file-2', removed: true, cleanup_finished: true, ready_file_count: 1 },
    ]) {
      expect(() => parseDocumentFileRemovalResponse(badRemoval)).toThrow(
        DocumentFileRemovalContractError,
      )
    }
  })
})
