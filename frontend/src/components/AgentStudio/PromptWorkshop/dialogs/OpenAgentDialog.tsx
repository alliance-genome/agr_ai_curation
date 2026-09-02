import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  InputAdornment,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  TextField,
  Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'

import type { CustomAgent } from '@/types/promptExplorer'

export interface OpenAgentDialogProps {
  open: boolean
  agents: CustomAgent[]
  loading: boolean
  selectedAgentId: string
  onSelect: (agentId: string) => void
  onClose: () => void
}

export default function OpenAgentDialog({ open, agents, loading, selectedAgentId, onSelect, onClose }: OpenAgentDialogProps) {
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) setSearch('')
  }, [open])

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) return agents
    return agents.filter((agent) => (
      agent.name.toLowerCase().includes(query) || (agent.description || '').toLowerCase().includes(query)
    ))
  }, [agents, search])

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="open-agent-title"
      PaperProps={{ sx: { maxHeight: '70vh' } }}
    >
      <DialogTitle id="open-agent-title">Open agent</DialogTitle>
      <DialogContent sx={{ pt: 0.5 }}>
        <TextField
          fullWidth
          autoFocus
          size="small"
          placeholder="Search agents"
          inputProps={{ 'aria-label': 'Search agents' }}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
              </InputAdornment>
            ),
          }}
          sx={{ mb: 1.5, mt: 0.5 }}
        />
        <Box sx={{ minHeight: 200, maxHeight: 320, overflow: 'auto' }}>
          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          ) : filtered.length === 0 ? (
            <Typography sx={{ textAlign: 'center', py: 4, color: 'text.secondary', fontSize: 13 }}>
              {search ? 'No agents match your search' : 'No saved agents yet'}
            </Typography>
          ) : (
            <List disablePadding aria-label="Saved agents">
              {filtered.map((agent) => (
                <ListItem key={agent.id} disablePadding>
                  <ListItemButton
                    onClick={() => onSelect(agent.id)}
                    selected={agent.id === selectedAgentId}
                    sx={{ borderRadius: 1, mb: 0.5 }}
                  >
                    <ListItemText
                      primary={agent.name}
                      secondary={agent.description || 'Custom agent'}
                      primaryTypographyProps={{ fontSize: 13.5 }}
                      secondaryTypographyProps={{ fontSize: 12 }}
                    />
                  </ListItemButton>
                </ListItem>
              ))}
            </List>
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small">
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  )
}
