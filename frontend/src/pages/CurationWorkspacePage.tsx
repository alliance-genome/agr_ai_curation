import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link as RouterLink, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material'

import {
  buildCurationPDFViewerOwner,
  dispatchPDFDocumentChanged,
} from '@/components/pdfViewer/pdfEvents'
import { EntityTagTable, type EntityTag } from '@/features/curation/entityTable'
import {
  buildEntityTagFieldChanges,
  buildManualCandidateDraft,
} from '@/features/curation/entityTable/workspaceEntityTags'
import {
  readCurationQueueNavigationState,
} from '@/features/curation/services/curationQueueNavigationService'
import { SubmissionPreviewDialog } from '@/features/curation/submission'
import {
  autosaveCurationCandidateDraft,
  createManualCurationCandidate,
  fetchCurationWorkspace,
  submitCurationCandidateDecision,
  validateCurationCandidate,
} from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
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
import {
  updateWorkspaceActiveCandidate,
} from '@/features/curation/workspace/workspaceState'

const WORKSPACE_STALE_TIME_MS = 60_000

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
  const [submissionDialogOpen, setSubmissionDialogOpen] = useState(false)
  const [tableError, setTableError] = useState<string | null>(null)
  const entityTags = workspace.entity_tags
  const candidateEvidenceByTagId = useMemo(
    () =>
      candidates.reduce<Record<string, CurationCandidate['evidence_anchors']>>((index, candidate) => {
        index[candidate.candidate_id] = candidate.evidence_anchors ?? []
        return index
      }, {}),
    [candidates],
  )

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

  const handleSaveTag = useCallback(async (tagId: string, updates: Partial<EntityTag>) => {
    setTableError(null)

    const candidate = candidates.find((currentCandidate) => currentCandidate.candidate_id === tagId)
    if (!candidate) {
      const missingCandidateError = new Error(`Unable to find candidate ${tagId} in the workspace.`)
      setTableError(missingCandidateError.message)
      throw missingCandidateError
    }

    try {
      const fieldChanges = buildEntityTagFieldChanges(candidate, updates)
      if (fieldChanges.length === 0) {
        return
      }

      await autosaveCurationCandidateDraft({
        session_id: workspace.session.session_id,
        candidate_id: candidate.candidate_id,
        draft_id: candidate.draft.draft_id,
        expected_version: candidate.draft.version,
        field_changes: fieldChanges,
      })

      await validateCurationCandidate({
        session_id: workspace.session.session_id,
        candidate_id: candidate.candidate_id,
        field_keys: fieldChanges.map((fieldChange) => fieldChange.field_key),
      })
      await refreshWorkspace(tagId)
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to save this entity row.'
      setTableError(message)
      throw error
    }
  }, [candidates, refreshWorkspace, workspace.session.session_id])

  const handleCreateManualTag = useCallback(async (tag: EntityTag) => {
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
      setActiveCandidate(response.candidate.candidate_id)
      await refreshWorkspace(response.candidate.candidate_id)
      return response.candidate.candidate_id
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to create this manual entity.'
      setTableError(message)
      throw error
    }
  }, [activeCandidateId, candidates, refreshWorkspace, setActiveCandidate, workspace.session.session_id])

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
                  onClick={() => setSubmissionDialogOpen(true)}
                  size="small"
                  variant="contained"
                  sx={{ fontSize: '0.75rem', py: 0.5 }}
                >
                  Preview submission
                </Button>
              </Stack>
            )}
            session={workspace.session}
          />
        )}
        entityTableSlot={(
          <EntityTagTable
            tags={entityTags}
            candidateEvidenceByTagId={candidateEvidenceByTagId}
            selectedTagId={activeCandidateId}
            onSelectTag={handleSelectTag}
            onAcceptTag={handleAcceptTag}
            onRejectTag={handleRejectTag}
            onAcceptAllValidated={handleAcceptAllValidated}
            onSaveTag={handleSaveTag}
            onCreateManualTag={handleCreateManualTag}
          />
        )}
      />

      <SubmissionPreviewDialog
        candidates={candidates}
        onClose={() => setSubmissionDialogOpen(false)}
        open={submissionDialogOpen}
        session={workspace.session}
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
