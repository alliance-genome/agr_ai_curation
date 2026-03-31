import type {
  CurationCandidate,
  CurationDraft,
  CurationDraftField,
  CurationDraftFieldChange,
} from '@/features/curation/types'
import type { EntityTag } from './types'
import { resolveEntityTypeCode } from './types'

const ENTITY_FIELD_KEYS = ['entity_name', 'gene_symbol']
const ENTITY_TYPE_FIELD_KEYS = ['entity_type', 'entity_type_code', 'entity_type_atp_code']
const SPECIES_FIELD_KEYS = ['species', 'taxon', 'taxon_id']
const TOPIC_FIELD_KEYS = ['topic', 'topic_name', 'topic_term', 'topic_curie']

function normalizeKey(value: string): string {
  return value.trim().toLowerCase()
}

function matchesField(field: CurationDraftField, acceptedKeys: readonly string[]): boolean {
  const fieldKey = normalizeKey(field.field_key)
  const fieldLabel = normalizeKey(field.label)

  return acceptedKeys.some((acceptedKey) => {
    const normalizedKey = normalizeKey(acceptedKey)
    return fieldKey === normalizedKey || fieldLabel === normalizedKey
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

function resolveEntityField(candidate: CurationCandidate): CurationDraftField {
  const entityField = findField(candidate.draft.fields, ENTITY_FIELD_KEYS)
  if (entityField === null) {
    throw new Error(`Candidate ${candidate.candidate_id} is missing an entity field for the entity table.`)
  }

  return entityField
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

function buildFieldChange(
  candidateId: string,
  field: CurationDraftField | null,
  value: string | undefined,
  logicalName: string,
): CurationDraftFieldChange | null {
  if (value === undefined) {
    return null
  }

  const nextValue = normalizeTextUpdate(value)
  if (field === null) {
    if (nextValue.length === 0) {
      return null
    }

    throw new Error(`Candidate ${candidateId} cannot store ${logicalName} because no backing draft field exists.`)
  }

  const currentValue = field.value
  if (currentValue !== null && currentValue !== undefined && typeof currentValue !== 'string') {
    throw new Error(`Candidate ${candidateId} has a non-string value for ${field.field_key}.`)
  }

  return currentValue === nextValue ? null : { field_key: field.field_key, value: nextValue }
}

function applyFieldChanges(fields: CurationDraftField[], fieldChanges: CurationDraftFieldChange[]): CurationDraftField[] {
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

export function buildEntityTagFieldChanges(
  candidate: CurationCandidate,
  updates: Partial<EntityTag>,
): CurationDraftFieldChange[] {
  const entityField = resolveEntityField(candidate)
  const entityTypeField = findField(candidate.draft.fields, ENTITY_TYPE_FIELD_KEYS)
  const speciesField = findField(candidate.draft.fields, SPECIES_FIELD_KEYS)
  const topicField = findField(candidate.draft.fields, TOPIC_FIELD_KEYS)

  const fieldChanges = [
    buildFieldChange(candidate.candidate_id, entityField, updates.entity_name, 'entity name'),
    buildFieldChange(candidate.candidate_id, speciesField, updates.species, 'species'),
    buildFieldChange(candidate.candidate_id, topicField, updates.topic, 'topic'),
  ]

  if (updates.entity_type !== undefined) {
    const entityType = normalizeSupportedEntityType(updates.entity_type)

    if (entityTypeField !== null) {
      const entityTypeChange = buildFieldChange(
        candidate.candidate_id,
        entityTypeField,
        entityType,
        'entity type',
      )
      fieldChanges.push(entityTypeChange)
    } else {
      throw new Error(
        `Candidate ${candidate.candidate_id} cannot store entity type ${entityType} because no backing draft field exists.`,
      )
    }
  }

  return fieldChanges.filter((fieldChange): fieldChange is CurationDraftFieldChange => fieldChange !== null)
}

export function buildManualCandidateDraft(
  templateCandidate: CurationCandidate,
  values: Pick<EntityTag, 'entity_name' | 'entity_type' | 'species' | 'topic'>,
  timestamp: string,
): CurationDraft {
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
      metadata: { ...templateCandidate.draft.metadata },
    },
  }

  const fieldChanges = buildEntityTagFieldChanges(templateCandidateDraft, values)

  return {
    ...templateCandidateDraft.draft,
    fields: applyFieldChanges(clonedFields, fieldChanges),
  }
}
