import type {
  CurationEnvelopeFieldPatchOperation,
  DomainEnvelopeReviewResolvedValue,
} from '@/features/curation/types'

// The backend's own wording for an override that leaves the id or label
// empty, by which of the two the value declares (resolvable_values.py).
const OVERRIDE_INCOMPLETE_MESSAGES: Record<string, string> = {
  'true:true': 'Enter both the identifier and the name for a curator override.',
  'true:false': 'Enter the identifier for a curator override.',
  'false:true': 'Enter the name for a curator override.',
}

// Plain words for a key name inside a curator-facing label.
const KEY_WORDS: Record<string, string> = { id: 'ID', curie: 'CURIE', uberon: 'UBERON' }

/** The curator's text for each identity key of a value, keyed by the value's own key names. */
export type HorizontalGridOverrideIdentity = Record<string, string>

function text(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

/** Every identity key a curator override sets: the identifier, the name, then validated keys. */
export function horizontalGridIdentityKeys(value: DomainEnvelopeReviewResolvedValue): string[] {
  return [value.id_key, value.label_key, ...value.validated_keys].filter(
    (key): key is string => Boolean(key),
  )
}

/** A curator-facing label for one identity key of a value. */
export function horizontalGridIdentityKeyLabel(
  value: DomainEnvelopeReviewResolvedValue,
  key: string,
): string {
  if (key === value.id_key) {
    return 'Identifier'
  }
  if (key === value.label_key) {
    return 'Name'
  }
  const words = key.split('_').filter(Boolean).map((word) => KEY_WORDS[word] ?? word)
  const phrase = words.join(' ')
  return phrase.charAt(0).toUpperCase() + phrase.slice(1)
}

/** Each identity key's current text, for the override editor's inputs. */
export function horizontalGridOverrideIdentity(
  value: DomainEnvelopeReviewResolvedValue,
): HorizontalGridOverrideIdentity {
  return Object.fromEntries(
    horizontalGridIdentityKeys(value).map((key) => [key, text(value.stored_identity[key])]),
  )
}

/**
 * Why an override cannot be sent as entered, or null. Every identity key is
 * required: the identifier and name with the backend's own wording, then each
 * validated key (e.g. a taxon) by name.
 */
export function horizontalGridOverrideProblem(
  value: DomainEnvelopeReviewResolvedValue,
  identity: HorizontalGridOverrideIdentity,
): string | null {
  const empty = (key: string | null) => Boolean(key) && !(identity[key as string] ?? '').trim()
  if (empty(value.id_key) || empty(value.label_key)) {
    return OVERRIDE_INCOMPLETE_MESSAGES[`${Boolean(value.id_key)}:${Boolean(value.label_key)}`]
  }
  const missing = value.validated_keys.filter((key) => empty(key))
  if (missing.length === 0) {
    return null
  }
  const labels = missing.map((key) => horizontalGridIdentityKeyLabel(value, key).toLowerCase())
  return `Enter the ${labels.join(' and the ')} for a curator override.`
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
 * A curator override as one atomic edit of every identity key of the value
 * (identifier, name and validated keys such as a taxon): a
 * ``replace_identity`` naming only identity keys, or for a saved profile's
 * attribute value a whole-value ``replace``. Either way the paper wording,
 * validation state and proposals stay as they are.
 */
export function horizontalGridOverridePatch(
  value: DomainEnvelopeReviewResolvedValue,
  identity: HorizontalGridOverrideIdentity,
): HorizontalGridOverridePatch {
  return overrideEdit(
    value,
    Object.fromEntries(
      horizontalGridIdentityKeys(value).map((key) => [key, (identity[key] ?? '').trim()]),
    ),
  )
}

/** Remove an override: the same edit with every identity key (validated keys too) cleared. */
export function horizontalGridRemoveOverridePatch(
  value: DomainEnvelopeReviewResolvedValue,
): HorizontalGridOverridePatch {
  return overrideEdit(
    value,
    Object.fromEntries(horizontalGridIdentityKeys(value).map((key) => [key, null])),
  )
}
