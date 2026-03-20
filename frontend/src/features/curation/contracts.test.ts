import { describe, expect, it } from 'vitest'

import {
  EVIDENCE_LOCATOR_QUALITIES,
  FIELD_VALIDATION_STATUSES,
  SUBMISSION_MODES,
  type SubmissionDomainAdapter,
  type SubmissionTargetKey,
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
  })

  it('uses adapter-owned submission target keys instead of shared integration enums', () => {
    const targetKey: SubmissionTargetKey = 'partner_submission_api'
    const adapter: SubmissionDomainAdapter = {
      adapter_key: 'workspace_adapter',
      supported_submission_modes: ['preview', 'export'],
      supported_target_keys: [targetKey, 'review_export_bundle'],
      build_submission_payload: ({ mode, target_key, payload_context }) => ({
        mode,
        target_key,
        adapter_key: 'workspace_adapter',
        candidate_ids: [String(payload_context.candidate_id ?? 'candidate-1')],
        payload_json: { ok: true },
        warnings: [],
      }),
    }

    const payload = adapter.build_submission_payload({
      mode: 'preview',
      target_key: targetKey,
      payload_context: { candidate_id: 'candidate-1' },
    })

    expect(adapter.adapter_key).toBe('workspace_adapter')
    expect(adapter.supported_target_keys).toContain(targetKey)
    expect(payload.target_key).toBe(targetKey)
    expect(payload.adapter_key).toBe('workspace_adapter')
  })
})
