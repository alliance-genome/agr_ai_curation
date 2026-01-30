/**
 * Admin Service - API hooks for admin endpoints
 *
 * Provides hooks for:
 * - Connection health monitoring
 */

import { useQuery, UseQueryOptions } from '@tanstack/react-query';

// =============================================================================
// Types
// =============================================================================

export interface ServiceHealthStatus {
  service_id: string;
  description: string;
  url: string;
  required: boolean;
  is_healthy: boolean | null;
  last_error: string | null;
}

export interface ConnectionsHealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  total_services: number;
  healthy_count: number;
  unhealthy_count: number;
  unknown_count: number;
  required_healthy: boolean;
  services: Record<string, ServiceHealthStatus>;
}

// =============================================================================
// API Functions
// =============================================================================

const fetchConnectionsHealth = async (): Promise<ConnectionsHealthResponse> => {
  const response = await fetch('/api/admin/health/connections', {
    credentials: 'include', // Include httpOnly cookies for authentication
  });

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
 * Hook to fetch and monitor external service connection health.
 *
 * Returns health status for all services defined in config/connections.yaml.
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
    refetchInterval: 60_000, // Check every minute
    retry: 1, // Retry once on failure
    staleTime: 30_000, // Consider data stale after 30 seconds
    ...options,
  });

/**
 * Get unhealthy services from the health response.
 *
 * @param health - ConnectionsHealthResponse from useConnectionsHealth
 * @returns Array of unhealthy service statuses
 */
export const getUnhealthyServices = (
  health: ConnectionsHealthResponse | undefined
): ServiceHealthStatus[] => {
  if (!health) return [];

  return Object.values(health.services).filter(
    (service) => service.is_healthy === false
  );
};

/**
 * Get unhealthy required services from the health response.
 *
 * @param health - ConnectionsHealthResponse from useConnectionsHealth
 * @returns Array of unhealthy required service statuses
 */
export const getUnhealthyRequiredServices = (
  health: ConnectionsHealthResponse | undefined
): ServiceHealthStatus[] => {
  if (!health) return [];

  return Object.values(health.services).filter(
    (service) => service.required && service.is_healthy === false
  );
};
