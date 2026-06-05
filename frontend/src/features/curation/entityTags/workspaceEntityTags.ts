import type {
  CurationCandidate,
  CurationDraft,
  CurationDraftField,
} from '@/features/curation/types'
import type { EntityTag } from './types'
import { resolveEntityTypeCode } from './types'

const ENTITY_FIELD_KEYS = ['entity_name', 'gene_symbol']
const ENTITY_TYPE_FIELD_KEYS = ['entity_type', 'entity_type_code', 'entity_type_atp_code']
const SPECIES_FIELD_KEYS = ['species', 'taxon', 'taxon_id']
const TOPIC_FIELD_KEYS = ['topic', 'topic_name', 'topic_term', 'topic_curie']

type ManualFieldChange = { field_key: string; value: string }

function normalizeKey(value: string): string {
  return value.trim().toLowerCase()
}

function matchesField(field: CurationDraftField, acceptedKeys: readonly string[]): boolean {
  const fieldKey = normalizeKey(field.field_key)
  const fieldLabel = normalizeKey(field.label)

  return acceptedKeys.some((acceptedKey) => {
    const normalizedKey = normalizeKey(acceptedKey)
    return fieldKey === normalizedKey ||
      fieldKey.endsWith(`.${normalizedKey}`) ||
      fieldLabel === normalizedKey
  })
}

function findField(
  fields: CurationDraftField[],
  acceptedKeys: readonly string[],
): CurationDraftField | null {
  for (const field of fields) {
    if (matchesField(field, acceptedKeys)) {
      return field
    }
  }

  return null
}

function normalizeTextUpdate(value: string): string {
  return value.trim()
}

function normalizeSupportedEntityType(entityType: string): string {
  const normalizedEntityType = normalizeTextUpdate(entityType)
  const entityTypeCode = resolveEntityTypeCode(normalizedEntityType)
  if (entityTypeCode === null) {
    throw new Error(`Entity type ${normalizedEntityType} is not a supported entity type.`)
  }

  return entityTypeCode
}

function buildManualFieldChange(
  candidateId: string,
  field: CurationDraftField | null,
  value: string | undefined,
): ManualFieldChange | null {
  if (value === undefined || field === null) {
    return null
  }

  const nextValue = normalizeTextUpdate(value)
  if (nextValue.length === 0) {
    return null
  }

  const currentValue = field.value
  if (currentValue !== null && currentValue !== undefined && typeof currentValue !== 'string') {
    throw new Error(`Candidate ${candidateId} has a non-string value for ${field.field_key}.`)
  }

  return currentValue === nextValue ? null : { field_key: field.field_key, value: nextValue }
}

function applyFieldChanges(
  fields: CurationDraftField[],
  fieldChanges: ManualFieldChange[],
): CurationDraftField[] {
  const changesByFieldKey = new Map(fieldChanges.map((fieldChange) => [fieldChange.field_key, fieldChange]))

  return fields.map((field) => {
    const fieldChange = changesByFieldKey.get(field.field_key)
    if (!fieldChange) {
      return {
        ...field,
        metadata: { ...field.metadata },
      }
    }

    return {
      ...field,
      value: fieldChange.value ?? null,
      seed_value: fieldChange.value ?? null,
      dirty: false,
      stale_validation: false,
      evidence_anchor_ids: [],
      validation_result: null,
      metadata: { ...field.metadata },
    }
  })
}

export function buildManualCandidateDraft(
  templateCandidate: CurationCandidate,
  values: Pick<EntityTag, 'entity_name' | 'entity_type' | 'species' | 'topic'>,
  timestamp: string,
): CurationDraft {
  const normalizedValues = {
    entity_name: normalizeTextUpdate(values.entity_name),
    entity_type: normalizeSupportedEntityType(values.entity_type),
    species: normalizeTextUpdate(values.species),
    topic: normalizeTextUpdate(values.topic),
  }
  const clonedFields = templateCandidate.draft.fields.map((field) => ({
    ...field,
    value: null,
    seed_value: null,
    dirty: false,
    stale_validation: false,
    evidence_anchor_ids: [],
    validation_result: null,
    metadata: { ...field.metadata },
  }))

  const templateCandidateDraft: CurationCandidate = {
    ...templateCandidate,
    draft: {
      ...templateCandidate.draft,
      draft_id: `manual-draft-${timestamp}`,
      candidate_id: `manual-candidate-${timestamp}`,
      version: 1,
      fields: clonedFields,
      created_at: timestamp,
      updated_at: timestamp,
      notes: null,
      metadata: {
        ...templateCandidate.draft.metadata,
        manual_object: normalizedValues,
      },
    },
  }

  const fields = templateCandidateDraft.draft.fields
  const entityField = findField(fields, ENTITY_FIELD_KEYS)
  const entityTypeField = findField(fields, ENTITY_TYPE_FIELD_KEYS)
  const speciesField = findField(fields, SPECIES_FIELD_KEYS)
  const topicField = findField(fields, TOPIC_FIELD_KEYS)
  const fieldChanges = [
    buildManualFieldChange(
      templateCandidateDraft.candidate_id,
      entityField,
      normalizedValues.entity_name,
    ),
    buildManualFieldChange(
      templateCandidateDraft.candidate_id,
      entityTypeField,
      normalizedValues.entity_type,
    ),
    buildManualFieldChange(
      templateCandidateDraft.candidate_id,
      speciesField,
      normalizedValues.species,
    ),
    buildManualFieldChange(
      templateCandidateDraft.candidate_id,
      topicField,
      normalizedValues.topic,
    ),
  ].filter((fieldChange): fieldChange is ManualFieldChange => fieldChange !== null)

  return {
    ...templateCandidateDraft.draft,
    fields: applyFieldChanges(clonedFields, fieldChanges),
  }
}
