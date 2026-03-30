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

import PdfViewer from '@/components/pdfViewer/PdfViewer'
import { dispatchPDFDocumentChanged } from '@/components/pdfViewer/pdfEvents'
import { EntityTagTable, type EntityTag } from '@/features/curation/entityTable'
import {
  readCurationQueueNavigationState,
} from '@/features/curation/services/curationQueueNavigationService'
import { SubmissionPreviewDialog } from '@/features/curation/submission'
import { fetchCurationWorkspace } from '@/features/curation/services/curationWorkspaceService'
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

function candidatesToEntityTags(candidates: CurationCandidate[]): EntityTag[] {
  return candidates.map((c) => ({
    tag_id: c.candidate_id,
    entity_name:
      c.display_label
        ?? (c.draft?.fields?.find((f) => f.field_key === 'entity_name')?.value as string)
        ?? '',
    entity_type: 'ATP:0000005',
    species: '',
    topic: '',
    db_status: 'not_found' as const,
    db_entity_id: null,
    source: c.source === 'extracted' ? ('ai' as const) : ('manual' as const),
    decision: c.status as EntityTag['decision'],
    evidence: c.evidence_anchors?.[0]
      ? {
          sentence_text:
            c.evidence_anchors[0].anchor.sentence_text
              ?? c.evidence_anchors[0].anchor.snippet_text
              ?? '',
          page_number: c.evidence_anchors[0].anchor.page_number ?? null,
          section_title: c.evidence_anchors[0].anchor.section_title ?? null,
          chunk_ids: c.evidence_anchors[0].anchor.chunk_ids ?? [],
        }
      : null,
    notes: null,
  }))
}

function CurationWorkspacePageContent({
  queueNavigationState,
}: {
  queueNavigationState: ReturnType<typeof readCurationQueueNavigationState>
}) {
  const {
    candidates,
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
  const [submissionDialogOpen, setSubmissionDialogOpen] = useState(false)

  const entityTags = useMemo(() => candidatesToEntityTags(candidates), [candidates])

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
        ? undefined
        : {
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
  ])

  return (
    <Box
      sx={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        p: 2,
        overflow: 'hidden',
        gap: 2,
      }}
    >
      {runtimeWarning ? (
        <Alert severity="warning">
          {runtimeWarning}
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
        pdfSlot={<PdfViewer />}
        entityTableSlot={<EntityTagTable tags={entityTags} />}
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
