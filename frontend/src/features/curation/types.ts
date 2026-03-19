/**
 * Shared frontend contract for curation inventory and workspace features.
 *
 * This mirrors backend/src/schemas/curation_workspace.py. Evidence-anchor,
 * field-validation, and submission payload details intentionally stop at
 * summary/reference models here so deeper subtype ownership can land in ALL-93.
 */

export const CURATION_WORKSPACE_SCHEMA_VERSION = '1.0' as const;

export type CurationDomain =
  | 'disease'
  | 'expression'
  | 'allele'
  | 'gene'
  | 'chemical'
  | 'phenotype';

export type CurationSessionStatus =
  | 'new'
  | 'in_progress'
  | 'ready'
  | 'paused'
  | 'submitted'
  | 'rejected';

export type CurationSessionSourceKind = 'chat' | 'flow' | 'bootstrap' | 'manual';

export type CurationCandidateStatus = 'pending' | 'editing' | 'reviewed' | 'submitted';

export type CurationCandidateDecision =
  | 'pending'
  | 'accepted'
  | 'accepted_with_changes'
  | 'rejected';

export type CurationDraftFieldInputKind =
  | 'text'
  | 'textarea'
  | 'select'
  | 'autocomplete'
  | 'multiselect'
  | 'boolean'
  | 'number'
  | 'date';

export type CurationDraftValueSource =
  | 'ai_seed'
  | 'curator_edit'
  | 'manual_annotation'
  | 'system_update';

export type CurationSessionSortBy =
  | 'prepared_at'
  | 'last_worked_at'
  | 'status'
  | 'domain'
  | 'paper_title'
  | 'pmid';

export type CurationSortOrder = 'asc' | 'desc';

export type CurationSubmissionStatus =
  | 'not_started'
  | 'pending'
  | 'succeeded'
  | 'failed';

export type CurationActionType =
  | 'session_created'
  | 'session_status_changed'
  | 'candidate_selected'
  | 'candidate_decision_changed'
  | 'draft_autosaved'
  | 'draft_reset'
  | 'field_updated'
  | 'validation_requested'
  | 'validation_completed'
  | 'submission_requested'
  | 'submission_completed';

export type CurationActionActorKind = 'system' | 'curator' | 'agent';

export interface CurationUserSummary {
  user_id: string;
  display_name?: string | null;
  email?: string | null;
}

export interface CurationDocumentSummary {
  document_id: string;
  pmid?: string | null;
  title: string;
  journal?: string | null;
  published_at?: string | null;
}

export interface CurationSessionOrigin {
  source_kind: CurationSessionSourceKind;
  flow_run_id?: string | null;
  chat_session_id?: string | null;
  trace_id?: string | null;
  label?: string | null;
}

export interface CurationExtractionResultSummary {
  extraction_result_id: string;
  document_id: string;
  domain: CurationDomain;
  source_kind: CurationSessionSourceKind;
  agent_key?: string | null;
  schema_key?: string | null;
  schema_version?: string | null;
  flow_run_id?: string | null;
  trace_id?: string | null;
  created_at: string;
}

export interface CurationEvidenceSummary {
  total_count: number;
  resolved_count: number;
  unresolved_count: number;
}

export interface CurationValidationSummary {
  total_count: number;
  validated_count: number;
  warning_count: number;
  error_count: number;
  stale_count: number;
  unvalidated_count: number;
}

export interface CurationSubmissionSummary {
  submission_id: string;
  status: CurationSubmissionStatus;
  target_system?: string | null;
  external_reference?: string | null;
  submitted_at?: string | null;
  last_attempted_at?: string | null;
  last_error?: string | null;
}

export interface CurationReviewProgress {
  total_candidates: number;
  pending_candidates: number;
  editing_candidates: number;
  reviewed_candidates: number;
  accepted_candidates: number;
  modified_candidates: number;
  rejected_candidates: number;
}

export interface CurationWorkspaceHydrationState {
  selected_candidate_id?: string | null;
  active_field_key?: string | null;
  active_evidence_anchor_id?: string | null;
  pdf_page?: number | null;
  editor_scroll_top?: number | null;
  panel_layout: Record<string, number>;
  updated_at?: string | null;
}

export interface CurationDraftFieldOption {
  value: string;
  label: string;
  disabled: boolean;
}

export interface CurationDraftField {
  field_key: string;
  label: string;
  input_kind: CurationDraftFieldInputKind;
  value?: unknown;
  ai_value?: unknown;
  placeholder?: string | null;
  help_text?: string | null;
  required: boolean;
  dirty: boolean;
  value_source: CurationDraftValueSource;
  options: CurationDraftFieldOption[];
  evidence_anchor_ids: string[];
  validation_snapshot_id?: string | null;
  validation_stale: boolean;
  last_updated_at?: string | null;
  updated_by?: CurationUserSummary | null;
}

export interface CurationDraftSection {
  section_key: string;
  label: string;
  fields: CurationDraftField[];
  collapsed: boolean;
}

export interface CurationDraft {
  draft_id: string;
  candidate_id: string;
  sections: CurationDraftSection[];
  is_dirty: boolean;
  dirty_field_keys: string[];
  last_saved_at?: string | null;
  last_saved_by?: CurationUserSummary | null;
  validation_stale: boolean;
}

export interface CurationCandidateSummary {
  candidate_id: string;
  session_id: string;
  queue_position: number;
  display_label: string;
  summary?: string | null;
  status: CurationCandidateStatus;
  decision: CurationCandidateDecision;
  confidence_score?: number | null;
  has_curator_edits: boolean;
  unresolved_ambiguity_count: number;
  evidence_summary: CurationEvidenceSummary;
  validation_summary: CurationValidationSummary;
  submission_summary?: CurationSubmissionSummary | null;
  last_reviewed_at?: string | null;
}

export interface CurationCandidate extends CurationCandidateSummary {
  draft: CurationDraft;
  source_extraction?: CurationExtractionResultSummary | null;
  evidence_anchor_ids: string[];
  validation_snapshot_ids: string[];
  context_summary?: string | null;
  unresolved_ambiguities: string[];
  notes?: string | null;
}

export interface CurationActionLogEntry {
  action_id: string;
  session_id: string;
  candidate_id?: string | null;
  action_type: CurationActionType;
  actor_kind: CurationActionActorKind;
  actor_id?: string | null;
  actor_display_name?: string | null;
  field_key?: string | null;
  previous_state?: Record<string, unknown> | null;
  new_state?: Record<string, unknown> | null;
  reason?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface CurationSessionSummary {
  session_id: string;
  status: CurationSessionStatus;
  domain: CurationDomain;
  document: CurationDocumentSummary;
  origin: CurationSessionOrigin;
  curator?: CurationUserSummary | null;
  candidate_count: number;
  reviewed_candidate_count: number;
  review_progress: CurationReviewProgress;
  evidence_summary: CurationEvidenceSummary;
  validation_summary: CurationValidationSummary;
  submission_summary?: CurationSubmissionSummary | null;
  prepared_at: string;
  last_worked_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface CurationSessionDetail extends CurationSessionSummary {
  active_candidate_id?: string | null;
  notes?: string | null;
  hydration?: CurationWorkspaceHydrationState | null;
  latest_extraction?: CurationExtractionResultSummary | null;
}

export interface CurationSessionStatsResponse {
  total_sessions: number;
  new_sessions: number;
  in_progress_sessions: number;
  ready_sessions: number;
  submitted_sessions: number;
  paused_sessions: number;
  rejected_sessions: number;
}

export interface CurationSessionListFilters {
  search?: string | null;
  statuses: CurationSessionStatus[];
  domains: CurationDomain[];
  curator_ids: string[];
  flow_run_id?: string | null;
  prepared_from?: string | null;
  prepared_to?: string | null;
  last_worked_from?: string | null;
  last_worked_to?: string | null;
  sort_by: CurationSessionSortBy;
  sort_order: CurationSortOrder;
}

export interface CurationPagination {
  page: number;
  page_size: number;
}

export interface CurationSessionListRequest {
  filters: CurationSessionListFilters;
  pagination: CurationPagination;
}

export interface CurationSessionListResponse {
  sessions: CurationSessionSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface CurationSessionNavigation {
  previous_session_id?: string | null;
  next_session_id?: string | null;
  queue_position?: number | null;
  total_sessions?: number | null;
}

export interface CurationWorkspaceResponse {
  schema_version: typeof CURATION_WORKSPACE_SCHEMA_VERSION;
  session: CurationSessionDetail;
  candidates: CurationCandidate[];
  action_log: CurationActionLogEntry[];
  navigation?: CurationSessionNavigation | null;
}

export interface CreateCurationSessionRequest {
  document_id: string;
  domain: CurationDomain;
  source_kind: CurationSessionSourceKind;
  extraction_result_id?: string | null;
  notes?: string | null;
}

export interface BootstrapCurationSessionRequest {
  domain?: CurationDomain | null;
  extraction_result_id?: string | null;
  force_refresh: boolean;
}

export interface UpdateCurationSessionRequest {
  status?: CurationSessionStatus | null;
  notes?: string | null;
  active_candidate_id?: string | null;
  hydration?: CurationWorkspaceHydrationState | null;
}

export interface CurationCandidateReviewRequest {
  decision: CurationCandidateDecision;
  draft?: CurationDraft | null;
  reason?: string | null;
  advance_queue: boolean;
}

export interface CurationCandidateReviewResponse {
  candidate: CurationCandidate;
  session: CurationSessionDetail;
  next_candidate_id?: string | null;
}

export interface CurationNextSessionResponse {
  session?: CurationSessionSummary | null;
  navigation?: CurationSessionNavigation | null;
}
