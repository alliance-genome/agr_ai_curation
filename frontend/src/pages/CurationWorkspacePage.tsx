import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link as RouterLink, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from '@mui/material'

import {
  buildCurationPDFViewerOwner,
  dispatchPDFDocumentChanged,
} from '@/components/pdfViewer/pdfEvents'
import { buildManualCandidateDraft } from '@/features/curation/entityTags/workspaceEntityTags'
import {
  readCurationQueueNavigationState,
} from '@/features/curation/services/curationQueueNavigationService'
import { SubmissionPreviewDialog } from '@/features/curation/submission'
import {
  buildCurationWorkspaceEnvelopeReviewRowsRequests,
  createManualCurationCandidate,
  deleteCurationCandidate,
  executeCurationSubmission,
  fetchCurationWorkspaceEnvelopeReviewRows,
  fetchCurationWorkspace,
  submitCurationCandidateDecision,
} from '@/features/curation/services/curationWorkspaceService'
import { CandidateFieldEditor } from '@/features/curation/editor'
import AddManualObjectDialog, {
  type ManualObjectDraft,
} from '@/features/curation/workspace/AddManualObjectDialog'
import type {
  CurationCandidate,
  CurationSubmissionPreviewResponse,
  CurationWorkspace,
} from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
  useCurationWorkspaceHydration,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import { CurationWorkspaceRuntimeProvider } from '@/features/curation/workspace/CurationWorkspaceRuntimeProvider'
import WorkspaceHeader from '@/features/curation/workspace/WorkspaceHeader'
import WorkspaceShell from '@/features/curation/workspace/WorkspaceShell'
import WorkspaceSessionNavigation from '@/features/curation/workspace/WorkspaceSessionNavigation'
import { buildWorkspaceEnvelopeObjectReviewRows } from '@/features/curation/workspace/envelopeObjectReviewRows'
import ObjectSelectorStrip from '@/features/curation/workspace/ObjectSelectorStrip'
import WorkPaneToolbar from '@/features/curation/workspace/WorkPaneToolbar'
import {
  countValidatedPending,
  isValidatedPendingCandidate,
} from '@/features/curation/workspace/workPaneToolbar'
import type { ObjectSelectorRow } from '@/features/curation/workspace/objectSelector'
import {
  buildWorkspaceExpectedEnvelopeRevisions,
  mergeSubmissionExecutionIntoWorkspace,
  updateWorkspaceActiveCandidate,
} from '@/features/curation/workspace/workspaceState'

const WORKSPACE_STALE_TIME_MS = 60_000
// Temporary curator-facing WIP gate. Set true to restore the existing SubmissionPreviewDialog path.
const SUBMISSION_PREVIEW_ENABLED = false

function queryErrorMessage(error: unknown): string | null {
  if (error === null || error === undefined) {
    return null
  }

  if (error instanceof Error) {
    return error.message
  }

  return String(error)
}

function findCandidate(
  candidates: CurationCandidate[],
  candidateId?: string | null,
): CurationCandidate | null {
  if (!candidateId) {
    return null
  }

  return candidates.find((candidate) => candidate.candidate_id === candidateId) ?? null
}

function selectEntityTemplateCandidate(
  candidates: CurationCandidate[],
  activeCandidateId: string | null,
): CurationCandidate | null {
  if (activeCandidateId) {
    const activeCandidate = candidates.find((candidate) => candidate.candidate_id === activeCandidateId)
    if (activeCandidate) {
      return activeCandidate
    }
  }

  return candidates[0] ?? null
}

function CurationWorkspacePageContent({
  queueNavigationState,
}: {
  queueNavigationState: ReturnType<typeof readCurationQueueNavigationState>
}) {
  const {
    activeCandidateId,
    candidates,
    setActiveCandidate,
    setWorkspace,
    workspace,
  } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const hydration = useCurationWorkspaceHydration()
  const runtimeWarning = autosave.warning ?? hydration.warning
  const workspaceDocument = workspace.session.document
  const workspaceDocumentId = workspaceDocument.document_id
  const workspaceDocumentPdfUrl = workspaceDocument.pdf_url
  const workspaceDocumentPageCount = workspaceDocument.page_count
  const workspaceDocumentTitle = workspaceDocument.title
  const workspaceDocumentViewerUrl = workspaceDocument.viewer_url
  const viewerOwnerToken = useMemo(
    () => buildCurationPDFViewerOwner(workspace.session.session_id),
    [workspace.session.session_id],
  )
  const [manualObjectDialogOpen, setManualObjectDialogOpen] = useState(false)
  const [manualObjectCreating, setManualObjectCreating] = useState(false)
  const [submissionDialogOpen, setSubmissionDialogOpen] = useState(false)
  const [submissionWipDialogOpen, setSubmissionWipDialogOpen] = useState(false)
  const [tableError, setTableError] = useState<string | null>(null)
  const envelopeReviewRequests = useMemo(
    () => buildCurationWorkspaceEnvelopeReviewRowsRequests(workspace),
    [workspace],
  )
  const hasEnvelopeObjectRows = envelopeReviewRequests.length > 0
  const envelopeRowsQuery = useQuery({
    queryKey: [
      'curation-workspace-envelope-review-rows',
      workspace.session.session_id,
      envelopeReviewRequests,
    ],
    queryFn: () => fetchCurationWorkspaceEnvelopeReviewRows(workspace),
    enabled: hasEnvelopeObjectRows,
    staleTime: WORKSPACE_STALE_TIME_MS,
  })
  const envelopeObjectRows = useMemo(
    () => buildWorkspaceEnvelopeObjectReviewRows({
      candidates,
      evidenceAnchorProjections: workspace.evidence_anchor_projections ?? [],
      reviewRowResponses: envelopeRowsQuery.data ?? [],
      validationSummaryProjections: workspace.validation_summary_projections ?? [],
    }),
    [
      candidates,
      envelopeRowsQuery.data,
      workspace.evidence_anchor_projections,
      workspace.validation_summary_projections,
    ],
  )
  const expectedEnvelopeRevisions = useMemo(
    () => buildWorkspaceExpectedEnvelopeRevisions(candidates),
    [candidates],
  )
  const objectSelectorRows = useMemo<ObjectSelectorRow[]>(() => {
    const envelopeRowsByCandidateId = new Map(
      envelopeObjectRows.map((row) => [row.candidate.candidate_id, row]),
    )

    return candidates.map((candidate) => {
      const envelopeRow = envelopeRowsByCandidateId.get(candidate.candidate_id)
      if (envelopeRow) {
        return envelopeRow
      }

      return {
        candidate,
        reviewRow: null,
      }
    })
  }, [candidates, envelopeObjectRows])
  const pendingCandidateCount = useMemo(
    () => candidates.filter((candidate) => candidate.status === 'pending').length,
    [candidates],
  )
  const validatedPendingCandidateIds = useMemo(
    () => candidates
      .filter(isValidatedPendingCandidate)
      .map((candidate) => candidate.candidate_id),
    [candidates],
  )
  const validatedPendingCount = useMemo(
    () => countValidatedPending(candidates),
    [candidates],
  )
  const envelopeReviewRowsError = queryErrorMessage(envelopeRowsQuery.error)

  const handleSubmitPreview = useCallback(async (
    previewResponse: CurationSubmissionPreviewResponse,
  ) => {
    const submissionPayload = previewResponse.submission.payload
    if (!submissionPayload) {
      throw new Error(
        'Direct submission requires a preview payload. Refresh the submission preview and try again.',
      )
    }

    const response = await executeCurationSubmission({
      session_id: workspace.session.session_id,
      target_key: previewResponse.submission.target_key,
      candidate_ids: submissionPayload.candidate_ids,
      mode: 'direct_submit',
      expected_envelope_revisions: expectedEnvelopeRevisions,
    })

    setWorkspace((currentWorkspace) =>
      mergeSubmissionExecutionIntoWorkspace(currentWorkspace, response)
    )
  }, [expectedEnvelopeRevisions, setWorkspace, workspace.session.session_id])

  useEffect(() => {
    const pdfUrl = workspaceDocumentPdfUrl ?? workspaceDocumentViewerUrl
    if (!hydration.isHydrated || !workspaceDocumentId || !pdfUrl) {
      return
    }

    dispatchPDFDocumentChanged(
      workspaceDocumentId,
      pdfUrl,
      workspaceDocumentTitle,
      workspaceDocumentPageCount ?? 1,
      hydration.restoredScrollPosition === null
        ? { ownerToken: viewerOwnerToken }
        : {
            ownerToken: viewerOwnerToken,
            viewerState: {
              scrollPosition: hydration.restoredScrollPosition,
            },
          },
    )
  }, [
    hydration.isHydrated,
    hydration.restoredScrollPosition,
    workspaceDocumentId,
    workspaceDocumentPageCount,
    workspaceDocumentPdfUrl,
    workspaceDocumentTitle,
    workspaceDocumentViewerUrl,
    viewerOwnerToken,
  ])

  const refreshWorkspace = useCallback(async (preferredActiveCandidateId: string | null) => {
    const nextWorkspace = await fetchCurationWorkspace(workspace.session.session_id)
    setWorkspace(
      updateWorkspaceActiveCandidate(
        nextWorkspace,
        preferredActiveCandidateId
          ?? nextWorkspace.active_candidate_id
          ?? nextWorkspace.session.current_candidate_id
          ?? null,
      ),
    )
  }, [setWorkspace, workspace.session.session_id])

  const handleSelectTag = useCallback((tagId: string) => {
    setTableError(null)
    setActiveCandidate(tagId)
  }, [setActiveCandidate])

  const handleAcceptTag = useCallback(async (tagId: string) => {
    setTableError(null)

    try {
      const draftSaved = await autosave.flush()
      if (!draftSaved) {
        throw new Error('Unable to save the current draft before updating this entity.')
      }

      await submitCurationCandidateDecision({
        session_id: workspace.session.session_id,
        candidate_id: tagId,
        action: 'accept',
        advance_queue: false,
      })
      await refreshWorkspace(activeCandidateId === tagId ? tagId : activeCandidateId)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to accept this entity.'
      setTableError(message)
      throw error
    }
  }, [activeCandidateId, autosave, refreshWorkspace, workspace.session.session_id])

  const handleRejectTag = useCallback(async (tagId: string) => {
    setTableError(null)

    try {
      const draftSaved = await autosave.flush()
      if (!draftSaved) {
        throw new Error('Unable to save the current draft before updating this entity.')
      }

      await submitCurationCandidateDecision({
        session_id: workspace.session.session_id,
        candidate_id: tagId,
        action: 'reject',
        advance_queue: false,
      })
      await refreshWorkspace(activeCandidateId === tagId ? tagId : activeCandidateId)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to reject this entity.'
      setTableError(message)
      throw error
    }
  }, [activeCandidateId, autosave, refreshWorkspace, workspace.session.session_id])

  const handleDeleteTag = useCallback(async (tagId: string) => {
    setTableError(null)

    const candidate = candidates.find((currentCandidate) => currentCandidate.candidate_id === tagId)
    if (!candidate) {
      const missingCandidateError = new Error(`Unable to find candidate ${tagId} in the workspace.`)
      setTableError(missingCandidateError.message)
      throw missingCandidateError
    }

    try {
      const draftSaved = await autosave.flush()
      if (!draftSaved) {
        throw new Error('Unable to save the current draft before deleting this entity.')
      }

      const response = await deleteCurationCandidate({
        session_id: workspace.session.session_id,
        candidate_id: candidate.candidate_id,
      })
      await refreshWorkspace(response.session.current_candidate_id ?? null)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to delete this entity.'
      setTableError(message)
      throw error
    }
  }, [autosave, candidates, refreshWorkspace, workspace.session.session_id])

  const handleAcceptAllValidated = useCallback(async (tagIds: string[]) => {
    setTableError(null)

    if (tagIds.length === 0) {
      return
    }

    try {
      const draftSaved = await autosave.flush()
      if (!draftSaved) {
        throw new Error('Unable to save the current draft before updating these entities.')
      }

      const decisionResults = await Promise.allSettled(tagIds.map((tagId) =>
        submitCurationCandidateDecision({
          session_id: workspace.session.session_id,
          candidate_id: tagId,
          action: 'accept',
          advance_queue: false,
        })))

      const rejectedResults = decisionResults.filter(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      )
      const fulfilledCount = decisionResults.length - rejectedResults.length

      if (fulfilledCount > 0) {
        await refreshWorkspace(activeCandidateId)
      }

      if (rejectedResults.length > 0) {
        const firstError = rejectedResults[0].reason
        const firstErrorMessage = firstError instanceof Error
          ? firstError.message
          : 'Unknown workspace mutation error.'

        throw new Error(
          `Accepted ${fulfilledCount} of ${tagIds.length} validated entities. First error: ${firstErrorMessage}`,
        )
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to accept all validated entities.'
      setTableError(message)
      throw error
    }
  }, [activeCandidateId, autosave, refreshWorkspace, workspace.session.session_id])

  const handleCreateManualTag = useCallback(async (tag: ManualObjectDraft) => {
    setTableError(null)

    const templateCandidate = selectEntityTemplateCandidate(candidates, activeCandidateId)
    if (!templateCandidate) {
      const missingTemplateError = new Error(
        'Unable to add an entity because this workspace has no candidate template to clone.',
      )
      setTableError(missingTemplateError.message)
      throw missingTemplateError
    }

    try {
      const timestamp = new Date().toISOString()
      const draft = buildManualCandidateDraft(
        templateCandidate,
        {
          entity_name: tag.entity_name,
          entity_type: tag.entity_type,
          species: tag.species,
          topic: tag.topic,
        },
        timestamp,
      )

      const response = await createManualCurationCandidate({
        session_id: workspace.session.session_id,
        adapter_key: templateCandidate.adapter_key,
        source: 'manual',
        display_label: tag.entity_name.trim(),
        draft,
        evidence_anchors: [],
      })
      await refreshWorkspace(response.candidate.candidate_id)
      setActiveCandidate(response.candidate.candidate_id)
      return response.candidate.candidate_id
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to create this manual entity.'
      setTableError(message)
      throw error
    }
  }, [activeCandidateId, candidates, refreshWorkspace, setActiveCandidate, workspace.session.session_id])

  const handleCreateManualObject = useCallback(async (draft: ManualObjectDraft) => {
    setManualObjectCreating(true)

    try {
      await handleCreateManualTag(draft)
      setManualObjectDialogOpen(false)
    } finally {
      setManualObjectCreating(false)
    }
  }, [handleCreateManualTag])

  return (
    <Box
      sx={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        overflow: 'hidden',
        gap: 2,
      }}
    >
      {runtimeWarning ? (
        <Alert severity="warning">
          {runtimeWarning}
        </Alert>
      ) : null}

      {tableError ? (
        <Alert severity="error">
          {tableError}
        </Alert>
      ) : null}

      {envelopeReviewRowsError ? (
        <Alert severity="error">
          {envelopeReviewRowsError}
        </Alert>
      ) : null}

      <WorkspaceShell
        headerSlot={(
          <WorkspaceHeader
            navigationSlot={(
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <WorkspaceSessionNavigation
                  currentSessionId={workspace.session.session_id}
                  queueContext={queueNavigationState?.queueContext}
                  queueRequest={queueNavigationState?.queueRequest}
                />
                <Button
                  onClick={() => {
                    if (SUBMISSION_PREVIEW_ENABLED) {
                      setSubmissionDialogOpen(true)
                      return
                    }
                    setSubmissionWipDialogOpen(true)
                  }}
                  size="small"
                  variant="contained"
                  sx={{
                    borderRadius: 1,
                    fontSize: '0.75rem',
                    fontWeight: 500,
                    letterSpacing: 0,
                    minHeight: 32,
                    py: 0.5,
                    textTransform: 'none',
                  }}
                >
                  Preview submission
                </Button>
              </Stack>
            )}
            session={workspace.session}
          />
        )}
        selectorSlot={(
          <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
            <WorkPaneToolbar
              totalCount={candidates.length}
              pendingCount={pendingCandidateCount}
              validatedPendingCount={validatedPendingCount}
              onAcceptAllValidated={() => {
                void handleAcceptAllValidated(validatedPendingCandidateIds)
              }}
              onAddObject={() => setManualObjectDialogOpen(true)}
            />
            <ObjectSelectorStrip
              activeCandidateId={activeCandidateId}
              onDelete={(candidateId) => {
                void handleDeleteTag(candidateId)
              }}
              onSelect={handleSelectTag}
              rows={objectSelectorRows}
            />
          </Box>
        )}
        fieldEditorSlot={(
          <CandidateFieldEditor
            onAcceptCandidate={handleAcceptTag}
            onRejectCandidate={handleRejectTag}
          />
        )}
      />

      {SUBMISSION_PREVIEW_ENABLED ? (
        <SubmissionPreviewDialog
          candidates={candidates}
          expectedEnvelopeRevisions={expectedEnvelopeRevisions}
          onClose={() => setSubmissionDialogOpen(false)}
          onSubmit={handleSubmitPreview}
          open={submissionDialogOpen}
          session={workspace.session}
        />
      ) : null}

      {/* Submission preview is intentionally disabled while the export/submit workflow is being rebuilt. */}
      <Dialog
        open={submissionWipDialogOpen}
        onClose={() => setSubmissionWipDialogOpen(false)}
        fullWidth
        maxWidth="sm"
      >
        <DialogTitle>Submission preview is in progress</DialogTitle>
        <DialogContent dividers>
          <Typography color="text.secondary" variant="body2">
            Submission preview and submission actions are a work in progress and are disabled
            at the moment. Curators can continue reviewing and validating objects here; the
            submission workflow will be re-enabled when it is ready.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSubmissionWipDialogOpen(false)} variant="contained">
            OK
          </Button>
        </DialogActions>
      </Dialog>

      <AddManualObjectDialog
        isCreating={manualObjectCreating}
        onCancel={() => setManualObjectDialogOpen(false)}
        onCreate={(draft) => {
          void handleCreateManualObject(draft)
        }}
        open={manualObjectDialogOpen}
      />
    </Box>
  )
}

function CurationWorkspacePage() {
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { sessionId, candidateId } = useParams<{
    sessionId: string
    candidateId?: string
  }>()
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null)
  const workspaceSessionId = typeof sessionId === 'string' && sessionId.length > 0
    ? sessionId
    : null
  const queueNavigationState = readCurationQueueNavigationState(location.state)

  const workspaceQuery = useQuery({
    queryKey: ['curation-workspace', workspaceSessionId],
    queryFn: async () => {
      if (!workspaceSessionId) {
        throw new Error('Missing curation session identifier.')
      }

      return fetchCurationWorkspace(workspaceSessionId)
    },
    enabled: workspaceSessionId !== null,
    staleTime: WORKSPACE_STALE_TIME_MS,
  })

  const workspace = workspaceQuery.data ?? null

  const setActiveCandidate = useCallback(
    (nextCandidateId: string | null, options?: { replace?: boolean }) => {
      if (!sessionId) {
        return
      }

      setActiveCandidateId(nextCandidateId)
      navigate(
        nextCandidateId
          ? `/curation/${sessionId}/${nextCandidateId}`
          : `/curation/${sessionId}`,
        {
          replace: options?.replace ?? false,
          state: location.state,
        },
      )
    },
    [location.state, navigate, sessionId],
  )

  const activeCandidate = useMemo(
    () => findCandidate(workspace?.candidates ?? [], activeCandidateId),
    [activeCandidateId, workspace?.candidates],
  )
  useEffect(() => {
    setActiveCandidateId(null)
  }, [workspaceSessionId])

  const setWorkspace = useCallback(
    (
      nextWorkspace:
        | CurationWorkspace
        | ((currentWorkspace: CurationWorkspace) => CurationWorkspace),
    ) => {
      if (!workspaceSessionId) {
        return
      }

      queryClient.setQueryData<CurationWorkspace | null>(
        ['curation-workspace', workspaceSessionId],
        (currentWorkspace) => {
          if (!currentWorkspace) {
            return currentWorkspace
          }

          return typeof nextWorkspace === 'function'
            ? nextWorkspace(currentWorkspace)
            : nextWorkspace
        },
      )
    },
    [queryClient, workspaceSessionId],
  )

  const contextValue = useMemo(() => {
    if (!workspace) {
      return null
    }

    return {
      workspace,
      setWorkspace,
      session: workspace.session,
      candidates: workspace.candidates,
      activeCandidateId,
      activeCandidate,
      setActiveCandidate,
    }
  }, [activeCandidate, activeCandidateId, setActiveCandidate, setWorkspace, workspace])

  if (!sessionId) {
    return (
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          p: 3,
        }}
      >
        <Alert
          severity="error"
          action={(
            <Button color="inherit" component={RouterLink} to="/curation">
              Back to inventory
            </Button>
          )}
        >
          Missing curation session identifier.
        </Alert>
      </Box>
    )
  }

  if (workspaceQuery.isLoading) {
    return (
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Stack spacing={2} alignItems="center">
          <CircularProgress />
          <Typography color="text.secondary">
            Loading curation workspace...
          </Typography>
        </Stack>
      </Box>
    )
  }

  if (!workspace || contextValue === null) {
    const message = workspaceQuery.error instanceof Error
      ? workspaceQuery.error.message
      : 'Unable to load this curation workspace.'

    return (
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          p: 3,
        }}
      >
        <Alert
          severity="error"
          action={(
            <Button color="inherit" onClick={() => void workspaceQuery.refetch()}>
              Retry
            </Button>
          )}
        >
          {message}
        </Alert>
      </Box>
    )
  }

  return (
    <CurationWorkspaceProvider value={contextValue}>
      <CurationWorkspaceRuntimeProvider routeCandidateId={candidateId}>
        <CurationWorkspacePageContent queueNavigationState={queueNavigationState} />
      </CurationWorkspaceRuntimeProvider>
    </CurationWorkspaceProvider>
  )
}

export default CurationWorkspacePage
