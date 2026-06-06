/**
 * Smart Default Utilities for Flow Builder
 *
 * These utilities classify flow nodes and inspect extractor topology.
 */

import type { AgentNode } from './types'

// =============================================================================
// Agent Category Helpers
// =============================================================================

/** Extraction agents produce raw data from documents */
export const EXTRACTION_AGENTS = ['pdf_extraction', 'gene_expression']

/** Validation agents validate/lookup entities - should typically use extractor output */
export const VALIDATION_AGENTS = [
  'gene', 'allele', 'disease', 'chemical',
  'gene_ontology', 'go_annotations', 'orthologs', 'ontology_term_validation'
]

/**
 * Check if an agent is an extraction agent.
 * Extraction agents produce raw data from documents.
 */
export const isExtractionAgent = (agentId: string): boolean =>
  EXTRACTION_AGENTS.includes(agentId)

/**
 * Check if an agent is a validation agent.
 * Validation agents validate/lookup entities and should typically use extractor output.
 */
export const isValidationAgent = (agentId: string): boolean =>
  VALIDATION_AGENTS.includes(agentId)

// =============================================================================
// Extractor Count Helpers
// =============================================================================

/**
 * Count the number of extraction agents in the flow.
 */
export const countExtractors = (nodes: AgentNode[]): number => {
  return nodes.filter(n => isExtractionAgent(n.data.agent_id)).length
}
