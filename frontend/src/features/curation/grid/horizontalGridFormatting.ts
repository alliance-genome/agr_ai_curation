import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'

// The cell text for a value no validator resolved (ALL-1283). The paper
// wording is shown on its own labelled line, never in its place.
export const HORIZONTAL_GRID_UNRESOLVED_TEXT = 'UNRESOLVED'

const LABEL_KEYS = ['name', 'label', 'display_name'] as const
const ID_KEYS = ['curie', 'id', 'identifier'] as const
// The keys the resolvable-value contract adds; the rest are the value's own keys.
const CONTRACT_KEYS = new Set([
  'mention',
  'resolution_state',
  'lookup_outcome',
  'validator_explanation',
  'validator_curator_message',
])

// A validator's overruled identity (resolvable_values.OVERRULED_KEY_PREFIX):
// informational only, never displayed. The extractor's own proposal
// (proposed_*) is never read as the validated value either.
const OVERRULED_KEY_PREFIX = 'overruled_'
const EXTRACTOR_PROPOSAL_PREFIX = 'proposed_'

// These readings follow the backend display rules (src/lib/flows/value_display.py)
// for values that reach the grid without a backend reading, such as curator edits.

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) {
    return true
  }
  if (typeof value === 'string') {
    return value.trim() === ''
  }
  if (Array.isArray(value)) {
    return value.length === 0
  }
  if (typeof value === 'object') {
    return Object.keys(value).length === 0
  }
  return false
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function firstText(value: Record<string, unknown>, keys: readonly string[]): string {
  for (const key of keys) {
    const item = value[key]
    if (typeof item === 'string' && item.trim()) {
      return item.trim()
    }
    if (typeof item === 'number' || typeof item === 'boolean') {
      return String(item)
    }
  }
  return ''
}

function labeled(label: string, identifier: string): string {
  return label && identifier ? `${label} (${identifier})` : label || identifier
}

function pairsText(value: Record<string, unknown>, keys: readonly string[]): string | null {
  const parts = keys.flatMap((key) => {
    const text = formatNested(value[key])
    return text === null ? [] : [`${key}: ${text}`]
  })
  return parts.length > 0 ? parts.join('; ') : null
}

function formatRecord(value: Record<string, unknown>): string | null {
  // A stored resolvable value reads as its validated identity, or UNRESOLVED;
  // its paper wording never stands in for the validated value.
  if ('resolution_state' in value || typeof value.mention === 'string') {
    if (value.resolution_state !== 'resolved' || !('lookup_outcome' in value)) {
      return HORIZONTAL_GRID_UNRESOLVED_TEXT
    }
    const identity = labeled(firstText(value, LABEL_KEYS), firstText(value, ID_KEYS))
    if (identity) {
      return identity
    }
    // Undeclared identity keys: show the validated content, never the paper
    // wording or the extractor's proposal.
    return pairsText(value, Object.keys(value).filter(
      (key) => !CONTRACT_KEYS.has(key)
        && !key.startsWith(OVERRULED_KEY_PREFIX)
        && !key.startsWith(EXTRACTOR_PROPOSAL_PREFIX),
    ))
  }

  const presentKeys = Object.keys(value).filter(
    (key) => !isEmpty(value[key]) && !key.startsWith(OVERRULED_KEY_PREFIX),
  )
  const termKeys = new Set<string>([...LABEL_KEYS, ...ID_KEYS])
  if (
    presentKeys.length > 0
    && presentKeys.every((key) => termKeys.has(key))
    && presentKeys.filter((key) => (LABEL_KEYS as readonly string[]).includes(key)).length <= 1
    && presentKeys.filter((key) => (ID_KEYS as readonly string[]).includes(key)).length <= 1
  ) {
    return labeled(firstText(value, LABEL_KEYS), firstText(value, ID_KEYS)) || null
  }

  return pairsText(value, presentKeys)
}

function formatNested(value: unknown): string | null {
  if (Array.isArray(value)) {
    // A list inside a record joins with ", " so "; " and " | " keep their meaning.
    const parts = value.flatMap((item) => {
      const text = formatNested(item)
      return text === null ? [] : [text]
    })
    return parts.length > 0 ? parts.join(', ') : null
  }
  return formatHorizontalGridValue(value)
}

/** Readable cell text for a stored value; objects never render as JSON. */
export function formatHorizontalGridValue(value: unknown): string | null {
  if (value === null || value === undefined || value === '') {
    return null
  }

  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  if (Array.isArray(value)) {
    // Lists join with "; ", and lists of records (or of lists) with " | " so
    // record boundaries stay visible.
    const separator = value.some((item) => isRecord(item) || Array.isArray(item)) ? ' | ' : '; '
    const parts = value.flatMap((item) => {
      const text = Array.isArray(item) ? formatNested(item) : formatHorizontalGridValue(item)
      return text === null ? [] : [text]
    })
    return parts.length > 0 ? parts.join(separator) : null
  }

  if (isRecord(value)) {
    return formatRecord(value)
  }

  return String(value)
}

/** The paper wording of the given values, or null when none was recorded. */
export function horizontalGridPaperWording(
  values: readonly DomainEnvelopeReviewResolvedValue[],
): string | null {
  const mentions = values.flatMap((value) => (
    value.mention?.trim() ? [value.mention.trim()] : []
  ))
  return mentions.length > 0 ? mentions.join('; ') : null
}

/** The lookup result of the given values, in plain words. */
export function horizontalGridLookupResult(
  values: readonly DomainEnvelopeReviewResolvedValue[],
): string {
  return values.map((value) => value.lookup_result).join('; ')
}

/** The validator's own words (and any unreadable-value issue) for the given values. */
export function horizontalGridValidatorWords(
  values: readonly DomainEnvelopeReviewResolvedValue[],
): string[] {
  return values.flatMap((value) => [
    ...(value.validator_explanation ? [`Validator explanation: ${value.validator_explanation}`] : []),
    ...(value.validator_curator_message ? [`Validator message: ${value.validator_curator_message}`] : []),
    ...(value.issue ? [value.issue] : []),
  ])
}

/** Labelled lines describing each value in full: validated value, paper wording, lookup, validator. */
export function horizontalGridValidationDetails(
  values: readonly DomainEnvelopeReviewResolvedValue[],
): string[][] {
  return values.map((value) => [
    `Value: ${value.display_text}`,
    ...(value.mention ? [`Paper wording: ${value.mention}`] : []),
    `Lookup result: ${value.lookup_result}`,
    ...horizontalGridValidatorWords([value]),
  ])
}
