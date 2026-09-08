import { Button, Stack, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'

import type { HorizontalGridRow } from './horizontalGridModel'

export interface HorizontalGridValidationPreviewRowActionsProps {
  onValidate: () => void
  row: HorizontalGridRow
}

export default function HorizontalGridValidationPreviewRowActions({
  onValidate,
  row,
}: HorizontalGridValidationPreviewRowActionsProps) {
  const theme = useTheme()
  const applicableCells = row.cells.filter((cell) => cell.hasField && cell.state !== null)
  const validatedCount = applicableCells.filter((cell) => cell.state === 'resolved').length
  const totalCount = applicableCells.length
  const allValidated = totalCount > 0 && validatedCount === totalCount
  const label = row.contextCell.value.identityLabel
  const teal = theme.palette.mode === 'dark' ? '#59c7b7' : '#076b65'
  const color = teal

  return (
    <Stack
      sx={{
        alignItems: "center",
        gap: '1px'
      }}>
      <Button
        aria-label={allValidated
          ? `All fields validated for ${label}`
          : `Validate all fields for ${label}`}
        disabled={allValidated || totalCount === 0}
        onClick={onValidate}
        size="small"
        sx={{
          backgroundColor: theme.palette.mode === 'dark'
            ? theme.palette.background.paper
            : '#fff',
          borderColor: theme.palette.mode === 'dark' ? alpha(color, 0.8) : '#67aaa5',
          borderRadius: '4px',
          color,
          cursor: 'pointer',
          fontSize: '9px',
          fontWeight: 760,
          lineHeight: 1,
          height: 22,
          minHeight: 22,
          minWidth: 0,
          p: '2px 4px',
          position: 'relative',
          textTransform: 'none',
          width: 58,
          '&::after': {
            content: '""',
            inset: '-11px -7px',
            position: 'absolute',
          },
          '&:hover': {
            backgroundColor: theme.palette.mode === 'dark'
              ? alpha(color, 0.2)
              : '#e9f5f2',
            borderColor: color,
          },
          '&.Mui-disabled': {
            backgroundColor: theme.palette.mode === 'dark'
              ? alpha(color, 0.14)
              : '#e9f5f2',
            borderColor: theme.palette.mode === 'dark' ? alpha(color, 0.65) : '#a6c8c5',
            color,
            cursor: 'default',
          },
        }}
        variant="outlined"
      >
        {allValidated ? 'Validated' : 'Validate'}
      </Button>
      <Typography
        aria-label={`${validatedCount} of ${totalCount} fields curator validated for ${label}`}
        sx={{
          color: "text.secondary",
          fontSize: '7.5px',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1
        }}>
        {validatedCount}/{totalCount}
      </Typography>
    </Stack>
  );
}
