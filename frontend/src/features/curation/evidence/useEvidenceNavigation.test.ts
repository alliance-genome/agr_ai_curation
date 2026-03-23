import { createElement } from 'react'
import { act, render, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { CurationEvidenceRecord } from '../types'
import type { UseEvidenceNavigationReturn } from './useEvidenceNavigation'
import { useEvidenceNavigation } from './useEvidenceNavigation'

type EvidenceRecordOverrides = Partial<Omit<CurationEvidenceRecord, 'anchor'>> & {
  anchor?: Partial<CurationEvidenceRecord['anchor']>
}

function createEvidenceRecord(
  anchorId: string,
  overrides: EvidenceRecordOverrides = {}
): CurationEvidenceRecord {
  const { anchor: anchorOverrides, ...recordOverrides } = overrides

  return {
    anchor_id: anchorId,
    candidate_id: 'candidate-1',
    source: 'extracted',
    field_keys: ['gene_symbol'],
    field_group_keys: ['identity'],
    is_primary: anchorId === 'anchor-1',
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: `Snippet for ${anchorId}`,
      viewer_search_text: `Search text for ${anchorId}`,
      page_number: 3,
      section_title: 'Results',
      chunk_ids: [`chunk-${anchorId}`],
      ...anchorOverrides,
    },
    created_at: '2026-03-20T12:00:00Z',
    updated_at: '2026-03-20T12:00:00Z',
    warnings: [],
    ...recordOverrides,
  }
}

describe('useEvidenceNavigation', () => {
  it('indexes evidence by field and group for downstream filtered views', () => {
    const primaryEvidence = createEvidenceRecord('anchor-1', {
      field_keys: ['gene_symbol', 'disease_name'],
      field_group_keys: ['identity'],
    })
    const diseaseEvidence = createEvidenceRecord('anchor-2', {
      field_keys: ['disease_name'],
      field_group_keys: ['clinical'],
    })

    const { result } = renderHook(() =>
      useEvidenceNavigation({ evidence: [primaryEvidence, diseaseEvidence] })
    )

    expect(result.current.candidateEvidence).toEqual([
      primaryEvidence,
      diseaseEvidence,
    ])
    expect(result.current.evidenceByAnchorId['anchor-1']).toBe(primaryEvidence)
    expect(result.current.evidenceByAnchorId['anchor-2']).toBe(diseaseEvidence)
    expect(result.current.evidenceByField.gene_symbol).toEqual([primaryEvidence])
    expect(result.current.evidenceByField.disease_name).toEqual([
      primaryEvidence,
      diseaseEvidence,
    ])
    expect(result.current.evidenceByGroup.identity).toEqual([primaryEvidence])
    expect(result.current.evidenceByGroup.clinical).toEqual([diseaseEvidence])
  })

  it('locks selection and emits a select navigation command', () => {
    const evidence = createEvidenceRecord('anchor-1')
    const evidenceRecords = [evidence]
    const { result } = renderHook(() =>
      useEvidenceNavigation({ evidence: evidenceRecords })
    )

    act(() => {
      result.current.selectEvidence(evidence)
    })

    expect(result.current.selectedEvidence).toBe(evidence)
    expect(result.current.hoveredEvidence).toBeNull()
    expect(result.current.pendingNavigation).toEqual({
      anchor: evidence.anchor,
      searchText: 'Search text for anchor-1',
      pageNumber: 3,
      sectionTitle: 'Results',
      mode: 'select',
    })
  })

  it('uses hover navigation transiently and restores the current selection when hover ends', () => {
    const selectedEvidence = createEvidenceRecord('anchor-1')
    const hoveredEvidence = createEvidenceRecord('anchor-2', {
      anchor: {
        viewer_search_text: 'Search text for anchor-2',
        page_number: 8,
        section_title: 'Discussion',
      },
    })
    const evidenceRecords = [selectedEvidence, hoveredEvidence]

    const { result } = renderHook(() =>
      useEvidenceNavigation({ evidence: evidenceRecords })
    )

    act(() => {
      result.current.selectEvidence(selectedEvidence)
      result.current.hoverEvidence(hoveredEvidence)
    })

    expect(result.current.selectedEvidence).toBe(selectedEvidence)
    expect(result.current.hoveredEvidence).toBe(hoveredEvidence)
    expect(result.current.pendingNavigation).toEqual({
      anchor: hoveredEvidence.anchor,
      searchText: 'Search text for anchor-2',
      pageNumber: 8,
      sectionTitle: 'Discussion',
      mode: 'hover',
    })

    act(() => {
      result.current.hoverEvidence(null)
    })

    expect(result.current.hoveredEvidence).toBeNull()
    expect(result.current.pendingNavigation).toEqual({
      anchor: selectedEvidence.anchor,
      searchText: 'Search text for anchor-1',
      pageNumber: 3,
      sectionTitle: 'Results',
      mode: 'select',
    })
  })

  it('does not re-emit selection when hover end occurs without an active transient hover', () => {
    const selectedEvidence = createEvidenceRecord('anchor-1')
    const evidenceRecords = [selectedEvidence]
    const { result } = renderHook(() =>
      useEvidenceNavigation({ evidence: evidenceRecords })
    )

    act(() => {
      result.current.selectEvidence(selectedEvidence)
    })

    act(() => {
      result.current.acknowledgeNavigation()
    })

    expect(result.current.pendingNavigation).toBeNull()

    act(() => {
      result.current.hoverEvidence(null)
    })

    expect(result.current.selectedEvidence).toBe(selectedEvidence)
    expect(result.current.hoveredEvidence).toBeNull()
    expect(result.current.pendingNavigation).toBeNull()
  })

  it('navigates without mutating selection state and clears stale commands after acknowledgement', () => {
    const evidence = createEvidenceRecord('anchor-1', {
      anchor: {
        viewer_search_text: null,
        page_number: 5,
        section_title: null,
      },
    })
    const evidenceRecords = [evidence]
    const { result } = renderHook(() =>
      useEvidenceNavigation({ evidence: evidenceRecords })
    )

    act(() => {
      result.current.navigateToEvidence(evidence)
    })

    expect(result.current.selectedEvidence).toBeNull()
    expect(result.current.hoveredEvidence).toBeNull()
    expect(result.current.pendingNavigation).toEqual({
      anchor: evidence.anchor,
      searchText: null,
      pageNumber: 5,
      sectionTitle: null,
      mode: 'select',
    })

    act(() => {
      result.current.acknowledgeNavigation()
    })

    expect(result.current.pendingNavigation).toBeNull()
  })

  it('clears navigation state and resets when the candidate evidence array changes', () => {
    const currentEvidence = [createEvidenceRecord('anchor-1')]
    const nextEvidence = [createEvidenceRecord('anchor-2')]
    const { result, rerender } = renderHook(
      ({ evidence }) => useEvidenceNavigation({ evidence }),
      {
        initialProps: { evidence: currentEvidence },
      }
    )

    act(() => {
      result.current.selectEvidence(currentEvidence[0])
      result.current.hoverEvidence(currentEvidence[0])
    })

    expect(result.current.selectedEvidence).toBe(currentEvidence[0])
    expect(result.current.hoveredEvidence).toBeNull()

    act(() => {
      result.current.clearEvidence()
    })

    expect(result.current.selectedEvidence).toBeNull()
    expect(result.current.hoveredEvidence).toBeNull()
    expect(result.current.pendingNavigation).toBeNull()

    act(() => {
      result.current.selectEvidence(currentEvidence[0])
    })

    act(() => {
      rerender({ evidence: nextEvidence })
    })

    expect(result.current.selectedEvidence).toBeNull()
    expect(result.current.hoveredEvidence).toBeNull()
    expect(result.current.pendingNavigation).toBeNull()
    expect(result.current.candidateEvidence).toEqual(nextEvidence)
  })

  it('does not expose stale selection or navigation during the first render of a candidate switch', () => {
    const currentEvidence = [createEvidenceRecord('anchor-1')]
    const nextEvidence = [createEvidenceRecord('anchor-2')]
    const snapshots: Array<
      Pick<
        UseEvidenceNavigationReturn,
        'selectedEvidence' | 'hoveredEvidence' | 'pendingNavigation'
      >
    > = []
    let latestHookValue: UseEvidenceNavigationReturn | null = null

    function getLatestHookValue(): UseEvidenceNavigationReturn {
      expect(latestHookValue).not.toBeNull()

      return latestHookValue as UseEvidenceNavigationReturn
    }

    function Probe({ evidence }: { evidence: CurationEvidenceRecord[] }) {
      const hookValue = useEvidenceNavigation({ evidence })
      latestHookValue = hookValue
      snapshots.push({
        selectedEvidence: hookValue.selectedEvidence,
        hoveredEvidence: hookValue.hoveredEvidence,
        pendingNavigation: hookValue.pendingNavigation,
      })

      return null
    }

    const { rerender } = render(createElement(Probe, { evidence: currentEvidence }))

    act(() => {
      latestHookValue?.selectEvidence(currentEvidence[0])
    })

    expect(getLatestHookValue().selectedEvidence).toBe(currentEvidence[0])

    snapshots.length = 0

    act(() => {
      rerender(createElement(Probe, { evidence: nextEvidence }))
    })

    expect(snapshots[0]).toEqual({
      selectedEvidence: null,
      hoveredEvidence: null,
      pendingNavigation: null,
    })
    expect(getLatestHookValue().selectedEvidence).toBeNull()
    expect(getLatestHookValue().hoveredEvidence).toBeNull()
    expect(getLatestHookValue().pendingNavigation).toBeNull()
  })
})
