import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Typography,
} from '@mui/material'

import {
  createCurationBenchmarkSnapshot,
  CurationWorkspaceRequestError,
  fetchBenchmarkDestinations,
  handoffCurationBenchmarkSnapshot,
} from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationBenchmarkDestination,
  CurationBenchmarkHandoffResponse,
  CurationBenchmarkSnapshotCreateResponse,
} from '@/features/curation/types'

export interface BenchmarkSnapshotEnvelopeOption {
  envelopeId: string
  revision: number
}

interface BenchmarkSnapshotDialogProps {
  envelopes: BenchmarkSnapshotEnvelopeOption[]
  hasUnsavedChanges: boolean
  onClose: () => void
  open: boolean
  sessionId: string
}

function requestErrorMessage(error: unknown): string {
  if (error instanceof CurationWorkspaceRequestError) {
    if (error.status === 401 || error.status === 403) {
      return 'You are not authorized to send this snapshot. Sign in again or ask an administrator for access.'
    }
    if (error.status === 409) {
      return 'This envelope revision is stale. Refresh the workspace before creating a snapshot.'
    }
  }
  return error instanceof Error ? error.message : 'The benchmark destination is unavailable.'
}

export default function BenchmarkSnapshotDialog({
  envelopes,
  hasUnsavedChanges,
  onClose,
  open,
  sessionId,
}: BenchmarkSnapshotDialogProps) {
  const [destinations, setDestinations] = useState<CurationBenchmarkDestination[]>([])
  const [selectedDestinationId, setSelectedDestinationId] = useState('')
  const [selectedEnvelopeId, setSelectedEnvelopeId] = useState('')
  const [destinationsLoading, setDestinationsLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [snapshots, setSnapshots] = useState<Record<string, CurationBenchmarkSnapshotCreateResponse>>({})
  const [handoffs, setHandoffs] = useState<Record<string, CurationBenchmarkHandoffResponse>>({})

  const selectedEnvelope = useMemo(
    () => envelopes.find((envelope) => envelope.envelopeId === selectedEnvelopeId) ?? envelopes[0] ?? null,
    [envelopes, selectedEnvelopeId],
  )
  const snapshotKey = JSON.stringify([sessionId, selectedEnvelope?.envelopeId, selectedEnvelope?.revision])
  const snapshot = snapshots[snapshotKey] ?? null
  const handoffKey = JSON.stringify([snapshotKey, selectedDestinationId])
  const handoff = handoffs[handoffKey] ?? null

  useEffect(() => {
    if (!open) {
      return
    }
    setDestinationsLoading(true)
    setError(null)

    let active = true
    void fetchBenchmarkDestinations()
      .then((response) => {
        if (!active) return
        setDestinations(response.destinations)
        setSelectedDestinationId((current) => response.destinations.some(
          (destination) => destination.destination_id === current,
        ) ? current : response.destinations[0]?.destination_id ?? '')
      })
      .catch((destinationError: unknown) => {
        if (active) {
          setDestinations([])
          setSelectedDestinationId('')
          setError(requestErrorMessage(destinationError))
        }
      })
      .finally(() => {
        if (active) setDestinationsLoading(false)
      })

    return () => {
      active = false
    }
  }, [open])

  const handleEnvelopeChange = (envelopeId: string) => {
    setSelectedEnvelopeId(envelopeId)
    setError(null)
  }

  const handleSnapshot = async (send: boolean) => {
    if (sending || !selectedEnvelope || hasUnsavedChanges || (send && !selectedDestinationId)) return

    setSending(true)
    setError(null)
    try {
      const createdSnapshot = snapshot ?? await createCurationBenchmarkSnapshot(
        sessionId,
        selectedEnvelope.envelopeId,
        { expected_revision: selectedEnvelope.revision },
      )
      if (!snapshot) setSnapshots((current) => ({ ...current, [snapshotKey]: createdSnapshot }))
      if (send) {
        const handoffResult = await handoffCurationBenchmarkSnapshot(
          createdSnapshot.snapshot_id,
          { destination_id: selectedDestinationId },
        )
        setHandoffs((current) => ({ ...current, [handoffKey]: handoffResult }))
      }
    } catch (sendError) {
      setError(requestErrorMessage(sendError))
    } finally {
      setSending(false)
    }
  }

  const destinationUnavailable = !destinationsLoading && destinations.length === 0
  const sendDisabled = sending
    || destinationsLoading
    || destinationUnavailable
    || !selectedDestinationId
    || hasUnsavedChanges
    || selectedEnvelope === null
    || handoff?.status === 'unknown'
    || handoff?.status === 'succeeded'

  return (
    <Dialog open={open} onClose={sending ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>Send snapshot to Benchmark</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography color="text.secondary" variant="body2">
            Create an immutable JSON snapshot of the displayed persisted envelope revision and send it to a configured comparison destination. Validation and approval status do not affect eligibility.
          </Typography>

          {hasUnsavedChanges ? (
            <Alert severity="warning">
              Save the current changes before creating a snapshot. The workspace will not export a different persisted revision.
            </Alert>
          ) : null}

          {envelopes.length === 0 ? (
            <Alert severity="info">This workspace does not contain a persisted envelope to snapshot.</Alert>
          ) : (
            <FormControl fullWidth size="small">
              <InputLabel id="benchmark-envelope-label">Persisted envelope</InputLabel>
              <Select
                labelId="benchmark-envelope-label"
                label="Persisted envelope"
                value={selectedEnvelope?.envelopeId ?? ''}
                disabled={sending}
                onChange={(event) => handleEnvelopeChange(event.target.value)}
              >
                {envelopes.map((envelope) => (
                  <MenuItem key={envelope.envelopeId} value={envelope.envelopeId}>
                    {envelope.envelopeId} · revision {envelope.revision}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {destinationsLoading ? (
            <Stack direction="row" spacing={1} alignItems="center" role="status">
              <CircularProgress size={18} />
              <Typography variant="body2">Loading benchmark destinations…</Typography>
            </Stack>
          ) : destinationUnavailable ? (
            <Alert severity="info">No benchmark destination is currently available.</Alert>
          ) : (
            <FormControl fullWidth size="small">
              <InputLabel id="benchmark-destination-label">Benchmark destination</InputLabel>
              <Select
                labelId="benchmark-destination-label"
                label="Benchmark destination"
                value={selectedDestinationId}
                disabled={sending}
                onChange={(event) => {
                  setSelectedDestinationId(event.target.value)
                  setError(null)
                }}
              >
                {destinations.map((destination) => (
                  <MenuItem key={destination.destination_id} value={destination.destination_id}>
                    {destination.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}

          {error ? <Alert severity="error">{error}</Alert> : null}
          {sending ? (
            <Stack direction="row" spacing={1} alignItems="center" role="status">
              <CircularProgress size={18} aria-hidden="true" />
              <Typography variant="body2">Preparing snapshot or delivering it to Benchmark…</Typography>
            </Stack>
          ) : null}
          {handoff?.status === 'succeeded' ? (
            <Alert severity="success">
              Snapshot delivery succeeded. Benchmark processing may continue in the destination.
            </Alert>
          ) : null}
          {handoff?.status === 'failed' ? (
            <Alert severity="error">
              Snapshot delivery failed. Download the preserved bundle for manual recovery.
            </Alert>
          ) : null}
          {handoff?.status === 'unknown' ? (
            <Alert severity="warning">
              Delivery could not be confirmed. The snapshot was preserved; it was not sent again automatically.
            </Alert>
          ) : null}

          {snapshot ? (
            <Button component="a" href={snapshot.download_path} download variant="outlined">
              Download benchmark bundle JSON
            </Button>
          ) : (
            <Button
              onClick={() => void handleSnapshot(false)}
              disabled={sending || hasUnsavedChanges || selectedEnvelope === null}
              variant="outlined"
            >
              Prepare benchmark bundle JSON for download
            </Button>
          )}
          {handoff?.status === 'succeeded' && handoff.redirect_path ? (
            <Button
              component="a"
              href={handoff.redirect_path}
              target="_blank"
              rel="noopener noreferrer"
              variant="contained"
            >
              Open Benchmark
            </Button>
          ) : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={sending}>Close</Button>
        <Button onClick={() => void handleSnapshot(true)} disabled={sendDisabled} variant="contained">
          {sending ? 'Sending snapshot…' : 'Send snapshot'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
