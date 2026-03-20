import { describe, expect, it } from 'vitest'

import {
  EVIDENCE_LOCATOR_QUALITIES,
  FIELD_VALIDATION_STATUSES,
  SUBMISSION_MODES,
  SUBMISSION_TARGET_SYSTEMS,
  type SubmissionDomainAdapter,
} from './contracts'

describe('curation contracts', () => {
  it('exposes the expected enum value surfaces', () => {
    expect(EVIDENCE_LOCATOR_QUALITIES).toEqual([
      'exact_quote',
      'normalized_quote',
      'section_only',
      'page_only',
      'document_only',
      'unresolved',
    ])

    expect(FIELD_VALIDATION_STATUSES).toEqual([
      'validated',
      'ambiguous',
      'not_found',
      'invalid_format',
      'conflict',
      'skipped',
      'overridden',
    ])

    expect(SUBMISSION_MODES).toEqual([
      'preview',
      'export',
      'direct_submit',
    ])

    expect(SUBMISSION_TARGET_SYSTEMS).toEqual([
      'alliance_curation_api',
      'abc_api',
      'bulk_ingest',
      'file_export',
      'file_upload',
    ])
  })

  it('mirrors the backend submission adapter naming surface', () => {
    const adapter: SubmissionDomainAdapter = {
      adapter_key: 'disease',
      supported_submission_modes: ['preview', 'export'],
      supported_target_systems: ['alliance_curation_api', 'file_export'],
      build_submission_payload: ({ mode, target_system, payload_context }) => ({
        mode,
        target_system,
        adapter_key: 'disease',
        candidate_ids: [String(payload_context.candidate_id ?? 'candidate-1')],
        payload_json: { ok: true },
        warnings: [],
      }),
    }

    const payload = adapter.build_submission_payload({
      mode: 'preview',
      target_system: 'alliance_curation_api',
      payload_context: { candidate_id: 'candidate-1' },
    })

    expect(adapter.adapter_key).toBe('disease')
    expect(payload.target_system).toBe('alliance_curation_api')
    expect(payload.adapter_key).toBe('disease')
  })
})
