import React, { useState, useEffect } from 'react'
import { Box, IconButton, Stack, Typography } from '@mui/material'
import { Close as CloseIcon, WarningAmber as WarningIcon } from '@mui/icons-material'
import { alpha, useTheme } from '@mui/material/styles'

/**
 * MaintenanceBanner Component
 *
 * Displays a toast notification in the bottom-left corner when a maintenance
 * message is configured in config/maintenance_message.txt.
 *
 * Features:
 * - Bottom-left positioned toast (doesn't interfere with navigation or chat)
 * - Dismissible with X button
 * - Fetches message on mount and every 5 minutes
 * - Yellow/orange theme matching site warnings
 */
const MaintenanceBanner: React.FC = () => {
  const theme = useTheme()
  const [message, setMessage] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [dismissed, setDismissed] = useState(false)

  const fetchMaintenanceMessage = async () => {
    try {
      const response = await fetch('/api/maintenance/message')
      if (response.ok) {
        const data = await response.json()
        setMessage(data.active ? data.message : null)
      } else {
        setMessage(null)
      }
    } catch (error) {
      console.warn('Failed to fetch maintenance message:', error)
      setMessage(null)
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchMaintenanceMessage()
    const interval = setInterval(fetchMaintenanceMessage, 5 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  if (isLoading || !message || dismissed) {
    return null
  }

  const backgroundColor = theme.palette.warning.main
  const borderColor = theme.palette.warning.dark
  const contrastColor = theme.palette.getContrastText(backgroundColor)

  return (
    <Box
      role="status"
      sx={{
        position: 'fixed',
        bottom: 20,
        left: 20,
        zIndex: 10000,
        backgroundColor,
        color: contrastColor,
        padding: '1rem',
        borderRadius: 1,
        boxShadow: `0 4px 12px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.35 : 0.18)}`,
        maxWidth: '360px',
        border: `2px solid ${borderColor}`,
      }}
    >
      <IconButton
        onClick={() => setDismissed(true)}
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
        <WarningIcon sx={{ flexShrink: 0, mt: 0.25 }} />
        <Box>
          <Typography component="div" sx={{ fontWeight: 600, mb: 0.25, fontSize: '0.95rem' }}>
            Scheduled Maintenance
          </Typography>
          <Typography component="div" sx={{ fontSize: '0.875rem', lineHeight: 1.4 }}>
            {message}
          </Typography>
        </Box>
      </Stack>
    </Box>
  )
}

export default MaintenanceBanner
