/**
 * Hook to get agent icon from registry metadata.
 *
 * Usage:
 *   const icon = useAgentIcon('gene_validation')  // Returns "🧬"
 *   const icon = useAgentIcon('unknown')  // Returns "❓"
 */

import { useAgentMetadata } from '../contexts/AgentMetadataContext'

/** Default fallback icon for unknown agents */
const DEFAULT_ICON = '❓'

/**
 * Get the icon for an agent from the registry metadata.
 *
 * @param agentId - The agent ID (e.g., 'gene_validation', 'allele_validation')
 * @returns The agent's icon emoji, or DEFAULT_ICON if not found
 */
export function useAgentIcon(agentId: string | undefined): string {
  const { agents, isLoading } = useAgentMetadata()

  // Return default while loading or if no agentId
  if (isLoading || !agentId) {
    return DEFAULT_ICON
  }

  // Look up agent in metadata
  const agent = agents[agentId]
  return agent?.icon ?? DEFAULT_ICON
}

/**
 * Get the name for an agent from the registry metadata.
 *
 * @param agentId - The agent ID (e.g., 'gene_validation', 'allele_validation')
 * @returns The agent's display name, or the agentId if not found
 */
export function useAgentName(agentId: string | undefined): string {
  const { agents, isLoading } = useAgentMetadata()

  if (isLoading || !agentId) {
    return agentId ?? 'Unknown Agent'
  }

  const agent = agents[agentId]
  return agent?.name ?? agentId
}

/**
 * Get full metadata for an agent.
 *
 * @param agentId - The agent ID
 * @returns The full agent metadata, or undefined if not found
 */
export function useAgentMetadataById(agentId: string | undefined) {
  const { agents, isLoading } = useAgentMetadata()

  if (isLoading || !agentId) {
    return undefined
  }

  return agents[agentId]
}
