import type { GenericProfileContract, ProfileValidatorMapping } from '@/services/genericProfileService'
import { profileFieldRows, type ProfileFieldAddress } from './profileEditorModel'

/** Persisted canonical keys, never display labels or positional table indices. */
export function profileFieldPath(value: GenericProfileContract, address: ProfileFieldAddress): string {
  const rows = profileFieldRows(value)
  return 'attributes.' + address.map((_, i) => {
    const field = rows.find(row => row.address.join('.') === address.slice(0, i + 1).join('.'))!.field
    return field.key + (field.value_schema.kind === 'array' && i < address.length - 1 ? '[]' : '')
  }).join('.')
}
export function mappingUsesField(mapping: ProfileValidatorMapping, path: string): boolean {
  return Object.values(mapping.inputs).some(input => (input.source ?? 'field') === 'field' && (input.field_path === path || input.field_path === `${path}[]`))
}
export function friendlyValidatorName(value: string): string {
  return (value.includes('--custom--') ? 'Custom ' + value.split('--custom--')[0] : value).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
}
