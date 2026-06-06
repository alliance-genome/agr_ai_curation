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

type AgentPredicate = (agentId: string) => boolean

// =============================================================================
// Extractor Finding Logic
// =============================================================================

/**
 * Find the nearest upstream extractor by traversing edges (BFS).
 * Falls back to most recent extractor in graph if no path exists.
 *
 * @param targetNodeId - The node ID to find an extractor for
 * @param nodes - All nodes in the graph
 * @param edges - All edges in the graph
 * @returns The nearest extractor node, or null if none found
 */
export const findNearestExtractor = (
  targetNodeId: string,
  nodes: AgentNode[],
  edges: { source: string; target: string }[],
  isExtractionPredicate: AgentPredicate = isExtractionAgent
): AgentNode | null => {
  // Build lookup maps
  const nodesById = new Map(nodes.map(n => [n.id, n]))
  const incomingByTarget = new Map<string, string[]>()
  edges.forEach(e => {
    const existing = incomingByTarget.get(e.target) || []
    incomingByTarget.set(e.target, [...existing, e.source])
  })

  // BFS upstream from target
  const queue = [...(incomingByTarget.get(targetNodeId) || [])]
  const visited = new Set<string>()

  while (queue.length > 0) {
    const nodeId = queue.shift()!
    if (visited.has(nodeId)) continue
    visited.add(nodeId)

    const node = nodesById.get(nodeId)
    if (!node) continue

    if (isExtractionPredicate(node.data.agent_id)) {
      return node
    }

    // Add parents to queue
    const parents = incomingByTarget.get(nodeId) || []
    queue.push(...parents)
  }

  // Fallback: find any extractor in the graph (most recent by node ID)
  const extractors = nodes
    .filter(n => isExtractionPredicate(n.data.agent_id))
    .sort((a, b) => {
      // Sort by node ID number descending (most recent first)
      const aNum = parseInt(a.id.replace('node_', '')) || 0
      const bNum = parseInt(b.id.replace('node_', '')) || 0
      return bNum - aNum
    })

  return extractors[0] || null
}

// =============================================================================
// Extractor Count Helpers
// =============================================================================

/**
 * Count the number of extraction agents in the flow.
 */
export const countExtractors = (nodes: AgentNode[]): number => {
  return nodes.filter(n => isExtractionAgent(n.data.agent_id)).length
}

/**
 * Get all extraction agents in the flow.
 */
export const getExtractors = (
  nodes: AgentNode[],
  isExtractionPredicate: AgentPredicate = isExtractionAgent
): AgentNode[] => {
  return nodes.filter(n => isExtractionPredicate(n.data.agent_id))
}
