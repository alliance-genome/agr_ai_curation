import React, { useState, useEffect } from 'react';
import {
  useConnectionsHealth,
  getUnhealthyServices,
  ServiceHealthStatus,
} from '../services/adminService';

type HealthStatusLevel = 'healthy' | 'degraded' | 'unhealthy';

/**
 * ConnectionsHealthBanner Component
 *
 * Displays a warning toast in the bottom-left corner when external services
 * are unhealthy or unavailable.
 *
 * Features:
 * - Polls /api/admin/health/connections every minute
 * - Shows warning for degraded status (optional services down)
 * - Shows error for unhealthy status (required services down)
 * - Dismissible with X button
 * - Reappears if status worsens after being dismissed
 * - Lists affected services with error messages
 */
const ConnectionsHealthBanner: React.FC = () => {
  // Track which status level was dismissed (null = not dismissed)
  const [dismissedLevel, setDismissedLevel] = useState<HealthStatusLevel | null>(null);
  const { data: health, isLoading, error } = useConnectionsHealth();

  const currentStatus = health?.status as HealthStatusLevel | undefined;

  // Reset dismissed state if status worsens (degraded -> unhealthy)
  useEffect(() => {
    if (dismissedLevel && currentStatus) {
      // Status severity: healthy < degraded < unhealthy
      const severityOrder: HealthStatusLevel[] = ['healthy', 'degraded', 'unhealthy'];
      const dismissedSeverity = severityOrder.indexOf(dismissedLevel);
      const currentSeverity = severityOrder.indexOf(currentStatus);

      // If current status is worse than what was dismissed, show banner again
      if (currentSeverity > dismissedSeverity) {
        setDismissedLevel(null);
      }
    }
  }, [currentStatus, dismissedLevel]);

  // Don't show if loading or no health data
  if (isLoading || !health) {
    return null;
  }

  // Only show when there are issues
  if (health.status === 'healthy') {
    return null;
  }

  // Don't show if this status level (or worse) was dismissed
  if (dismissedLevel === currentStatus) {
    return null;
  }

  const unhealthyServices = getUnhealthyServices(health);

  // Choose styling based on severity
  const isError = health.status === 'unhealthy';
  const backgroundColor = isError ? '#f44336' : '#ff9800';
  const borderColor = isError ? '#d32f2f' : '#f57c00';
  const title = isError
    ? 'Service Unavailable'
    : 'Service Degraded';
  const message = isError
    ? 'Required services are unavailable. Some features may not work.'
    : 'Some optional services are unavailable. Core features still work.';

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 80, // Above MaintenanceBanner if both showing
        left: 20,
        zIndex: 9999,
        backgroundColor,
        color: '#fff',
        padding: '1rem',
        borderRadius: '8px',
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.25)',
        maxWidth: '400px',
        border: `2px solid ${borderColor}`,
      }}
    >
      {/* Close button */}
      <button
        onClick={() => setDismissedLevel(currentStatus || null)}
        style={{
          position: 'absolute',
          top: 8,
          right: 8,
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          padding: 4,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: '4px',
          color: '#fff',
        }}
        aria-label="Dismiss"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
        </svg>
      </button>

      {/* Content */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: '0.75rem', paddingRight: '1.5rem' }}>
        {/* Warning/Error icon */}
        <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" style={{ flexShrink: 0, marginTop: 2 }}>
          {isError ? (
            // Error icon
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z" />
          ) : (
            // Warning icon
            <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z" />
          )}
        </svg>

        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, marginBottom: '0.25rem', fontSize: '0.95rem' }}>
            {title}
          </div>
          <div style={{ fontSize: '0.875rem', lineHeight: 1.4, marginBottom: '0.5rem' }}>
            {message}
          </div>

          {/* List unhealthy services */}
          {unhealthyServices.length > 0 && (
            <div style={{ fontSize: '0.8rem', opacity: 0.9 }}>
              <div style={{ marginBottom: '0.25rem', fontWeight: 500 }}>
                Affected services:
              </div>
              <ul style={{ margin: 0, paddingLeft: '1.25rem' }}>
                {unhealthyServices.slice(0, 4).map((service) => (
                  <li key={service.service_id}>
                    {service.service_id}
                    {service.required && ' (required)'}
                    {service.last_error && (
                      <span style={{ opacity: 0.8 }}> - {service.last_error}</span>
                    )}
                  </li>
                ))}
                {unhealthyServices.length > 4 && (
                  <li>...and {unhealthyServices.length - 4} more</li>
                )}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ConnectionsHealthBanner;
