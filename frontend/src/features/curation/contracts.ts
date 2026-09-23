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

export type ResolutionState = 'resolved' | 'unresolved'

/** One validated value behind a review field: the paper's wording and the validation result. */
export interface DomainEnvelopeReviewResolvedValue {
  value_path: string
  /** "label (ID)" when resolved, otherwise the literal UNRESOLVED. */
  display_text: string
  /** Paper wording; a legacy value reads "... (legacy, unverified)". */
  mention?: string | null
  resolution_state: ResolutionState
  /** Backend-owned lookup outcome code (resolvable_values.LookupOutcome). */
  lookup_outcome: string
  /** The lookup outcome in plain words, e.g. "Not found". */
  lookup_result: string
  validator_explanation?: string | null
  validator_curator_message?: string | null
  /** A plain sentence when the stored value could not be read; it then reads as unresolved. */
  issue?: string | null
  /** Set when a curator's identity edit resolved the value (lookup_outcome curator_override). */
  curator_override?: DomainEnvelopeReviewCuratorOverride | null
  /** Open warnings where a validator disagrees with the curator override. */
  override_disagreements: string[]
  /** Payload paths of the value's identity keys: what a curator edits, or clears to remove an override. */
  identity_field_paths: string[]
  /** The value's identifier key (e.g. curie). */
  id_key: string | null
  /** The value's name key (e.g. name). */
  label_key: string | null
  /** Further identity keys only a validator fills (e.g. taxon). */
  validated_keys: string[]
  /** Each identity key's value as stored (null when absent): a replace_identity `before`. */
  stored_identity: Record<string, unknown>
  /** The value as stored: a profile value's whole-value replace, a list element's removal. */
  stored_value?: Record<string, unknown> | null
  /** The value's own field is protected, which blocks a curator override. */
  container_protected: boolean
  /** A curator may override this value (its field is open and every identity field editable). */
  overridable: boolean
}

export interface DomainEnvelopeReviewCuratorOverride {
  /** The curator's account id; never shown to curators. */
  actor_id: string
  /** The curator's display name, when the override record has one. */
  actor_display_name?: string | null
  /** ISO 8601 time of the override. */
  at: string
}

export interface DomainEnvelopeReviewFieldResolution {
  /**
   * Main cell text: the validated value or UNRESOLVED, never paper wording.
   * For one of a value's own leaves, that leaf in plain words.
   */
  display_text: string
  values: DomainEnvelopeReviewResolvedValue[]
  /** Set when the field is one of its value's own leaves (mention, lookup_outcome, ...). */
  leaf_key?: string | null
}

export interface DomainEnvelopeReviewRowSummaryField {
  field_path: string
  label: string
  value?: unknown | null
  field_type?: string | null
  metadata: Record<string, unknown>
  resolution?: DomainEnvelopeReviewFieldResolution | null
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
  label: string | null
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
