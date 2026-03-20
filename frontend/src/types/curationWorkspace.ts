/**
 * Shared curation-workspace contract types mirrored from the backend schemas.
 *
 * These interfaces intentionally stay close to the API payload shape so later
 * evidence, validation, and submission features can share one contract surface.
 */

export type EvidenceAnchorKind =
  | 'chunk'
  | 'doc_item'
  | 'snippet'
  | 'sentence'
  | 'section'
  | 'page'
  | 'document'

export type EvidenceLocatorQuality =
  | 'exact_quote'
  | 'normalized_quote'
  | 'section_only'
  | 'page_only'
  | 'document_only'
  | 'unresolved'

export type EvidenceDecisionSupport = 'supports' | 'contradicts' | 'context_only'

export interface EvidenceAnchor {
  anchor_kind: EvidenceAnchorKind
  locator_quality: EvidenceLocatorQuality
  supports_decision: EvidenceDecisionSupport
  document_id: string
  chunk_id?: string | null
  doc_item_ids: string[]
  page_number?: number | null
  section_title?: string | null
  section_path: string[]
  figure_reference?: string | null
  snippet_text?: string | null
  sentence_text?: string | null
  normalized_text?: string | null
  viewer_search_text?: string | null
  pdfx_markdown_start_offset?: number | null
  pdfx_markdown_end_offset?: number | null
}

export type FieldValidationStatus =
  | 'validated'
  | 'ambiguous'
  | 'not_found'
  | 'invalid_format'
  | 'conflict'
  | 'skipped'
  | 'overridden'

export interface FieldValidationCandidateMatch {
  matched_value: string
  candidate_id?: string | null
  display_label?: string | null
  confidence?: number | null
  metadata: Record<string, unknown>
}

export interface FieldValidationResult {
  status: FieldValidationStatus
  resolver?: string | null
  candidate_matches: FieldValidationCandidateMatch[]
  warnings: string[]
}

export type SubmissionMode = 'preview' | 'export' | 'direct_submit'

export type SubmissionTargetSystem =
  | 'alliance_curation_api'
  | 'abc_api'
  | 'ingest_bulk_submission'
  | 'file_export_upload'

export interface SubmissionDomainAdapterContract<
  TPayload extends Record<string, unknown> = Record<string, unknown>,
> {
  domain: string
  adapter_name: string
  adapter_version?: string | null
  target_schema?: string | null
  payload: TPayload
}

export interface SubmissionPayload<
  TPayload extends Record<string, unknown> = Record<string, unknown>,
> {
  mode: SubmissionMode
  target_system: SubmissionTargetSystem
  domain_adapter: SubmissionDomainAdapterContract<TPayload>
}
