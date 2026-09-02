import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputAdornment,
  InputLabel,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  MenuItem,
  Select,
  TextField,
  Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'

import type { ToolLibraryItem } from '@/types/promptExplorer'

import { MONO_FONT } from '../workshopStyles'

export interface ToolLibraryDialogProps {
  open: boolean
  tools: ToolLibraryItem[]
  attachedToolIds: string[]
  onConfirm: (toolIds: string[]) => void
  onClose: () => void
}

function footerLabel(adds: number, removes: number): string {
  if (adds > 0) return `Attach ${adds} ${adds === 1 ? 'tool' : 'tools'}`
  if (removes > 0) return `Remove ${removes} ${removes === 1 ? 'tool' : 'tools'}`
  return 'Attach tools'
}

export default function ToolLibraryDialog({ open, tools, attachedToolIds, onConfirm, onClose }: ToolLibraryDialogProps) {
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('all')
  const [selected, setSelected] = useState<string[]>(attachedToolIds)

  useEffect(() => {
    if (!open) return
    setSearch('')
    setCategory('all')
    setSelected(attachedToolIds)
  }, [open, attachedToolIds])

  const categories = useMemo(() => {
    const unique = Array.from(new Set(tools.map((tool) => tool.category).filter(Boolean)))
    unique.sort((a, b) => a.localeCompare(b))
    return unique
  }, [tools])

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    return tools.filter((tool) => {
      const matchesCategory = category === 'all' || tool.category === category
      const matchesSearch = !query
        || tool.display_name.toLowerCase().includes(query)
        || tool.tool_key.toLowerCase().includes(query)
        || tool.category.toLowerCase().includes(query)
      return matchesCategory && matchesSearch
    })
  }, [tools, category, search])

  const attachedSet = new Set(attachedToolIds)
  const selectedSet = new Set(selected)
  const adds = selected.filter((toolKey) => !attachedSet.has(toolKey)).length
  const removes = attachedToolIds.filter((toolKey) => !selectedSet.has(toolKey)).length
  const hasChanges = adds > 0 || removes > 0
  const attachableCount = tools.filter((tool) => tool.allow_attach).length

  const toggle = (tool: ToolLibraryItem) => {
    if (!tool.allow_attach) return
    setSelected((prev) => (
      prev.includes(tool.tool_key) ? prev.filter((key) => key !== tool.tool_key) : [...prev, tool.tool_key]
    ))
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="tool-library-title"
      PaperProps={{ sx: { maxHeight: '75vh' } }}
    >
      <DialogTitle id="tool-library-title" sx={{ pb: 0.5 }}>
        Add tools
        <Typography component="div" sx={{ fontSize: 12.5, fontWeight: 400, color: 'text.secondary' }}>
          {attachedToolIds.length} attached · {attachableCount} available
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ pt: 1 }}>
        <Box sx={{ display: 'flex', gap: 1.5, mb: 1.5, mt: 0.5, flexWrap: 'wrap' }}>
          <TextField
            autoFocus
            size="small"
            placeholder="Search tools"
            inputProps={{ 'aria-label': 'Search tools' }}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                </InputAdornment>
              ),
            }}
            sx={{ flex: 1, minWidth: 200 }}
          />
          <FormControl size="small" sx={{ width: 180 }}>
            <InputLabel id="tool-library-category-label">Category</InputLabel>
            <Select
              labelId="tool-library-category-label"
              label="Category"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            >
              <MenuItem value="all">All categories</MenuItem>
              {categories.map((entry) => (
                <MenuItem key={entry} value={entry}>
                  {entry}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>
        <Box sx={{ minHeight: 240, maxHeight: 380, overflow: 'auto', border: (theme) => `1px solid ${theme.palette.divider}`, borderRadius: 1.5 }}>
          {filtered.length === 0 ? (
            <Typography sx={{ textAlign: 'center', py: 4, color: 'text.secondary', fontSize: 13 }}>
              No tools match your search
            </Typography>
          ) : (
            <List disablePadding aria-label="Tool library">
              {filtered.map((tool) => {
                const checked = selectedSet.has(tool.tool_key)
                const attachable = tool.allow_attach
                const labelId = `tool-library-${tool.tool_key}`
                return (
                  <ListItem
                    key={tool.tool_key}
                    disablePadding
                    sx={{ borderBottom: (theme) => `1px solid ${theme.palette.divider}`, '&:last-of-type': { borderBottom: 0 } }}
                  >
                    <ListItemButton
                      role="checkbox"
                      aria-checked={checked}
                      aria-labelledby={labelId}
                      aria-disabled={!attachable || undefined}
                      onClick={() => toggle(tool)}
                      sx={{ alignItems: 'flex-start', gap: 1, py: 0.75, cursor: attachable ? 'pointer' : 'not-allowed' }}
                    >
                      <Checkbox
                        size="small"
                        edge="start"
                        checked={checked}
                        tabIndex={-1}
                        disableRipple
                        disabled={!attachable}
                        inputProps={{ 'aria-hidden': true }}
                        sx={{ p: 0.5 }}
                      />
                      <ListItemText
                        id={labelId}
                        primary={(
                          <Typography component="span" sx={{ fontFamily: MONO_FONT, fontSize: 12.5, fontWeight: 500, color: attachable ? 'text.primary' : 'text.disabled' }}>
                            {tool.tool_key}
                          </Typography>
                        )}
                        secondary={
                          attachable
                            ? `${tool.display_name} · ${tool.description}`
                            : `Disabled by policy for custom agents: ${tool.description}`
                        }
                        secondaryTypographyProps={{ fontSize: 12, color: attachable ? 'text.secondary' : 'text.disabled' }}
                      />
                      {!attachable && (
                        <Typography component="span" sx={{ fontSize: 11, px: 0.75, border: (theme) => `1px solid ${theme.palette.divider}`, borderRadius: 999, alignSelf: 'center', color: 'text.disabled' }}>
                          Policy
                        </Typography>
                      )}
                    </ListItemButton>
                  </ListItem>
                )
              })}
            </List>
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small">
          Cancel
        </Button>
        <Button onClick={() => onConfirm(selected)} variant="contained" size="small" disabled={!hasChanges}>
          {footerLabel(adds, removes)}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
