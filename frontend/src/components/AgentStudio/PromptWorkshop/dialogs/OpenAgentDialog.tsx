import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
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
  ownedAgentIds: string[]
  cloning: boolean
  error: string | null
  onClone: (agentId: string) => void
  loading: boolean
  selectedAgentId: string
  onSelect: (agentId: string) => void
  onClose: () => void
}

export default function OpenAgentDialog({ open, agents, ownedAgentIds, cloning, error, onClone, loading, selectedAgentId, onSelect, onClose }: OpenAgentDialogProps) {
  const [search, setSearch] = useState('')
  const [previewId, setPreviewId] = useState('')
  const preview = agents.find((agent) => agent.id === previewId)
  const ownedIds = new Set(ownedAgentIds)

  useEffect(() => {
    if (open) {
      setSearch('')
      setPreviewId('')
    }
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
      onClose={cloning ? undefined : onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="open-agent-title"
      slotProps={{
        paper: { sx: { maxHeight: '70vh' } }
      }}
    >
      <DialogTitle id="open-agent-title">Open agent</DialogTitle>
      <DialogContent sx={{ pt: 0.5 }}>
        <Typography sx={{ fontSize: 12, mb: 1 }}>Open your agents or preview and clone agents shared with your project.</Typography>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        <TextField
          fullWidth
          autoFocus
          size="small"
          placeholder="Search agents"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          sx={{ mb: 1.5, mt: 0.5 }}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
                </InputAdornment>
              ),
            },

            htmlInput: { 'aria-label': 'Search agents' }
          }} />
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
                    disabled={cloning}
                    onClick={() => ownedIds.has(agent.id) ? onSelect(agent.id) : setPreviewId(agent.id)}
                    selected={agent.id === selectedAgentId}
                    sx={{ borderRadius: 1, mb: 0.5 }}
                  >
                    <ListItemText
                      primary={agent.name}
                      secondary={`${ownedIds.has(agent.id) ? 'Yours' : `Shared by user ${agent.user_id}`} · ${agent.visibility === 'project' ? 'Project shared' : 'Private'} · ${agent.description || 'Custom agent'}`}
                      slotProps={{
                        primary: { sx: { fontSize: 13.5 } },
                        secondary: { sx: { fontSize: 12 } }
                      }} />
                  </ListItemButton>
                </ListItem>
              ))}
            </List>
          )}
        </Box>
        {preview && !ownedIds.has(preview.id) && (
          <Box sx={{ mt: 2, borderTop: 1, borderColor: 'divider', pt: 1.5 }}>
            <Typography variant="subtitle2">{preview.name} · Read-only</Typography>
            <Typography sx={{ fontSize: 12, mb: 1 }}>Clone this agent to create your own private editable copy.</Typography>
            <TextField
              label="Shared agent prompt"
              value={preview.custom_prompt}
              multiline
              fullWidth
              slotProps={{ input: { readOnly: true } }}
            />
            <Button sx={{ mt: 1 }} variant="contained" size="small" disabled={cloning} onClick={() => onClone(preview.id)}>
              {cloning ? 'Cloning…' : 'Clone to Workshop'}
            </Button>
          </Box>
        )}
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small" disabled={cloning}>
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  );
}
