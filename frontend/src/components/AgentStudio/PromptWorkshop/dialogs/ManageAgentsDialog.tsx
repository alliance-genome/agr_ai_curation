import {
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'

import type { CustomAgent } from '@/types/promptExplorer'

export interface ManageAgentsDialogProps {
  open: boolean
  agents: CustomAgent[]
  loading: boolean
  saving: boolean
  selectedAgentId: string
  onOpenAgent: (agentId: string) => void
  onDeleteAgent: (agent: CustomAgent) => void
  onClose: () => void
}

export default function ManageAgentsDialog({
  open,
  agents,
  loading,
  saving,
  selectedAgentId,
  onOpenAgent,
  onDeleteAgent,
  onClose,
}: ManageAgentsDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="manage-agents-title"
      PaperProps={{ sx: { maxHeight: '70vh' } }}
    >
      <DialogTitle id="manage-agents-title" sx={{ pb: 0.5 }}>
        Manage agents
        <Typography component="div" sx={{ fontSize: 12.5, fontWeight: 400, color: 'text.secondary' }}>
          Open or delete your saved agents
        </Typography>
      </DialogTitle>
      <DialogContent sx={{ pt: 1 }}>
        <Box sx={{ minHeight: 200, maxHeight: 360, overflow: 'auto' }}>
          {loading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress size={24} />
            </Box>
          ) : agents.length === 0 ? (
            <Typography sx={{ textAlign: 'center', py: 4, color: 'text.secondary', fontSize: 13 }}>
              No saved agents yet
            </Typography>
          ) : (
            <List disablePadding aria-label="Saved agents">
              {agents.map((agent) => {
                const isOpen = agent.id === selectedAgentId
                return (
                  <ListItem
                    key={agent.id}
                    disablePadding
                    sx={{
                      mb: 0.5,
                      border: (theme) => `1px solid ${theme.palette.divider}`,
                      borderRadius: 1,
                      backgroundColor: isOpen ? 'action.selected' : 'transparent',
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', width: '100%', py: 0.5, px: 1.25, gap: 1 }}>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography sx={{ fontSize: 13.5 }} noWrap>
                          {agent.name}
                        </Typography>
                        <Typography sx={{ fontSize: 12, color: 'text.secondary' }} noWrap>
                          {agent.description || 'Custom agent'}
                          {isOpen ? ' · Currently open' : ''}
                        </Typography>
                      </Box>
                      <Button size="small" onClick={() => onOpenAgent(agent.id)} aria-label={`Open ${agent.name}`} sx={{ textTransform: 'none' }}>
                        Open
                      </Button>
                      <IconButton
                        size="small"
                        onClick={() => onDeleteAgent(agent)}
                        aria-label={`Delete ${agent.name}`}
                        disabled={saving}
                        sx={{ color: 'error.main' }}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Box>
                  </ListItem>
                )
              })}
            </List>
          )}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={onClose} size="small">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  )
}
