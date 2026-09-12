/** Pure transformations of the one Workshop draft. No persistence or proposal lifecycle. */
import type {
  GenericProfileContract, GenericProfileField, GenericProfileValueSchema,
} from '@/services/genericProfileService'
import { canonicalAuthoringJson } from '../authoringContext'

/** Indices, not editable keys, keep a selected field address stable while typing. */
export type ProfileFieldAddress = number[]

export interface ProfileFieldRow {
  address: ProfileFieldAddress
  field: GenericProfileField
  canonicalPath: string
  schemaPath: string
  depth: number
}

export function childSchema(schema: GenericProfileValueSchema): Extract<GenericProfileValueSchema, { kind: 'object' }> | null {
  if (schema.kind === 'array') return childSchema(schema.items)
  return schema.kind === 'object' ? schema : null
}

export function profileFieldRows(contract: GenericProfileContract): ProfileFieldRow[] {
  const rows: ProfileFieldRow[] = []
  function visit(fields: GenericProfileField[], parent: number[], path: string, schemaPath: string) {
    fields.forEach((field, index) => {
      const address = [...parent, index]
      const canonicalPath = `${path}.${field.key}`
      const fieldSchemaPath = `${schemaPath}[${index}]`
      rows.push({ address, field, canonicalPath, schemaPath: fieldSchemaPath, depth: parent.length })
      let schema = field.value_schema
      let nestedPath = canonicalPath
      let nestedSchemaPath = `${fieldSchemaPath}.value_schema`
      while (schema.kind === 'array') {
        schema = schema.items
        nestedPath += '[]'
        nestedSchemaPath += '.items'
      }
      if (schema.kind === 'object') visit(schema.fields, address, nestedPath, `${nestedSchemaPath}.fields`)
    })
  }
  visit(contract.fields, [], 'attributes', 'fields')
  return rows
}

function transformChildren(schema: GenericProfileValueSchema, transform: (fields: GenericProfileField[]) => GenericProfileField[]): GenericProfileValueSchema {
  if (schema.kind === 'array') return { ...schema, items: transformChildren(schema.items, transform) }
  if (schema.kind !== 'object') throw new Error('Select a group or repeating group to add child fields.')
  return { ...schema, fields: transform(schema.fields) }
}

export function editProfileFields(
  contract: GenericProfileContract,
  parent: ProfileFieldAddress,
  transform: (fields: GenericProfileField[]) => GenericProfileField[],
): GenericProfileContract {
  function visit(fields: GenericProfileField[], remaining: number[]): GenericProfileField[] {
    if (remaining.length === 0) return transform(fields)
    const [index, ...rest] = remaining
    if (!fields[index]) throw new Error('That field no longer exists. Select a field again.')
    return fields.map((field, i) => i !== index ? field : {
      ...field, value_schema: transformChildren(field.value_schema, (children) => visit(children, rest)),
    })
  }
  return { ...contract, fields: visit(contract.fields, parent) }
}

export function updateProfileField(contract: GenericProfileContract, address: ProfileFieldAddress, patch: Partial<GenericProfileField>): GenericProfileContract {
  if (!address.length) throw new Error('Select a field to edit.')
  const index = address[address.length - 1]
  return editProfileFields(contract, address.slice(0, -1), (fields) => {
    if (!fields[index]) throw new Error('That field no longer exists. Select a field again.')
    return fields.map((field, i) => i === index ? { ...field, ...patch } : field)
  })
}

export function uniqueFieldKey(fields: GenericProfileField[], stem = 'new_field'): string {
  const names = new Set(fields.flatMap((field) => [field.key, ...(field.source_labels ?? [])]
    .map((name) => name.normalize('NFKC').trim().toLowerCase().replace(/[\s-]+/g, '_'))))
  let key = stem
  let suffix = 2
  while (names.has(key)) key = `${stem}_${suffix++}`
  return key
}

export function addProfileField(contract: GenericProfileContract, parent: ProfileFieldAddress): GenericProfileContract {
  return editProfileFields(contract, parent, (fields) => [...fields, {
    key: uniqueFieldKey(fields), display_name: 'New field', description: '',
    required: false, nullable: false, source_labels: [], value_schema: { kind: 'string' },
  }])
}

export function duplicateProfileField(contract: GenericProfileContract, address: ProfileFieldAddress): GenericProfileContract {
  const index = address[address.length - 1]
  return editProfileFields(contract, address.slice(0, -1), (fields) => {
    const source = fields[index]
    if (!source) throw new Error('Select a field to duplicate.')
    const copy = structuredClone(source)
    copy.key = uniqueFieldKey(fields, `${source.key}_copy`)
    copy.display_name = `${source.display_name || source.key} (copy)`
    // A sibling copy must not compete for the original source labels. Nested
    // children have their own namespace; retain their declared source labels.
    copy.source_labels = []
    return [...fields.slice(0, index + 1), copy, ...fields.slice(index + 1)]
  })
}

export function removeProfileField(contract: GenericProfileContract, address: ProfileFieldAddress): GenericProfileContract {
  const index = address[address.length - 1]
  return editProfileFields(contract, address.slice(0, -1), (fields) => fields.filter((_, i) => i !== index))
}

export function moveProfileField(contract: GenericProfileContract, address: ProfileFieldAddress, direction: -1 | 1): GenericProfileContract {
  const index = address[address.length - 1]
  return editProfileFields(contract, address.slice(0, -1), (fields) => {
    const destination = index + direction
    if (!fields[index] || destination < 0 || destination >= fields.length) return fields
    const next = [...fields]
    ;[next[index], next[destination]] = [next[destination], next[index]]
    return next
  })
}

export type FriendlyProfileKind = GenericProfileValueSchema['kind'] | 'repeating_group'
export const PROFILE_KIND_LABELS: Record<FriendlyProfileKind, string> = {
  string: 'Text', integer: 'Whole number', number: 'Number', boolean: 'Yes/no',
  enum: 'Choose from a list', array: 'List of values', object: 'Group of related details',
  repeating_group: 'Repeating group',
}

export function friendlyProfileKind(schema: GenericProfileValueSchema): FriendlyProfileKind {
  return schema.kind === 'array' && schema.items.kind === 'object' ? 'repeating_group' : schema.kind
}

export function schemaForKind(kind: FriendlyProfileKind): GenericProfileValueSchema {
  if (kind === 'repeating_group') return { kind: 'array', items: { kind: 'object', fields: [] } }
  if (kind === 'array') return { kind, items: { kind: 'string' } }
  if (kind === 'object') return { kind, fields: [] }
  if (kind === 'enum') return { kind, values: [] }
  return { kind }
}

export function profileExampleValue(schema: GenericProfileValueSchema): unknown {
  switch (schema.kind) {
    case 'string': return 'Example text'
    case 'integer': return 1
    case 'number': return 1.5
    case 'boolean': return true
    case 'enum': return schema.values[0] ?? 'Choose a value'
    case 'array': return [profileExampleValue(schema.items)]
    case 'object': return Object.fromEntries(schema.fields.map((field) => [field.key, profileExampleValue(field.value_schema)]))
  }
}

export function profileExampleRecord(contract: GenericProfileContract): Record<string, unknown> {
  return {
    label: 'Example record — placeholder, not paper evidence',
    semantic_class: contract.semantic_class,
    attributes: profileExampleValue({ kind: 'object', fields: contract.fields }),
  }
}

export interface ProfileDraftChange {
  path: string
  kind: 'added' | 'removed' | 'changed'
  before?: unknown
  after?: unknown
}

/** Exact leaf changes, preserving ordered field indices and mapping values. */
export function compareProfileDrafts(before: GenericProfileContract | null, after: GenericProfileContract): ProfileDraftChange[] {
  const changes: ProfileDraftChange[] = []
  function visit(left: unknown, right: unknown, path: string) {
    if (left === undefined && right === undefined) return
    if (left === undefined) { changes.push({ path, kind: 'added', after: right }); return }
    if (right === undefined) { changes.push({ path, kind: 'removed', before: left }); return }
    if (canonicalAuthoringJson(left) === canonicalAuthoringJson(right)) return
    if (left !== null && right !== null && typeof left === 'object' && typeof right === 'object'
        && Array.isArray(left) === Array.isArray(right)) {
      const leftRecord = left as Record<string, unknown>
      const rightRecord = right as Record<string, unknown>
      for (const key of new Set([...Object.keys(leftRecord), ...Object.keys(rightRecord)])) {
        visit(leftRecord[key], rightRecord[key], Array.isArray(left) ? `${path}[${key}]` : path ? `${path}.${key}` : key)
      }
    } else changes.push({ path, kind: 'changed', before: left, after: right })
  }
  visit(before ?? undefined, after, '')
  return changes
}

/** The shared ALL-1051 lifecycle owns when this pure value is applied/undone. */
export function profileCandidateToDraft(candidate: GenericProfileContract): GenericProfileContract {
  return structuredClone(candidate)
}
