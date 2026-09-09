import { useState } from 'react'
import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Menu, MenuItem, Stack, TextField, Tooltip, Typography } from '@mui/material'
import DragIndicatorIcon from '@mui/icons-material/DragIndicator'
import VisibilityOffOutlinedIcon from '@mui/icons-material/VisibilityOffOutlined'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import AddIcon from '@mui/icons-material/Add'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import StopIcon from '@mui/icons-material/Stop'
import type { FlowSummaryResponse } from '@/services/agentStudioService'

type Props = {
  flows: FlowSummaryResponse[]
  selectedIds: string[]
  onChange: (ids: string[]) => Promise<boolean>
  saving: boolean
  onRun: (flow: FlowSummaryResponse) => void
  onStop?: () => void
  runningId: string | null
  canRun: boolean
  onOpenWorkspace: () => void
}

/** Personal shortcuts only: none of these controls deletes or edits a saved flow. */
export default function FlowShortcutCards({ flows, selectedIds, onChange, saving, onRun, onStop, runningId, canRun, onOpenWorkspace }: Props) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [dragged, setDragged] = useState<string | null>(null)
  const [dropId, setDropId] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ element: HTMLElement; id: string } | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const byId = new Map(flows.map(flow => [flow.id, flow]))
  const selected = selectedIds.flatMap(id => byId.has(id) ? [byId.get(id)!] : [])
  const available = flows.filter(flow => !selectedIds.includes(flow.id) && `${flow.name} ${flow.description || ''}`.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => a.name.localeCompare(b.name))
  const change = async (ids: string[], message: string) => {
    if (await onChange(ids)) setAnnouncement(message)
  }
  const move = (id: string, target: number) => {
    const ids = selected.map(flow => flow.id)
    const source = ids.indexOf(id)
    if (source < 0 || target < 0 || target >= ids.length || target === source) return
    ids.splice(source, 1); ids.splice(target, 0, id)
    void change(ids, `${byId.get(id)?.name} moved to position ${target + 1}.`)
  }
  return <Stack spacing={1.5}>
    <Box>
      <Typography variant="body2" color="text.secondary">Keep the flows you use here. Drag the dots to change their order.</Typography>
      <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
        <Button variant="outlined" startIcon={<AddIcon />} onClick={() => { setQuery(''); setPickerOpen(true) }} disabled={saving}>Add flow</Button>
        <Button onClick={onOpenWorkspace} sx={{ ml: 'auto' }}>Flows workspace</Button>
      </Stack>
    </Box>
    <Typography role="status" aria-live="polite" variant="caption" color="text.secondary">{saving ? 'Saving your list…' : announcement}</Typography>
    {selected.length === 0 && <Box sx={{ p: 3, border: 1, borderColor: 'divider', borderRadius: 2, textAlign: 'center' }}>
      <Typography fontWeight={600}>Your flow list is empty</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>Choose Add flow to bring a saved flow here. Hidden flows stay in your workspace.</Typography>
    </Box>}
    <Stack component="ul" aria-label="Your flow shortcuts" spacing={1} sx={{ p: 0, m: 0, listStyle: 'none' }}>
      {selected.map((flow, index) => <Box component="li" key={flow.id}
        onDragOver={event => { if (dragged && !saving) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; setDropId(flow.id) } }}
        onDrop={event => { event.preventDefault(); if (dragged && !saving) move(dragged, index); setDragged(null); setDropId(null) }}
        sx={{ border: 1, borderColor: dropId === flow.id ? 'primary.main' : 'divider', borderRadius: 2, bgcolor: 'background.paper', opacity: dragged === flow.id ? 0.5 : 1, boxShadow: theme => dropId === flow.id ? `0 -3px 0 ${theme.palette.primary.main}` : undefined }}>
        <Stack direction="row" alignItems="center" spacing={0.5} sx={{ p: 1 }}>
          <Tooltip title="Drag to reorder, or use the arrow keys">
            <IconButton aria-label={`Reorder ${flow.name}`} aria-disabled={saving} draggable={!saving}
              onDragStart={event => {
                event.dataTransfer.setData('text/plain', flow.id)
                event.dataTransfer.effectAllowed = 'move'
                const card = event.currentTarget.closest('li')
                if (card) {
                  const bounds = card.getBoundingClientRect()
                  // Native drag previews are translucent; use the whole card instead of just its handle.
                  event.dataTransfer.setDragImage(card, event.clientX - bounds.left, event.clientY - bounds.top)
                }
                setDragged(flow.id)
              }}
              onDragEnd={() => { setDragged(null); setDropId(null) }}
              onKeyDown={event => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); if (saving) return; move(flow.id, index + (event.key === 'ArrowUp' ? -1 : 1)) } }}
              sx={{ cursor: saving ? 'default' : 'grab', '&:active': { cursor: 'grabbing' } }}><DragIndicatorIcon /></IconButton>
          </Tooltip>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="body2" fontWeight={600} sx={{ overflowWrap: 'anywhere' }}>{flow.name}</Typography>
            <Typography variant="caption" color="text.secondary">{flow.step_count} steps</Typography>
          </Box>
          {runningId === flow.id && onStop
            ? <Button size="small" startIcon={<StopIcon />} onClick={onStop}>Stop</Button>
            : <Button size="small" variant="contained" startIcon={<PlayArrowIcon />} disabled={!canRun || Boolean(runningId)} onClick={() => onRun(flow)}>Run</Button>}
          <Tooltip title="Hide from this list. The flow stays in your workspace."><span>
            <IconButton aria-label={`Hide ${flow.name}`} disabled={saving || runningId === flow.id} onClick={() => void change(selectedIds.filter(id => id !== flow.id), `${flow.name} hidden. Use Add flow to bring it back.`)}><VisibilityOffOutlinedIcon fontSize="small" /></IconButton>
          </span></Tooltip>
          <IconButton aria-label={`More options for ${flow.name}`} onClick={event => setMenu({ element: event.currentTarget, id: flow.id })} disabled={saving}><MoreVertIcon fontSize="small" /></IconButton>
        </Stack>
        {flow.description && <Typography variant="body2" color="text.secondary" sx={{ px: 2, pb: 1.5, pl: 6.5, overflowWrap: 'anywhere' }}>{flow.description}</Typography>}
      </Box>)}
    </Stack>
    <Menu anchorEl={menu?.element} open={Boolean(menu)} onClose={() => setMenu(null)}>
      <MenuItem disabled={!menu || selectedIds.indexOf(menu.id) <= 0} onClick={() => { if (menu) move(menu.id, selectedIds.indexOf(menu.id) - 1); setMenu(null) }}>Move up</MenuItem>
      <MenuItem disabled={!menu || selectedIds.indexOf(menu.id) >= selectedIds.length - 1} onClick={() => { if (menu) move(menu.id, selectedIds.indexOf(menu.id) + 1); setMenu(null) }}>Move down</MenuItem>
    </Menu>
    <Dialog open={pickerOpen} onClose={() => setPickerOpen(false)} fullWidth maxWidth="sm">
      <DialogTitle>Add a flow to your list</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2, flexShrink: 0 }}>Choose from your saved flows, including ones you’ve hidden. This adds a shortcut, not a copy.</Typography>
        <TextField autoFocus fullWidth label="Search saved flows" value={query} onChange={event => setQuery(event.target.value)} sx={{ mb: 2, mt: 0.5, flexShrink: 0 }} />
        <Stack role="region" aria-label="Saved flows to add" tabIndex={0} spacing={1} sx={{ overflowY: 'auto', minHeight: 0, maxHeight: '50dvh', pr: 1, overscrollBehavior: 'contain' }}>
          {available.map(flow => <Stack key={flow.id} direction="row" alignItems="center" spacing={2} sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5, flexShrink: 0 }}>
            <Box sx={{ flex: 1, minWidth: 0 }}><Typography fontWeight={600} variant="body2" sx={{ overflowWrap: 'anywhere' }}>{flow.name}</Typography><Typography variant="body2" color="text.secondary">{flow.description}</Typography></Box>
            <Button disabled={saving} aria-label={`Add ${flow.name}`} onClick={() => void change([...selectedIds, flow.id], `${flow.name} added to your list.`)}>Add</Button>
          </Stack>)}
          {!available.length && <Alert severity="info">{query ? 'No matching flows to add.' : flows.length ? 'All your saved flows are already in your list.' : 'No saved flows yet. Create one in the Flows workspace.'}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions><Button onClick={() => setPickerOpen(false)}>Done</Button></DialogActions>
    </Dialog>
  </Stack>
}
