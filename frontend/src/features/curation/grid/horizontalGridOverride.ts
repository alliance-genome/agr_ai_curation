import type {
  CurationEnvelopeFieldPatchOperation,
  DomainEnvelopeReviewResolvedValue,
} from '@/features/curation/types'

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
  operation: CurationEnvelopeFieldPatchOperation
  field_path: string
  value: Record<string, unknown>
  before: Record<string, unknown>
}

// Mirrors the backend's is_generic_attribute_path: a saved profile's attributes.
export function isHorizontalGridProfileAttributePath(path: string): boolean {
  return path === 'attributes' || path.startsWith('attributes.') || path.startsWith('attributes[')
}

function identityField(value: DomainEnvelopeReviewResolvedValue, key: string): string {
  return value.value_path ? `${value.value_path}.${key}` : key
}

function overrideEdit(
  value: DomainEnvelopeReviewResolvedValue,
  identity: Record<string, unknown>,
): HorizontalGridOverridePatch {
  if (isHorizontalGridProfileAttributePath(value.value_path)) {
    // A profile value takes no replace_identity: one whole-value replace
    // whose identity keys change and every other key stays as stored.
    if (!value.stored_value) {
      throw new Error(`Profile value '${value.value_path}' carries no stored value to replace`)
    }
    return {
      operation: 'replace',
      field_path: value.value_path,
      value: { ...value.stored_value, ...identity },
      before: value.stored_value,
    }
  }
  const firstKey = value.id_key ?? value.label_key
  if (!firstKey) {
    throw new Error(`Resolvable value '${value.value_path}' declares no identifier or name key`)
  }
  return {
    operation: 'replace_identity',
    field_path: identityField(value, firstKey),
    value: identity,
    // The current value of each key the patch names (null when absent).
    before: Object.fromEntries(
      Object.keys(identity).map((key) => [key, value.stored_identity[key] ?? null]),
    ),
  }
}

/**
 * A curator override as one atomic edit of the value's identifier and name:
 * a ``replace_identity`` naming only identity keys, or for a saved profile's
 * attribute value a whole-value ``replace``. Either way the paper wording,
 * validation state and proposals stay as they are.
 */
export function horizontalGridOverridePatch(
  value: DomainEnvelopeReviewResolvedValue,
  identity: HorizontalGridOverrideIdentity,
): HorizontalGridOverridePatch {
  return overrideEdit(value, {
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
  return overrideEdit(value, Object.fromEntries(keys.map((key) => [key, null])))
}
