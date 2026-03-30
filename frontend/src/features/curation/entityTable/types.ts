export const ENTITY_TAG_DECISIONS = ['pending', 'accepted', 'rejected'] as const
export type EntityTagDecision = (typeof ENTITY_TAG_DECISIONS)[number]

export const ENTITY_TAG_SOURCES = ['ai', 'manual'] as const
export type EntityTagSource = (typeof ENTITY_TAG_SOURCES)[number]

export const DB_VALIDATION_STATUSES = ['validated', 'ambiguous', 'not_found'] as const
export type DbValidationStatus = (typeof DB_VALIDATION_STATUSES)[number]

export const ENTITY_TYPE_CODES = [
  'ATP:0000005', // gene
  'ATP:0000006', // allele
  'ATP:0000123', // species
  'ATP:0000027', // strain
  'ATP:0000025', // genotype
  'ATP:0000026', // fish
  'ATP:0000013', // transgenic construct
  'ATP:0000110', // transgenic allele
  'ATP:0000285', // classical allele
  'ATP:0000093', // sequence targeting reagent
] as const
export type EntityTypeCode = (typeof ENTITY_TYPE_CODES)[number]

export const ENTITY_TYPE_LABELS: Record<EntityTypeCode, string> = {
  'ATP:0000005': 'gene',
  'ATP:0000006': 'allele',
  'ATP:0000123': 'species',
  'ATP:0000027': 'strain',
  'ATP:0000025': 'genotype',
  'ATP:0000026': 'fish',
  'ATP:0000013': 'transgenic construct',
  'ATP:0000110': 'transgenic allele',
  'ATP:0000285': 'classical allele',
  'ATP:0000093': 'sequence targeting reagent',
}

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
