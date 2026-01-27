/**
 * Context for agent metadata from AGENT_REGISTRY.
 *
 * Provides icons, names, and categories for all agents.
 * Fetches from /api/agent-studio/registry/metadata on mount.
 */

import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import {
  fetchRegistryMetadata,
  AgentMetadata,
  RegistryMetadataResponse,
} from '../services/agentStudioService'
import { logger } from '../services/logger'

/**
 * Context value type
 */
interface AgentMetadataContextValue {
  /** Agent metadata indexed by agent ID */
  agents: Record<string, AgentMetadata>
  /** Loading state */
  isLoading: boolean
  /** Error message if fetch failed */
  error: string | null
  /** Refresh metadata from API */
  refresh: () => Promise<void>
}

const AgentMetadataContext = createContext<AgentMetadataContextValue | undefined>(undefined)

interface AgentMetadataProviderProps {
  children: ReactNode
}

/**
 * Provider component that fetches and caches agent metadata.
 */
export const AgentMetadataProvider: React.FC<AgentMetadataProviderProps> = ({ children }) => {
  const [agents, setAgents] = useState<Record<string, AgentMetadata>>({})
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchMetadata = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const response = await fetchRegistryMetadata()
      setAgents(response.agents)
      logger.debug('Agent metadata loaded', {
        component: 'AgentMetadataContext',
        agentCount: Object.keys(response.agents).length,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load agent metadata'
      setError(message)
      logger.error('Failed to fetch agent metadata', {
        component: 'AgentMetadataContext',
        error: message,
      })
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchMetadata()
  }, [])

  const value: AgentMetadataContextValue = {
    agents,
    isLoading,
    error,
    refresh: fetchMetadata,
  }

  return (
    <AgentMetadataContext.Provider value={value}>
      {children}
    </AgentMetadataContext.Provider>
  )
}

/**
 * Hook to access agent metadata context.
 * Must be used within AgentMetadataProvider.
 */
export function useAgentMetadata(): AgentMetadataContextValue {
  const context = useContext(AgentMetadataContext)
  if (context === undefined) {
    throw new Error('useAgentMetadata must be used within AgentMetadataProvider')
  }
  return context
}
