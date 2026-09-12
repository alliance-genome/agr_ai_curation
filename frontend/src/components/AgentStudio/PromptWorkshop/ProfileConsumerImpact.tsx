import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Stack, Typography } from '@mui/material'
import { listGenericProfileConsumers, type ProfileConsumerPage } from '@/services/genericProfileService'

interface Props { profileId: string; disabled?: boolean }

/** This view has no write callbacks: older consumers can never be retargeted here. */
export default function ProfileConsumerImpact(props: Props) {
  return <ConsumerList key={props.profileId} {...props} />
}

function ConsumerList({ profileId, disabled = false }: Props) {
  const [page, setPage] = useState<ProfileConsumerPage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const requestId = useRef(0)
  useEffect(() => () => { requestId.current += 1 }, [])
  const load = async (after?: string) => {
    const request = ++requestId.current
    setLoading(true)
    setError(null)
    try {
      const result = await listGenericProfileConsumers(profileId, after)
      if (request !== requestId.current) return
      setPage((previous) => ({ ...result, consumers: after && previous
        ? [...new Map([...previous.consumers, ...result.consumers].map((row) => [row.key, row])).values()]
        : result.consumers }))
    } catch (error) {
      if (request === requestId.current) setError(error instanceof Error ? error.message : 'Could not load saved uses.')
    } finally {
      if (request === requestId.current) setLoading(false)
    }
  }
  return <Stack component="section" aria-label="Saved uses of this profile" spacing={1}>
    <Typography variant="subtitle1" component="h4">Where this structure is used</Typography>
    <Typography variant="body2">Saved agents and flow steps keep their exact revisions. Saving your edits does not update these uses. Only references you can currently access are shown, including history and archived items.</Typography>
    <Button disabled={loading || disabled} onClick={() => void load()}>
      {page ? 'Refresh saved uses' : 'Show saved uses'}
    </Button>
    {loading && <Typography role="status">Loading saved uses…</Typography>}
    {error && <Alert severity="error">{error} Previously loaded uses are retained. Retry with the same load button.</Alert>}
    {page && <>
      <Typography variant="body2">Latest profile revision when checked: {page.head_revision}.</Typography>
      {!loading && page.consumers.length === 0 && <Typography>No saved uses visible to you were found. This does not establish that nobody else uses this profile.</Typography>}
      <Stack component="ul" spacing={1} sx={{ pl: 3, m: 0 }}>
        {page.consumers.map((row) => <Stack component="li" key={row.key} sx={{ display: 'list-item', overflowWrap: 'anywhere' }}>
          <Typography>{row.kind === 'flow' ? 'Flow' : 'Agent'}: {row.name}{row.archived ? ' · archived' : ''}</Typography>
          <Typography variant="body2">
            Profile revision {row.profile_revision}{row.profile_revision < page.head_revision ? ' · older profile revision' : ''}; agent revision {row.agent_revision}
            {row.kind === 'agent' ? (row.is_current_agent_revision ? ' · current agent configuration' : ' · historical agent configuration') : ` · step ${row.node_id}`}
          </Typography>
          <details><summary>Exact saved identity</summary><Typography variant="body2">Agent revision: {row.agent_revision_id}{row.flow_id ? `; flow: ${row.flow_id}` : ''}</Typography></details>
        </Stack>)}
      </Stack>
      {page.next_cursor && <Button disabled={loading || disabled} onClick={() => void load(page.next_cursor ?? undefined)}>Load more saved uses</Button>}
    </>}
  </Stack>
}
