// Shared curation workspace contracts stay project-agnostic. Concrete domain
// behavior and downstream integration identifiers belong behind adapters.
export const EVIDENCE_ANCHOR_KINDS = [
  'snippet',
  'sentence',
  'section',
  'figure',
  'table',
  'page',
  'document',
] as const

export type EvidenceAnchorKind = (typeof EVIDENCE_ANCHOR_KINDS)[number]

export const EVIDENCE_LOCATOR_QUALITIES = [
  'exact_quote',
  'normalized_quote',
  'section_only',
  'page_only',
  'document_only',
  'unresolved',
] as const

export type EvidenceLocatorQuality = (typeof EVIDENCE_LOCATOR_QUALITIES)[number]

export const EVIDENCE_SUPPORTS_DECISIONS = [
  'supports',
  'contradicts',
  'neutral',
] as const

export type EvidenceSupportsDecision = (typeof EVIDENCE_SUPPORTS_DECISIONS)[number]

export interface EvidenceAnchor {
  anchor_kind: EvidenceAnchorKind
  locator_quality: EvidenceLocatorQuality
  supports_decision: EvidenceSupportsDecision
  snippet_text?: string | null
  sentence_text?: string | null
  normalized_text?: string | null
  viewer_search_text?: string | null
  viewer_highlightable?: boolean
  page_number?: number | null
  page_label?: string | null
  section_title?: string | null
  subsection_title?: string | null
  figure_reference?: string | null
  table_reference?: string | null
  chunk_ids: string[]
}

export interface DomainEnvelopeProjectionRef {
  envelope_id: string
  object_id: string
  envelope_revision: number
}

export const DOMAIN_ENVELOPE_VALIDATION_STATUSES = [
  'unresolved',
  'planned',
  'blocked',
  'under_development',
  'resolved',
  'waived',
] as const

export type DomainEnvelopeValidationStatus =
  (typeof DOMAIN_ENVELOPE_VALIDATION_STATUSES)[number]

export interface DomainEnvelopeEvidenceAnchorProjection {
  anchor_id: string
  evidence_record_id: string
  envelope_id: string
  object_id: string
  object_type?: string | null
  field_path?: string | null
  envelope_revision: number
  document_id?: string | null
  quote?: string | null
  page_number?: number | null
  page_label?: string | null
  chunk_id?: string | null
  chunk_ids: string[]
  section_title?: string | null
  subsection_title?: string | null
  figure_reference?: string | null
  table_reference?: string | null
  source_id?: string | null
  source_title?: string | null
  source_url?: string | null
  anchor: EvidenceAnchor
  metadata: Record<string, unknown>
}

export interface DomainEnvelopeValidationFindingProjection {
  finding_id: string
  envelope_id: string
  object_id?: string | null
  object_type?: string | null
  field_path?: string | null
  envelope_revision: number
  severity: string
  finding_status: string
  summary_status: DomainEnvelopeValidationStatus
  code?: string | null
  message: string
  details: Record<string, unknown>
}

export interface DomainEnvelopeValidationSummaryProjection {
  summary_id: string
  envelope_id: string
  object_id?: string | null
  object_type?: string | null
  field_path?: string | null
  envelope_revision: number
  status: DomainEnvelopeValidationStatus
  highest_severity?: string | null
  finding_count: number
  open_finding_count: number
  finding_ids: string[]
  codes: string[]
  messages: string[]
  findings: DomainEnvelopeValidationFindingProjection[]
}

export interface DomainEnvelopeReviewRowSummaryField {
  field_path: string
  label: string
  value?: unknown | null
  field_type?: string | null
  metadata: Record<string, unknown>
}

export interface DomainEnvelopeReviewRow {
  envelope_id: string
  object_id: string
  envelope_revision: number
  domain_pack_id: string
  domain_pack_version?: string | null
  object_type: string
  object_role?: string | null
  status: string
  validation_state: string
  projection_type: string
  projection_key: string
  display_label?: string | null
  secondary_label?: string | null
  summary_fields: DomainEnvelopeReviewRowSummaryField[]
  schema_provider?: string | null
  schema_ref: Record<string, unknown>
  object_model_ref: Record<string, unknown>
  model_field_ref: Record<string, unknown>
  metadata: Record<string, unknown>
}

export interface DomainEnvelopeReviewRowsResponse {
  envelope_id: string
  envelope_revision: number
  row_count: number
  rows: DomainEnvelopeReviewRow[]
}

export interface ValidationCandidateMatch {
  label: string
  identifier?: string | null
  matched_value?: string | null
  score?: number | null
}

export const FIELD_VALIDATION_STATUSES = [
  'validated',
  'ambiguous',
  'not_found',
  'invalid_format',
  'conflict',
  'skipped',
  'overridden',
] as const

export type FieldValidationStatus = (typeof FIELD_VALIDATION_STATUSES)[number]

export interface FieldValidationResult {
  status: FieldValidationStatus
  resolver?: string | null
  candidate_matches: ValidationCandidateMatch[]
  warnings: string[]
}

export const SUBMISSION_MODES = [
  'preview',
  'export',
  'direct_submit',
] as const

export type SubmissionMode = (typeof SUBMISSION_MODES)[number]

export type SubmissionTargetKey = string

export type SubmissionPayloadJson = Record<string, unknown> | Array<unknown>

// This mirrors the backend contract shape. The "at least one payload variant"
// invariant is enforced by backend validation, not by TypeScript at runtime.
export interface SubmissionPayloadContract {
  mode: SubmissionMode
  target_key: SubmissionTargetKey
  adapter_key: string
  candidate_ids: string[]
  payload_json?: SubmissionPayloadJson | null
  payload_text?: string | null
  content_type?: string | null
  filename?: string | null
  warnings: string[]
}

export interface SubmissionDomainAdapter {
  adapter_key: string
  supported_submission_modes: SubmissionMode[]
  supported_target_keys: SubmissionTargetKey[]
  build_submission_payload(args: {
    mode: SubmissionMode
    target_key: SubmissionTargetKey
    payload_context: Record<string, unknown>
  }): SubmissionPayloadContract
}
