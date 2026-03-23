import { useCallback, useState } from 'react'
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { dispatchPDFViewerEvidenceAnchorSelected } from '@/components/pdfViewer/pdfEvents'
import type {
  CurationCandidate,
  CurationEvidenceRecord,
} from '@/features/curation/types'
import { createEvidenceRecord } from '../evidence/testFactories'
import {
  FIELD_ROW_DATA_ATTRIBUTE,
  PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
  PDF_TO_FORM_HIGHLIGHT_DURATION_MS,
  usePdfToFormLinking,
} from './usePdfToFormLinking'

function buildCandidate(
  candidateId: string,
  fieldKeys: string[],
): CurationCandidate {
  return {
    candidate_id: candidateId,
    session_id: 'session-1',
    source: 'manual',
    status: 'pending',
    order: 0,
    adapter_key: 'test',
    display_label: candidateId,
    unresolved_ambiguities: [],
    draft: {
      draft_id: `draft-${candidateId}`,
      candidate_id: candidateId,
      adapter_key: 'test',
      version: 1,
      fields: fieldKeys.map((fieldKey, index) => ({
        field_key: fieldKey,
        label: `Label ${fieldKey}`,
        value: `${fieldKey}-value`,
        seed_value: `${fieldKey}-value`,
        field_type: 'string',
        order: index,
        required: false,
        read_only: false,
        dirty: false,
        stale_validation: false,
        evidence_anchor_ids: [],
        validation_result: null,
        metadata: {},
      })),
      created_at: '2026-03-20T12:00:00Z',
      updated_at: '2026-03-20T12:00:00Z',
      metadata: {},
    },
    evidence_anchors: [],
    created_at: '2026-03-20T12:00:00Z',
    updated_at: '2026-03-20T12:00:00Z',
    metadata: {},
  }
}

function buildEvidenceIndex(
  evidence: CurationEvidenceRecord[],
): Record<string, CurationEvidenceRecord> {
  return evidence.reduce<Record<string, CurationEvidenceRecord>>((index, record) => {
    index[record.anchor_id] = record
    return index
  }, {})
}

function getFieldRow(fieldKey: string): HTMLElement {
  const fieldRow = document.querySelector<HTMLElement>(
    `[${FIELD_ROW_DATA_ATTRIBUTE}="${fieldKey}"]`,
  )
  if (!fieldRow) {
    throw new Error(`Expected field row for ${fieldKey}`)
  }

  return fieldRow
}

function LinkingHarness({
  candidates,
  evidence,
  initialActiveCandidateId,
  onSetActiveCandidate,
}: {
  candidates: CurationCandidate[]
  evidence: CurationEvidenceRecord[]
  initialActiveCandidateId: string
  onSetActiveCandidate?: (candidateId: string | null) => void
}) {
  const [activeCandidateId, setActiveCandidateId] = useState(initialActiveCandidateId)
  const activeCandidate = candidates.find(
    (candidate) => candidate.candidate_id === activeCandidateId,
  ) ?? null
  const handleSetActiveCandidate = useCallback((candidateId: string | null) => {
    onSetActiveCandidate?.(candidateId)
    setActiveCandidateId(candidateId ?? '')
  }, [onSetActiveCandidate])

  usePdfToFormLinking({
    activeCandidateId,
    candidates,
    evidenceByAnchorId: buildEvidenceIndex(evidence),
    setActiveCandidate: handleSetActiveCandidate,
  })

  return (
    <div>
      {activeCandidate?.draft.fields.map((field) => (
        <div
          data-field-key={field.field_key}
          data-testid={`linking-harness-row-${field.field_key}`}
          key={field.field_key}
        >
          {field.label}
        </div>
      ))}
    </div>
  )
}

describe('usePdfToFormLinking', () => {
  const originalScrollIntoView = HTMLElement.prototype.scrollIntoView
  let scrollIntoViewMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    scrollIntoViewMock = vi.fn()
    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock
  })

  afterEach(() => {
    HTMLElement.prototype.scrollIntoView = originalScrollIntoView
    vi.useRealTimers()
  })

  it('resolves an anchor to the first matching field row, scrolls it into view, and clears the highlight after the timeout', async () => {
    const candidate = buildCandidate('candidate-1', ['field_a', 'field_b'])
    const evidence = createEvidenceRecord('anchor-1', {
      candidate_id: 'candidate-1',
      field_keys: ['missing_field', 'field_b'],
    })

    render(
      <LinkingHarness
        candidates={[candidate]}
        evidence={[evidence]}
        initialActiveCandidateId="candidate-1"
      />,
    )

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected('anchor-1')
    })

    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'center',
      inline: 'nearest',
    })
    expect(getFieldRow('field_b')).toHaveClass(
      PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
    )

    expect(getFieldRow('field_a')).not.toHaveClass(
      PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
    )

    act(() => {
      vi.advanceTimersByTime(PDF_TO_FORM_HIGHLIGHT_DURATION_MS)
    })

    expect(getFieldRow('field_b')).not.toHaveClass(
      PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
    )
  })

  it('switches to the matched candidate before scrolling the linked field row', async () => {
    const setActiveCandidate = vi.fn()
    const firstCandidate = buildCandidate('candidate-1', ['field_a'])
    const secondCandidate = buildCandidate('candidate-2', ['field_b'])
    const evidence = createEvidenceRecord('anchor-2', {
      candidate_id: 'candidate-2',
      field_keys: ['field_b'],
    })

    render(
      <LinkingHarness
        candidates={[firstCandidate, secondCandidate]}
        evidence={[evidence]}
        initialActiveCandidateId="candidate-1"
        onSetActiveCandidate={setActiveCandidate}
      />,
    )

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected('anchor-2')
    })

    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-2')
    expect(getFieldRow('field_b')).toHaveClass(
      PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
    )
    expect(
      document.querySelector(`[${FIELD_ROW_DATA_ATTRIBUTE}="field_a"]`),
    ).not.toBeInTheDocument()
    expect(scrollIntoViewMock).toHaveBeenCalledTimes(1)
  })
})
