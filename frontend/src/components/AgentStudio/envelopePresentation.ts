/**
 * Pure helpers that turn domain-envelope metadata into the words and groupings
 * the Envelope tab renders. No MOD-specific branches: unknown providers and
 * roles fall through to their raw keys.
 */

import type {
  DomainEnvelopeFieldMetadata,
  DomainEnvelopeMetadata,
  DomainEnvelopeObjectMetadata,
  ValidationAttachmentOption,
} from '@/services/agentStudioService'

const SOURCE_OF_TRUTH_WORDS: Record<string, string> = {
  alliance_linkml: 'LinkML',
  curation_db: 'Curation DB',
  metadata: 'Extractor',
}

/** Word for a field's source of truth. Absent means the extractor owns the value. */
export function sourceOfTruthWord(key?: string | null): string {
  if (!key) return 'Extractor'
  return SOURCE_OF_TRUTH_WORDS[key] ?? key
}

export function fieldTypeLabel(field: Pick<DomainEnvelopeFieldMetadata, 'field_type' | 'enum_ref'>): string {
  if (field.field_type === 'enum' && field.enum_ref) {
    return `choice: ${field.enum_ref}`
  }
  return field.field_type
}

export type ValidatorPolicyBadge = 'Blocking' | 'Opt-out' | null

export function validatorPolicyBadge(
  attachment: Pick<ValidationAttachmentOption, 'blocking' | 'allow_opt_out'>
): ValidatorPolicyBadge {
  if (attachment.blocking) return 'Blocking'
  if (attachment.allow_opt_out) return 'Opt-out'
  return null
}

/** One sentence that states the validator's policy in curator words. */
export function validatorPolicySentence(
  attachment: Pick<ValidationAttachmentOption, 'blocking' | 'allow_opt_out' | 'required'>
): string {
  if (attachment.blocking) {
    return 'Blocking: a row cannot be submitted until this check passes.'
  }
  if (attachment.allow_opt_out) {
    return 'Curators may opt out per flow.'
  }
  if (attachment.required) {
    return 'Required: runs on every row.'
  }
  return 'Advisory: findings are shown but do not block submission.'
}

export interface EnvelopeFieldGroupView {
  id: string
  /** Null when the object declares no field groups (flat table). */
  label: string | null
  fields: DomainEnvelopeFieldMetadata[]
}

/**
 * Group an object's fields under its declared field groups, in declared order.
 * Field paths the object does not define are ignored. Fields outside every
 * group land in a trailing "Other fields" group. Objects without groups
 * return one unlabeled group holding every field.
 */
export function groupObjectFields(object: DomainEnvelopeObjectMetadata): EnvelopeFieldGroupView[] {
  if (object.field_groups.length === 0) {
    return [{ id: `${object.object_type}:all`, label: null, fields: object.fields }]
  }

  const fieldsByPath = new Map(object.fields.map((field) => [field.field_path, field]))
  const placed = new Set<string>()
  const groups: EnvelopeFieldGroupView[] = []

  for (const group of object.field_groups) {
    const fields = group.field_paths
      .map((path) => fieldsByPath.get(path))
      .filter((field): field is DomainEnvelopeFieldMetadata => Boolean(field) && !placed.has(field!.field_path))
    fields.forEach((field) => placed.add(field.field_path))
    if (fields.length > 0) {
      groups.push({ id: `${object.object_type}:${group.id}`, label: group.label, fields })
    }
  }

  const rest = object.fields.filter((field) => !placed.has(field.field_path))
  if (rest.length > 0) {
    groups.push({ id: `${object.object_type}:other`, label: 'Other fields', fields: rest })
  }
  return groups
}

export interface EnvelopeValidatorView {
  validatorId: string
  label: string
  state: ValidationAttachmentOption['state']
  stateExplanation?: string
  description?: string
  policySentence: string
  /** Distinct field paths this validator covers on the object. */
  fieldCount: number
  /** True when the validator has an object- or pack-scope attachment. */
  coversWholeObject: boolean
}

/**
 * List each validator that touches an object once, with the number of fields
 * it covers. Field attachments are deduplicated by validator id.
 */
export function objectValidators(object: DomainEnvelopeObjectMetadata): EnvelopeValidatorView[] {
  const byValidator = new Map<string, { attachment: ValidationAttachmentOption; fields: Set<string>; wholeObject: boolean }>()

  const register = (attachment: ValidationAttachmentOption, fieldPath: string | null) => {
    const existing = byValidator.get(attachment.validator_id)
    if (existing) {
      if (fieldPath) existing.fields.add(fieldPath)
      if (!fieldPath) existing.wholeObject = true
      return
    }
    byValidator.set(attachment.validator_id, {
      attachment,
      fields: new Set(fieldPath ? [fieldPath] : []),
      wholeObject: !fieldPath,
    })
  }

  object.validation_attachments.forEach((attachment) => register(attachment, null))
  object.fields.forEach((field) => {
    field.validation_attachments.forEach((attachment) => register(attachment, field.field_path))
  })

  return Array.from(byValidator.values()).map(({ attachment, fields, wholeObject }) => ({
    validatorId: attachment.validator_id,
    label: attachment.label,
    state: attachment.state,
    stateExplanation: attachment.state_explanation,
    description: attachment.description,
    policySentence: validatorPolicySentence(attachment),
    fieldCount: fields.size,
    coversWholeObject: wholeObject,
  }))
}

export const CURATABLE_OBJECT_ROLE = 'curatable_unit'

export interface EnvelopeObjectChoice {
  id: string
  label: string
  objects: DomainEnvelopeObjectMetadata[]
}

/**
 * Picker entries: one per curatable object plus one "Embedded references (N)"
 * entry for the rest. When no object is marked curatable, every object gets
 * its own entry.
 */
export function envelopeObjectChoices(metadata: DomainEnvelopeMetadata): EnvelopeObjectChoice[] {
  const curatable = metadata.object_definitions.filter((object) => object.object_role === CURATABLE_OBJECT_ROLE)
  const embedded = metadata.object_definitions.filter((object) => object.object_role !== CURATABLE_OBJECT_ROLE)

  if (curatable.length === 0) {
    return metadata.object_definitions.map((object) => ({
      id: object.object_type,
      label: object.display_name,
      objects: [object],
    }))
  }

  const choices: EnvelopeObjectChoice[] = curatable.map((object) => ({
    id: object.object_type,
    label: object.display_name,
    objects: [object],
  }))
  if (embedded.length > 0) {
    choices.push({
      id: 'embedded-references',
      label: `Embedded references (${embedded.length})`,
      objects: embedded,
    })
  }
  return choices
}

export interface EnvelopeCounts {
  activeValidators: number
  underDevelopmentValidators: number
  requiredFields: number
  blockingChecks: number
}

function distinctValidatorIds(attachments: ValidationAttachmentOption[], state: ValidationAttachmentOption['state']): number {
  return new Set(attachments.filter((attachment) => attachment.state === state).map((attachment) => attachment.validator_id)).size
}

export function envelopeCounts(metadata: DomainEnvelopeMetadata): EnvelopeCounts {
  return {
    activeValidators: distinctValidatorIds(metadata.validation_attachments, 'active'),
    underDevelopmentValidators: distinctValidatorIds(metadata.validation_attachments, 'under_development'),
    requiredFields: metadata.object_definitions.reduce(
      (count, object) => count + object.fields.filter((field) => field.required).length,
      0
    ),
    blockingChecks: metadata.validation_summary.blocking,
  }
}

export function shortCommit(value?: string | null): string {
  if (!value) return ''
  return /^[0-9a-f]{12,}$/i.test(value) ? value.slice(0, 8) : value
}

export function humanizeStatus(value: string): string {
  return value.replace(/_/g, ' ')
}
