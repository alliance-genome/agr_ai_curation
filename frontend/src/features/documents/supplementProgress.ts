/**
 * Supplement progress metadata (interface I1) and shared API types for supplement screens
 * (ALL-1460 / ALL-1745 / ALL-1738).
 *
 * Backend writers:
 * - `backend/src/lib/document_sources/file_reasons.py` (`FileReason`, `SupplementState`)
 * - `backend/src/lib/document_sources/supplement_wait.py` (`_Entry.as_metadata`, `_Stage.record`)
 * - `backend/src/lib/document_sources/supplement_add.py` (`_supplement_entries`)
 * - `backend/src/lib/pdf_jobs/service.py` (`_close_open_supplement_entries`)
 * - `backend/src/lib/document_files/file_removal.py` (`removal_check`, `remove_paper_file`)
 */

import { CHECK_REASON_CODES, OBSERVATION_CODES } from './paperSourceFiles'

export const SUPPLEMENT_STATES = [
  'queued',
  'waiting',
  'adding',
  'added',
  'left_out',
] as const
export type SupplementState = (typeof SUPPLEMENT_STATES)[number]

export const SUPPLEMENT_JOB_KINDS = [
  'supplement_add',
  'supplement_wait',
] as const
export type SupplementJobKind = (typeof SUPPLEMENT_JOB_KINDS)[number]

export const WAITING_FOR_SUPPLEMENTS_STAGE = 'waiting_for_supplements' as const
export const ADDING_SUPPLEMENTS_STAGE = 'adding_supplements' as const

/** Endings of an import's supplement stage (P7b; `SUPPLEMENT_STAGE_REASONS`). */
export const SUPPLEMENT_STAGE_REASON_CODES = [
  'login_expired',
  'wait_limit_reached',
  'import_cancelled',
  'supplement_stage_failed',
  'provider_answer_unknown',
  'paper_busy_when_ready',
] as const
export type SupplementStageReasonCode = (typeof SUPPLEMENT_STAGE_REASON_CODES)[number]

/**
 * Per-file add refusal codes (P7c1 / P7c2; `ADD_REFUSAL_REASONS` excluding `CHECK_REASONS`).
 * `pdf_supplement_not_supported_yet` is kept here for historical jobs created before P7c2
 * and retired from the per-paper listing table (ALL-1738 item 1).
 */
export const ADD_REFUSAL_REASON_CODES = [
  'already_in_paper',
  'file_not_in_reference',
  'file_not_tickable',
  'file_changed_since_listed',
  'source_file_missing',
  'source_unavailable',
  'source_download_failed',
  'access_denied',
  'markdown_invalid',
  'paper_text_limit',
  'paper_file_limit',
  'pdf_supplement_not_supported_yet',
  'pdf_too_large',
  'pdf_invalid',
  'pdf_access_denied',
] as const
export type AddRefusalReasonCode = (typeof ADD_REFUSAL_REASON_CODES)[number]

/** Every known `reason_code` that can appear on a `metadata.supplements[]` entry. */
export const SUPPLEMENT_OUTCOME_REASON_CODES = [
  ...OBSERVATION_CODES,
  ...SUPPLEMENT_STAGE_REASON_CODES,
  ...CHECK_REASON_CODES,
  ...ADD_REFUSAL_REASON_CODES,
] as const
export type SupplementOutcomeReasonCode = (typeof SUPPLEMENT_OUTCOME_REASON_CODES)[number]

const SUPPLEMENT_STATE_SET: ReadonlySet<string> = new Set(SUPPLEMENT_STATES)
const SUPPLEMENT_JOB_KIND_SET: ReadonlySet<string> = new Set(SUPPLEMENT_JOB_KINDS)
const SUPPLEMENT_OUTCOME_REASON_SET: ReadonlySet<string> = new Set(SUPPLEMENT_OUTCOME_REASON_CODES)

const ISO_8601_WITH_TZ_RE =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/

export function isKnownSupplementOutcomeReasonCode(
  code: string | null | undefined,
): code is SupplementOutcomeReasonCode {
  return typeof code === 'string' && SUPPLEMENT_OUTCOME_REASON_SET.has(code)
}

/**
 * Strictly validate an ISO-8601 date-time string with an explicit timezone (`Z` or `±HH:MM`).
 * Rejects numeric shorthands (`"0"`, `"123"`), date-only strings, naive timestamps, and
 * impossible calendar dates such as February 30.
 */
export function isValidIsoTimestampWithTz(value: unknown): value is string {
  if (typeof value !== 'string') {
    return false
  }
  const trimmed = value.trim()
  if (trimmed !== value) {
    return false
  }
  const match = ISO_8601_WITH_TZ_RE.exec(trimmed)
  if (!match) {
    return false
  }
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) {
    return false
  }
  const checkDate = new Date(Date.UTC(year, month - 1, day))
  if (
    checkDate.getUTCFullYear() !== year
    || checkDate.getUTCMonth() !== month - 1
    || checkDate.getUTCDate() !== day
  ) {
    return false
  }
  return Number.isFinite(Date.parse(trimmed))
}

/** One recognized supplement entry from a job's `metadata.supplements[]` (interface I1). */
export interface RecognizedSupplementProgressEntry {
  recognized: true
  provider_file_id: string
  display_name: string | null
  state: SupplementState
  /** Raw backend code (known or future unknown); worded via `supplementOutcomeReasonText`. */
  reason_code: string | null
  observed_at: string | null
  file_id: string | null
  measured_value: number | null
  limit_value: number | null
}

/**
 * A supplement entry whose state or required identifiers this screen does not recognize.
 * Kept as a row (never silently dropped) and described with N29 without exposing raw fields.
 */
export interface UnrecognizedSupplementProgressEntry {
  recognized: false
  provider_file_id: string | null
  display_name: string | null
}

/** One entry parsed from a job's `metadata.supplements[]` (interface I1). */
export type SupplementProgressEntry =
  | RecognizedSupplementProgressEntry
  | UnrecognizedSupplementProgressEntry

/** Parsed supplement progress metadata for a processing or supplement-add job. */
export interface SupplementJobProgress {
  supplements: SupplementProgressEntry[]
  supplement_wait_ends_at: string | null
  job_kind: SupplementJobKind | null
  /** Optional `job.current_stage`, used to distinguish pre-main vs post-main `queued` labels. */
  current_stage: string | null
}

/** One supplement choice sent with `POST /api/weaviate/documents/import/source-identifiers` (I6). */
export interface ChosenSupplementInput {
  identifier: string
  provider_file_id: string
  md5: string | null
  converted_md5: string | null
  /**
   * Listed display name carried through import requests so outcomes never fall back to raw ids (G2).
   *
   * IMPORTANT: Part 3 must NOT serialize `display_name` on the wire until Part 0 adds it to
   * `DocumentSourceSupplementChoice`, which currently declares `ConfigDict(extra="forbid")`.
   */
  display_name?: string | null
}

/** One converted file sent with `POST /api/weaviate/documents/{document_id}/files`. */
export interface AddPaperFileInput {
  provider_file_id: string
  md5: string | null
  converted_md5: string
}

/** Request payload for `POST /api/weaviate/documents/{document_id}/files`. */
export interface AddPaperFilesRequest {
  files: AddPaperFileInput[]
}

/** Known refusal codes returned by `file_removal.py` (pre-check and DELETE). */
export const FILE_REMOVAL_REFUSAL_CODES = [
  'import_in_progress',
  'paper_not_completed',
  'file_not_in_paper',
  'main_file_not_removable',
  'last_file_not_removable',
  'file_quoted_in_review',
] as const
export type FileRemovalRefusalCode = (typeof FILE_REMOVAL_REFUSAL_CODES)[number]

/** Response from `GET /api/weaviate/documents/{document_id}/files/{file_id}/removal-check`. */
export interface DocumentFileRemovalCheckResponse {
  document_id: string
  file_id: string
  removable: boolean
  error: FileRemovalRefusalCode | string | null
  session_ids?: string[]
  newest_result_quotes_file: boolean
  has_review_session: boolean
  review_and_curate_would_refuse: boolean
}

/** Response from `DELETE /api/weaviate/documents/{document_id}/files/{file_id}`. */
export interface DocumentFileRemovalResponse {
  document_id: string
  file_id: string
  removed: boolean
  cleanup_finished: boolean
  ready_file_count: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function nonEmptyStringOrNull(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function nonNegativeIntegerOrNull(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    return null
  }
  return value
}

function parseSupplementEntry(raw: unknown): SupplementProgressEntry {
  if (!isRecord(raw)) {
    return {
      recognized: false,
      provider_file_id: null,
      display_name: null,
    }
  }
  const providerFileId = nonEmptyStringOrNull(raw.provider_file_id)
  const displayName = nonEmptyStringOrNull(raw.display_name)
  if (providerFileId === null || typeof raw.state !== 'string' || !SUPPLEMENT_STATE_SET.has(raw.state)) {
    return {
      recognized: false,
      provider_file_id: providerFileId,
      display_name: displayName,
    }
  }

  return {
    recognized: true,
    provider_file_id: providerFileId,
    display_name: displayName,
    state: raw.state as SupplementState,
    reason_code: nonEmptyStringOrNull(raw.reason_code),
    observed_at: nonEmptyStringOrNull(raw.observed_at),
    file_id: nonEmptyStringOrNull(raw.file_id),
    measured_value: nonNegativeIntegerOrNull(raw.measured_value),
    limit_value: nonNegativeIntegerOrNull(raw.limit_value),
  }
}

export interface ParseSupplementJobProgressOptions {
  currentStage?: string | null
}

/**
 * Defensively parse I1 supplement progress from `job.metadata`.
 *
 * Returns `null` when supplement metadata is absent, not an array, or empty (`[]`), so
 * screens never render an empty "0 files" section. Every element in a non-empty
 * `metadata.supplements` array is preserved — entries with an unknown `state` or missing
 * required fields are kept as `{ recognized: false, ... }` so a chosen file never
 * silently vanishes from the list.
 */
export function parseSupplementJobProgress(
  metadata: unknown,
  options?: ParseSupplementJobProgressOptions,
): SupplementJobProgress | null {
  if (!isRecord(metadata) || !Array.isArray(metadata.supplements) || metadata.supplements.length === 0) {
    return null
  }

  const supplements = metadata.supplements.map(parseSupplementEntry)
  const rawKind = metadata.job_kind
  const jobKind: SupplementJobKind | null =
    typeof rawKind === 'string' && SUPPLEMENT_JOB_KIND_SET.has(rawKind)
      ? (rawKind as SupplementJobKind)
      : null

  return {
    supplements,
    supplement_wait_ends_at: isValidIsoTimestampWithTz(metadata.supplement_wait_ends_at)
      ? metadata.supplement_wait_ends_at
      : null,
    job_kind: jobKind,
    current_stage: nonEmptyStringOrNull(options?.currentStage),
  }
}

/** Convenience wrapper that reads I1 progress and `current_stage` from any job-like object. */
export function getJobSupplementProgress(
  job: { metadata?: Record<string, unknown> | null; current_stage?: string | null } | null | undefined,
): SupplementJobProgress | null {
  return parseSupplementJobProgress(job?.metadata, { currentStage: job?.current_stage })
}

/** True when at least one recognized supplement entry is still waiting for provider conversion. */
export function hasWaitingSupplements(progress: SupplementJobProgress | null | undefined): boolean {
  return Boolean(progress?.supplements.some((entry) => entry.recognized && entry.state === 'waiting'))
}

/**
 * True when the job is past the main paper phase (supplement wait or supplement add stage),
 * so a `queued` supplement is waiting to be added rather than waiting for the main paper.
 */
export function isPostMainSupplementPhase(
  progress: Pick<SupplementJobProgress, 'job_kind' | 'current_stage' | 'supplements'>,
  currentStageOverride?: string | null,
): boolean {
  const stage = nonEmptyStringOrNull(currentStageOverride) ?? progress.current_stage
  if (stage === WAITING_FOR_SUPPLEMENTS_STAGE || stage === ADDING_SUPPLEMENTS_STAGE) {
    return true
  }
  if (progress.job_kind === 'supplement_wait' || progress.job_kind === 'supplement_add') {
    return true
  }
  return progress.supplements.some(
    (entry) =>
      entry.recognized
      && (entry.state === 'waiting' || entry.state === 'adding' || entry.state === 'added'),
  )
}
