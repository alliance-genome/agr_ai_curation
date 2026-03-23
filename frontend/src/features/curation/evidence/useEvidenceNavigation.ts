import { useLayoutEffect, useMemo, useReducer, useRef } from 'react'

import type { CurationEvidenceRecord } from '../types'
import type {
  EvidenceNavigationCommand,
  EvidenceNavigationState,
} from './types'

export interface UseEvidenceNavigationOptions {
  evidence: CurationEvidenceRecord[]
}

export interface UseEvidenceNavigationReturn extends EvidenceNavigationState {
  selectEvidence: (evidence: CurationEvidenceRecord | null) => void
  hoverEvidence: (evidence: CurationEvidenceRecord | null) => void
  navigateToEvidence: (evidence: CurationEvidenceRecord) => void
  clearEvidence: () => void
  acknowledgeNavigation: () => void
}

interface EvidenceNavigationRuntimeState {
  selectedEvidence: CurationEvidenceRecord | null
  hoveredEvidence: CurationEvidenceRecord | null
  pendingNavigation: EvidenceNavigationCommand | null
}

type EvidenceNavigationAction =
  | { type: 'select'; evidence: CurationEvidenceRecord | null }
  | { type: 'hover'; evidence: CurationEvidenceRecord | null }
  | { type: 'navigate'; evidence: CurationEvidenceRecord }
  | { type: 'acknowledge' }
  | { type: 'clear' }
  | { type: 'reset' }

const INITIAL_NAVIGATION_STATE: EvidenceNavigationRuntimeState = {
  selectedEvidence: null,
  hoveredEvidence: null,
  pendingNavigation: null,
}

function buildNavigationCommand(
  evidence: CurationEvidenceRecord,
  mode: EvidenceNavigationCommand['mode']
): EvidenceNavigationCommand {
  return {
    anchor: evidence.anchor,
    searchText: evidence.anchor.viewer_search_text ?? null,
    pageNumber: evidence.anchor.page_number ?? null,
    sectionTitle: evidence.anchor.section_title ?? null,
    mode,
  }
}

function evidenceNavigationReducer(
  state: EvidenceNavigationRuntimeState,
  action: EvidenceNavigationAction
): EvidenceNavigationRuntimeState {
  switch (action.type) {
    case 'select':
      if (action.evidence === null) {
        return {
          ...state,
          selectedEvidence: null,
          pendingNavigation:
            state.hoveredEvidence === null
              ? null
              : buildNavigationCommand(state.hoveredEvidence, 'hover'),
        }
      }

      return {
        selectedEvidence: action.evidence,
        hoveredEvidence: null,
        pendingNavigation: buildNavigationCommand(action.evidence, 'select'),
      }
    case 'hover':
      if (action.evidence === null) {
        return {
          ...state,
          hoveredEvidence: null,
          pendingNavigation:
            state.selectedEvidence === null
              ? null
              : buildNavigationCommand(state.selectedEvidence, 'select'),
        }
      }

      return {
        ...state,
        hoveredEvidence: action.evidence,
        pendingNavigation: buildNavigationCommand(action.evidence, 'hover'),
      }
    case 'navigate':
      return {
        ...state,
        pendingNavigation: buildNavigationCommand(action.evidence, 'select'),
      }
    case 'acknowledge':
      return {
        ...state,
        pendingNavigation: null,
      }
    case 'clear':
    case 'reset':
      return INITIAL_NAVIGATION_STATE
    default:
      return state
  }
}

function buildEvidenceIndex(
  evidence: CurationEvidenceRecord[],
  getKeys: (record: CurationEvidenceRecord) => string[]
): Record<string, CurationEvidenceRecord[]> {
  return evidence.reduce<Record<string, CurationEvidenceRecord[]>>(
    (index, record) => {
      for (const key of new Set(getKeys(record))) {
        if (key.length === 0) {
          continue
        }

        if (index[key] === undefined) {
          index[key] = []
        }

        index[key].push(record)
      }

      return index
    },
    {}
  )
}

export function useEvidenceNavigation({
  evidence,
}: UseEvidenceNavigationOptions): UseEvidenceNavigationReturn {
  const [navigationState, dispatch] = useReducer(
    evidenceNavigationReducer,
    INITIAL_NAVIGATION_STATE
  )
  const previousEvidenceRef = useRef(evidence)
  const evidenceChanged = previousEvidenceRef.current !== evidence

  useLayoutEffect(() => {
    if (!evidenceChanged) {
      return
    }

    previousEvidenceRef.current = evidence
    dispatch({ type: 'reset' })
  }, [evidence, evidenceChanged])

  const visibleNavigationState = evidenceChanged
    ? INITIAL_NAVIGATION_STATE
    : navigationState

  const evidenceByField = useMemo(
    () => buildEvidenceIndex(evidence, (record) => record.field_keys),
    [evidence]
  )
  const evidenceByAnchorId = useMemo(
    () =>
      evidence.reduce<Record<string, CurationEvidenceRecord>>((index, record) => {
        index[record.anchor_id] = record

        return index
      }, {}),
    [evidence]
  )
  const evidenceByGroup = useMemo(
    () => buildEvidenceIndex(evidence, (record) => record.field_group_keys),
    [evidence]
  )

  return {
    selectedEvidence: visibleNavigationState.selectedEvidence,
    hoveredEvidence: visibleNavigationState.hoveredEvidence,
    pendingNavigation: visibleNavigationState.pendingNavigation,
    candidateEvidence: evidence,
    evidenceByAnchorId,
    evidenceByField,
    evidenceByGroup,
    selectEvidence: (selectedEvidence) =>
      dispatch({ type: 'select', evidence: selectedEvidence }),
    hoverEvidence: (hoveredEvidence) =>
      dispatch({ type: 'hover', evidence: hoveredEvidence }),
    navigateToEvidence: (navigationEvidence) =>
      dispatch({ type: 'navigate', evidence: navigationEvidence }),
    clearEvidence: () => dispatch({ type: 'clear' }),
    acknowledgeNavigation: () => dispatch({ type: 'acknowledge' }),
  }
}
