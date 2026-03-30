import type { EntityTypeCode } from './literatureEntityTypeCatalog'

export const ENTITY_TAG_DECISIONS = ['pending', 'accepted', 'rejected'] as const
export type EntityTagDecision = (typeof ENTITY_TAG_DECISIONS)[number]

export const ENTITY_TAG_SOURCES = ['ai', 'manual'] as const
export type EntityTagSource = (typeof ENTITY_TAG_SOURCES)[number]

export const DB_VALIDATION_STATUSES = ['validated', 'ambiguous', 'not_found'] as const
export type DbValidationStatus = (typeof DB_VALIDATION_STATUSES)[number]

export {
  ENTITY_TYPE_CODES,
  ENTITY_TYPE_LABELS,
  getEntityTypeLabel,
  isEntityTypeCode,
  type EntityTypeCode,
} from './literatureEntityTypeCatalog'

export interface EntityTagEvidence {
  sentence_text: string
  page_number: number | null
  section_title: string | null
  chunk_ids: string[]
}

export interface EntityTag {
  tag_id: string
  entity_name: string
  entity_type: EntityTypeCode | string
  species: string
  topic: string
  db_status: DbValidationStatus
  db_entity_id: string | null
  source: EntityTagSource
  decision: EntityTagDecision
  evidence: EntityTagEvidence | null
  notes: string | null
}
