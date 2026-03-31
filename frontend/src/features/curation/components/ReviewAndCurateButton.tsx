import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RateReview as RateReviewIcon } from '@mui/icons-material'
import { Button, CircularProgress, IconButton, Tooltip } from '@mui/material'
import type { ButtonProps, IconButtonProps } from '@mui/material'
import type { SxProps, Theme } from '@mui/material/styles'

import { emitGlobalToast } from '@/lib/globalNotifications'
import {
  openCurationWorkspace,
  type CurationWorkspaceLaunchTarget,
} from '@/features/curation/navigation/openCurationWorkspace'

export interface ReviewAndCurateButtonProps extends CurationWorkspaceLaunchTarget {
  label?: string
  disabled?: boolean
  iconOnly?: boolean
  size?: 'small' | 'medium' | 'large'
  variant?: ButtonProps['variant']
  color?: ButtonProps['color']
  sx?: SxProps<Theme>
  onOpened?: (sessionId: string) => void
  onError?: (message: string) => void
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Failed to open the curation workspace.'
}

export default function ReviewAndCurateButton({
  sessionId,
  documentId,
  flowRunId,
  originSessionId,
  adapterKeys,
  label = 'Review & Curate',
  disabled = false,
  iconOnly = false,
  size = 'small',
  variant = 'text',
  color = 'primary',
  sx,
  onOpened,
  onError,
}: ReviewAndCurateButtonProps) {
  const navigate = useNavigate()
  const [isOpening, setIsOpening] = useState(false)

  const handleOpen = async () => {
    if (disabled || isOpening) {
      return
    }

    setIsOpening(true)

    try {
      const openedSessionId = await openCurationWorkspace({
        sessionId,
        documentId,
        flowRunId,
        originSessionId,
        adapterKeys,
        navigate,
      })
      onOpened?.(openedSessionId)
    } catch (error) {
      const message = getErrorMessage(error)
      emitGlobalToast({
        message: `Review & Curate failed: ${message}`,
        severity: 'error',
      })
      onError?.(message)
    } finally {
      setIsOpening(false)
    }
  }

  if (iconOnly) {
    return (
      <Tooltip title={label}>
        <span>
          <IconButton
            aria-label={label}
            onClick={() => void handleOpen()}
            disabled={disabled || isOpening}
            size={size as IconButtonProps['size']}
            color={color as IconButtonProps['color']}
            sx={sx}
          >
            {isOpening ? <CircularProgress size={18} color="inherit" /> : <RateReviewIcon fontSize="small" />}
          </IconButton>
        </span>
      </Tooltip>
    )
  }

  return (
    <Button
      type="button"
      onClick={() => void handleOpen()}
      disabled={disabled || isOpening}
      size={size}
      variant={variant}
      color={color}
      sx={sx}
      startIcon={
        isOpening ? <CircularProgress size={16} color="inherit" /> : <RateReviewIcon fontSize="small" />
      }
    >
      {isOpening ? 'Opening...' : label}
    </Button>
  )
}
