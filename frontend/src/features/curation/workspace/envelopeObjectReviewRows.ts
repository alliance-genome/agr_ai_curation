import type {
  CurationCandidate,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeProjectionRef,
  DomainEnvelopeReviewRow,
  DomainEnvelopeReviewRowsResponse,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'

export interface WorkspaceEnvelopeObjectReviewRow {
  candidate: CurationCandidate
  projectionRef: DomainEnvelopeProjectionRef
  reviewRow: DomainEnvelopeReviewRow | null
  evidenceAnchors: DomainEnvelopeEvidenceAnchorProjection[]
  validationSummaries: DomainEnvelopeValidationSummaryProjection[]
}

function projectionKey(projectionRef: DomainEnvelopeProjectionRef): string {
  return [
    projectionRef.envelope_id,
    projectionRef.object_id,
    projectionRef.envelope_revision,
  ].join('\u0000')
}

function reviewRowKey(row: DomainEnvelopeReviewRow): string {
  return [
    row.envelope_id,
    row.object_id,
    row.envelope_revision,
  ].join('\u0000')
}

function projectionMatchesRef(
  projection:
    | DomainEnvelopeEvidenceAnchorProjection
    | DomainEnvelopeValidationSummaryProjection,
  projectionRef: DomainEnvelopeProjectionRef,
): boolean {
  return (
    projection.envelope_id === projectionRef.envelope_id &&
    projection.object_id === projectionRef.object_id &&
    projection.envelope_revision === projectionRef.envelope_revision
  )
}

export function buildWorkspaceEnvelopeObjectReviewRows({
  candidates,
  evidenceAnchorProjections = [],
  reviewRowResponses,
  validationSummaryProjections = [],
}: {
  candidates: CurationCandidate[]
  evidenceAnchorProjections?: DomainEnvelopeEvidenceAnchorProjection[]
  reviewRowResponses: DomainEnvelopeReviewRowsResponse[]
  validationSummaryProjections?: DomainEnvelopeValidationSummaryProjection[]
}): WorkspaceEnvelopeObjectReviewRow[] {
  const rowsByProjection = new Map<string, DomainEnvelopeReviewRow>()
  for (const response of reviewRowResponses) {
    for (const row of response.rows) {
      rowsByProjection.set(reviewRowKey(row), row)
    }
  }

  return candidates
    .filter((candidate) => candidate.projection_ref)
    .map((candidate) => {
      const projectionRef = candidate.projection_ref as DomainEnvelopeProjectionRef

      return {
        candidate,
        projectionRef,
        reviewRow: rowsByProjection.get(projectionKey(projectionRef)) ?? null,
        evidenceAnchors: evidenceAnchorProjections.filter((projection) =>
          projectionMatchesRef(projection, projectionRef),
        ),
        validationSummaries: validationSummaryProjections.filter((projection) =>
          projectionMatchesRef(projection, projectionRef),
        ),
      }
    })
}
