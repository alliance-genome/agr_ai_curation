import { useState } from 'react'
import { Alert, Box, Button, FormControlLabel, Radio, RadioGroup, Typography } from '@mui/material'
import { listAgentExecutionRevisions } from '@/services/agentStudioService'
import type { AgentExecutionRevision } from '@/types/agentExecution'
import type { NodeDraftValues } from './useNodeDraft'

interface Props {
  agentKey: string
  selection: NodeDraftValues['executionSelection']
  onChange: (selection: NodeDraftValues['executionSelection']) => void
}

/** Browse only: selecting a revision never restores or changes the agent head. */
export default function ExecutionRevisionPicker({ agentKey, selection, onChange }: Props) {
  const [opened, setOpened] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [revisions, setRevisions] = useState<AgentExecutionRevision[]>([])
  const [cursor, setCursor] = useState<number | null>(null)

  const load = async (before?: number) => {
    setOpened(true)
    setLoading(true)
    setError(false)
    try {
      const page = await listAgentExecutionRevisions(agentKey.slice(3), before)
      setRevisions((previous) => before === undefined ? page.revisions : [...previous, ...page.revisions])
      setCursor(page.next_before_revision)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Box component="section" aria-label="Saved agent revision" sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <Typography variant="subtitle2">Saved agent revision</Typography>
      <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
        {selection.execution_receipt
          ? `Revision ${selection.execution_receipt.revision}`
          : selection.agent_revision_id || 'No revision selected'}
      </Typography>
      <Typography variant="body2" color="text.secondary">
        This step keeps its saved instructions, tools, and output structure. Choose another revision here, then Apply and save the flow to validate it.
      </Typography>
      {!opened && <Button onClick={() => { void load() }}>Choose a saved revision</Button>}
      {opened && (
        <>
          <RadioGroup
            aria-label="Available saved revisions"
            value={selection.agent_revision_id || ''}
            onChange={(_, id) => {
              const revision = revisions.find((item) => item.id === id)
              if (!revision) return
              onChange({
                agent_revision_id: revision.id,
                execution_receipt: {
                  agent_id: revision.agent_id,
                  agent_key: agentKey,
                  agent_revision_id: revision.id,
                  revision: revision.revision,
                  fingerprint: revision.fingerprint,
                  output_contract: revision.snapshot.output_contract,
                },
              })
            }}
          >
            {revisions.map((revision) => (
              <FormControlLabel key={revision.id} value={revision.id} control={<Radio />}
                label={`Revision ${revision.revision}${revision.notes ? ` — ${revision.notes}` : ''}`} />
            ))}
          </RadioGroup>
          {loading && <Typography role="status">Loading saved revisions…</Typography>}
          {error && <Alert severity="error">Could not load revisions. Your selection has not changed.</Alert>}
          {!loading && !error && revisions.length === 0 && <Typography>No accessible saved revisions were found.</Typography>}
          {(error || cursor !== null) && (
            <Button disabled={loading} onClick={() => { void load(cursor ?? undefined) }}>
              {error ? 'Retry loading revisions' : 'Load older revisions'}
            </Button>
          )}
        </>
      )}
    </Box>
  )
}
