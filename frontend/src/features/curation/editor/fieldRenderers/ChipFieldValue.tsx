import { Box, Chip } from '@mui/material'

function formatChipLabel(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }

  if (
    typeof value === 'string' ||
    typeof value === 'number' ||
    typeof value === 'boolean'
  ) {
    return String(value)
  }

  try {
    return JSON.stringify(value)
  } catch {
    return '[value]'
  }
}

export default function ChipFieldValue({ value }: { value: unknown }) {
  const items = Array.isArray(value)
    ? value
    : value === null || value === undefined
      ? []
      : [value]

  if (items.length === 0) {
    return null
  }

  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, minWidth: 0 }}>
      {items.map((item, index) => {
        const label = formatChipLabel(item)
        return label ? (
          <Chip key={`${label}-${index}`} label={label} size="small" variant="outlined" />
        ) : null
      })}
    </Box>
  )
}
