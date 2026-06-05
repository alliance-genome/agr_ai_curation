import type {
  CurationDraftField,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'

export type FieldStateKind = 'resolved' | 'needs-review' | 'ai-unconfirmed'

type SummariesForField = (
  field: CurationDraftField,
) => DomainEnvelopeValidationSummaryProjection[]

export function fieldState(
  field: CurationDraftField,
  summaries: DomainEnvelopeValidationSummaryProjection[],
): FieldStateKind {
  if (field.stale_validation) {
    return 'needs-review'
  }

  if (summaries.some((summary) => summary.status === 'unresolved' || summary.status === 'blocked')) {
    return 'needs-review'
  }

  if (
    summaries.length > 0 &&
    summaries.every((summary) => summary.status === 'resolved' || summary.status === 'waived')
  ) {
    return 'resolved'
  }

  return 'ai-unconfirmed'
}

export function sortFieldsNeedsReviewFirst(
  fields: CurationDraftField[],
  summariesForField: SummariesForField,
): CurationDraftField[] {
  return [...fields].sort((left, right) => {
    const leftRank = fieldState(left, summariesForField(left)) === 'needs-review' ? 0 : 1
    const rightRank = fieldState(right, summariesForField(right)) === 'needs-review' ? 0 : 1

    if (leftRank !== rightRank) {
      return leftRank - rightRank
    }

    return left.order - right.order
  })
}
