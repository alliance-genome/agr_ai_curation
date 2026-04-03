import type { AgentMetadata } from '@/services/agentStudioService'

import { isExtractionAgent, isValidationAgent } from './smartDefaultUtils'

type AgentMetadataLookup = Record<string, AgentMetadata>

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
  agentMetadata: AgentMetadataLookup
): boolean =>
  matchesMetadataClassification(agentId, agentMetadata, {
    categoryIncludes: ['output'],
    subcategoryIncludes: ['output', 'format'],
  })
