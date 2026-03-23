import { useCallback, useEffect, useRef, useState } from 'react'

import { onPDFViewerEvidenceAnchorSelected } from '@/components/pdfViewer/pdfEvents'
import type {
  CurationCandidate,
  CurationEvidenceRecord,
} from '@/features/curation/types'

const FIELD_ROW_DATA_ATTRIBUTE = 'data-field-key'
const PDF_TO_FORM_HIGHLIGHT_CLASSNAME = 'pdf-to-form-linked-field'
const PDF_TO_FORM_HIGHLIGHT_DURATION_MS = 1_800

interface PdfToFormTarget {
  anchorId: string
  candidateId: string
  fieldKey: string | null
}

export interface UsePdfToFormLinkingOptions {
  activeCandidateId: string | null
  candidates: CurationCandidate[]
  evidenceByAnchorId: Record<string, CurationEvidenceRecord>
  setActiveCandidate: (
    candidateId: string | null,
    options?: { replace?: boolean },
  ) => void
}

function findFieldRowElement(fieldKey: string): HTMLElement | null {
  return Array.from(document.querySelectorAll<HTMLElement>(`[${FIELD_ROW_DATA_ATTRIBUTE}]`))
    .find((element) => element.dataset.fieldKey === fieldKey) ?? null
}

function findCandidateFieldKey(
  candidate: CurationCandidate,
  evidence: CurationEvidenceRecord,
): string | null {
  const fieldKeys = new Set(candidate.draft.fields.map((field) => field.field_key))

  for (const fieldKey of evidence.field_keys) {
    if (fieldKeys.has(fieldKey)) {
      return fieldKey
    }
  }

  return null
}

export function resolvePdfToFormTarget(
  anchorId: string,
  candidates: CurationCandidate[],
  evidenceByAnchorId: Record<string, CurationEvidenceRecord>,
): PdfToFormTarget | null {
  const evidence = evidenceByAnchorId[anchorId]
  if (!evidence) {
    return null
  }

  const candidate = candidates.find(
    (entry) => entry.candidate_id === evidence.candidate_id,
  )
  if (!candidate) {
    return null
  }

  return {
    anchorId: evidence.anchor_id,
    candidateId: candidate.candidate_id,
    fieldKey: findCandidateFieldKey(candidate, evidence),
  }
}

export function usePdfToFormLinking({
  activeCandidateId,
  candidates,
  evidenceByAnchorId,
  setActiveCandidate,
}: UsePdfToFormLinkingOptions): void {
  const [pendingTarget, setPendingTarget] = useState<PdfToFormTarget | null>(null)
  const highlightedElementRef = useRef<HTMLElement | null>(null)
  const highlightTimeoutRef = useRef<number | null>(null)

  const clearHighlightedField = useCallback(() => {
    if (highlightTimeoutRef.current !== null) {
      window.clearTimeout(highlightTimeoutRef.current)
      highlightTimeoutRef.current = null
    }

    highlightedElementRef.current?.classList.remove(PDF_TO_FORM_HIGHLIGHT_CLASSNAME)
    highlightedElementRef.current = null
  }, [])

  useEffect(() => {
    return () => {
      clearHighlightedField()
    }
  }, [clearHighlightedField])

  useEffect(() => {
    const unsubscribe = onPDFViewerEvidenceAnchorSelected((event) => {
      const anchorId = event.detail?.anchorId
      if (!anchorId) {
        return
      }

      const nextTarget = resolvePdfToFormTarget(
        anchorId,
        candidates,
        evidenceByAnchorId,
      )
      if (!nextTarget) {
        return
      }

      setPendingTarget(nextTarget)
      if (nextTarget.candidateId !== activeCandidateId) {
        setActiveCandidate(nextTarget.candidateId)
      }
    })

    return unsubscribe
  }, [
    activeCandidateId,
    candidates,
    evidenceByAnchorId,
    setActiveCandidate,
  ])

  useEffect(() => {
    if (!pendingTarget || pendingTarget.candidateId !== activeCandidateId) {
      return
    }

    if (!pendingTarget.fieldKey) {
      setPendingTarget(null)
      return
    }

    const targetField = findFieldRowElement(pendingTarget.fieldKey)
    if (!targetField) {
      setPendingTarget(null)
      return
    }

    clearHighlightedField()

    targetField.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
      inline: 'nearest',
    })
    targetField.classList.add(PDF_TO_FORM_HIGHLIGHT_CLASSNAME)
    highlightedElementRef.current = targetField
    highlightTimeoutRef.current = window.setTimeout(() => {
      if (highlightedElementRef.current === targetField) {
        targetField.classList.remove(PDF_TO_FORM_HIGHLIGHT_CLASSNAME)
        highlightedElementRef.current = null
      }
      highlightTimeoutRef.current = null
    }, PDF_TO_FORM_HIGHLIGHT_DURATION_MS)
    setPendingTarget(null)
  }, [activeCandidateId, clearHighlightedField, pendingTarget])
}

export {
  FIELD_ROW_DATA_ATTRIBUTE,
  PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
  PDF_TO_FORM_HIGHLIGHT_DURATION_MS,
}
