import { Typography } from '@mui/material'

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return 'empty'
  }
  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value)
  }
  return JSON.stringify(value)
}

export default function DivergenceFieldValue({
  proposedValue,
  value,
}: {
  proposedValue?: unknown
  value: unknown
}) {
  if (proposedValue === undefined || Object.is(proposedValue, value)) {
    return null
  }

  return (
    <Typography color="text.secondary" sx={{ mt: 0.5 }} variant="caption">
      AI proposed: {formatValue(proposedValue)}
    </Typography>
  )
}
