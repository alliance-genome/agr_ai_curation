export {
  EVIDENCE_ANCHOR_KINDS,
  EVIDENCE_LOCATOR_QUALITIES,
  EVIDENCE_SUPPORTS_DECISIONS,
  FIELD_VALIDATION_STATUSES,
  SUBMISSION_MODES,
} from './contracts'

export type {
  EvidenceAnchor,
  EvidenceAnchorKind,
  EvidenceLocatorQuality,
  EvidenceSupportsDecision,
  FieldValidationResult,
  FieldValidationStatus,
  SubmissionDomainAdapter,
  SubmissionMode,
  SubmissionPayloadContract,
  SubmissionPayloadJson,
  SubmissionTargetKey,
  ValidationCandidateMatch,
} from './contracts'

import type {
  EvidenceAnchor,
  FieldValidationResult,
  SubmissionMode,
  SubmissionPayloadContract,
  SubmissionTargetKey,
} from './contracts'

export const CURATION_SESSION_STATUSES = [
  'new',
  'in_progress',
  'paused',
  'ready_for_submission',
  'submitted',
  'rejected',
] as const

export type CurationSessionStatus = (typeof CURATION_SESSION_STATUSES)[number]

export const CURATION_CANDIDATE_STATUSES = [
  'pending',
  'accepted',
  'rejected',
] as const

export type CurationCandidateStatus = (typeof CURATION_CANDIDATE_STATUSES)[number]

export const CURATION_CANDIDATE_SOURCES = [
  'extracted',
  'manual',
  'imported',
] as const

export type CurationCandidateSource = (typeof CURATION_CANDIDATE_SOURCES)[number]

export const CURATION_CANDIDATE_ACTIONS = [
  'accept',
  'reject',
  'reset',
] as const

export type CurationCandidateAction = (typeof CURATION_CANDIDATE_ACTIONS)[number]

export const CURATION_VALIDATION_SNAPSHOT_STATES = [
  'not_requested',
  'pending',
  'completed',
  'failed',
  'stale',
] as const

export type CurationValidationSnapshotState =
  (typeof CURATION_VALIDATION_SNAPSHOT_STATES)[number]

export const CURATION_VALIDATION_SCOPES = [
  'candidate',
  'session',
] as const

export type CurationValidationScope = (typeof CURATION_VALIDATION_SCOPES)[number]

export const CURATION_ACTION_TYPES = [
  'session_created',
  'session_status_updated',
  'session_assigned',
  'candidate_created',
  'candidate_updated',
  'candidate_accepted',
  'candidate_rejected',
  'candidate_reset',
  'validation_requested',
  'validation_completed',
  'evidence_recomputed',
  'evidence_manual_added',
  'submission_previewed',
  'submission_executed',
  'submission_retried',
] as const

export type CurationActionType = (typeof CURATION_ACTION_TYPES)[number]

export const CURATION_ACTOR_TYPES = [
  'user',
  'system',
  'adapter',
] as const

export type CurationActorType = (typeof CURATION_ACTOR_TYPES)[number]

export const CURATION_EVIDENCE_SOURCES = [
  'extracted',
  'manual',
  'recomputed',
] as const

export type CurationEvidenceSource = (typeof CURATION_EVIDENCE_SOURCES)[number]

export const CURATION_SESSION_SORT_FIELDS = [
  'prepared_at',
  'last_worked_at',
  'status',
  'document_title',
  'candidate_count',
  'validation',
  'evidence',
  'curator',
] as const

export type CurationSessionSortField = (typeof CURATION_SESSION_SORT_FIELDS)[number]

export const CURATION_SORT_DIRECTIONS = [
  'asc',
  'desc',
] as const

export type CurationSortDirection = (typeof CURATION_SORT_DIRECTIONS)[number]

export const CURATION_QUEUE_NAVIGATION_DIRECTIONS = [
  'next',
  'previous',
] as const

export type CurationQueueNavigationDirection =
  (typeof CURATION_QUEUE_NAVIGATION_DIRECTIONS)[number]

export const CURATION_SUBMISSION_STATUSES = [
  'preview_ready',
  'export_ready',
  'queued',
  'accepted',
  'validation_errors',
  'conflict',
  'manual_review_required',
  'failed',
] as const

export type CurationSubmissionStatus = (typeof CURATION_SUBMISSION_STATUSES)[number]

export const CURATION_EXTRACTION_SOURCE_KINDS = [
  'chat',
  'flow',
  'manual_import',
] as const

export type CurationExtractionSourceKind =
  (typeof CURATION_EXTRACTION_SOURCE_KINDS)[number]

export type CurationStructuredPayload = Record<string, unknown> | Array<unknown>

export interface CurationActorRef {
  actor_id?: string | null
  display_name?: string | null
  email?: string | null
}

export interface CurationAdapterRef {
  adapter_key: string
  profile_key?: string | null
  display_label?: string | null
  profile_label?: string | null
  color_token?: string | null
  metadata: Record<string, unknown>
}

export interface CurationDocumentRef {
  document_id: string
  title: string
  pmid?: string | null
  doi?: string | null
  citation_label?: string | null
  pdf_url?: string | null
  viewer_url?: string | null
  publication_year?: number | null
}

export interface CurationDateRange {
  from_at?: string | null
  to_at?: string | null
}

export interface CurationEvidenceQualityCounts {
  exact_quote: number
  normalized_quote: number
  section_only: number
  page_only: number
  document_only: number
  unresolved: number
}

export interface CurationEvidenceSummary {
  total_anchor_count: number
  resolved_anchor_count: number
  viewer_highlightable_anchor_count: number
  quality_counts: CurationEvidenceQualityCounts
  degraded: boolean
  warnings: string[]
}

export interface CurationValidationCounts {
  validated: number
  ambiguous: number
  not_found: number
  invalid_format: number
  conflict: number
  skipped: number
  overridden: number
}

export interface CurationValidationSummary {
  state: CurationValidationSnapshotState
  counts: CurationValidationCounts
  last_validated_at?: string | null
  stale_field_keys: string[]
  warnings: string[]
}

export interface CurationSessionProgress {
  total_candidates: number
  reviewed_candidates: number
  pending_candidates: number
  accepted_candidates: number
  rejected_candidates: number
  manual_candidates: number
}

export interface CurationCandidateSubmissionReadiness {
  candidate_id: string
  ready: boolean
  blocking_reasons: string[]
  warnings: string[]
}

export interface CurationDraftField {
  field_key: string
  label: string
  value?: unknown | null
  seed_value?: unknown | null
  field_type?: string | null
  group_key?: string | null
  group_label?: string | null
  order: number
  required: boolean
  read_only: boolean
  dirty: boolean
  stale_validation: boolean
  evidence_anchor_ids: string[]
  validation_result?: FieldValidationResult | null
  metadata: Record<string, unknown>
}

export interface CurationDraft {
  draft_id: string
  candidate_id: string
  adapter_key: string
  version: number
  title?: string | null
  summary?: string | null
  fields: CurationDraftField[]
  notes?: string | null
  created_at: string
  updated_at: string
  last_saved_at?: string | null
  metadata: Record<string, unknown>
}

export interface CurationEvidenceRecord {
  anchor_id: string
  candidate_id: string
  source: CurationEvidenceSource
  field_keys: string[]
  field_group_keys: string[]
  is_primary: boolean
  anchor: EvidenceAnchor
  created_at: string
  updated_at: string
  warnings: string[]
}

export interface CurationValidationSnapshot {
  snapshot_id: string
  scope: CurationValidationScope
  session_id: string
  candidate_id?: string | null
  adapter_key?: string | null
  state: CurationValidationSnapshotState
  field_results: Record<string, FieldValidationResult>
  summary: CurationValidationSummary
  requested_at?: string | null
  completed_at?: string | null
  warnings: string[]
}

export interface CurationCandidate {
  candidate_id: string
  session_id: string
  source: CurationCandidateSource
  status: CurationCandidateStatus
  order: number
  adapter_key: string
  profile_key?: string | null
  display_label?: string | null
  secondary_label?: string | null
  confidence?: number | null
  conversation_summary?: string | null
  unresolved_ambiguities: string[]
  extraction_result_id?: string | null
  draft: CurationDraft
  evidence_anchors: CurationEvidenceRecord[]
  validation?: CurationValidationSummary | null
  evidence_summary?: CurationEvidenceSummary | null
  created_at: string
  updated_at: string
  last_reviewed_at?: string | null
  metadata: Record<string, unknown>
}

export interface CurationActionLogEntry {
  action_id: string
  session_id: string
  candidate_id?: string | null
  draft_id?: string | null
  action_type: CurationActionType
  actor_type: CurationActorType
  actor?: CurationActorRef | null
  occurred_at: string
  previous_session_status?: CurationSessionStatus | null
  new_session_status?: CurationSessionStatus | null
  previous_candidate_status?: CurationCandidateStatus | null
  new_candidate_status?: CurationCandidateStatus | null
  changed_field_keys: string[]
  evidence_anchor_ids: string[]
  reason?: string | null
  message?: string | null
  metadata: Record<string, unknown>
}

export interface CurationSessionFilters {
  statuses?: CurationSessionStatus[]
  adapter_keys?: string[]
  profile_keys?: string[]
  domain_keys?: string[]
  curator_ids?: string[]
  tags?: string[]
  flow_run_id?: string | null
  document_id?: string | null
  search?: string | null
  prepared_between?: CurationDateRange | null
  last_worked_between?: CurationDateRange | null
  saved_view_id?: string | null
}

export interface CurationSavedView {
  view_id: string
  name: string
  description?: string | null
  filters: CurationSessionFilters
  sort_by: CurationSessionSortField
  sort_direction: CurationSortDirection
  is_default: boolean
  created_by?: CurationActorRef | null
  created_at: string
  updated_at: string
}

export interface CurationPageInfo {
  page: number
  page_size: number
  total_items: number
  total_pages: number
  has_next_page: boolean
  has_previous_page: boolean
}

export interface CurationFlowRunSummary {
  flow_run_id: string
  display_label?: string | null
  session_count: number
  reviewed_count: number
  pending_count: number
  submitted_count: number
  last_activity_at?: string | null
}

export interface CurationQueueContext {
  filters: CurationSessionFilters
  sort_by: CurationSessionSortField
  sort_direction: CurationSortDirection
  position?: number | null
  total_sessions?: number | null
  previous_session_id?: string | null
  next_session_id?: string | null
}

export interface CurationSubmissionRecord {
  submission_id: string
  session_id: string
  adapter_key: string
  mode: SubmissionMode
  target_key: SubmissionTargetKey
  status: CurationSubmissionStatus
  readiness: CurationCandidateSubmissionReadiness[]
  payload?: SubmissionPayloadContract | null
  requested_at: string
  completed_at?: string | null
  external_reference?: string | null
  response_message?: string | null
  validation_errors: string[]
  warnings: string[]
}

export interface CurationExtractionResultRecord {
  extraction_result_id: string
  document_id: string
  adapter_key?: string | null
  profile_key?: string | null
  domain_key?: string | null
  agent_key: string
  source_kind: CurationExtractionSourceKind
  origin_session_id?: string | null
  trace_id?: string | null
  flow_run_id?: string | null
  user_id?: string | null
  candidate_count: number
  conversation_summary?: string | null
  payload_json: CurationStructuredPayload
  created_at: string
  metadata: Record<string, unknown>
}

export interface CurationSessionSummary {
  session_id: string
  status: CurationSessionStatus
  adapter: CurationAdapterRef
  document: CurationDocumentRef
  flow_run_id?: string | null
  progress: CurationSessionProgress
  validation?: CurationValidationSummary | null
  evidence?: CurationEvidenceSummary | null
  current_candidate_id?: string | null
  assigned_curator?: CurationActorRef | null
  created_by?: CurationActorRef | null
  prepared_at: string
  last_worked_at?: string | null
  notes?: string | null
  warnings: string[]
  tags: string[]
}

export interface CurationReviewSession extends CurationSessionSummary {
  session_version: number
  extraction_results: CurationExtractionResultRecord[]
  latest_submission?: CurationSubmissionRecord | null
  submitted_at?: string | null
  paused_at?: string | null
  rejection_reason?: string | null
}

export interface CurationWorkspace {
  session: CurationReviewSession
  candidates: CurationCandidate[]
  active_candidate_id?: string | null
  queue_context?: CurationQueueContext | null
  action_log: CurationActionLogEntry[]
  submission_history: CurationSubmissionRecord[]
  saved_view_context?: CurationSavedView | null
}

export interface CurationSessionStats {
  total_sessions: number
  domain_count: number
  new_sessions: number
  in_progress_sessions: number
  ready_for_submission_sessions: number
  paused_sessions: number
  submitted_sessions: number
  rejected_sessions: number
  assigned_to_current_user: number
  assigned_to_others: number
  submitted_last_7_days: number
}

export interface CurationSessionListRequest {
  filters?: CurationSessionFilters
  sort_by?: CurationSessionSortField
  sort_direction?: CurationSortDirection
  page?: number
  page_size?: number
  group_by_flow_run?: boolean
}

export interface CurationSessionListResponse {
  sessions: CurationSessionSummary[]
  page_info: CurationPageInfo
  applied_filters: CurationSessionFilters
  sort_by: CurationSessionSortField
  sort_direction: CurationSortDirection
  flow_run_groups: CurationFlowRunSummary[]
}

export interface CurationSessionStatsRequest {
  filters?: CurationSessionFilters
}

export interface CurationSessionStatsResponse {
  stats: CurationSessionStats
  applied_filters: CurationSessionFilters
}

export interface CurationFlowRunListRequest {
  filters?: CurationSessionFilters
}

export interface CurationFlowRunListResponse {
  flow_runs: CurationFlowRunSummary[]
  applied_filters: CurationSessionFilters
}

export interface CurationFlowRunSessionsRequest {
  flow_run_id: string
  filters?: CurationSessionFilters
  page?: number
  page_size?: number
}

export interface CurationFlowRunSessionsResponse {
  flow_run: CurationFlowRunSummary
  sessions: CurationSessionSummary[]
  page_info: CurationPageInfo
}

export interface CurationSavedViewListResponse {
  views: CurationSavedView[]
}

export interface CurationSavedViewCreateRequest {
  name: string
  description?: string | null
  filters: CurationSessionFilters
  sort_by: CurationSessionSortField
  sort_direction: CurationSortDirection
  is_default?: boolean
}

export interface CurationSavedViewCreateResponse {
  view: CurationSavedView
}

export interface CurationSavedViewDeleteResponse {
  deleted_view_id: string
}

export interface CurationNextSessionRequest {
  current_session_id?: string | null
  direction?: CurationQueueNavigationDirection
  filters?: CurationSessionFilters
  sort_by?: CurationSessionSortField
  sort_direction?: CurationSortDirection
}

export interface CurationNextSessionResponse {
  session?: CurationSessionSummary | null
  queue_context: CurationQueueContext
}

export interface CurationWorkspaceRequest {
  session_id: string
  candidate_id?: string | null
  include_action_log?: boolean
  include_submission_history?: boolean
}

export interface CurationWorkspaceResponse {
  workspace: CurationWorkspace
}

export interface CurationSessionCreateRequest {
  document_id: string
  adapter_key: string
  profile_key?: string | null
  curator_id?: string | null
  seed_extraction_result_ids?: string[]
  notes?: string | null
}

export interface CurationSessionCreateResponse {
  created: boolean
  workspace: CurationWorkspace
}

export interface CurationDocumentBootstrapRequest {
  document_id: string
  adapter_key?: string | null
  profile_key?: string | null
  domain_key?: string | null
  source_extraction_result_id?: string | null
  curator_id?: string | null
  force_rebuild?: boolean
}

export interface CurationDocumentBootstrapResponse {
  created: boolean
  workspace: CurationWorkspace
}

export interface CurationSessionUpdateRequest {
  session_id: string
  status?: CurationSessionStatus | null
  notes?: string | null
  curator_id?: string | null
  current_candidate_id?: string | null
}

export interface CurationSessionUpdateResponse {
  session: CurationReviewSession
  action_log_entry?: CurationActionLogEntry | null
}

export interface CurationDraftFieldChange {
  field_key: string
  value?: unknown | null
  revert_to_seed?: boolean
}

export interface CurationCandidateDraftUpdateRequest {
  session_id: string
  candidate_id: string
  draft_id: string
  expected_version?: number | null
  field_changes?: CurationDraftFieldChange[]
  notes?: string | null
  autosave?: boolean
}

export interface CurationCandidateDraftUpdateResponse {
  candidate: CurationCandidate
  draft: CurationDraft
  validation_snapshot?: CurationValidationSnapshot | null
  action_log_entry?: CurationActionLogEntry | null
}

export interface CurationCandidateDecisionRequest {
  session_id: string
  candidate_id: string
  action: CurationCandidateAction
  reason?: string | null
  advance_queue?: boolean
}

export interface CurationCandidateDecisionResponse {
  candidate: CurationCandidate
  session: CurationReviewSession
  next_candidate_id?: string | null
  action_log_entry: CurationActionLogEntry
}

export interface CurationManualCandidateCreateRequest {
  session_id: string
  adapter_key: string
  profile_key?: string | null
  source?: CurationCandidateSource
  display_label?: string | null
  draft: CurationDraft
  evidence_anchors?: CurationEvidenceRecord[]
}

export interface CurationManualCandidateCreateResponse {
  candidate: CurationCandidate
  session: CurationReviewSession
  action_log_entry: CurationActionLogEntry
}

export interface CurationEvidenceResolveRequest {
  session_id: string
  candidate_id: string
  field_key?: string | null
  anchor: EvidenceAnchor
  replace_existing?: boolean
}

export interface CurationEvidenceResolveResponse {
  evidence_record: CurationEvidenceRecord
  candidate: CurationCandidate
}

export interface CurationManualEvidenceCreateRequest {
  session_id: string
  candidate_id: string
  field_keys?: string[]
  field_group_keys?: string[]
  anchor: EvidenceAnchor
  is_primary?: boolean
}

export interface CurationManualEvidenceCreateResponse {
  evidence_record: CurationEvidenceRecord
  candidate: CurationCandidate
  action_log_entry: CurationActionLogEntry
}

export interface CurationEvidenceRecomputeRequest {
  session_id: string
  candidate_ids?: string[]
  force?: boolean
}

export interface CurationEvidenceRecomputeResponse {
  session: CurationReviewSession
  updated_evidence_records: CurationEvidenceRecord[]
  action_log_entry: CurationActionLogEntry
}

export interface CurationCandidateValidationRequest {
  session_id: string
  candidate_id: string
  field_keys?: string[]
  force?: boolean
}

export interface CurationCandidateValidationResponse {
  candidate: CurationCandidate
  validation_snapshot: CurationValidationSnapshot
}

export interface CurationSessionValidationRequest {
  session_id: string
  candidate_ids?: string[]
  force?: boolean
}

export interface CurationSessionValidationResponse {
  session: CurationReviewSession
  session_validation: CurationValidationSnapshot
  candidate_validations: CurationValidationSnapshot[]
}

export interface CurationSubmissionPreviewRequest {
  session_id: string
  mode: SubmissionMode
  target_key: SubmissionTargetKey
  candidate_ids?: string[]
  include_payload?: boolean
}

export interface CurationSubmissionPreviewResponse {
  submission: CurationSubmissionRecord
  session_validation?: CurationValidationSnapshot | null
}

export interface CurationSubmissionExecuteRequest {
  session_id: string
  target_key: SubmissionTargetKey
  candidate_ids?: string[]
  mode?: SubmissionMode
}

export interface CurationSubmissionExecuteResponse {
  submission: CurationSubmissionRecord
  session: CurationReviewSession
  action_log_entry: CurationActionLogEntry
}

export interface CurationSubmissionRetryRequest {
  submission_id: string
  reason?: string | null
}

export interface CurationSubmissionRetryResponse {
  submission: CurationSubmissionRecord
  action_log_entry: CurationActionLogEntry
}

export interface CurationSubmissionHistoryResponse {
  submission: CurationSubmissionRecord
}

export interface CurationExtractionPersistenceRequest {
  document_id: string
  agent_key: string
  source_kind: CurationExtractionSourceKind
  adapter_key?: string | null
  profile_key?: string | null
  domain_key?: string | null
  origin_session_id?: string | null
  trace_id?: string | null
  flow_run_id?: string | null
  user_id?: string | null
  candidate_count?: number
  conversation_summary?: string | null
  payload_json: CurationStructuredPayload
  metadata?: Record<string, unknown>
}

export interface CurationExtractionPersistenceResponse {
  extraction_result: CurationExtractionResultRecord
}
