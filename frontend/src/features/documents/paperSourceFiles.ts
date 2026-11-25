/**
 * The provider's files of an imported paper (ALL-1460, P6f's per-paper listing).
 *
 * `GET /api/weaviate/documents/{id}/source-files` lists every file the provider holds
 * for the paper, with whether each is already in the paper and, if not, whether it
 * can be added and why not (codes only). A refusal of the whole listing comes back as
 * `{detail: {error: <code>}}`.
 *
 * A file whose reason, check or observation code this screen does not know yet (the
 * backend can gain a code before its wording lands) is kept as an unrecognised row, so
 * the rest of the list still shows; the code itself is never shown. Any other
 * departure from the shape is a contract error for the whole listing.
 */

export const NOT_TICKABLE_REASON_CODES = [
  'provider_not_converted_yet',
  'large_data_file',
  'type_not_supported',
  'check_not_passed',
  'already_in_paper',
  // P6f (agreed with b-p6f 23 Nov): the provider's main file is not the paper's stored
  // main. The per-paper listing never offers a main for adding.
  'main_differs_from_paper',
  // Card 36's `pdf_supplement_not_supported_yet` was retired from listings after P7c2
  // (ALL-1738 item 1); it remains only in historical job-entry outcomes (`supplementProgress.ts`).
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

/** A file is either tickable with no reason, or not tickable with one (checked when parsed). */
export type PaperSourceFileTickability =
  | { tickable: true; not_tickable_reason: null }
  | { tickable: false; not_tickable_reason: NotTickableReasonCode }

export type PaperSourceFile = PaperSourceFileFields & PaperSourceFileTickability

interface PaperSourceFileFields {
  recognized: true
  provider_file_id: string
  display_name: string | null
  file_role: (typeof FILE_ROLES)[number] | null
  /** The converted text's MD5; null when the provider holds no converted text. */
  converted_md5: string | null
  category: (typeof CATEGORIES)[number] | null
  status: (typeof FILE_STATUSES)[number]
  in_this_paper: boolean
  last_provider_observation: { code: ObservationCode; observed_at: string } | null
  check: PaperSourceFileCheck
}

/** A file with a code this screen does not know: named, never described by its code. */
export interface UnrecognizedPaperSourceFile {
  recognized: false
  provider_file_id: string
  display_name: string | null
  in_this_paper: boolean
}

export type PaperSourceListedFile = PaperSourceFile | UnrecognizedPaperSourceFile

export interface PaperSourceListing {
  files: PaperSourceListedFile[]
  not_shown_count: number
  duplicate_copies_count: number
}

export type PaperSourceListingResult =
  | { kind: 'listing'; listing: PaperSourceListing }
  /** The HTTP status tells causes of one code apart (a missing sign-in is 409, a reader that is down 503). */
  | { kind: 'refused'; code: ListingRefusalCode; httpStatus: number }

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

/** A code field: a string this screen may not know yet. Anything but a string (or null) is malformed. */
class UnknownCode extends Error {}

function codeOrNull<T extends string>(value: unknown, allowed: readonly T[], field: string): T | null {
  if (value === null) {
    return null
  }
  if (typeof value !== 'string') {
    throw new PaperSourceFilesContractError(`Listing field "${field}" must be a code or null`)
  }
  const match = allowed.find((candidate) => candidate === value)
  if (match === undefined) {
    throw new UnknownCode(field)
  }
  return match
}

function code<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  const parsed = codeOrNull(value, allowed, field)
  if (parsed === null) {
    throw new PaperSourceFilesContractError(`Listing field "${field}" must be a code`)
  }
  return parsed
}

function stringOrNull(value: unknown, field: string): string | null {
  if (value !== null && typeof value !== 'string') {
    throw new PaperSourceFilesContractError(`Listing field "${field}" must be a string or null`)
  }
  return value
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
    status: code(value.status, CHECK_STATUSES, 'check.status'),
    reason: codeOrNull(value.reason, CHECK_REASON_CODES, 'check.reason'),
    measured_value: countOrNull(value.measured_value, 'check.measured_value'),
    limit_value: countOrNull(value.limit_value, 'check.limit_value'),
    few_characters_count: countOrNull(value.few_characters_count, 'check.few_characters_count'),
  }
}

function parseFile(value: unknown): PaperSourceListedFile {
  if (!isRecord(value)) {
    throw new PaperSourceFilesContractError('A listed file must be an object')
  }
  if (typeof value.provider_file_id !== 'string' || value.provider_file_id.length === 0) {
    throw new PaperSourceFilesContractError('A listed file must name its provider file')
  }
  const displayName = stringOrNull(value.display_name, 'display_name')
  if (typeof value.in_this_paper !== 'boolean' || typeof value.tickable !== 'boolean') {
    throw new PaperSourceFilesContractError('A listed file must say whether it is in the paper and can be chosen')
  }
  const observation = value.last_provider_observation
  if (observation !== null && (!isRecord(observation) || typeof observation.observed_at !== 'string')) {
    throw new PaperSourceFilesContractError('Listing field "last_provider_observation" is malformed')
  }
  const fileRole = oneOfOrNull(value.file_role, FILE_ROLES, 'file_role')
  const convertedMd5 = stringOrNull(value.converted_md5, 'converted_md5')
  if (value.tickable && fileRole === 'main') {
    throw new PaperSourceFilesContractError('The per-paper listing never offers the main file for adding')
  }
  if (value.tickable && convertedMd5 === null) {
    throw new PaperSourceFilesContractError('A supplement that can be added must have converted text')
  }
  const tickable = value.tickable
  const known = {
    provider_file_id: value.provider_file_id,
    display_name: displayName,
    file_role: fileRole,
    converted_md5: convertedMd5,
    category: oneOfOrNull(value.category, CATEGORIES, 'category'),
    status: oneOf(value.status, FILE_STATUSES, 'status'),
    in_this_paper: value.in_this_paper,
  }
  try {
    const reason = codeOrNull(value.not_tickable_reason, NOT_TICKABLE_REASON_CODES, 'not_tickable_reason')
    let tickability: PaperSourceFileTickability
    if (tickable && reason === null) {
      tickability = { tickable: true, not_tickable_reason: null }
    } else if (!tickable && reason !== null) {
      tickability = { tickable: false, not_tickable_reason: reason }
    } else {
      throw new PaperSourceFilesContractError('A listed file must be either tickable or give a reason, not both or neither')
    }
    return {
      recognized: true,
      ...known,
      ...tickability,
      last_provider_observation: observation === null
        ? null
        : {
            code: code(observation.code, OBSERVATION_CODES, 'last_provider_observation.code'),
            observed_at: observation.observed_at as string,
          },
      check: parseCheck(value.check),
    }
  } catch (error) {
    if (!(error instanceof UnknownCode)) {
      throw error
    }
    return { recognized: false, provider_file_id: known.provider_file_id, display_name: displayName, in_this_paper: known.in_this_paper }
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
  return { kind: 'refused', code: refusal, httpStatus: response.status }
}
