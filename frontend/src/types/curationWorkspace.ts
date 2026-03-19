/**
 * Shared curation-workspace contract types mirrored from the backend schemas.
 *
 * These interfaces intentionally stay close to the API payload shape so later
 * evidence, validation, and submission features can share one contract surface.
 */

export type EvidenceAnchorKind = 'chunk' | 'doc_item' | 'bbox' | 'sentence' | 'snippet'

export type EvidenceLocatorQuality = 'exact' | 'approximate' | 'degraded' | 'unknown'

export type EvidenceDecisionSupport = 'supports' | 'contradicts' | 'context_only'

export interface EvidenceAnchorBoundingBox {
  left: number
  top: number
  right: number
  bottom: number
  coord_origin?: 'BOTTOMLEFT' | 'TOPLEFT' | 'BOTTOMRIGHT' | 'TOPRIGHT'
}

export interface EvidenceAnchor {
  anchor_kind: EvidenceAnchorKind
  locator_quality: EvidenceLocatorQuality
  supports_decision: EvidenceDecisionSupport
  document_id?: string | null
  page_number?: number | null
  chunk_id?: string | null
  doc_item_ids: string[]
  bbox?: EvidenceAnchorBoundingBox | null
  snippet?: string | null
  sentence?: string | null
}

export type FieldValidationStatus = 'pending' | 'valid' | 'ambiguous' | 'invalid'

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

export type SubmissionMode = 'preview' | 'submit'

export type SubmissionTargetSystem = 'alliance_curation_api' | 'abc_api'

export interface SubmissionDomainAdapterContract<
  TPayload extends Record<string, unknown> = Record<string, unknown>,
> {
  domain: string
  adapter_name: string
  adapter_version?: string | null
  payload: TPayload
}

export interface SubmissionPayload<
  TPayload extends Record<string, unknown> = Record<string, unknown>,
> {
  mode: SubmissionMode
  target_system: SubmissionTargetSystem
  domain_adapter: SubmissionDomainAdapterContract<TPayload>
}

