import React, { useState, useEffect } from 'react';
import { Box, IconButton, Stack, Typography } from '@mui/material';
import {
  Close as CloseIcon,
  ErrorOutline as ErrorIcon,
  WarningAmber as WarningIcon,
} from '@mui/icons-material';
import { alpha, useTheme } from '@mui/material/styles';
import {
  useConnectionsHealth,
  getUnhealthyServices,
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
  const theme = useTheme();
  // Track which status level was dismissed (null = not dismissed)
  const [dismissedLevel, setDismissedLevel] = useState<HealthStatusLevel | null>(null);
  const { data: health, isLoading } = useConnectionsHealth();

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
  const backgroundColor = isError ? theme.palette.error.main : theme.palette.warning.main;
  const borderColor = isError ? theme.palette.error.dark : theme.palette.warning.dark;
  const contrastColor = theme.palette.getContrastText(backgroundColor);
  const title = isError
    ? 'Service Unavailable'
    : 'Service Degraded';
  const message = isError
    ? 'Required services are unavailable. Some features may not work.'
    : 'Some optional services are unavailable. Core features still work.';

  return (
    <Box
      role="status"
      sx={{
        position: 'fixed',
        bottom: 80, // Above MaintenanceBanner if both showing
        left: 20,
        zIndex: 9999,
        backgroundColor,
        color: contrastColor,
        padding: '1rem',
        borderRadius: 1,
        boxShadow: `0 4px 12px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.35 : 0.18)}`,
        maxWidth: '400px',
        border: `2px solid ${borderColor}`,
      }}
    >
      <IconButton
        onClick={() => setDismissedLevel(currentStatus || null)}
        size="small"
        sx={{
          position: 'absolute',
          top: 8,
          right: 8,
          color: contrastColor,
          backgroundColor: alpha(contrastColor, 0.08),
          '&:hover': {
            backgroundColor: alpha(contrastColor, 0.16),
          },
          '&:focus-visible': {
            outline: `2px solid ${alpha(contrastColor, 0.75)}`,
            outlineOffset: 2,
          },
        }}
        aria-label="Dismiss"
      >
        <CloseIcon fontSize="small" />
      </IconButton>

      <Stack direction="row" alignItems="flex-start" spacing={1.5} sx={{ pr: 3 }}>
        {isError ? (
          <ErrorIcon sx={{ flexShrink: 0, mt: 0.25 }} />
        ) : (
          <WarningIcon sx={{ flexShrink: 0, mt: 0.25 }} />
        )}

        <Box sx={{ flex: 1 }}>
          <Typography component="div" sx={{ fontWeight: 600, mb: 0.25, fontSize: '0.95rem' }}>
            {title}
          </Typography>
          <Typography component="div" sx={{ fontSize: '0.875rem', lineHeight: 1.4, mb: 0.5 }}>
            {message}
          </Typography>

          {/* List unhealthy services */}
          {unhealthyServices.length > 0 && (
            <Box sx={{ fontSize: '0.8rem', opacity: 0.9 }}>
              <Typography component="div" sx={{ mb: 0.25, fontWeight: 500, fontSize: 'inherit' }}>
                Affected services:
              </Typography>
              <Box component="ul" sx={{ m: 0, pl: '1.25rem' }}>
                {unhealthyServices.slice(0, 4).map((service) => (
                  <li key={service.service_id}>
                    {service.service_id}
                    {service.required && ' (required)'}
                    {service.last_error && (
                      <Box component="span" sx={{ opacity: 0.8 }}> - {service.last_error}</Box>
                    )}
                  </li>
                ))}
                {unhealthyServices.length > 4 && (
                  <li>...and {unhealthyServices.length - 4} more</li>
                )}
              </Box>
            </Box>
          )}
        </Box>
      </Stack>
    </Box>
  );
};

export default ConnectionsHealthBanner;
