/**
 * Approved curator-facing wording for paper file codes and supplement screens
 * (ALL-1460 / ALL-1745 / ALL-1738).
 *
 * The backend sends codes and numbers only. This is the single wording table shared by
 * Add Literature, the PDF Jobs panel, and the Library (details dialog and Files column).
 *
 * Provider labels are always parameterized (never hardcoded to a single deployment's
 * provider name in core code):
 * - Library listing texts accept `{provider}` (the provider's configured `displayLabel`).
 * - Job-progress, wait-outcome, and refusal helpers accept `providerShortLabel` (falling
 *   back to `providerLabel`, or the generic `DEFAULT_PROVIDER_LABEL` when no provider
 *   presentation is available).
 */
import type {
  CheckReasonCode,
  CheckStatus,
  ListingRefusalCode,
  NotTickableReasonCode,
  ObservationCode,
  PaperSourceListedFile,
} from '@/features/documents/paperSourceFiles'
import {
  isKnownSupplementOutcomeReasonCode,
  isValidIsoTimestampWithTz,
  type FileRemovalRefusalCode,
  type SupplementOutcomeReasonCode,
  type SupplementState,
} from '@/features/documents/supplementProgress'

type ProviderText = (provider: string) => string

/** Shared retry button text (Card 38). */
export const TRY_AGAIN_BUTTON_TEXT = 'Try again'

/**
 * Generic fallback provider phrase used only when a caller renders provider-worded job copy
 * without configured provider presentation metadata. Never hardcodes a deployment-specific name.
 */
export const DEFAULT_PROVIDER_LABEL = 'the literature source'

export interface SupplementProviderLabelOptions {
  /** Provider's short label for job/import copy (e.g. from `providerMetadata.shortLabel`). */
  providerShortLabel?: string | null
  /** Provider's full display label (e.g. from `providerMetadata.displayLabel`). */
  providerLabel?: string | null
}

function nonEmptyLabel(value: string | null | undefined): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1
}

/** Resolve the provider short label for job/outcome wording from configured presentation metadata. */
export function resolveProviderShortLabel(options?: SupplementProviderLabelOptions): string {
  return (
    nonEmptyLabel(options?.providerShortLabel)
    ?? nonEmptyLabel(options?.providerLabel)
    ?? DEFAULT_PROVIDER_LABEL
  )
}

/** Resolve the short provider label from a document's provenance/provider metadata, or null if unset. */
export function resolveSupplementProviderShortLabel(
  source?: {
    providerMetadata?: {
      shortLabel?: string | null
      displayLabel?: string | null
    } | null
  } | null,
): string | null {
  return (
    nonEmptyLabel(source?.providerMetadata?.shortLabel)
    ?? nonEmptyLabel(source?.providerMetadata?.displayLabel)
  )
}

function sentenceLead(provider: string): string {
  return provider === DEFAULT_PROVIDER_LABEL
    ? 'The literature source'
    : provider
}

/** Approved Card 34 line for papers not imported from the configured document-source provider. */
export function uploadedPaperFilesText(provider?: string | null): string {
  const label = nonEmptyLabel(provider) ?? DEFAULT_PROVIDER_LABEL
  return `Files can be added only to papers imported from ${label}.`
}

/**
 * Why a provider's file cannot be added to the paper (P6f's not-tickable codes; Card 8).
 * Card 36's `pdf_supplement_not_supported_yet` is retired from listings (ALL-1738 item 1)
 * and remains only in `SUPPLEMENT_OUTCOME_REASON_TEXT` (N17) for historical jobs.
 */
export const NOT_TICKABLE_REASON_TEXT: Record<NotTickableReasonCode, ProviderText> = {
  provider_not_converted_yet: (provider) => `Not converted by ${provider} yet`,
  large_data_file: () => 'Large data file. Files like this are not read.',
  type_not_supported: () => 'File type not supported',
  // Replaced by the check's own reason or status (CHECK_*_TEXT); never shown alone.
  check_not_passed: () => 'Its converted text did not pass the check',
  already_in_paper: () => 'Already in this paper',
  main_differs_from_paper: (provider) =>
    `${provider} now has a different main file for this paper than the one in the Library`,
  paper_not_importable: (provider) => `This paper cannot be imported from ${provider}`,
  paper_already_in_library: () => 'You already have this paper in the Library',
}

/** Why a converted text was refused (the checks R1, R2, R4 to R7; Card 8). */
export const CHECK_REASON_TEXT: Record<CheckReasonCode, ProviderText> = {
  too_large: () => 'This file is too large to read reliably',
  invalid_text: () => 'Its converted text contains characters that cannot be read',
  table_cells_mismatch: () => 'A table in its converted text has rows that do not match its header',
  table_row_too_wide: () => 'A table row is too long to keep whole',
  table_stray_separator: () => 'A table in its converted text is laid out in a way that cannot be read reliably',
  title_count: () => 'Its converted text does not have one clear title, so it cannot be read reliably',
}

/** When the check could not run (no reason code): what stopped it (Card 8). */
export const CHECK_STATUS_TEXT: Record<Exclude<CheckStatus, 'passed' | 'refused'>, ProviderText> = {
  text_unavailable: (provider) => `${provider} could not provide its converted text just now`,
  text_missing: (provider) => `Its converted text is missing in ${provider}`,
  text_access_denied: (provider) => `${provider} refused access to its converted text`,
  text_without_checksum: (provider) => `${provider} did not give the details needed to add its converted text safely`,
  // Reopening within PAPER_SOURCE_FILES_STALE_MS checks nothing; only a refresh does.
  check_limit_reached: () => 'Not checked yet. Refresh this paper\'s details to check it.',
  not_checked: () => 'Not checked yet',
}

/** What the provider last told an import about a chosen file, with the date (Card 8). */
export const OBSERVATION_TEXT: Record<ObservationCode, (provider: string, date: string) => string> = {
  provider_not_converted: (provider, date) => `${provider} had not converted this file when it was last asked (${date})`,
  provider_conversion_failed: (provider, date) => `${provider} tried and could not convert this file (${date})`,
  not_converted_in_time: (provider, date) => `${provider} had not converted this file when the import stopped waiting (${date})`,
}

/** Why the provider's list of this paper's files cannot be shown (Card 9 + Card 34). */
export const LISTING_REFUSAL_TEXT: Record<ListingRefusalCode, ProviderText | null> = {
  not_imported_from_provider: (provider) => uploadedPaperFilesText(provider),
  // No advice to import again: the import would find this paper by its main file and
  // answer that it is already in the Library.
  paper_reference_mismatch: (provider) => `This paper's entry in ${provider} has been merged into another entry, so its files cannot be listed here.`,
  paper_reference_incomplete: (provider) => `This paper's ${provider} entry is not recorded completely, so its files cannot be listed.`,
  // A 409: the request carried no sign-in for the provider. The 503 of this code (the
  // server's own reader is down) is READER_UNAVAILABLE_TEXT; see listingRefusalText.
  document_source_curator_token_unavailable: (provider) => `Sign in again to see this paper's files in ${provider}.`,
  // The provider import is switched off on this server: nothing to show.
  document_source_import_disabled: null,
  reference_not_found: (provider) => `${provider} no longer has an entry for this paper, so its files cannot be listed.`,
}

/** The server cannot reach the provider with its own reader (a 503 with the sign-in code; Card 9). */
export const READER_UNAVAILABLE_TEXT: ProviderText = (provider) =>
  `This server cannot reach ${provider} just now, so this paper's files there cannot be listed.`

/** The words for a refused listing; null when nothing is to be said. */
export function listingRefusalText(code: ListingRefusalCode, httpStatus: number, provider: string): string | null {
  if (code === 'document_source_curator_token_unavailable' && httpStatus === 503) {
    return READER_UNAVAILABLE_TEXT(provider)
  }
  const text = LISTING_REFUSAL_TEXT[code]
  return text ? text(provider) : null
}

/** A converted file that passed with little text (the R3 note counts characters only; Card 8). */
export function fewCharactersNote(count: number): string {
  return `Little text: about ${count.toLocaleString('en-US')} characters`
}

/** Files of the paper in the provider that the curator cannot open: counted, never named (Card 8). */
export function notShownText(count: number, provider: string): string {
  return count === 1
    ? `1 more file in ${provider} is not listed because you cannot open it.`
    : `${count.toLocaleString('en-US')} more files in ${provider} are not listed because you cannot open them.`
}

/** Further copies of a listed file (same content) that are not listed again (Card 8). */
export function duplicateCopiesText(count: number): string {
  return count === 1
    ? '1 further copy of a listed file is not listed again.'
    : `${count.toLocaleString('en-US')} further copies of listed files are not listed again.`
}

/** A converted supplement that could be added (Card 8). */
export const ADDABLE_TEXT: ProviderText = (provider) => `Converted by ${provider}; not added to this paper`

/** A file with a code this screen does not know yet (Card 8 / N29). */
export const UNRECOGNIZED_FILE_TEXT = 'This screen cannot describe this file yet.'

/** The listing's answer could not be read (not the provider's fault; Card 9). */
export const LISTING_UNREADABLE_TEXT: ProviderText = (provider) => `This paper's files in ${provider} could not be shown.`

/** The line that says why a provider's file is not in the paper, or that it could be. */
export function sourceFileStatusText(file: PaperSourceListedFile, provider: string): string {
  if (!file.recognized) {
    return UNRECOGNIZED_FILE_TEXT
  }
  if (file.tickable) {
    // Only a supplement with converted text is ever tickable (checked when parsed).
    return ADDABLE_TEXT(provider)
  }
  if (file.not_tickable_reason === 'check_not_passed') {
    if (file.check.reason !== null) {
      return CHECK_REASON_TEXT[file.check.reason](provider)
    }
    if (file.check.status !== 'passed' && file.check.status !== 'refused') {
      return CHECK_STATUS_TEXT[file.check.status](provider)
    }
  }
  return NOT_TICKABLE_REASON_TEXT[file.not_tickable_reason](provider)
}

// ---------------------------------------------------------------------------
// Jobs panel and per-file supplement outcomes (N7–N29, Card 20, Card 39, Card 40)
// ---------------------------------------------------------------------------

/** Approved post-main label for a converted supplement still waiting in `queued` state. */
export const QUEUED_AFTER_MAIN_READY_TEXT = 'Waiting to be added'

export interface SupplementStateTextOptions extends SupplementProviderLabelOptions {
  /** True once the main paper is ready (post-main `queued` entries show "Waiting to be added"). */
  mainReady?: boolean
}

/** Per-file supplement state label formatters (N7, post-main queued, Card 39, N8, N9, left_out). */
export const SUPPLEMENT_STATE_TEXT: Record<
  SupplementState,
  (options?: SupplementStateTextOptions) => string
> = {
  queued: (options) =>
    options?.mainReady ? QUEUED_AFTER_MAIN_READY_TEXT : 'Waiting for the main paper',
  waiting: (options) => `Waiting for ${resolveProviderShortLabel(options)}`,
  adding: () => 'Being added',
  added: () => 'Added',
  left_out: () => 'Not added',
}

/** Word a recognized supplement progress state. */
export function supplementStateText(
  state: SupplementState,
  options?: SupplementStateTextOptions,
): string {
  return SUPPLEMENT_STATE_TEXT[state](options)
}

/**
 * Format a strict timezone-bearing ISO-8601 wait-end timestamp for display in N10,
 * or return `null` if invalid.
 */
export function formatSupplementWaitEndTime(isoTimestamp: string): string | null {
  if (!isValidIsoTimestampWithTz(isoTimestamp)) {
    return null
  }
  const parsed = new Date(isoTimestamp)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }
  return parsed.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

/** Wait-end deadline line shown while at least one supplement is waiting (N10). */
export function supplementWaitEndsText(
  time: string,
  provider?: string | SupplementProviderLabelOptions | null,
): string {
  const shortLabel =
    typeof provider === 'string'
      ? (nonEmptyLabel(provider) ?? DEFAULT_PROVIDER_LABEL)
      : resolveProviderShortLabel(provider ?? undefined)
  return `${sentenceLead(shortLabel)} has until ${time} to convert them.`
}

/**
 * Format a byte limit exactly: use a decimal SI unit (`GB`, `MB`, `KB`) ONLY when it
 * represents the byte threshold with zero rounding error (e.g. `20_000_000` -> `20 MB`,
 * `2_000_000` -> `2 MB`, `1_500_000` -> `1.5 MB`), and otherwise show the exact byte
 * count (e.g. `1_060_000` -> `1,060,000 bytes`) so a refusal never overstates the limit.
 */
export function formatByteLimit(bytes: number): string {
  if (bytes >= 1_000_000_000) {
    if (bytes % 100_000_000 === 0) {
      return `${Number((bytes / 1_000_000_000).toFixed(1))} GB`
    }
    return `${bytes.toLocaleString('en-US')} bytes`
  }
  if (bytes >= 1_000_000) {
    if (bytes % 100_000 === 0) {
      return `${Number((bytes / 1_000_000).toFixed(1))} MB`
    }
    return `${bytes.toLocaleString('en-US')} bytes`
  }
  if (bytes >= 1_000) {
    if (bytes % 100 === 0) {
      return `${Number((bytes / 1_000).toFixed(1))} KB`
    }
    return `${bytes.toLocaleString('en-US')} bytes`
  }
  return bytes === 1 ? '1 byte' : `${bytes.toLocaleString('en-US')} bytes`
}

export interface SupplementOutcomeReasonOptions extends SupplementProviderLabelOptions {
  limitValue?: number | null
}

type SupplementOutcomeReasonFormatter = (options?: SupplementOutcomeReasonOptions) => string

/**
 * Approved per-file outcome wording for `metadata.supplements[].reason_code`
 * (N11–N28, Card 8 checks, Card 20 PDF codes, Card 40 conversion wait codes).
 * Unknown codes and missing required limits return N29 via `supplementOutcomeReasonText`.
 */
export const SUPPLEMENT_OUTCOME_REASON_TEXT: Record<
  SupplementOutcomeReasonCode,
  SupplementOutcomeReasonFormatter
> = {
  // Card 40: conversion wait outcomes (with Library Add second sentence restored in Part 4).
  provider_not_converted: (options) => {
    const provider = resolveProviderShortLabel(options)
    return `${sentenceLead(provider)} has not converted this file. You can add it later from the Library once ${provider} shows it as converted.`
  },
  provider_conversion_failed: (options) =>
    `${sentenceLead(resolveProviderShortLabel(options))} tried and could not convert this file.`,
  not_converted_in_time: (options) => {
    const provider = resolveProviderShortLabel(options)
    return `${sentenceLead(provider)} had not converted this file when the import stopped waiting. You can add it later from the Library once ${provider} shows it as converted.`
  },

  // N11–N16: supplement stage endings.
  login_expired: () => 'Not added: your sign-in expired before it could be added.',
  wait_limit_reached: () => 'Not added: the import\'s time for supplements ran out before this file could be added.',
  import_cancelled: () => 'Not added: the import was cancelled.',
  supplement_stage_failed: () => 'Not added: adding this paper\'s supplements failed.',
  provider_answer_unknown: (options) =>
    `Not added: ${resolveProviderShortLabel(options)}'s answer about this file could not be read.`,
  paper_busy_when_ready: () => 'Not added: the paper was being changed when its main paper became ready.',

  // Card 8: converted-text check refusals when emitted during an add.
  too_large: () => CHECK_REASON_TEXT.too_large(''),
  invalid_text: () => CHECK_REASON_TEXT.invalid_text(''),
  table_cells_mismatch: () => CHECK_REASON_TEXT.table_cells_mismatch(''),
  table_row_too_wide: () => CHECK_REASON_TEXT.table_row_too_wide(''),
  table_stray_separator: () => CHECK_REASON_TEXT.table_stray_separator(''),
  title_count: () => CHECK_REASON_TEXT.title_count(''),

  // N17–N28: per-file add refusals.
  pdf_supplement_not_supported_yet: () =>
    'Not added: PDF supplements could not be added when this import ran.',
  file_not_in_reference: (options) =>
    `Not added: ${resolveProviderShortLabel(options)} no longer lists this file for the paper.`,
  file_not_tickable: (options) =>
    `Not added: ${resolveProviderShortLabel(options)} has no converted text for this file.`,
  file_changed_since_listed: (options) =>
    `Not added: the file changed in ${resolveProviderShortLabel(options)} after it was listed. Try again.`,
  source_file_missing: (options) =>
    `Not added: its converted text is missing in ${resolveProviderShortLabel(options)}.`,
  source_unavailable: (options) =>
    `Not added: ${resolveProviderShortLabel(options)} could not provide its converted text just now. Try again.`,
  source_download_failed: () => 'Not added: its converted text could not be downloaded. Try again.',
  access_denied: (options) =>
    `Not added: ${resolveProviderShortLabel(options)} refused access to its converted text.`,
  markdown_invalid: () => 'Not added: its converted text could not be read.',
  paper_text_limit: (options) =>
    isPositiveInteger(options?.limitValue)
      ? `Not added: the paper would hold more text than AI Curation reads (${formatByteLimit(options.limitValue)}).`
      : UNRECOGNIZED_FILE_TEXT,
  paper_file_limit: (options) =>
    isPositiveInteger(options?.limitValue)
      ? `Not added: the paper already has the most files allowed (${options.limitValue.toLocaleString('en-US')}).`
      : UNRECOGNIZED_FILE_TEXT,
  already_in_paper: () => 'Already in this paper',

  // Card 20: PDF supplement outcomes.
  pdf_too_large: (options) =>
    isPositiveInteger(options?.limitValue)
      ? `This PDF is larger than ${formatByteLimit(options.limitValue)}, so it was not added.`
      : UNRECOGNIZED_FILE_TEXT,
  pdf_invalid: () => 'This PDF could not be opened, so it was not added.',
  pdf_access_denied: (options) =>
    `You can read this file's text in ${resolveProviderShortLabel(options)} but not its PDF, so it was not added.`,
}

/** Word a supplement outcome `reason_code`; unknown or missing codes return N29. */
export function supplementOutcomeReasonText(
  code: string | null | undefined,
  options?: SupplementOutcomeReasonOptions,
): string {
  if (!isKnownSupplementOutcomeReasonCode(code)) {
    return UNRECOGNIZED_FILE_TEXT
  }
  return SUPPLEMENT_OUTCOME_REASON_TEXT[code](options)
}

// ---------------------------------------------------------------------------
// Add Literature screen (N1–N6)
// ---------------------------------------------------------------------------

export const ADD_LITERATURE_TEXT = {
  /** N1 */
  chooseSupplements: 'Choose supplements',
  /** N2 */
  mainPaperAlwaysImported: 'Main paper (always imported)',
  /** N5 */
  openInLibraryToAddFiles: 'Open it in the Library to add its files.',
  /** N6 */
  listingFailedImportMainOnly:
    'Could not list this paper\'s files. You can import the main paper now and add supplements later from the Library.',
} as const

/** N3: description for an unconverted supplement that can be ticked before import. */
export function unconvertedTickableSupplementText(provider: string): string {
  return `Not converted by ${provider} yet. If you choose it, the import asks ${provider} to convert it; the main paper does not wait for it.`
}

/** N4: Import button label when one or more supplements are selected. */
export function importWithSupplementsButtonText(count: number): string {
  return count === 1
    ? 'Import with 1 supplement'
    : `Import with ${count.toLocaleString('en-US')} supplements`
}

// ---------------------------------------------------------------------------
// Library Add screen (N33–N37, Card 34, Card 41)
// ---------------------------------------------------------------------------

export const LIBRARY_ADD_TEXT = {
  /** N34 & N35 */
  importStillRunning:
    'This paper\'s import is still running. You can add or remove files when it has finished.',
  /** N36 */
  fileRemovalUnfinished: 'A file of this paper is still being removed. Try again in a moment.',
  /** N37 */
  addingFilesHeading: 'Adding files',
} as const

/** Card 41 (`file_not_tickable` request refusal). */
export function fileNoLongerChoosableText(provider?: string | null): string {
  const label = nonEmptyLabel(provider) ?? DEFAULT_PROVIDER_LABEL
  return `This file can no longer be chosen: ${label} has not converted it and this import will not ask ${label} to.`
}

/** N33: Library Add button label. */
export function addFilesButtonText(count: number): string {
  return count === 1
    ? 'Add 1 file'
    : `Add ${count.toLocaleString('en-US')} files`
}

/** Card 41: request-level `paper_file_limit` refusal text. Returns N29 if limit is invalid. */
export function paperFileLimitRefusalText(limit: number): string {
  if (!isPositiveInteger(limit)) {
    return UNRECOGNIZED_FILE_TEXT
  }
  return `Too many files for one paper: at most ${limit.toLocaleString('en-US')}, including the main paper.`
}

export interface AddFilesRefusalOptions {
  /** Provider's configured display label (required for `document_source_curator_token_unavailable`). */
  provider?: string | null
  /** Optional provider short label for `file_not_tickable` (falls back to `provider`). */
  providerShortLabel?: string | null
  /** HTTP status code (409 vs 503 disambiguates `document_source_curator_token_unavailable`). */
  httpStatus?: number
  /** Numeric file limit carried by `paper_file_limit` refusals. */
  limitValue?: number | null
}

/** Map an import or Library-add request refusal code to approved curator text. */
export function addFilesRefusalText(
  code: string | null | undefined,
  options?: AddFilesRefusalOptions,
): string | null {
  switch (code) {
    case 'import_in_progress':
      return LIBRARY_ADD_TEXT.importStillRunning
    case 'file_removal_unfinished':
      return LIBRARY_ADD_TEXT.fileRemovalUnfinished
    case 'file_not_tickable':
      return fileNoLongerChoosableText(
        nonEmptyLabel(options?.providerShortLabel) ?? nonEmptyLabel(options?.provider),
      )
    case 'paper_file_limit':
      return isPositiveInteger(options?.limitValue)
        ? paperFileLimitRefusalText(options.limitValue)
        : UNRECOGNIZED_FILE_TEXT
    case 'document_source_curator_token_unavailable': {
      const provider = nonEmptyLabel(options?.provider)
      if (!provider) {
        return UNRECOGNIZED_FILE_TEXT
      }
      if (options?.httpStatus === 409) {
        return listingRefusalText('document_source_curator_token_unavailable', 409, provider)
      }
      if (options?.httpStatus === 503) {
        return listingRefusalText('document_source_curator_token_unavailable', 503, provider)
      }
      return UNRECOGNIZED_FILE_TEXT
    }
    case 'document_source_import_disabled':
      return null
    default:
      return UNRECOGNIZED_FILE_TEXT
  }
}

// ---------------------------------------------------------------------------
// Library Remove screen (N38–N44, Card 2 W-D1, Card 3 W-D2, Card 19)
// ---------------------------------------------------------------------------

/**
 * Approved strings for removing a supplement from a paper.
 * Card 3 (W-D2): when `cleanup_finished` is `false`, nothing extra is shown in the UI
 * (the backend reports cleanup failures to Sentry); N45 is intentionally omitted.
 */
export const LIBRARY_REMOVE_TEXT = {
  /** N38 */
  buttonLabel: 'Remove',
  /** N39 */
  confirmBody:
    'AI Curation will no longer read this file or show it in the viewer. Evidence already saved stays as it is.',
  /** N39 */
  confirmButton: 'Remove file',
  /** N39 */
  cancelButton: 'Cancel',
  /** N40 */
  newestResultQuotesFileWarning:
    'This paper\'s newest result quotes this file, so Review & Curate will ask you to run the extraction again.',
  /** Card 19 (409 busy) */
  operationInProgress: 'Another operation on this paper is in progress; try again in a moment.',
  /** Card 19 (503 busy) */
  serverBusy: 'The server is busy right now; try removing the file again in a moment.',
  /** Card 19 (Review & Curate after removal) */
  reviewAndCurateAfterRemoval:
    'This paper\'s newest result quotes a file that was removed from the paper. Run the extraction again to review it.',
} as const

/** N38: screen-reader label for the Remove button on a file row. */
export function removeFileAriaLabel(fileName: string): string {
  return `Remove ${fileName}`
}

/** N39: confirmation dialog title when removing a file. */
export function removeFileDialogTitle(fileName: string): string {
  return `Remove ${fileName}?`
}

/**
 * Card 2 (W-D1): refusal when one or more review sessions quote the file (no `{names}`).
 * Returns N29 if `count` is not a positive integer so a missing count never fabricates "1".
 */
export function quotedInReviewSessionsText(count: number): string {
  if (!isPositiveInteger(count)) {
    return UNRECOGNIZED_FILE_TEXT
  }
  return count === 1
    ? 'This file is quoted in 1 review session. To remove it, delete the paper.'
    : `This file is quoted in ${count.toLocaleString('en-US')} review sessions. To remove it, delete the paper.`
}

/** Approved wording for every structured refusal code from `file_removal.py`. */
export const FILE_REMOVAL_REFUSAL_TEXT: Record<
  FileRemovalRefusalCode,
  (options?: { sessionCount?: number }) => string
> = {
  import_in_progress: () => LIBRARY_ADD_TEXT.importStillRunning,
  main_file_not_removable: () => 'The main paper cannot be removed. To remove it, delete the paper.',
  last_file_not_removable: () => 'A paper\'s only file cannot be removed. To remove it, delete the paper.',
  paper_not_completed: () => 'Files can be removed once the paper has finished processing.',
  file_not_in_paper: () => 'This file is no longer in the paper.',
  file_quoted_in_review: (options) =>
    isPositiveInteger(options?.sessionCount)
      ? quotedInReviewSessionsText(options.sessionCount)
      : UNRECOGNIZED_FILE_TEXT,
}

/** Map a removal refusal code to approved curator text; unknown codes return N29. */
export function fileRemovalRefusalText(
  code: string | null | undefined,
  options?: { sessionCount?: number },
): string {
  if (typeof code === 'string' && Object.prototype.hasOwnProperty.call(FILE_REMOVAL_REFUSAL_TEXT, code)) {
    return FILE_REMOVAL_REFUSAL_TEXT[code as FileRemovalRefusalCode](options)
  }
  return UNRECOGNIZED_FILE_TEXT
}

// ---------------------------------------------------------------------------
// Library Files column (N46–N47)
// ---------------------------------------------------------------------------

export const LIBRARY_FILES_COLUMN_TEXT = {
  /** N46 */
  header: 'Files',
} as const

/** N47: screen-reader text for the Library Files column cell. */
export function filesCountAriaLabel(count: number): string {
  return count === 1 ? '1 file' : `${count.toLocaleString('en-US')} files`
}
