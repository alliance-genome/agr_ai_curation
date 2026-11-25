/**
 * Connection Health Service - API hooks for curator status banner
 *
 * Polls the public minimal status endpoint (`GET /api/health/connections`).
 */

import { useQuery, UseQueryOptions } from '@tanstack/react-query';

// =============================================================================
// Configuration
// =============================================================================

/**
 * Health check polling configuration.
 *
 * To customize polling interval, set VITE_HEALTH_POLL_INTERVAL_MS environment
 * variable during build (e.g., VITE_HEALTH_POLL_INTERVAL_MS=30000 for 30 seconds).
 *
 * Default: 60000ms (1 minute)
 */
const HEALTH_POLL_INTERVAL_MS = parseInt(
  import.meta.env.VITE_HEALTH_POLL_INTERVAL_MS || '60000',
  10
);

/**
 * How long data is considered fresh before refetching in background.
 * Default: 30 seconds
 */
const HEALTH_STALE_TIME_MS = parseInt(
  import.meta.env.VITE_HEALTH_STALE_TIME_MS || '30000',
  10
);

// =============================================================================
// Types
// =============================================================================

export interface UnhealthyServiceStatus {
  description: string;
  required: boolean;
}

export interface ConnectionsHealthResponse {
  observed_at: string;
  status: 'healthy' | 'degraded' | 'unhealthy';
  required_healthy: boolean;
  unhealthy_services: UnhealthyServiceStatus[];
}

// =============================================================================
// API Functions
// =============================================================================

export const fetchConnectionsHealth = async (): Promise<ConnectionsHealthResponse> => {
  const response = await fetch('/api/health/connections');

  if (!response.ok) {
    if (response.status === 503) {
      throw new Error('Service is starting up');
    }
    throw new Error('Failed to fetch connections health');
  }

  return response.json();
};

// =============================================================================
// Query Hooks
// =============================================================================

/**
 * Hook to fetch and monitor minimal public service connection health status.
 *
 * Automatically refetches every 60 seconds.
 *
 * @example
 * const { data, isLoading, error } = useConnectionsHealth();
 * if (data?.status === 'degraded') {
 *   // Show warning banner
 * }
 */
export const useConnectionsHealth = (
  options?: UseQueryOptions<ConnectionsHealthResponse>
) =>
  useQuery<ConnectionsHealthResponse>({
    queryKey: ['connections-health'],
    queryFn: fetchConnectionsHealth,
    refetchInterval: HEALTH_POLL_INTERVAL_MS,
    retry: 1, // Retry once on failure
    staleTime: HEALTH_STALE_TIME_MS,
    ...options,
  });
