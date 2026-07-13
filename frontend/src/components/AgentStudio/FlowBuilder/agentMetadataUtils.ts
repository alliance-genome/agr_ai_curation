import type { AgentMetadata } from '@/services/agentStudioService'

import { isExtractionAgent, isValidationAgent } from './smartDefaultUtils'

type AgentMetadataLookup = Record<string, AgentMetadata>

const SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS = new Set([
  'chat_output',
  'chat_output_formatter',
  'csv_formatter',
  'tsv_formatter',
  'json_formatter',
])

interface MetadataClassification {
  categoryIncludes?: string[]
  subcategoryIncludes?: string[]
}

const normalizeMetadataValue = (value?: string): string => (value || '').trim().toLowerCase()

const matchesAnyClassification = (value: string, candidates: string[]): boolean =>
  candidates.some((candidate) => value.includes(candidate))

const matchesMetadataClassification = (
  agentId: string,
  agentMetadata: AgentMetadataLookup,
  classification: MetadataClassification
): boolean => {
  const metadata = agentMetadata[agentId]
  if (!metadata) return false

  const category = normalizeMetadataValue(metadata.category)
  const subcategory = normalizeMetadataValue(metadata.subcategory)
  const categoryIncludes = classification.categoryIncludes ?? []
  const subcategoryIncludes = classification.subcategoryIncludes ?? []

  return (
    matchesAnyClassification(category, categoryIncludes) ||
    matchesAnyClassification(subcategory, subcategoryIncludes)
  )
}

export const isExtractionAgentFromMetadata = (
  agentId: string,
  agentMetadata: AgentMetadataLookup
): boolean => {
  if (isExtractionAgent(agentId)) return true

  return matchesMetadataClassification(agentId, agentMetadata, {
    categoryIncludes: ['extract'],
    subcategoryIncludes: ['pdf extraction'],
  })
}

export const isValidationAgentFromMetadata = (
  agentId: string,
  agentMetadata: AgentMetadataLookup
): boolean => {
  if (isValidationAgent(agentId)) return true

  return matchesMetadataClassification(agentId, agentMetadata, {
    categoryIncludes: ['validation'],
    subcategoryIncludes: ['data validation'],
  })
}

export const isOutputFormatterAgentFromMetadata = (
  agentId: string,
  _agentMetadata: AgentMetadataLookup
): boolean => SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS.has(agentId)

export const resolveOutputFormatterIncludeEvidence = (
  agentId: string,
  agentMetadata: AgentMetadataLookup,
  includeEvidence?: boolean | null
): boolean | undefined => {
  if (!isOutputFormatterAgentFromMetadata(agentId, agentMetadata)) {
    return undefined
  }

  return includeEvidence !== false
}
