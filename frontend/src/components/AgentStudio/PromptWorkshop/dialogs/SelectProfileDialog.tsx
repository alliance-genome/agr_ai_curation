import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Typography } from '@mui/material'
import { getGenericProfile, getGenericProfileRevision, listGenericProfiles, type GenericProfileDetail, type GenericProfileSummary } from '@/services/genericProfileService'
import { profileFieldRows } from '../profileEditorModel'

interface Props {
  onSelect: (profile: GenericProfileDetail) => void
  onClose: () => void
}

/** Browse immutable source data; nothing enters the authorable draft until Use. */
export default function SelectProfileDialog({ onSelect, onClose }: Props) {
  const [profiles, setProfiles] = useState<GenericProfileSummary[]>([])
  const [cursor, setCursor] = useState<string | undefined>()
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selection, setSelection] = useState<GenericProfileDetail | null>(null)
  const [loadingSelection, setLoadingSelection] = useState(false)
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const requestId = useRef(0)
  useEffect(() => () => { requestId.current += 1 }, [])
  useEffect(() => {
    let canceled = false
    setLoading(true)
    setError(null)
    void listGenericProfiles(cursor).then((page) => {
      if (canceled) return
      setProfiles((existing) => cursor ? [...existing, ...page.profiles.filter((profile) => !existing.some((item) => item.id === profile.id))] : page.profiles)
      setNextCursor(page.next_cursor)
    }).catch((error: unknown) => {
      if (!canceled) setError(error instanceof Error ? error.message : 'Could not load saved structures.')
    }).finally(() => { if (!canceled) setLoading(false) })
    return () => { canceled = true }
  }, [cursor, attempt])

  const inspect = async (profile: GenericProfileSummary) => {
    const request = ++requestId.current
    setLoadingSelection(true)
    setSelection(null)
    setSelectionError(null)
    try {
      const [detail, revision] = await Promise.all([
        getGenericProfile(profile.id), getGenericProfileRevision(profile.id, profile.head_revision),
      ])
      if (detail.profile.id !== profile.id || revision.profile_id !== profile.id || revision.revision !== profile.head_revision) {
        throw new Error('The selected revision identity changed. Reload the list and choose again.')
      }
      if (request === requestId.current) setSelection({ ...detail, revision })
    } catch (error) {
      if (request === requestId.current) setSelectionError(error instanceof Error ? error.message : 'Could not load this structure. Choose it again to retry.')
    } finally {
      if (request === requestId.current) setLoadingSelection(false)
    }
  }

  return <Dialog open onClose={onClose} fullWidth maxWidth="md" aria-labelledby="select-profile-title">
    <DialogTitle id="select-profile-title">Use an existing Output Structure</DialogTitle>
    <DialogContent>
      <Stack spacing={2}>
        <Typography>Choose a saved revision to inspect. Using it replaces only the Output Structure in this draft, including any unsaved field edits. Nothing is saved yet.</Typography>
        {error && <Alert severity="error" action={<Button onClick={() => setAttempt((value) => value + 1)}>Retry</Button>}>{error}</Alert>}
        {loading && <Typography role="status">Loading saved structures…</Typography>}
        {!loading && !error && profiles.length === 0 && <Typography>No saved structures are available. Cancel to keep creating your own.</Typography>}
        {profiles.map((profile) => <Button key={profile.id} variant={selection?.profile.id === profile.id ? 'contained' : 'outlined'}
          onClick={() => void inspect(profile)} sx={{ justifyContent: 'flex-start', textAlign: 'left' }}>
          {profile.name} · revision {profile.head_revision} · {profile.semantic_class}
        </Button>)}
        {nextCursor && <Button disabled={loading || Boolean(error)} onClick={() => setCursor(nextCursor)}>Load more structures</Button>}
        {loadingSelection && <Typography role="status">Loading the selected revision…</Typography>}
        {selectionError && <Alert severity="error">{selectionError} Select the structure again to retry.</Alert>}
        {selection && <Stack spacing={1} component="section" aria-label="Selected structure preview">
          <Typography variant="h6">{selection.revision.contract.name} · revision {selection.revision.revision}</Typography>
          <Typography>{selection.revision.contract.description}</Typography>
          <Typography>Record class: {selection.revision.contract.semantic_class}</Typography>
          <Typography>{selection.can_edit ? 'You can save edits as a new revision when editing an existing agent.' : 'This source is not editable. Saving changes creates your own copy.'} Existing consumers keep their saved revisions.</Typography>
          {selection.profile.head_revision !== selection.revision.revision && <Alert severity="info">A newer revision exists. This selection still uses the displayed revision; cancel and reopen to refresh the list.</Alert>}
          {profileFieldRows(selection.revision.contract).map((row) => <Typography key={row.schemaPath}>{row.field.display_name || row.field.key} · {row.field.value_schema.kind}</Typography>)}
        </Stack>}
      </Stack>
    </DialogContent>
    <DialogActions>
      <Button onClick={onClose}>Cancel</Button>
      <Button variant="contained" disabled={!selection || loadingSelection} onClick={() => { if (selection) onSelect(selection) }}>Use this revision</Button>
    </DialogActions>
  </Dialog>
}
