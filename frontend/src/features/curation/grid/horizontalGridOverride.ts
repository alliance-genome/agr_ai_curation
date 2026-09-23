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
    identifier: value.id_key ? text(value.stored_identity[value.id_key]) : '',
    name: value.label_key ? text(value.stored_identity[value.label_key]) : '',
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

export interface HorizontalGridOverridePatch {
  // One identity field of the value: the bare key for the object itself.
  field_path: string
  value: Record<string, unknown>
  before: Record<string, unknown>
}

function identityField(value: DomainEnvelopeReviewResolvedValue, key: string): string {
  return value.value_path ? `${value.value_path}.${key}` : key
}

function identityPatch(
  value: DomainEnvelopeReviewResolvedValue,
  identity: Record<string, unknown>,
): HorizontalGridOverridePatch {
  const firstKey = value.id_key ?? value.label_key
  if (!firstKey) {
    throw new Error(`Resolvable value '${value.value_path}' declares no identifier or name key`)
  }
  return {
    field_path: identityField(value, firstKey),
    value: identity,
    // The current value of each key the patch names (null when absent).
    before: Object.fromEntries(
      Object.keys(identity).map((key) => [key, value.stored_identity[key] ?? null]),
    ),
  }
}

/**
 * A curator override as one atomic ``replace_identity`` edit: the value's
 * identifier and name together. Nothing but identity keys is sent, so the
 * paper wording, validation state and proposals stay as they are.
 */
export function horizontalGridOverridePatch(
  value: DomainEnvelopeReviewResolvedValue,
  identity: HorizontalGridOverrideIdentity,
): HorizontalGridOverridePatch {
  return identityPatch(value, {
    ...(value.id_key ? { [value.id_key]: identity.identifier.trim() } : {}),
    ...(value.label_key ? { [value.label_key]: identity.name.trim() } : {}),
  })
}

/** Remove an override: the same edit with every identity key (validated keys too) cleared. */
export function horizontalGridRemoveOverridePatch(
  value: DomainEnvelopeReviewResolvedValue,
): HorizontalGridOverridePatch {
  const keys = [value.id_key, value.label_key, ...value.validated_keys].filter(
    (key): key is string => Boolean(key),
  )
  return identityPatch(value, Object.fromEntries(keys.map((key) => [key, null])))
}
