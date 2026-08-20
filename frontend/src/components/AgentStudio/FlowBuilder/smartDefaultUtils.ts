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

/**
 * Check if an agent is an extraction agent.
 * Extraction agents produce raw data from documents.
 */
export const isExtractionAgent = (agentId: string): boolean =>
  EXTRACTION_AGENTS.includes(agentId)

// =============================================================================
// Extractor Count Helpers
// =============================================================================

/**
 * Count the number of extraction agents in the flow.
 */
export const countExtractors = (nodes: AgentNode[]): number => {
  return nodes.filter(n => isExtractionAgent(n.data.agent_id)).length
}
