/**
 * The words for every file code a curator can see in the Library (ALL-1460, P10b-ui).
 *
 * The backend sends codes only. This is the one place their wording lives, so every
 * screen that shows a paper's files says the same thing. All texts are proposals
 * awaiting Chris's sign-off. `{provider}` is the provider's own display label
 * (configuration), never a name written into core code.
 */
import type {
  CheckReasonCode,
  CheckStatus,
  ListingRefusalCode,
  NotTickableReasonCode,
  ObservationCode,
  PaperSourceFile,
} from '@/features/documents/paperSourceFiles'

type ProviderText = (provider: string) => string

/** Why a provider's file cannot be added to the paper (P6f's not-tickable codes). */
export const NOT_TICKABLE_REASON_TEXT: Record<NotTickableReasonCode, ProviderText> = {
  provider_not_converted_yet: (provider) => `Not converted by ${provider} yet`,
  large_data_file: () => 'Large data file; these are not read',
  type_not_supported: () => 'File type not supported',
  // Replaced by the check's own reason or status (CHECK_*_TEXT); never shown alone.
  check_not_passed: () => 'Its converted text did not pass the check',
  already_in_paper: () => 'Already in this paper',
  paper_not_importable: (provider) => `This paper cannot be imported from ${provider}`,
  paper_already_in_library: () => 'You already have this paper in the Library',
}

/** Why a converted text was refused (the checks R1, R2, R4 to R7). */
export const CHECK_REASON_TEXT: Record<CheckReasonCode, ProviderText> = {
  too_large: () => 'This file is too large to read reliably',
  invalid_text: () => 'Its converted text contains characters that cannot be read',
  table_cells_mismatch: () => 'A table in its converted text has rows that do not match its header',
  table_row_too_wide: () => 'A table row is too long to keep whole',
  table_stray_separator: () => 'A table in its converted text has a misplaced separator line',
  title_count: () => 'Its converted text does not have exactly one title',
}

/** When the check could not run (no reason code): what stopped it. */
export const CHECK_STATUS_TEXT: Record<Exclude<CheckStatus, 'passed' | 'refused'>, ProviderText> = {
  text_unavailable: (provider) => `${provider} could not provide its converted text just now`,
  text_missing: (provider) => `Its converted text is missing in ${provider}`,
  text_access_denied: (provider) => `${provider} refused access to its converted text`,
  text_without_checksum: () => 'Its converted text has no checksum, so it cannot be added safely',
  check_limit_reached: () => 'Not checked yet; it is checked the next time this list is opened',
  not_checked: () => 'Not checked yet',
}

/** What the provider last told an import about a chosen file, with the date. */
export const OBSERVATION_TEXT: Record<ObservationCode, (provider: string, date: string) => string> = {
  provider_not_converted: (provider, date) => `${provider} had not converted this file when it was last asked (${date})`,
  provider_conversion_failed: (provider, date) => `${provider} tried and could not convert this file (${date})`,
  not_converted_in_time: (provider, date) => `${provider} had not converted this file when the import stopped waiting (${date})`,
}

/** Why the provider's list of this paper's files cannot be shown. */
export const LISTING_REFUSAL_TEXT: Record<ListingRefusalCode, ProviderText | null> = {
  // An upload: the paper has no provider files to list. Whether to say so is Q4 for Chris.
  not_imported_from_provider: null,
  paper_reference_mismatch: (provider) => `This paper's entry in ${provider} has been merged into another entry, so its files cannot be listed here. Import the paper again from its current entry.`,
  paper_reference_incomplete: (provider) => `This paper's ${provider} entry is not recorded completely, so its files cannot be listed.`,
  document_source_curator_token_unavailable: (provider) => `Sign in again to see this paper's files in ${provider}.`,
  // The provider import is switched off on this server: nothing to show.
  document_source_import_disabled: null,
  reference_not_found: (provider) => `${provider} no longer has an entry for this paper, so its files cannot be listed.`,
}

/** A converted file that passed with little text (the R3 note). */
export function fewCharactersNote(count: number): string {
  return `Mostly images: about ${count.toLocaleString('en-US')} characters of text`
}

/** Files of the paper in the provider that the curator cannot open: counted, never named. */
export function notShownText(count: number, provider: string): string {
  return count === 1
    ? `1 more file in ${provider} is not listed because you cannot open it.`
    : `${count.toLocaleString('en-US')} more files in ${provider} are not listed because you cannot open them.`
}

/** Further copies of a listed file (same content) that are not listed again. */
export function duplicateCopiesText(count: number): string {
  return count === 1
    ? '1 further copy of a listed file is not listed again.'
    : `${count.toLocaleString('en-US')} further copies of listed files are not listed again.`
}

/** A file that could be added (the add itself comes later). */
export const ADDABLE_TEXT: ProviderText = (provider) => `Converted by ${provider}; not added to this paper`

/** The line that says why a provider's file is not in the paper, or that it could be. */
export function sourceFileStatusText(file: PaperSourceFile, provider: string): string {
  if (file.tickable) {
    return ADDABLE_TEXT(provider)
  }
  if (file.not_tickable_reason === null) {
    throw new Error(`Listed file ${file.provider_file_id} is neither tickable nor gives a reason`)
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
