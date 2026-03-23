import {
  Chip,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'

import type { Theme } from '@mui/material/styles'

import type {
  CurationDraftField,
  FieldValidationStatus,
} from '../types'

interface ValidationBadgeTone {
  color: 'default' | 'error' | 'info' | 'success' | 'warning'
  label: string
  variant?: 'filled' | 'outlined'
}

const STATUS_TONES: Record<FieldValidationStatus, ValidationBadgeTone> = {
  validated: {
    color: 'success',
    label: 'Validated',
  },
  ambiguous: {
    color: 'warning',
    label: 'Ambiguous',
  },
  not_found: {
    color: 'error',
    label: 'Not found',
  },
  invalid_format: {
    color: 'error',
    label: 'Invalid format',
  },
  conflict: {
    color: 'warning',
    label: 'Conflict',
  },
  skipped: {
    color: 'default',
    label: 'Skipped',
    variant: 'outlined',
  },
  overridden: {
    color: 'info',
    label: 'Overridden',
  },
}

function buildBadgeTooltip(field: CurationDraftField): string | null {
  const parts: string[] = []

  if (field.dirty) {
    parts.push('Curator value differs from the AI seed.')
  }

  if (field.stale_validation) {
    parts.push('Validation is refreshing after a draft change.')
  }

  if (field.validation_result?.resolver) {
    parts.push(`Resolver: ${field.validation_result.resolver}`)
  }

  for (const warning of field.validation_result?.warnings ?? []) {
    parts.push(warning)
  }

  return parts.length > 0 ? parts.join(' ') : null
}

function dirtyIndicatorSx(theme: Theme) {
  return {
    '&.MuiChip-root': {
      color: theme.palette.warning.dark,
      borderColor: theme.palette.warning.main,
      backgroundColor: theme.palette.warning.light,
    },
  }
}

export interface ValidationBadgeProps {
  field: CurationDraftField
}

export default function ValidationBadge({ field }: ValidationBadgeProps) {
  const validationResult = field.validation_result
  const tone = validationResult ? STATUS_TONES[validationResult.status] : null
  const tooltip = buildBadgeTooltip(field)

  if (!field.dirty && !field.stale_validation && !tone) {
    return null
  }

  const content = (
    <Stack
      alignItems="center"
      direction="row"
      flexWrap="wrap"
      gap={0.5}
      useFlexGap
    >
      {field.dirty ? (
        <Chip
          aria-label={`${field.label} dirty indicator`}
          label="Edited"
          size="small"
          sx={dirtyIndicatorSx}
          variant="outlined"
        />
      ) : null}

      {tone ? (
        <Chip
          aria-label={`${field.label} validation ${tone.label.toLowerCase()}`}
          color={tone.color}
          label={tone.label}
          size="small"
          variant={
            field.stale_validation
              ? 'outlined'
              : (tone.variant ?? 'filled')
          }
        />
      ) : null}

      {field.stale_validation ? (
        <Typography
          color="warning.main"
          variant="caption"
        >
          Refreshing
        </Typography>
      ) : null}
    </Stack>
  )

  if (!tooltip) {
    return content
  }

  return (
    <Tooltip title={tooltip}>
      <span>{content}</span>
    </Tooltip>
  )
}
