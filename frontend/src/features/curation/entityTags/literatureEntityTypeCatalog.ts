// Temporary literature UI entity-type catalog for the entity-table redesign.
// TODO: Replace this local catalog with a live source-of-truth lookup from the
// literature UI/database so we stop maintaining these codes by hand here.

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

export function isEntityTypeCode(value: string): value is EntityTypeCode {
  return ENTITY_TYPE_CODES.includes(value as EntityTypeCode)
}

const ENTITY_TYPE_ALIASES = Object.fromEntries(
  Object.entries(ENTITY_TYPE_LABELS).map(([code, label]) => [label, code as EntityTypeCode]),
) as Record<string, EntityTypeCode>

function normalizeEntityTypeAlias(value: string): string {
  return value.trim().toLowerCase()
}

export function resolveEntityTypeCode(entityType: string): EntityTypeCode | null {
  if (isEntityTypeCode(entityType)) {
    return entityType
  }

  return ENTITY_TYPE_ALIASES[normalizeEntityTypeAlias(entityType)] ?? null
}

export function getEntityTypeLabel(entityType: string): string {
  const entityTypeCode = resolveEntityTypeCode(entityType)
  if (entityTypeCode !== null) {
    return ENTITY_TYPE_LABELS[entityTypeCode]
  }

  throw new Error(
    `Unknown entity type code "${entityType}" - add it to ENTITY_TYPE_LABELS in literatureEntityTypeCatalog.ts`,
  )
}
