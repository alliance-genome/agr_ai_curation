import type { GenericProfileValueSchema } from '@/services/genericProfileService'
export function answerSummary(schema: GenericProfileValueSchema): string {
  if (schema.kind === 'array') return `Multiple ${schema.items.kind === 'object' ? 'sets of details' : answerSummary(schema.items).toLowerCase() + ' answers'}`
  return ({ string: 'Text', integer: 'Whole number', number: 'Number', boolean: 'Yes or no', enum: 'One of your choices', object: 'Related details' })[schema.kind]
}
export function answerExample(schema: GenericProfileValueSchema): string {
  if (schema.kind === 'array') return answerExample(schema.items)
  if (schema.kind === 'object') return schema.fields.map((field) => field.display_name || field.key).join(' + ')
  if (schema.kind === 'enum') return schema.values.map((choice) => choice.replaceAll('_', ' ')).join(' / ')
  return ({ string: 'Words or labels from the paper', integer: 'For example, 3', number: 'For example, 3.5', boolean: 'Yes / No' })[schema.kind]
}
