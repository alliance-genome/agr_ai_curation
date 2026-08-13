import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline'
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline'
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked'
import { Box, Tooltip } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { FieldStateKind } from './fieldState'

export interface FieldStateIndicatorProps {
  fieldKey: string
  state: FieldStateKind
}

export default function FieldStateIndicator({
  fieldKey,
  state,
}: FieldStateIndicatorProps) {
  const theme = useTheme()
  const presentation = {
    'needs-review': {
      label: 'Needs review',
      color: theme.palette.warning.main,
      backgroundColor: alpha(theme.palette.warning.main, 0.12),
      icon: <ErrorOutlineIcon fontSize="small" />,
    },
    resolved: {
      label: 'Resolved',
      color: theme.palette.success.main,
      backgroundColor: alpha(theme.palette.success.main, 0.12),
      icon: <CheckCircleOutlineIcon fontSize="small" />,
    },
    'ai-unconfirmed': {
      label: 'AI unconfirmed',
      color: theme.palette.text.secondary,
      backgroundColor: alpha(theme.palette.common.white, 0.06),
      icon: <RadioButtonUncheckedIcon fontSize="small" />,
    },
  } satisfies Record<FieldStateKind, {
    label: string
    color: string
    backgroundColor: string
    icon: JSX.Element
  }>
  const current = presentation[state]

  return (
    <Tooltip arrow title={current.label}>
      <Box
        aria-label={current.label}
        data-testid={`field-state-indicator-${fieldKey}`}
        role="img"
        sx={{
          alignItems: 'center',
          backgroundColor: current.backgroundColor,
          borderRadius: 1,
          color: current.color,
          display: 'inline-flex',
          height: 24,
          justifyContent: 'center',
          mt: 0.35,
          width: 24,
        }}
      >
        {current.icon}
      </Box>
    </Tooltip>
  )
}
