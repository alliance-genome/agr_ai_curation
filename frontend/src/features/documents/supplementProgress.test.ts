import { describe, expect, it } from 'vitest'

import {
  getJobSupplementProgress,
  hasWaitingSupplements,
  isKnownSupplementOutcomeReasonCode,
  parseSupplementJobProgress,
} from './supplementProgress'

describe('supplementProgress parser (interface I1)', () => {
  it('parses valid supplement_wait and supplement_add job metadata in backend wire shapes', () => {
    const metadata = {
      job_kind: 'supplement_wait',
      supplement_wait_ends_at: '2026-11-24T15:30:00+00:00',
      supplements: [
        {
          provider_file_id: '20',
          display_name: 'Table_S1.xlsx',
          state: 'waiting',
          reason_code: null,
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '30',
          display_name: 'Figure_S2.pdf',
          state: 'added',
          reason_code: null,
          observed_at: null,
          file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '40',
          display_name: null,
          state: 'left_out',
          reason_code: 'paper_file_limit',
          observed_at: '2026-11-24T15:10:00+00:00',
          file_id: null,
          measured_value: 21,
          limit_value: 20,
        },
      ],
    }

    const parsed = parseSupplementJobProgress(metadata)

    expect(parsed).toEqual({
      job_kind: 'supplement_wait',
      supplement_wait_ends_at: '2026-11-24T15:30:00+00:00',
      supplements: [
        {
          provider_file_id: '20',
          display_name: 'Table_S1.xlsx',
          state: 'waiting',
          reason_code: null,
          observed_at: null,
          file_id: null,
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '30',
          display_name: 'Figure_S2.pdf',
          state: 'added',
          reason_code: null,
          observed_at: null,
          file_id: 'aaaaaaaa-0000-0000-0000-000000000002',
          measured_value: null,
          limit_value: null,
        },
        {
          provider_file_id: '40',
          display_name: null,
          state: 'left_out',
          reason_code: 'paper_file_limit',
          observed_at: '2026-11-24T15:10:00+00:00',
          file_id: null,
          measured_value: 21,
          limit_value: 20,
        },
      ],
    })
    expect(hasWaitingSupplements(parsed)).toBe(true)
  })

  it('returns null when metadata is absent, malformed, or has zero entries', () => {
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
      { supplements: [null, 123, {}, { provider_file_id: '', state: 'waiting' }, { provider_file_id: '1', state: 'bogus' }] },
    ]) {
      expect(parseSupplementJobProgress(candidate)).toBeNull()
    }
    expect(getJobSupplementProgress(null)).toBeNull()
    expect(getJobSupplementProgress({ metadata: null })).toBeNull()
    expect(hasWaitingSupplements(null)).toBe(false)
  })

  it('skips malformed entries without throwing and sanitizes bad optional fields on valid entries', () => {
    const parsed = parseSupplementJobProgress({
      job_kind: 'unknown_kind',
      supplement_wait_ends_at: 'not-a-date',
      supplements: [
        null,
        { provider_file_id: 'bad-state', state: 'running' },
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
      supplement_wait_ends_at: null,
      supplements: [
        {
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

  it('reads supplement progress from a job object via getJobSupplementProgress', () => {
    const parsed = getJobSupplementProgress({
      metadata: {
        job_kind: 'supplement_add',
        supplements: [
          {
            provider_file_id: '20',
            display_name: 'Table_S1.xlsx',
            state: 'adding',
            reason_code: null,
            observed_at: null,
            file_id: null,
            measured_value: null,
            limit_value: null,
          },
        ],
      },
    })

    expect(parsed?.job_kind).toBe('supplement_add')
    expect(parsed?.supplements).toHaveLength(1)
    expect(parsed?.supplements[0]?.state).toBe('adding')
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
})
