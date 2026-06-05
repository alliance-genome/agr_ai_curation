import type {
  CurationCandidate,
  CurationValidationCounts,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'

const BLOCKING_VALIDATION_COUNT_KEYS: Array<keyof CurationValidationCounts> = [
  'ambiguous',
  'not_found',
  'invalid_format',
  'conflict',
]

function hasBlockingEnvelopeSummary(
  summaries: DomainEnvelopeValidationSummaryProjection[] | undefined,
): boolean {
  return (summaries ?? []).some((summary) =>
    summary.open_finding_count > 0 &&
    (summary.status === 'unresolved' || summary.status === 'blocked'))
}

function hasResolvedEnvelopeSummaries(
  summaries: DomainEnvelopeValidationSummaryProjection[] | undefined,
): boolean {
  return Boolean(summaries?.length) &&
    summaries!.every((summary) =>
      summary.open_finding_count === 0 &&
      (summary.status === 'resolved' || summary.status === 'waived'))
}

function hasCompletedValidationSummary(candidate: CurationCandidate): boolean {
  const validation = candidate.validation

  if (!validation || validation.state !== 'completed') {
    return false
  }

  if (
    validation.warnings.length > 0 ||
    validation.stale_field_keys.length > 0 ||
    validation.counts.validated === 0
  ) {
    return false
  }

  return BLOCKING_VALIDATION_COUNT_KEYS.every((key) => validation.counts[key] === 0)
}

export function isValidatedPendingCandidate(candidate: CurationCandidate): boolean {
  if (candidate.status !== 'pending') {
    return false
  }

  if (hasBlockingEnvelopeSummary(candidate.validation_summary_projections)) {
    return false
  }

  return hasCompletedValidationSummary(candidate) ||
    hasResolvedEnvelopeSummaries(candidate.validation_summary_projections)
}

export function countValidatedPending(candidates: CurationCandidate[]): number {
  return candidates.filter(isValidatedPendingCandidate).length
}
