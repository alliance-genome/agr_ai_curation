import type { DomainEnvelopeReviewFieldResolution } from '@/features/curation/types'

// The cell text for a value no validator resolved (ALL-1283). The paper
// wording is shown on its own labelled line, never in its place.
export const HORIZONTAL_GRID_UNRESOLVED_TEXT = 'UNRESOLVED'

const LABEL_KEYS = ['name', 'label', 'display_name'] as const
const ID_KEYS = ['curie', 'id', 'identifier'] as const

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

function formatRecord(value: Record<string, unknown>): string | null {
  // A stored resolvable value reads as its validated identity, or UNRESOLVED;
  // its paper wording never stands in for the validated value.
  if ('resolution_state' in value || typeof value.mention === 'string') {
    if (value.resolution_state !== 'resolved' || !('lookup_outcome' in value)) {
      return HORIZONTAL_GRID_UNRESOLVED_TEXT
    }
    return labeled(firstText(value, LABEL_KEYS), firstText(value, ID_KEYS)) || null
  }

  const presentKeys = Object.keys(value).filter((key) => !isEmpty(value[key]))
  const termKeys = new Set<string>([...LABEL_KEYS, ...ID_KEYS])
  if (
    presentKeys.length > 0
    && presentKeys.every((key) => termKeys.has(key))
    && presentKeys.filter((key) => (LABEL_KEYS as readonly string[]).includes(key)).length <= 1
    && presentKeys.filter((key) => (ID_KEYS as readonly string[]).includes(key)).length <= 1
  ) {
    return labeled(firstText(value, LABEL_KEYS), firstText(value, ID_KEYS)) || null
  }

  const parts = presentKeys.flatMap((key) => {
    const text = formatNested(value[key])
    return text === null ? [] : [`${key}: ${text}`]
  })
  return parts.length > 0 ? parts.join('; ') : null
}

function formatNested(value: unknown): string | null {
  if (Array.isArray(value)) {
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
    // Record boundaries stay visible when a list holds structured values.
    const separator = value.some((item) => isRecord(item) || Array.isArray(item)) ? ' | ' : ', '
    return value.map((item) => formatHorizontalGridValue(item) ?? '—').join(separator)
  }

  if (isRecord(value)) {
    return formatRecord(value)
  }

  return String(value)
}

/** The paper wording of every value behind a field, or null when none was recorded. */
export function horizontalGridPaperWording(
  resolution: DomainEnvelopeReviewFieldResolution,
): string | null {
  const mentions = resolution.values.flatMap((value) => (
    value.mention?.trim() ? [value.mention.trim()] : []
  ))
  return mentions.length > 0 ? mentions.join('; ') : null
}

/** The lookup result of every value behind a field, in plain words. */
export function horizontalGridLookupResult(
  resolution: DomainEnvelopeReviewFieldResolution,
): string {
  return resolution.values.map((value) => value.lookup_result).join('; ')
}

/** Labelled lines describing each value's lookup result and the validator's own words. */
export function horizontalGridValidationDetails(
  resolution: DomainEnvelopeReviewFieldResolution,
): string[][] {
  const several = resolution.values.length > 1
  return resolution.values.map((value) => [
    ...(several ? [`Value: ${value.display_text}`] : []),
    ...(several && value.mention ? [`Paper wording: ${value.mention}`] : []),
    `Lookup result: ${value.lookup_result}`,
    ...(value.validator_explanation ? [`Validator explanation: ${value.validator_explanation}`] : []),
    ...(value.validator_curator_message ? [`Validator message: ${value.validator_curator_message}`] : []),
  ])
}
