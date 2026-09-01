import type { CurationCandidate, CurationDraftField } from '@/features/curation/types'
import { resolveEnvelopeFieldPath } from '@/features/curation/workspace/workspaceState'

type ReviewPolicy =
  | { mode: 'all' }
  | { mode: 'groups'; decisionGroups: readonly string[] }
  | { mode: 'fields'; decisionFields: readonly string[] }

// This catalog is intentionally exhaustive for current stageable workspace
// envelope types. A decision field is something a curator signs off on as part
// of the extracted/exported record. Supporting source text, confidence,
// rationale, and provenance stay in the envelope but do not become peer grid
// columns or acquire preview-validation controls. Unknown future envelope types
// fall back to showing all fields so new data cannot disappear silently.
export const HORIZONTAL_GRID_REVIEW_POLICIES: Readonly<Record<string, ReviewPolicy>> = {
  'agr.alliance.allele:AllelePaperEvidenceAssociation': { mode: 'all' },
  'agr.alliance.allele:Allele': { mode: 'all' },
  'agr.alliance.disease:DiseaseAnnotation': { mode: 'all' },
  'agr.alliance.disease:GeneDiseaseAnnotation': { mode: 'all' },
  'agr.alliance.disease:AlleleDiseaseAnnotation': { mode: 'all' },
  'agr.alliance.disease:AGMDiseaseAnnotation': { mode: 'all' },
  'agr.alliance.gene_expression:GeneExpressionAnnotation': { mode: 'all' },
  'agr.alliance.go:GOCuratableObject': {
    mode: 'groups',
    decisionGroups: ['identity', 'annotation'],
  },
  'agr.alliance.phenotype:PhenotypeAnnotation': { mode: 'all' },
  'agr.alliance.phenotype:PhenotypeSubject': { mode: 'all' },
  'gene:gene_mention_evidence': {
    mode: 'groups',
    decisionGroups: ['identity'],
  },
  'generic:generic_object': {
    mode: 'fields',
    decisionFields: ['label', 'class_key', 'semantic_class', 'description', 'attributes'],
  },
  'generic:generic_claim': {
    mode: 'fields',
    decisionFields: ['label', 'class_key', 'claim_text', 'claim_type'],
  },
  'generic:generic_reagent_candidate': {
    mode: 'fields',
    decisionFields: [
      'label',
      'class_key',
      'source',
      'source_identifier',
      'count',
      'reagent_type',
    ],
  },
}

function metadataString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function isHorizontalGridDecisionField(
  candidate: CurationCandidate,
  field: CurationDraftField,
): boolean {
  const domainPackId = metadataString(candidate.metadata.domain_pack_id) ?? candidate.adapter_key
  const objectType = metadataString(candidate.metadata.object_type)
  if (!objectType) {
    return true
  }

  const policy = HORIZONTAL_GRID_REVIEW_POLICIES[`${domainPackId}:${objectType}`]
  if (!policy || policy.mode === 'all') {
    return true
  }
  if (policy.mode === 'groups') {
    return field.group_key !== null && policy.decisionGroups.includes(field.group_key)
  }
  return policy.decisionFields.includes(resolveEnvelopeFieldPath(field))
}
