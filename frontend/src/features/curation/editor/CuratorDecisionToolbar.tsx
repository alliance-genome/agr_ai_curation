import { useCallback, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material'

import { submitCurationCandidateDecision } from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
  CurationCandidateAction,
  CurationCandidateDecisionResponse,
} from '@/features/curation/types'
import {
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import {
  appendWorkspaceActionLogEntry,
  replaceWorkspaceCandidate,
  replaceWorkspaceSession,
  updateWorkspaceActiveCandidate,
} from '@/features/curation/workspace/workspaceState'

import RejectReasonDialog from './RejectReasonDialog'
import ResetConfirmationDialog from './ResetConfirmationDialog'

function orderCandidates(candidates: CurationCandidate[]): CurationCandidate[] {
  return [...candidates].sort((left, right) => left.order - right.order)
}

function formatCandidateValue(value: unknown): string | null {
  if (typeof value === 'string') {
    const normalized = value.trim()
    return normalized.length > 0 ? normalized : null
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  if (Array.isArray(value)) {
    const values = value
      .map((entry) => formatCandidateValue(entry))
      .filter((entry): entry is string => entry !== null)
    return values.length > 0 ? values.join(', ') : null
  }

  return null
}

function candidatePrimaryLabel(candidate: CurationCandidate): string {
  const explicitLabel = [
    candidate.display_label,
    candidate.secondary_label,
    candidate.draft.title,
  ].find((label) => typeof label === 'string' && label.trim().length > 0)
  if (explicitLabel) {
    return explicitLabel
  }

  for (const field of candidate.draft.fields) {
    const fieldValue = formatCandidateValue(field.value ?? field.seed_value)
    if (fieldValue) {
      return fieldValue
    }
  }

  return candidate.candidate_id
}

function nextCandidateId(
  orderedCandidates: CurationCandidate[],
  activeCandidateId: string | null,
): string | null {
  if (!activeCandidateId) {
    return null
  }

  const currentIndex = orderedCandidates.findIndex(
    (candidate) => candidate.candidate_id === activeCandidateId,
  )
  if (currentIndex < 0) {
    return null
  }

  return orderedCandidates[currentIndex + 1]?.candidate_id ?? null
}

function candidateContextLabel(
  activeCandidate: CurationCandidate | null,
  orderedCandidates: CurationCandidate[],
  adapterLabel: string,
): string {
  if (!activeCandidate) {
    return 'Select a candidate to review.'
  }

  const currentIndex = orderedCandidates.findIndex(
    (candidate) => candidate.candidate_id === activeCandidate.candidate_id,
  )
  const position = currentIndex >= 0 ? currentIndex + 1 : 0
  return `Candidate ${position} of ${orderedCandidates.length} — ${adapterLabel} / ${candidatePrimaryLabel(activeCandidate)}`
}

export default function CuratorDecisionToolbar() {
  const {
    activeCandidate,
    activeCandidateId,
    candidates,
    session,
    setActiveCandidate,
    setWorkspace,
  } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const [pendingAction, setPendingAction] = useState<CurationCandidateAction | null>(null)
  const [isRejectDialogOpen, setIsRejectDialogOpen] = useState(false)
  const [isResetDialogOpen, setIsResetDialogOpen] = useState(false)
  const [rejectReason, setRejectReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const orderedCandidates = useMemo(() => orderCandidates(candidates), [candidates])
  const adapterLabel = session.adapter.display_label ?? session.adapter.adapter_key
  const skipCandidateId = useMemo(
    () => nextCandidateId(orderedCandidates, activeCandidateId),
    [activeCandidateId, orderedCandidates],
  )
  const isSubmitting = pendingAction !== null

  const applyDecisionResponse = useCallback(
    (
      response: CurationCandidateDecisionResponse,
      fallbackCandidateId: string | null,
    ) => {
      const nextActiveCandidateId = (
        response.next_candidate_id
        ?? response.session.current_candidate_id
        ?? fallbackCandidateId
      )

      setWorkspace((currentWorkspace) => {
        const nextWorkspace = appendWorkspaceActionLogEntry(
          replaceWorkspaceSession(
            replaceWorkspaceCandidate(currentWorkspace, response.candidate),
            response.session,
          ),
          response.action_log_entry,
        )

        return updateWorkspaceActiveCandidate(nextWorkspace, nextActiveCandidateId)
      })
    },
    [setWorkspace],
  )

  const submitDecision = useCallback(
    async (
      action: CurationCandidateAction,
      options: {
        reason?: string | null
        advanceQueue?: boolean
      } = {},
    ): Promise<boolean> => {
      if (!activeCandidate) {
        return false
      }

      setError(null)
      setPendingAction(action)

      try {
        const draftSaved = await autosave.flush()
        if (!draftSaved) {
          setError('Unable to save the current draft before updating this candidate.')
          return false
        }

        const response = await submitCurationCandidateDecision({
          session_id: session.session_id,
          candidate_id: activeCandidate.candidate_id,
          action,
          reason: options.reason?.trim() || undefined,
          advance_queue: options.advanceQueue ?? true,
        })

        applyDecisionResponse(response, activeCandidate.candidate_id)
        if (response.next_candidate_id) {
          setActiveCandidate(response.next_candidate_id)
        }
        return true
      } catch (submissionError) {
        setError(
          submissionError instanceof Error
            ? submissionError.message
            : 'Unable to update this candidate.',
        )
        return false
      } finally {
        setPendingAction(null)
      }
    },
    [activeCandidate, applyDecisionResponse, autosave, session.session_id, setActiveCandidate],
  )

  const handleAccept = useCallback(() => {
    void submitDecision('accept')
  }, [submitDecision])

  const handleRejectConfirm = useCallback(async () => {
    const rejected = await submitDecision('reject', { reason: rejectReason })
    if (!rejected) {
      return
    }

    setIsRejectDialogOpen(false)
    setRejectReason('')
  }, [rejectReason, submitDecision])

  const handleResetConfirm = useCallback(async () => {
    const reset = await submitDecision('reset', { advanceQueue: false })
    if (!reset) {
      return
    }

    setIsResetDialogOpen(false)
  }, [submitDecision])

  const contextLabel = useMemo(
    () => candidateContextLabel(activeCandidate, orderedCandidates, adapterLabel),
    [activeCandidate, adapterLabel, orderedCandidates],
  )

  return (
    <>
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          gap: 1,
          px: 2,
          py: 1.25,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 1.5,
            flexWrap: 'wrap',
          }}
        >
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              fontSize: '0.8rem',
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flexShrink: 1,
            }}
          >
            {contextLabel}
          </Typography>

          <Stack direction="row" flexShrink={0} spacing={0.75} useFlexGap>
            <Button
              color="inherit"
              disabled={!activeCandidate || isSubmitting}
              onClick={() => setIsResetDialogOpen(true)}
              size="small"
              variant="outlined"
              sx={{ fontSize: '0.75rem', py: 0.4, px: 1.25, minWidth: 0 }}
            >
              {pendingAction === 'reset' ? 'Resetting...' : 'Reset'}
            </Button>
            <Button
              disabled={!skipCandidateId || isSubmitting}
              onClick={() => {
                if (!skipCandidateId) {
                  return
                }
                setError(null)
                setActiveCandidate(skipCandidateId)
              }}
              size="small"
              variant="outlined"
              sx={{ fontSize: '0.75rem', py: 0.4, px: 1.25, minWidth: 0 }}
            >
              Skip
            </Button>
            <Button
              color="error"
              disabled={!activeCandidate || isSubmitting}
              onClick={() => setIsRejectDialogOpen(true)}
              size="small"
              variant="outlined"
              sx={{ fontSize: '0.75rem', py: 0.4, px: 1.5, minWidth: 0 }}
            >
              {pendingAction === 'reject' ? 'Rejecting...' : 'Reject'}
            </Button>
            <Button
              color="success"
              disabled={!activeCandidate || isSubmitting}
              onClick={handleAccept}
              size="small"
              variant="contained"
              sx={{ fontSize: '0.75rem', py: 0.4, px: 1.5, minWidth: 0, fontWeight: 700 }}
            >
              {pendingAction === 'accept' ? 'Accepting...' : 'Accept'}
            </Button>
          </Stack>
        </Box>

        {error ? <Alert severity="error" sx={{ py: 0.25 }}>{error}</Alert> : null}
      </Box>

      <RejectReasonDialog
        onClose={() => {
          if (isSubmitting) {
            return
          }
          setIsRejectDialogOpen(false)
          setRejectReason('')
        }}
        onConfirm={handleRejectConfirm}
        onReasonChange={setRejectReason}
        open={isRejectDialogOpen}
        reason={rejectReason}
        submitting={pendingAction === 'reject'}
      />

      <ResetConfirmationDialog
        onClose={() => {
          if (isSubmitting) {
            return
          }
          setIsResetDialogOpen(false)
        }}
        onConfirm={handleResetConfirm}
        open={isResetDialogOpen}
        submitting={pendingAction === 'reset'}
      />
    </>
  )
}
