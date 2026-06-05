import type { CurationDraftField } from '@/features/curation/types'

export type RenderAs =
  | 'default'
  | 'json'
  | 'chip'
  | 'curie-chip'
  | 'sub-table'
  | 'evidence-locator'
  | 'term-chip'
  | 'divergence'
  | 'notes'

const KNOWN_RENDERERS = new Set<RenderAs>([
  'chip',
  'curie-chip',
  'sub-table',
  'evidence-locator',
  'term-chip',
  'divergence',
  'notes',
  'json',
  'default',
])

export function fieldHints(field: CurationDraftField): Record<string, unknown> {
  const metadata = field.metadata
  const nested = metadata.field_metadata
  return nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : {}
}

export function resolveRenderAs(field: CurationDraftField): RenderAs {
  const hint = fieldHints(field).render_as

  if (typeof hint === 'string' && KNOWN_RENDERERS.has(hint as RenderAs)) {
    return hint as RenderAs
  }

  if (
    field.field_type === 'array' ||
    field.field_type === 'object' ||
    field.field_type === 'json'
  ) {
    return 'json'
  }

  return 'default'
}

export { default as ChipFieldValue } from './ChipFieldValue'
export { default as CurieChipFieldValue } from './CurieChipFieldValue'
export { default as DivergenceFieldValue } from './DivergenceFieldValue'
export { default as EvidenceLocatorFieldValue } from './EvidenceLocatorFieldValue'
export { default as SubTableFieldValue } from './SubTableFieldValue'
