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
  pdfx_markdown_offset_start?: number | null
  pdfx_markdown_offset_end?: number | null
  page_number?: number | null
  page_label?: string | null
  section_title?: string | null
  subsection_title?: string | null
  figure_reference?: string | null
  table_reference?: string | null
  chunk_ids: string[]
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
