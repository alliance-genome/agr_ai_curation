/**
 * The provider's files of an imported paper (ALL-1460, P6f's per-paper listing).
 *
 * `GET /api/weaviate/documents/{id}/source-files` lists every file the provider holds
 * for the paper, with whether each is already in the paper and, if not, whether it
 * can be added and why not (codes only). A refusal of the whole listing comes back as
 * `{detail: {error: <code>}}`. Anything outside the known codes is a contract error.
 */

export const NOT_TICKABLE_REASON_CODES = [
  'provider_not_converted_yet',
  'large_data_file',
  'type_not_supported',
  'check_not_passed',
  'already_in_paper',
  'paper_not_importable',
  'paper_already_in_library',
] as const
export type NotTickableReasonCode = (typeof NOT_TICKABLE_REASON_CODES)[number]

export const CHECK_REASON_CODES = [
  'too_large',
  'invalid_text',
  'table_cells_mismatch',
  'table_row_too_wide',
  'table_stray_separator',
  'title_count',
] as const
export type CheckReasonCode = (typeof CHECK_REASON_CODES)[number]

export const CHECK_STATUSES = [
  'passed',
  'refused',
  'text_unavailable',
  'text_missing',
  'text_access_denied',
  'text_without_checksum',
  'check_limit_reached',
  'not_checked',
] as const
export type CheckStatus = (typeof CHECK_STATUSES)[number]

export const OBSERVATION_CODES = [
  'provider_not_converted',
  'provider_conversion_failed',
  'not_converted_in_time',
] as const
export type ObservationCode = (typeof OBSERVATION_CODES)[number]

export const LISTING_REFUSAL_CODES = [
  'not_imported_from_provider',
  'paper_reference_mismatch',
  'paper_reference_incomplete',
  'document_source_curator_token_unavailable',
  'document_source_import_disabled',
  'reference_not_found',
] as const
export type ListingRefusalCode = (typeof LISTING_REFUSAL_CODES)[number]

const FILE_ROLES = ['main', 'supplement'] as const
const CATEGORIES = ['convertible', 'large_data_file', 'unsupported_type'] as const
const FILE_STATUSES = ['ready', 'no_converted_text', 'large_data_file', 'type_not_supported'] as const

export interface PaperSourceFileCheck {
  status: CheckStatus
  reason: CheckReasonCode | null
  measured_value: number | null
  limit_value: number | null
  few_characters_count: number | null
}

export interface PaperSourceFile {
  provider_file_id: string
  display_name: string | null
  file_role: (typeof FILE_ROLES)[number] | null
  category: (typeof CATEGORIES)[number] | null
  status: (typeof FILE_STATUSES)[number]
  in_this_paper: boolean
  tickable: boolean
  not_tickable_reason: NotTickableReasonCode | null
  last_provider_observation: { code: ObservationCode; observed_at: string } | null
  check: PaperSourceFileCheck
}

export interface PaperSourceListing {
  files: PaperSourceFile[]
  not_shown_count: number
  duplicate_copies_count: number
}

export type PaperSourceListingResult =
  | { kind: 'listing'; listing: PaperSourceListing }
  | { kind: 'refused'; code: ListingRefusalCode }

export class PaperSourceFilesContractError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PaperSourceFilesContractError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  const match = allowed.find((candidate) => candidate === value)
  if (match === undefined) {
    throw new PaperSourceFilesContractError(`Listing field "${field}" has an unknown value`)
  }
  return match
}

function oneOfOrNull<T extends string>(value: unknown, allowed: readonly T[], field: string): T | null {
  return value === null ? null : oneOf(value, allowed, field)
}

function countOrNull(value: unknown, field: string): number | null {
  if (value === null) {
    return null
  }
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new PaperSourceFilesContractError(`Listing field "${field}" must be a whole number or null`)
  }
  return value
}

function count(value: unknown, field: string): number {
  const parsed = countOrNull(value, field)
  if (parsed === null) {
    throw new PaperSourceFilesContractError(`Listing field "${field}" must be a whole number`)
  }
  return parsed
}

function parseCheck(value: unknown): PaperSourceFileCheck {
  if (!isRecord(value)) {
    throw new PaperSourceFilesContractError('A listed file must have its check')
  }
  return {
    status: oneOf(value.status, CHECK_STATUSES, 'check.status'),
    reason: oneOfOrNull(value.reason, CHECK_REASON_CODES, 'check.reason'),
    measured_value: countOrNull(value.measured_value, 'check.measured_value'),
    limit_value: countOrNull(value.limit_value, 'check.limit_value'),
    few_characters_count: countOrNull(value.few_characters_count, 'check.few_characters_count'),
  }
}

function parseFile(value: unknown): PaperSourceFile {
  if (!isRecord(value)) {
    throw new PaperSourceFilesContractError('A listed file must be an object')
  }
  if (typeof value.provider_file_id !== 'string' || value.provider_file_id.length === 0) {
    throw new PaperSourceFilesContractError('A listed file must name its provider file')
  }
  if (value.display_name !== null && typeof value.display_name !== 'string') {
    throw new PaperSourceFilesContractError('Listing field "display_name" must be a string or null')
  }
  if (typeof value.in_this_paper !== 'boolean' || typeof value.tickable !== 'boolean') {
    throw new PaperSourceFilesContractError('A listed file must say whether it is in the paper and can be chosen')
  }
  const observation = value.last_provider_observation
  if (observation !== null && (!isRecord(observation) || typeof observation.observed_at !== 'string')) {
    throw new PaperSourceFilesContractError('Listing field "last_provider_observation" is malformed')
  }
  return {
    provider_file_id: value.provider_file_id,
    display_name: value.display_name,
    file_role: oneOfOrNull(value.file_role, FILE_ROLES, 'file_role'),
    category: oneOfOrNull(value.category, CATEGORIES, 'category'),
    status: oneOf(value.status, FILE_STATUSES, 'status'),
    in_this_paper: value.in_this_paper,
    tickable: value.tickable,
    not_tickable_reason: oneOfOrNull(value.not_tickable_reason, NOT_TICKABLE_REASON_CODES, 'not_tickable_reason'),
    last_provider_observation: observation === null
      ? null
      : {
          code: oneOf(observation.code, OBSERVATION_CODES, 'last_provider_observation.code'),
          observed_at: observation.observed_at as string,
        },
    check: parseCheck(value.check),
  }
}

export function parsePaperSourceListing(value: unknown): PaperSourceListing {
  if (!isRecord(value) || !Array.isArray(value.files)) {
    throw new PaperSourceFilesContractError('The listing must hold a list of files')
  }
  return {
    files: value.files.map(parseFile),
    not_shown_count: count(value.not_shown_count, 'not_shown_count'),
    duplicate_copies_count: count(value.duplicate_copies_count, 'duplicate_copies_count'),
  }
}

export async function fetchPaperSourceFiles(
  documentId: string,
  signal?: AbortSignal,
): Promise<PaperSourceListingResult> {
  const response = await fetch(`/api/weaviate/documents/${encodeURIComponent(documentId)}/source-files`, {
    credentials: 'include',
    signal,
  })
  const payload = await response.json() as unknown
  if (response.ok) {
    return { kind: 'listing', listing: parsePaperSourceListing(payload) }
  }
  const detail = isRecord(payload) ? payload.detail : undefined
  const code = isRecord(detail) ? detail.error : undefined
  const refusal = LISTING_REFUSAL_CODES.find((candidate) => candidate === code)
  if (refusal === undefined) {
    throw new Error(`Failed to list the paper's files in the provider (${response.status})`)
  }
  return { kind: 'refused', code: refusal }
}
