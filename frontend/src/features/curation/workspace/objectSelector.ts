import type { CurationCandidate } from '@/features/curation/types'
import type { WorkspaceEnvelopeObjectReviewRow } from './envelopeObjectReviewRows'

type CandidateProgressInput = Pick<CurationCandidate, 'candidate_id' | 'status'>

export type ObjectSelectorProgressKind = 'done' | 'current' | 'pending' | 'rejected'

export interface ObjectSelectorProgressSegment {
  id: string
  kind: ObjectSelectorProgressKind
}

export interface ObjectSelectorPosition {
  position: number
  total: number
}

export function progressSegments(
  candidates: CandidateProgressInput[],
  activeCandidateId: string | null,
): ObjectSelectorProgressSegment[] {
  return candidates.map((candidate) => {
    if (candidate.candidate_id === activeCandidateId) {
      return {
        id: candidate.candidate_id,
        kind: 'current',
      }
    }

    if (candidate.status === 'accepted') {
      return {
        id: candidate.candidate_id,
        kind: 'done',
      }
    }

    if (candidate.status === 'rejected') {
      return {
        id: candidate.candidate_id,
        kind: 'rejected',
      }
    }

    return {
      id: candidate.candidate_id,
      kind: 'pending',
    }
  })
}

export function selectorPosition(
  candidates: CandidateProgressInput[],
  activeCandidateId: string | null,
): ObjectSelectorPosition {
  const index = candidates.findIndex((candidate) => candidate.candidate_id === activeCandidateId)

  return {
    position: index >= 0 ? index + 1 : 0,
    total: candidates.length,
  }
}

export function adjacentCandidateId(
  rows: WorkspaceEnvelopeObjectReviewRow[],
  activeCandidateId: string | null,
  direction: 'previous' | 'next',
): string | null {
  const index = rows.findIndex((row) => row.candidate.candidate_id === activeCandidateId)

  if (index < 0) {
    return null
  }

  const nextIndex = direction === 'previous' ? index - 1 : index + 1
  return rows[nextIndex]?.candidate.candidate_id ?? null
}

export function readableObjectType(value?: string | null): string {
  if (!value) {
    return 'Curation object'
  }

  const readable = value
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[._-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

  return readable
    ? `${readable.charAt(0).toUpperCase()}${readable.slice(1)}`
    : 'Curation object'
}

export function objectSelectorLabel(row: WorkspaceEnvelopeObjectReviewRow): string {
  const reviewLabel = row.reviewRow?.display_label?.trim()
  const candidateLabel = row.candidate.display_label?.trim()
  const draftTitle = row.candidate.draft.title?.trim()

  return reviewLabel || candidateLabel || draftTitle || row.projectionRef.object_id
}

export function objectSelectorType(row: WorkspaceEnvelopeObjectReviewRow): string {
  return readableObjectType(row.reviewRow?.object_type)
}
