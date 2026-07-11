import { useCallback, useEffect, useRef, useState } from 'react'

import { onPDFViewerEvidenceAnchorSelected } from '@/components/pdfViewer/pdfEvents'
import type {
  CurationCandidate,
} from '@/features/curation/types'
import { resolveEnvelopeFieldPathCandidates } from './workspaceState'

const FIELD_ROW_DATA_ATTRIBUTE = 'data-field-key'
const PDF_TO_FORM_HIGHLIGHT_CLASSNAME = 'pdf-to-form-linked-field'
const PDF_TO_FORM_HIGHLIGHT_DURATION_MS = 1_800

interface PdfToFormTarget {
  anchorId: string
  candidateId: string
  fieldKey: string
}

interface ScopedPdfToFormTarget extends PdfToFormTarget {
  documentId: string
  ownerToken: string
}

export interface UsePdfToFormLinkingOptions {
  activeCandidateId: string | null
  candidates: CurationCandidate[]
  documentId: string
  evidenceByAnchorId: Record<string, PdfToFormEvidence>
  ownerToken: string
  setActiveCandidate: (
    candidateId: string | null,
    options?: { replace?: boolean },
  ) => void
}

export interface PdfToFormEvidence {
  anchorId: string
  candidateId: string
  fieldPaths: string[]
}

function findFieldRowElement(fieldKey: string): HTMLElement | null {
  return Array.from(document.querySelectorAll<HTMLElement>(`[${FIELD_ROW_DATA_ATTRIBUTE}]`))
    .find((element) => element.dataset.fieldKey === fieldKey) ?? null
}

function findCandidateFieldKey(
  candidate: CurationCandidate,
  evidence: PdfToFormEvidence,
): string | null {
  for (const fieldPath of evidence.fieldPaths) {
    for (const field of candidate.draft.fields) {
      if (resolveEnvelopeFieldPathCandidates(field).has(fieldPath)) {
        return field.field_key
      }
    }
  }

  return null
}

export function resolvePdfToFormTarget(
  anchorId: string,
  candidates: CurationCandidate[],
  evidenceByAnchorId: Record<string, PdfToFormEvidence>,
): PdfToFormTarget | null {
  const evidence = evidenceByAnchorId[anchorId]
  if (!evidence) {
    return null
  }

  const candidate = candidates.find(
    (entry) => entry.candidate_id === evidence.candidateId,
  )
  if (!candidate) {
    return null
  }

  const fieldKey = findCandidateFieldKey(candidate, evidence)
  if (!fieldKey) {
    return null
  }

  return {
    anchorId: evidence.anchorId,
    candidateId: candidate.candidate_id,
    fieldKey,
  }
}

export function usePdfToFormLinking({
  activeCandidateId,
  candidates,
  documentId,
  evidenceByAnchorId,
  ownerToken,
  setActiveCandidate,
}: UsePdfToFormLinkingOptions): void {
  const [pendingTarget, setPendingTarget] = useState<ScopedPdfToFormTarget | null>(null)
  const highlightedElementRef = useRef<HTMLElement | null>(null)
  const highlightTimeoutRef = useRef<number | null>(null)
  const optionsRef = useRef<UsePdfToFormLinkingOptions>()
  optionsRef.current = {
    activeCandidateId,
    candidates,
    documentId,
    evidenceByAnchorId,
    ownerToken,
    setActiveCandidate,
  }

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
      const current = optionsRef.current
      const anchorId = event.detail?.anchorId
      if (
        !current
        || !anchorId
        || event.detail.documentId !== current.documentId
        || event.detail.ownerToken !== current.ownerToken
      ) {
        return
      }

      const nextTarget = resolvePdfToFormTarget(
        anchorId,
        current.candidates,
        current.evidenceByAnchorId,
      )
      if (!nextTarget) {
        return
      }

      setPendingTarget({
        ...nextTarget,
        documentId: current.documentId,
        ownerToken: current.ownerToken,
      })
      if (nextTarget.candidateId !== current.activeCandidateId) {
        current.setActiveCandidate(nextTarget.candidateId)
      }
    })

    return unsubscribe
  }, [])

  useEffect(() => {
    setPendingTarget(null)
    clearHighlightedField()
  }, [clearHighlightedField, documentId, ownerToken])

  useEffect(() => {
    if (
      !pendingTarget
      || pendingTarget.candidateId !== activeCandidateId
      || pendingTarget.documentId !== documentId
      || pendingTarget.ownerToken !== ownerToken
    ) {
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
    targetField.querySelector<HTMLElement>(
      'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )?.focus({ preventScroll: true })
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
  }, [
    activeCandidateId,
    clearHighlightedField,
    documentId,
    ownerToken,
    pendingTarget,
  ])
}

export {
  FIELD_ROW_DATA_ATTRIBUTE,
  PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
  PDF_TO_FORM_HIGHLIGHT_DURATION_MS,
}
