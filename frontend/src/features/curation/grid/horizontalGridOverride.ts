import type { DomainEnvelopeReviewResolvedValue } from '@/features/curation/types'

// The backend's own wording for an override missing half its identity.
export const HORIZONTAL_GRID_OVERRIDE_INCOMPLETE_MESSAGE =
  'Enter both the identifier and the name for a curator override.'

export interface HorizontalGridOverrideIdentity {
  identifier: string
  name: string
}

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

/** The identifier and name a value currently holds, for the override editor's inputs. */
export function horizontalGridOverrideIdentity(
  value: DomainEnvelopeReviewResolvedValue,
): HorizontalGridOverrideIdentity {
  return {
    identifier: value.id_key ? text(value.stored_value[value.id_key]) : '',
    name: value.label_key ? text(value.stored_value[value.label_key]) : '',
  }
}

/** Why an override cannot be sent as entered, or null. Both declared keys are required. */
export function horizontalGridOverrideProblem(
  value: DomainEnvelopeReviewResolvedValue,
  identity: HorizontalGridOverrideIdentity,
): string | null {
  const missing = (value.id_key && !identity.identifier.trim())
    || (value.label_key && !identity.name.trim())
  return missing ? HORIZONTAL_GRID_OVERRIDE_INCOMPLETE_MESSAGE : null
}

/**
 * The whole value with the curator's identifier and name: one edit, so a first
 * override carries both. Every other key (paper wording, validation state,
 * validator words, overruled and proposed keys) passes through unchanged.
 */
export function horizontalGridOverridePatchValue(
  value: DomainEnvelopeReviewResolvedValue,
  identity: HorizontalGridOverrideIdentity,
): Record<string, unknown> {
  return {
    ...value.stored_value,
    ...(value.id_key ? { [value.id_key]: identity.identifier.trim() } : {}),
    ...(value.label_key ? { [value.label_key]: identity.name.trim() } : {}),
  }
}

/** The whole value with every identity key cleared, which withdraws a curator override. */
export function horizontalGridRemoveOverridePatchValue(
  value: DomainEnvelopeReviewResolvedValue,
): Record<string, unknown> {
  const cleared: Record<string, unknown> = { ...value.stored_value }
  for (const key of [value.id_key, value.label_key, ...value.validated_keys]) {
    if (key) {
      cleared[key] = null
    }
  }
  return cleared
}
