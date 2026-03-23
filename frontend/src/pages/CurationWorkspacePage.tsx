import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import { Link as RouterLink, useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material'

import PdfViewer from '@/components/pdfViewer/PdfViewer'
import { dispatchPDFDocumentChanged } from '@/components/pdfViewer/pdfEvents'
import {
  getAdapterLabel,
  getValidationLabel,
} from '@/features/curation/inventory/inventoryPresentation'
import {
  EvidencePanel,
  useEvidenceNavigation,
} from '@/features/curation/evidence'
import {
  readCurationQueueNavigationState,
} from '@/features/curation/services/curationQueueNavigationService'
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
import CandidateQueue from '@/features/curation/workspace/CandidateQueue'
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

function getCandidateValidationSummary(candidate: CurationCandidate | null): string {
  if (!candidate?.validation) {
    return 'Validation details load into the editor panel in a later ticket.'
  }

  return getValidationLabel(candidate.validation)
}

function WorkspaceSlotPlaceholder({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string
  title: string
  description: string
  children?: ReactNode
}) {
  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 1.5,
        p: 2,
        overflow: 'auto',
      }}
    >
      <Stack spacing={0.75}>
        <Typography color="text.secondary" variant="overline">
          {eyebrow}
        </Typography>
        <Typography variant="h6">
          {title}
        </Typography>
        <Typography color="text.secondary" variant="body2">
          {description}
        </Typography>
      </Stack>

      {children}
    </Box>
  )
}

function CurationWorkspacePageContent({
  queueNavigationState,
}: {
  queueNavigationState: ReturnType<typeof readCurationQueueNavigationState>
}) {
  const { activeCandidate, workspace } = useCurationWorkspaceContext()
  const autosave = useCurationWorkspaceAutosave()
  const hydration = useCurationWorkspaceHydration()
  const runtimeWarning = autosave.warning ?? hydration.warning
  const evidenceNavigation = useEvidenceNavigation({
    evidence: activeCandidate?.evidence_anchors ?? [],
  })

  useEffect(() => {
    const document = workspace.session.document
    const pdfUrl = document?.pdf_url ?? document?.viewer_url
    if (!hydration.isHydrated || !document?.document_id || !pdfUrl) {
      return
    }

    dispatchPDFDocumentChanged(
      document.document_id,
      pdfUrl,
      document.title,
      0,
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
    workspace.session.document.document_id,
    workspace.session.document.pdf_url,
    workspace.session.document.title,
    workspace.session.document.viewer_url,
  ])

  const queueSlot = <CandidateQueue />

  const toolbarSlot = (
    <WorkspaceSlotPlaceholder
      description="ALL-117 owns the real Review and Curate actions. These buttons are layout placeholders only in this shell pass."
      eyebrow="Decision Toolbar"
      title="Review controls"
    >
      <Stack direction="row" flexWrap="wrap" spacing={1} useFlexGap>
        <Button disabled size="small" variant="contained">
          Accept
        </Button>
        <Button disabled size="small" variant="outlined">
          Reject
        </Button>
        <Button disabled size="small" variant="outlined">
          Reset
        </Button>
      </Stack>
      <Typography color="text.secondary" variant="body2">
        {activeCandidate
          ? `Ready for ${activeCandidate.display_label ?? activeCandidate.candidate_id}`
          : 'Select a candidate to review.'}
      </Typography>
    </WorkspaceSlotPlaceholder>
  )

  const editorSlot = (
    <WorkspaceSlotPlaceholder
      description="ALL-122 will replace this with the shared annotation editor. The shell keeps session and draft context visible here for now."
      eyebrow="Annotation Editor"
      title={activeCandidate?.draft.draft_id ?? 'Editor placeholder'}
    >
      <Stack spacing={1}>
        <Typography variant="body2">
          Adapter: {getAdapterLabel(workspace.session.adapter)}
        </Typography>
        <Typography variant="body2">
          Draft version: {activeCandidate?.draft.version ?? 'Unavailable'}
        </Typography>
        <Typography variant="body2">
          Session version: {workspace.session.session_version}
        </Typography>
        <Typography variant="body2">
          Validation: {getCandidateValidationSummary(activeCandidate)}
        </Typography>
      </Stack>

      {workspace.session.warnings.length > 0 ? (
        <>
          <Divider />
          <Typography color="text.secondary" variant="body2">
            {workspace.session.warnings.length} session warning
            {workspace.session.warnings.length === 1 ? '' : 's'} available for review.
          </Typography>
        </>
      ) : null}
    </WorkspaceSlotPlaceholder>
  )

  const evidenceSlot = (
    <EvidencePanel
      candidateEvidence={evidenceNavigation.candidateEvidence}
      evidenceByGroup={evidenceNavigation.evidenceByGroup}
      hoveredEvidence={evidenceNavigation.hoveredEvidence}
      selectEvidence={evidenceNavigation.selectEvidence}
      selectedEvidence={evidenceNavigation.selectedEvidence}
    />
  )

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
        editorSlot={editorSlot}
        evidenceSlot={evidenceSlot}
        headerSlot={(
          <WorkspaceHeader
            navigationSlot={(
              <WorkspaceSessionNavigation
                currentSessionId={workspace.session.session_id}
                queueContext={queueNavigationState?.queueContext}
                queueRequest={queueNavigationState?.queueRequest}
              />
            )}
            session={workspace.session}
          />
        )}
        pdfSlot={
          <PdfViewer
            onNavigationComplete={evidenceNavigation.acknowledgeNavigation}
            pendingNavigation={evidenceNavigation.pendingNavigation}
          />
        }
        queueSlot={queueSlot}
        toolbarSlot={toolbarSlot}
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
