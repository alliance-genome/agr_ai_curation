import { Box, Chip, Tooltip, Typography } from '@mui/material'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function curieFromRecord(value: Record<string, unknown>): string | null {
  const curie = value.curie ?? value.id ?? value.identifier
  return typeof curie === 'string' && curie.trim() ? curie : null
}

function labelFromRecord(value: Record<string, unknown>): string | null {
  const label = value.name ?? value.label ?? value.display_name
  return typeof label === 'string' && label.trim() ? label : null
}

function values(value: unknown): unknown[] {
  if (Array.isArray(value)) {
    return value
  }
  return value === null || value === undefined || value === '' ? [] : [value]
}

export default function CurieChipFieldValue({
  label,
  value,
}: {
  label?: string
  value: unknown
}) {
  const items = values(value)

  if (items.length === 0) {
    return null
  }

  return (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, minWidth: 0 }}>
      {items.map((item, index) => {
        const record = isRecord(item) ? item : null
        const curie = record ? curieFromRecord(record) : String(item)
        const displayLabel = record ? labelFromRecord(record) ?? label ?? curie : label ?? curie

        return (
          <Tooltip key={`${curie}-${index}`} title={displayLabel === curie ? '' : curie}>
            <Chip
              label={(
                <Box component="span" sx={{ display: 'inline-flex', gap: 0.55, minWidth: 0 }}>
                  <Typography component="span" sx={{ fontSize: 'inherit', fontWeight: 700 }}>
                    {displayLabel}
                  </Typography>
                  {displayLabel !== curie ? (
                    <Typography
                      component="span"
                      sx={{ color: 'text.secondary', fontSize: 'inherit' }}
                    >
                      {curie}
                    </Typography>
                  ) : null}
                </Box>
              )}
              size="small"
              variant="outlined"
            />
          </Tooltip>
        )
      })}
    </Box>
  )
}
