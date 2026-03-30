import { useState } from 'react'
import {
  Button,
  MenuItem,
  Select,
  TableCell,
  TableRow,
  TextField,
} from '@mui/material'
import type { EntityTag } from './types'
import { ENTITY_TYPE_CODES, ENTITY_TYPE_LABELS, getEntityTypeLabel } from './types'

interface InlineEditRowProps {
  tag: EntityTag
  onSave: (tagId: string, updates: Partial<EntityTag>) => void
  onCancel: () => void
}

export default function InlineEditRow({ tag, onSave, onCancel }: InlineEditRowProps) {
  const [entityName, setEntityName] = useState(tag.entity_name)
  const [entityType, setEntityType] = useState(tag.entity_type)
  const [species, setSpecies] = useState(tag.species)
  const [topic, setTopic] = useState(tag.topic)

  const cellSx = { py: 0.5, px: 0.75 }
  const inputSx = { fontSize: '0.75rem' }
  const saveDisabled = entityName.trim().length === 0 || entityType.trim().length === 0

  const handleSave = () => {
    onSave(tag.tag_id, {
      entity_name: entityName,
      entity_type: entityType,
      species,
      topic,
    })
  }

  return (
    <TableRow sx={{ backgroundColor: 'action.hover' }}>
      <TableCell sx={cellSx}>
        <TextField
          size="small"
          value={entityName}
          onChange={(e) => setEntityName(e.target.value)}
          inputProps={{ 'aria-label': 'Entity name', sx: inputSx }}
          fullWidth
        />
      </TableCell>
      <TableCell sx={cellSx}>
        <Select
          size="small"
          value={entityType}
          onChange={(e) => setEntityType(e.target.value)}
          inputProps={{ 'aria-label': 'Entity type' }}
          sx={{ fontSize: '0.75rem' }}
          displayEmpty
          fullWidth
          renderValue={(value) => {
            if (typeof value !== 'string' || value.length === 0) {
              return 'Select type'
            }

            return getEntityTypeLabel(value)
          }}
        >
          <MenuItem value="" disabled sx={{ fontSize: '0.75rem' }}>
            Select type
          </MenuItem>
          {ENTITY_TYPE_CODES.map((code) => (
            <MenuItem key={code} value={code} sx={{ fontSize: '0.75rem' }}>
              {ENTITY_TYPE_LABELS[code]}
            </MenuItem>
          ))}
        </Select>
      </TableCell>
      <TableCell sx={cellSx}>
        <TextField
          size="small"
          value={species}
          onChange={(e) => setSpecies(e.target.value)}
          inputProps={{ 'aria-label': 'Species', sx: inputSx }}
          fullWidth
        />
      </TableCell>
      <TableCell sx={cellSx}>
        <TextField
          size="small"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          inputProps={{ 'aria-label': 'Topic', sx: inputSx }}
          fullWidth
        />
      </TableCell>
      <TableCell sx={cellSx} />
      <TableCell sx={cellSx} />
      <TableCell sx={cellSx}>
        <Button size="small" variant="contained" onClick={handleSave} disabled={saveDisabled} sx={{ fontSize: '0.65rem', mr: 0.5, minWidth: 0, px: 1, py: 0.25 }}>
          Save
        </Button>
        <Button size="small" variant="text" onClick={onCancel} sx={{ fontSize: '0.65rem', minWidth: 0, px: 1, py: 0.25 }}>
          Cancel
        </Button>
      </TableCell>
    </TableRow>
  )
}
