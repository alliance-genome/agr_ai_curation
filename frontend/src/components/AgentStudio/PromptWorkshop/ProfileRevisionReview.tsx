import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Typography } from '@mui/material'
import { compareGenericProfileRevision, getGenericProfile, type GenericProfileDetail, type ProfileRevisionComparison } from '@/services/genericProfileService'
import { canonicalAuthoringJson } from '../authoringContext'
import type { WorkshopOutputDraft } from './workshopOutputDraft'
import ProfileConsumerImpact from './ProfileConsumerImpact'

interface Props {
  value: WorkshopOutputDraft
  onLoadRevision: (profile: GenericProfileDetail) => void
  onMakeCopy: () => void
  disabled?: boolean
}

/** Read-only comparison is not an AI proposal and owns no proposal lifecycle. */
export default function ProfileRevisionReview({ value, onLoadRevision, onMakeCopy, disabled = false }: Props) {
  const [result, setResult] = useState<{ key: string; comparison: ProfileRevisionComparison; source: GenericProfileDetail } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmLoad, setConfirmLoad] = useState(false)
  const requestId = useRef(0)
  useEffect(() => () => { requestId.current += 1 }, [])
  const key = canonicalAuthoringJson(value)
  const current = result?.key === key
  const compare = async (latest: boolean) => {
    if (!value.profilePin || !value.profileContract) return
    const request = ++requestId.current
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const source = await getGenericProfile(value.profilePin.profile_id)
      if (source.profile.id !== value.profilePin.profile_id) throw new Error('The loaded profile identity changed. Compare again.')
      const revision = latest ? source.revision.revision : value.profilePin.revision
      const comparison = await compareGenericProfileRevision(value.profilePin.profile_id, revision, value.profileContract)
      if (comparison.base_revision.profile_id !== value.profilePin.profile_id || comparison.base_revision.revision !== revision
        || (!latest && (comparison.base_revision.id !== value.profilePin.profile_revision_id || comparison.base_revision.fingerprint !== value.profilePin.fingerprint))) {
        throw new Error('The comparison returned a different saved revision. Reload and compare again.')
      }
      if (request === requestId.current) setResult({ key, comparison, source: { ...source, revision: comparison.base_revision } })
    } catch (error) {
      if (request === requestId.current) setError(error instanceof Error ? error.message : 'Could not compare revisions. Check the draft and try again.')
    } finally {
      if (request === requestId.current) setLoading(false)
    }
  }
  return <Stack component="section" aria-label="Profile revision comparison" spacing={1.5}>
    <Typography variant="h6" component="h3">Saved revision and changes</Typography>
    <Typography>Selected profile revision {value.profilePin?.revision}. Comparing does not save or change this draft. Older agents and flows keep their pins.</Typography>
    <Stack direction="row" useFlexGap flexWrap="wrap" gap={1}>
      <Button disabled={loading || disabled} onClick={() => void compare(false)}>Compare with selected revision</Button>
      <Button disabled={loading || disabled} onClick={() => void compare(true)}>Compare with latest revision</Button>
      <Button disabled={disabled} onClick={onMakeCopy}>Use a separate profile copy</Button>
    </Stack>
    {loading && <Typography role="status">Comparing structure requirements…</Typography>}
    {error && <Alert severity="error">{error} Your draft has not changed. Correct any invalid fields and compare again.</Alert>}
    {result && <>
      {!current && <Alert severity="warning">This comparison is out of date because the draft changed. Compare again before loading a revision.</Alert>}
      <Typography>Saved revision {result.comparison.base_revision.revision} → your draft</Typography>
      <Alert severity={result.comparison.compatibility.some((finding) => finding.breaking) ? 'warning' : 'info'}>
        {result.comparison.compatibility.some((finding) => finding.breaking)
          ? 'Some edits change requirements: records accepted by the saved revision may not fit this draft.'
          : 'No breaking structure changes were found. This is not a semantic validation or submission-readiness check.'}
      </Alert>
      {result.comparison.compatibility.length === 0 && <Typography>No contract changes.</Typography>}
      {result.comparison.compatibility.map((finding, index) => <Stack key={`${finding.path}:${finding.code}:${index}`} spacing={0.5}>
        <Typography>{finding.path}: {finding.code.replaceAll('_', ' ')} · {finding.breaking ? 'changes requirements' : 'informational'}</Typography>
        <details><summary>Show exact change</summary><Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>Before: {JSON.stringify(finding.before)}; Draft: {JSON.stringify(finding.after)}</Typography></details>
      </Stack>)}
      <Button disabled={!current || loading || disabled} onClick={() => setConfirmLoad(true)}>Load compared revision into draft</Button>
    </>}
    {value.profilePin && <ProfileConsumerImpact profileId={value.profilePin.profile_id} disabled={disabled} />}
    <Dialog open={confirmLoad} onClose={() => setConfirmLoad(false)} aria-labelledby="load-profile-revision-title">
      <DialogTitle id="load-profile-revision-title">Replace draft structure?</DialogTitle>
      <DialogContent>This replaces unsaved structure edits with the compared saved revision. Other agent settings stay unchanged. Nothing is saved until you save the agent.</DialogContent>
      <DialogActions>
        <Button autoFocus onClick={() => setConfirmLoad(false)}>Keep editing</Button>
        <Button disabled={!current || loading || disabled} onClick={() => {
          if (result && current && !disabled) onLoadRevision(result.source)
          setConfirmLoad(false)
        }}>Load revision</Button>
      </DialogActions>
    </Dialog>
  </Stack>
}
