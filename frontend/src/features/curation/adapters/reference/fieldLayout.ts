import type { CurationAdapterFieldLayoutEntry } from '../types'

export const REFERENCE_ADAPTER_KEY = 'reference'

export const REFERENCE_FIELD_LAYOUT: readonly CurationAdapterFieldLayoutEntry[] = [
  {
    fieldPath: 'citation.title',
    label: 'Title',
    groupKey: 'citation_details',
    groupLabel: 'Citation details',
    order: 0,
  },
  {
    fieldPath: 'citation.authors',
    label: 'Authors',
    groupKey: 'citation_details',
    groupLabel: 'Citation details',
    order: 10,
    widget: 'reference_author_list',
  },
  {
    fieldPath: 'citation.journal',
    label: 'Journal',
    groupKey: 'citation_details',
    groupLabel: 'Citation details',
    order: 20,
  },
  {
    fieldPath: 'citation.publication_year',
    label: 'Publication year',
    groupKey: 'citation_details',
    groupLabel: 'Citation details',
    order: 30,
  },
  {
    fieldPath: 'citation.reference_type',
    label: 'Reference type',
    groupKey: 'citation_details',
    groupLabel: 'Citation details',
    order: 40,
  },
  {
    fieldPath: 'identifiers.doi',
    label: 'DOI',
    groupKey: 'identifiers',
    groupLabel: 'Identifiers',
    order: 100,
  },
  {
    fieldPath: 'identifiers.pmid',
    label: 'PMID',
    groupKey: 'identifiers',
    groupLabel: 'Identifiers',
    order: 110,
  },
] as const

export const REFERENCE_FIELD_LAYOUT_BY_PATH = new Map(
  REFERENCE_FIELD_LAYOUT.map((field) => [field.fieldPath, field]),
)
