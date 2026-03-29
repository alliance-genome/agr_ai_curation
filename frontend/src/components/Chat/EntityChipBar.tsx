import { Box } from '@mui/material'

export interface EntityChipBarItem {
  entity: string
  quoteCount: number
  colorHex: string
  chipBackground: string
  chipBorder: string
  activeBackground: string
  inactiveBackground: string
  inactiveBorder: string
}

interface EntityChipBarProps {
  items: EntityChipBarItem[]
  activeEntity: string | null
  onEntityToggle: (entity: string) => void
}

export default function EntityChipBar({
  items,
  activeEntity,
  onEntityToggle,
}: EntityChipBarProps) {
  const hasActiveEntity = activeEntity !== null

  return (
    <Box
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: '6px',
      }}
    >
      {items.map((item) => {
        const isActive = activeEntity === item.entity
        const backgroundColor = isActive
          ? item.activeBackground
          : hasActiveEntity
            ? item.inactiveBackground
            : item.chipBackground
        const border = isActive
          ? `2px solid ${item.colorHex}`
          : `1px solid ${hasActiveEntity ? item.inactiveBorder : item.chipBorder}`

        return (
          <Box
            aria-label={`${item.entity} ${item.quoteCount}`}
            aria-pressed={isActive}
            component="button"
            key={item.entity}
            onClick={() => onEntityToggle(item.entity)}
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              borderRadius: '14px',
              px: isActive ? '11px' : '12px',
              py: isActive ? '3px' : '4px',
              border,
              backgroundColor,
              color: '#ffffff',
              fontSize: '12px',
              fontWeight: isActive ? 600 : 400,
              lineHeight: 1.2,
              cursor: 'pointer',
              opacity: hasActiveEntity && !isActive ? 0.6 : 1,
              transition: 'background-color 150ms ease, border-color 150ms ease, opacity 150ms ease',
            }}
            type="button"
          >
            <Box component="span">{item.entity}</Box>
            <Box
              component="span"
              sx={{
                opacity: isActive ? 0.8 : 0.6,
              }}
            >
              {item.quoteCount}
            </Box>
          </Box>
        )
      })}
    </Box>
  )
}
