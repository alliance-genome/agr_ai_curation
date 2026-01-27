/**
 * Smart Default Utilities for Flow Builder
 *
 * These utilities help configure validator agents to use extractor output
 * by default, rather than the previous validator's output.
 */

import type { AgentNode } from './types'

// =============================================================================
// Agent Category Helpers
// =============================================================================

/** Extraction agents produce raw data from documents */
export const EXTRACTION_AGENTS = ['pdf', 'gene_expression']

/** Validation agents validate/lookup entities - should typically use extractor output */
export const VALIDATION_AGENTS = [
  'gene', 'allele', 'disease', 'chemical',
  'gene_ontology', 'go_annotations', 'orthologs', 'ontology_mapping'
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
  edges: { source: string; target: string }[]
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

    if (isExtractionAgent(node.data.agent_id)) {
      return node
    }

    // Add parents to queue
    const parents = incomingByTarget.get(nodeId) || []
    queue.push(...parents)
  }

  // Fallback: find any extractor in the graph (most recent by node ID)
  const extractors = nodes
    .filter(n => isExtractionAgent(n.data.agent_id))
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
export const getExtractors = (nodes: AgentNode[]): AgentNode[] => {
  return nodes.filter(n => isExtractionAgent(n.data.agent_id))
}

/**
 * Check if a validator's custom_input explicitly references an EXISTING extractor output.
 * Returns false if the referenced extractor has been deleted.
 */
export const validatorHasExplicitExtractorInput = (
  node: AgentNode,
  extractors: AgentNode[]
): boolean => {
  if (node.data.input_source !== 'custom' || !node.data.custom_input) {
    return false
  }
  // Check if custom_input contains an EXISTING extractor's output_key
  // (not a deleted one)
  return extractors.some(ext =>
    node.data.custom_input?.includes(`{{${ext.data.output_key}}}`)
  )
}

/**
 * Check if a validator needs configuration (topology-aware).
 * Uses BFS to find connected upstream extractors first, then falls back to global count.
 * Returns { needsConfig: boolean, reason?: string }
 */
export const validatorNeedsConfiguration = (
  validatorId: string,
  nodes: AgentNode[],
  edges: { source: string; target: string }[]
): { needsConfig: boolean; reason?: string } => {
  const validator = nodes.find(n => n.id === validatorId)
  if (!validator || !isValidationAgent(validator.data.agent_id)) {
    return { needsConfig: false }
  }

  const extractors = getExtractors(nodes)

  // If validator already has explicit extractor input, it's configured
  if (validatorHasExplicitExtractorInput(validator, extractors)) {
    return { needsConfig: false }
  }

  // Step 1: Check topology via BFS for connected upstream extractor
  // Note: findNearestExtractor has fallback behavior that returns ANY extractor
  // even if not connected. We need to check if it's actually connected.
  // For this, we check if there's actually a path via edges
  const hasConnectedUpstream = (() => {
    const nodesById = new Map(nodes.map(n => [n.id, n]))
    const incomingByTarget = new Map<string, string[]>()
    edges.forEach(e => {
      const existing = incomingByTarget.get(e.target) || []
      incomingByTarget.set(e.target, [...existing, e.source])
    })

    // BFS to find if there's actually a connected extractor
    const queue = [...(incomingByTarget.get(validatorId) || [])]
    const visited = new Set<string>()

    while (queue.length > 0) {
      const nodeId = queue.shift()!
      if (visited.has(nodeId)) continue
      visited.add(nodeId)

      const node = nodesById.get(nodeId)
      if (!node) continue

      if (isExtractionAgent(node.data.agent_id)) {
        return true // Found connected extractor
      }

      const parents = incomingByTarget.get(nodeId) || []
      queue.push(...parents)
    }
    return false
  })()

  if (hasConnectedUpstream) {
    // Found a connected upstream extractor - no ambiguity
    return { needsConfig: false }
  }

  // Step 2: No upstream extractor found via edges, check global count
  const extractorCount = extractors.length

  if (extractorCount === 0) {
    // No extractors at all - user must configure manually, but no error
    return { needsConfig: false }
  } else if (extractorCount === 1) {
    // Single extractor exists but not connected - fallback behavior handles it
    return { needsConfig: false }
  } else {
    // Multiple extractors exist, none connected - ambiguous
    return {
      needsConfig: true,
      reason: 'Ambiguous input source: Multiple extractors detected. Please explicitly select one.'
    }
  }
}
