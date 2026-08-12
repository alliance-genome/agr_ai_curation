import type {
  CurationCandidate,
  DomainEnvelopeReviewRow,
} from '@/features/curation/types'

export interface ObjectSelectorRow {
  candidate: CurationCandidate
  reviewRow?: DomainEnvelopeReviewRow | null
}

export function objectSelectorLabel(row: ObjectSelectorRow): string {
  const reviewLabel = row.reviewRow?.display_label?.trim()
  const candidateLabel = row.candidate.display_label?.trim()
  const draftTitle = row.candidate.draft.title?.trim()
  const objectId = row.candidate.projection_ref?.object_id

  return reviewLabel || candidateLabel || draftTitle || objectId || row.candidate.candidate_id
}
