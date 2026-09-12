import { describe, expect, it } from 'vitest'
import type { GenericProfileContract } from '@/services/genericProfileService'
import {
  addProfileField, compareProfileDrafts, duplicateProfileField, moveProfileField,
  profileCandidateToDraft, profileExampleRecord, profileFieldRows, removeProfileField,
  schemaForKind, updateProfileField,
} from './profileEditorModel'

const profile: GenericProfileContract = {
  name: 'Records', semantic_class: 'record', fields: [
    { key: 'sources', source_labels: ['Reported sources'], value_schema: { kind: 'array', items: {
      kind: 'object', fields: [{ key: 'name', value_schema: { kind: 'string' } }],
    } } },
    { key: 'confirmed', value_schema: { kind: 'boolean' } },
  ], validator_mappings: [],
}

describe('profile editor model', () => {
  it('addresses fields by index while projecting canonical paths and error locations', () => {
    const rows = profileFieldRows(profile)
    expect(rows[1]).toMatchObject({ address: [0, 0], canonicalPath: 'attributes.sources[].name',
      schemaPath: 'fields[0].value_schema.items.fields[0]' })
    const edited = updateProfileField(profile, [0, 0], { key: 'unsubmitted key text' })
    expect(profileFieldRows(edited)[1].field.key).toBe('unsubmitted key text')
    expect(profileFieldRows(profile)[1].field.key).toBe('name')
  })

  it('adds nested children and unique default keys without mutating the saved contract', () => {
    const next = addProfileField(addProfileField(profile, [0]), [0])
    expect(profileFieldRows(next).map((row) => row.field.key)).toEqual(['sources', 'name', 'new_field', 'new_field_2', 'confirmed'])
    expect(profileFieldRows(profile)).toHaveLength(3)
    expect(() => addProfileField(profile, [1])).toThrow('Select a group')
  })

  it('duplicates nested structures but does not duplicate sibling source aliases', () => {
    const next = duplicateProfileField(profile, [0])
    expect(next.fields[1]).toMatchObject({ key: 'sources_copy', source_labels: [], value_schema: profile.fields[0].value_schema })
    expect(next.fields[1].value_schema).not.toBe(profile.fields[0].value_schema)
  })

  it('moves/removes whole field subtrees without silently retargeting mappings', () => {
    const next = moveProfileField(profile, [0], 1)
    expect(next.fields.map((field) => field.key)).toEqual(['confirmed', 'sources'])
    expect(removeProfileField(next, [1]).fields.map((field) => field.key)).toEqual(['confirmed'])
    expect(moveProfileField(profile, [0], -1)).toEqual(profile)
    expect(profile.fields[0].key).toBe('sources')
  })

  it('generates typed neutral preview data using canonical keys only', () => {
    expect(profileExampleRecord(profile)).toMatchObject({ semantic_class: 'record', attributes: {
      sources: [{ name: 'Example text' }], confirmed: true,
    } })
    expect(JSON.stringify(profileExampleRecord(profile))).not.toContain('Reported sources')
    expect(schemaForKind('repeating_group')).toEqual({ kind: 'array', items: { kind: 'object', fields: [] } })
  })

  it('renders exact changed aliases, removed fields, metadata, and added values without owning proposal lifecycle', () => {
    const candidate = { ...profile, name: 'New name', fields: [
      { ...profile.fields[0], source_labels: ['New source heading'] },
    ] }
    expect(compareProfileDrafts(profile, candidate)).toEqual(expect.arrayContaining([
      { path: 'name', kind: 'changed', before: 'Records', after: 'New name' },
      { path: 'fields[0].source_labels[0]', kind: 'changed', before: 'Reported sources', after: 'New source heading' },
      { path: 'fields[1]', kind: 'removed', before: profile.fields[1] },
    ]))
    expect(compareProfileDrafts(profile, profile)).toEqual([])
    const draft = profileCandidateToDraft(candidate)
    draft.fields[0].source_labels!.push('Another heading')
    expect(candidate.fields[0].source_labels).toEqual(['New source heading'])
  })
})
