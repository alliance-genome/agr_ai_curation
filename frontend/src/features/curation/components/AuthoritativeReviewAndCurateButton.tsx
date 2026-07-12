import { useState } from 'react'
import { ExpandMore as ExpandMoreIcon, RateReview as RateReviewIcon } from '@mui/icons-material'
import { Button, IconButton, Menu, MenuItem, Tooltip, Typography } from '@mui/material'
import type { ButtonProps, IconButtonProps } from '@mui/material'

import ReviewAndCurateButton, { type ReviewAndCurateButtonProps } from './ReviewAndCurateButton'

type AuthoritativeReviewAndCurateButtonProps = Omit<
  ReviewAndCurateButtonProps,
  'sessionId'
> & {
  authoritativeReviewSessionIds: string[]
  disabledReason?: string
}

function sessionChoiceLabel(sessionId: string, adapterKey: string | undefined, index: number): string {
  const adapterLabel = adapterKey?.trim().replaceAll('_', ' ')
  return adapterLabel
    ? `${adapterLabel} — ${sessionId}`
    : `Review session ${index + 1} — ${sessionId}`
}

export default function AuthoritativeReviewAndCurateButton({
  authoritativeReviewSessionIds,
  adapterKeys,
  disabledReason,
  disabled = false,
  iconOnly = false,
  label = 'Review & Curate',
  size = 'small',
  variant = 'text',
  color = 'primary',
  sx,
  ...launchTarget
}: AuthoritativeReviewAndCurateButtonProps) {
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null)

  const sessionIds = authoritativeReviewSessionIds
  if (sessionIds.length <= 1) {
    const effectiveLabel = sessionIds.length === 0 && disabledReason
      ? `${label} unavailable: ${disabledReason}`
      : label
    return (
      <ReviewAndCurateButton
        {...launchTarget}
        sessionId={sessionIds[0]}
        adapterKeys={adapterKeys}
        disabled={disabled || sessionIds.length === 0}
        iconOnly={iconOnly}
        label={effectiveLabel}
        size={size}
        variant={variant}
        color={color}
        sx={sx}
      />
    )
  }

  const handleOpenMenu = (event: React.MouseEvent<HTMLElement>) => {
    setMenuAnchorEl(event.currentTarget)
  }

  const selector = iconOnly ? (
    <IconButton
      aria-label={`${label}: choose review session`}
      onClick={handleOpenMenu}
      disabled={disabled}
      size={size as IconButtonProps['size']}
      color={color as IconButtonProps['color']}
      sx={sx}
    >
      <RateReviewIcon fontSize="small" />
    </IconButton>
  ) : (
    <Button
      type="button"
      onClick={handleOpenMenu}
      disabled={disabled}
      size={size}
      variant={variant as ButtonProps['variant']}
      color={color}
      sx={sx}
      startIcon={<RateReviewIcon fontSize="small" />}
      endIcon={<ExpandMoreIcon fontSize="small" />}
    >
      {label}
    </Button>
  )

  return (
    <>
      {iconOnly ? <Tooltip title={`${label}: choose review session`}><span>{selector}</span></Tooltip> : selector}
      <Menu
        anchorEl={menuAnchorEl}
        open={Boolean(menuAnchorEl)}
        onClose={() => setMenuAnchorEl(null)}
      >
        {sessionIds.map((sessionId, index) => (
          <MenuItem key={sessionId} disableGutters>
            <ReviewAndCurateButton
              sessionId={sessionId}
              label={sessionChoiceLabel(sessionId, adapterKeys?.[index], index)}
              size="small"
              variant="text"
              sx={{
                width: '100%',
                justifyContent: 'flex-start',
                px: 2,
                textTransform: 'none',
              }}
            />
          </MenuItem>
        ))}
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', px: 2, pb: 1 }}>
          Choose the adapter-specific prepared session.
        </Typography>
      </Menu>
    </>
  )
}
