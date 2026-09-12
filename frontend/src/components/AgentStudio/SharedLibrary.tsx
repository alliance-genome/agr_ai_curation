import { useEffect, useRef, useState } from 'react'
import { Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField, Typography } from '@mui/material'
import { cloneAgentToWorkshop, cloneFlow, listAllFlows, listCustomAgents, listToolIdeaRequests } from '@/services/agentStudioService'
import { notifyFlowListInvalidated } from '@/features/flows/flowListInvalidation'
import type { ToolIdeaSummary } from '@/types/promptExplorer'

type ArtifactType = 'agent' | 'flow' | 'tool_idea'
interface ArtifactMetadata {
  id: string
  title: string
  description: string
  owner: number
  mine: boolean
  project?: string | null
  shared: boolean
  status?: string
}
type Artifact = ArtifactMetadata & (
  | { type: 'agent'; agentId: string }
  | { type: 'flow' }
  | { type: 'tool_idea'; idea: ToolIdeaSummary }
)
const labels: Record<ArtifactType, string> = { agent: 'Agent', flow: 'Flow', tool_idea: 'Tool Idea' }

interface SharedLibraryProps {
  active: boolean
  onOpenAgent: (id: string) => void
  onOpenFlow: (id: string) => void
  onReuseToolIdea: (idea: ToolIdeaSummary) => void
}

export default function SharedLibrary({ active, onOpenAgent, onOpenFlow, onReuseToolIdea }: SharedLibraryProps) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [type, setType] = useState('all')
  const [ownership, setOwnership] = useState('all')
  const [search, setSearch] = useState('')
  const [idea, setIdea] = useState<ToolIdeaSummary | null>(null)
  const [cloning, setCloning] = useState(false)
  const visitRef = useRef(0)

  useEffect(() => {
    // A completed clone must not navigate away from work started on another tab.
    visitRef.current += 1
  }, [active])

  useEffect(() => {
    if (!active) return
    let cancelled = false
    setLoading(true)
    setError(null)
    setActionError(null)
    Promise.all([listCustomAgents(undefined, 'visible'), listCustomAgents(), listToolIdeaRequests(), listAllFlows()])
      .then(([visible, owned, ideas, flows]) => {
        if (cancelled) return
        const ownedIds = new Set(owned.custom_agents.map((agent) => agent.id))
        setArtifacts([
          ...visible.custom_agents.map((agent): Artifact => ({
            id: agent.id, agentId: agent.agent_id, type: 'agent', title: agent.name, description: agent.description ?? '',
            owner: agent.user_id, mine: ownedIds.has(agent.id), project: agent.project_id, shared: agent.visibility === 'project',
          })),
          ...ideas.tool_ideas.map((request): Artifact => ({
            id: request.id, type: 'tool_idea', title: request.title, description: request.description,
            owner: request.user_id, mine: 'opus_conversation' in request, project: request.project_id,
            shared: Boolean(request.project_id), status: request.status,
            // Deliberately select the reusable summary, excluding owner-private fields.
            idea: { id: request.id, title: request.title, description: request.description, user_id: request.user_id,
              project_id: request.project_id, status: request.status, created_at: request.created_at, updated_at: request.updated_at },
          })),
          ...flows.flows.map((flow): Artifact => ({
            id: flow.id, type: 'flow', title: flow.name, description: flow.description ?? '',
            owner: flow.user_id, mine: flow.is_owner, project: flow.project_id, shared: flow.visibility === 'project',
          })),
        ])
      }).catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Unable to load the shared library')
      }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [active, revision])

  const clone = async (artifact: Artifact) => {
    const visit = visitRef.current
    setCloning(true)
    setActionError(null)
    try {
      if (artifact.type === 'agent') {
        const copy = await cloneAgentToWorkshop(artifact.agentId)
        if (visit === visitRef.current) onOpenAgent(copy.id)
      } else if (artifact.type === 'flow') {
        const copy = await cloneFlow(artifact.id)
        notifyFlowListInvalidated({ flowId: copy.id, reason: 'created' })
        if (visit === visitRef.current) onOpenFlow(copy.id)
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Unable to clone artifact')
    } finally { setCloning(false) }
  }
  const query = search.trim().toLowerCase()
  const filtered = artifacts.filter((artifact) => (
    (type === 'all' || type === artifact.type)
    && (ownership === 'all' || (ownership === 'mine' ? artifact.mine : artifact.shared))
    && `${artifact.title} ${artifact.description} ${artifact.owner} ${artifact.project ?? ''}`.toLowerCase().includes(query)
  ))

  return <Box sx={{ p: 2, height: '100%', overflow: 'auto' }}>
    <Typography variant="h6">Shared Library</Typography>
    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Discover your artifacts and work shared with your project. Clone a teammate’s agent or flow to edit your own private copy.</Typography>
    <Stack direction="row" useFlexGap spacing={1} sx={{ mb: 2, flexWrap: 'wrap' }}>
      <TextField select size="small" label="Artifact type" value={type} onChange={(event) => setType(event.target.value)} sx={{ minWidth: 140 }}>
        <MenuItem value="all">All types</MenuItem>
        {Object.entries(labels).map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
      </TextField>
      <TextField select size="small" label="Ownership" value={ownership} onChange={(event) => setOwnership(event.target.value)} sx={{ minWidth: 180 }}>
        <MenuItem value="all">All visible</MenuItem><MenuItem value="mine">Mine</MenuItem><MenuItem value="shared">Shared with project</MenuItem>
      </TextField>
      <TextField size="small" label="Search artifacts" value={search} onChange={(event) => setSearch(event.target.value)} sx={{ flex: 1, minWidth: 160 }} />
      <Button disabled={loading || cloning} onClick={() => setRevision((value) => value + 1)}>Refresh</Button>
    </Stack>
    {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
    {actionError && <Alert severity="error" sx={{ mb: 1 }}>{actionError}</Alert>}
    {loading ? <CircularProgress aria-label="Loading shared library" /> : !error && filtered.length === 0 ? <Typography>No artifacts match your filters.</Typography> : !error && <Stack component="ul" aria-label="Shared artifacts" spacing={1} sx={{ p: 0, listStyle: 'none' }}>
      {filtered.map((artifact) => <Box component="li" key={`${artifact.type}:${artifact.id}`} sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}>
        <Typography variant="subtitle2">{artifact.title}</Typography>
        <Stack direction="row" spacing={0.5} useFlexGap sx={{ my: 0.5, flexWrap: 'wrap' }}>
          <Chip size="small" label={labels[artifact.type]} /><Chip size="small" label={artifact.shared ? 'Shared with project' : 'Private'} />
          {artifact.status && <Chip size="small" label={artifact.status.replaceAll('_', ' ')} />}
        </Stack>
        <Typography variant="caption" color="text.secondary">{artifact.mine ? 'You' : `Owner #${artifact.owner}`} · {artifact.project ? `Project: ${artifact.project}` : 'No project'}</Typography>
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{artifact.description}</Typography>
        <Stack direction="row" useFlexGap spacing={1} sx={{ mt: 1, flexWrap: 'wrap' }}>
          {artifact.type === 'agent' && artifact.mine && <Button size="small" disabled={cloning} onClick={() => onOpenAgent(artifact.id)}>Open in Workshop</Button>}
          {artifact.type !== 'tool_idea' && <Button size="small" variant={artifact.mine ? 'outlined' : 'contained'} disabled={cloning} onClick={() => void clone(artifact)}>{artifact.type === 'agent' ? 'Clone to Workshop' : 'Clone to edit'}</Button>}
          {artifact.type === 'flow' && <Button size="small" disabled={cloning} onClick={() => onOpenFlow(artifact.id)}>{artifact.mine ? 'Open flow' : 'Open read-only'}</Button>}
          {artifact.type === 'flow' && <Button size="small" disabled={cloning} href={`/?flow=${encodeURIComponent(artifact.id)}`}>Run in workspace</Button>}
          {artifact.type === 'tool_idea' && <Button size="small" onClick={() => setIdea(artifact.idea)}>Open request context</Button>}
        </Stack>
      </Box>)}
    </Stack>}
    <Dialog open={Boolean(idea)} onClose={() => setIdea(null)} fullWidth maxWidth="sm">
      <DialogTitle>{idea?.title}</DialogTitle>
      <DialogContent><Typography sx={{ whiteSpace: 'pre-wrap' }}>{idea?.description}</Typography><Typography variant="caption">{idea?.status} · Owner #{idea?.user_id} · {idea?.project_id ?? 'No project'}</Typography></DialogContent>
      <DialogActions><Button onClick={() => setIdea(null)}>Close</Button><Button onClick={() => { if (idea) onReuseToolIdea(idea); setIdea(null) }}>Discuss request with AI Chat</Button></DialogActions>
    </Dialog>
  </Box>
}
