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
import { alpha, useTheme } from '@mui/material/styles'

import PdfViewer from '@/components/pdfViewer/PdfViewer'
import { dispatchPDFDocumentChanged } from '@/components/pdfViewer/pdfEvents'
import {
  EvidenceChipGroup,
  EvidencePanel,
  useEvidenceNavigation,
  type UseEvidenceNavigationReturn,
} from '@/features/curation/evidence'
import {
  getAdapterLabel,
  getValidationLabel,
} from '@/features/curation/inventory/inventoryPresentation'
import {
  readCurationQueueNavigationState,
} from '@/features/curation/services/curationQueueNavigationService'
import { fetchCurationWorkspace } from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
  CurationDraftField,
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

interface DraftFieldGroup {
  key: string
  label: string
  fields: CurationDraftField[]
}

function formatDraftFieldValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return 'Not set'
  }

  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  return JSON.stringify(value)
}

function buildDraftFieldGroups(fields: CurationDraftField[]): DraftFieldGroup[] {
  const groups = new Map<string, DraftFieldGroup>()

  for (const field of [...fields].sort((left, right) => left.order - right.order)) {
    const groupKey = field.group_key?.trim() || 'ungrouped'
    const groupLabel = field.group_label?.trim() || 'Other fields'
    const existingGroup = groups.get(groupKey)

    if (existingGroup) {
      existingGroup.fields.push(field)
      continue
    }

    groups.set(groupKey, {
      key: groupKey,
      label: groupLabel,
      fields: [field],
    })
  }

  return Array.from(groups.values())
}

function DraftFieldEvidencePreview({
  activeCandidate,
  evidenceNavigation,
}: {
  activeCandidate: CurationCandidate | null
  evidenceNavigation: Pick<
    UseEvidenceNavigationReturn,
    | 'evidenceByAnchorId'
    | 'hoverEvidence'
    | 'hoveredEvidence'
    | 'selectEvidence'
    | 'selectedEvidence'
  >
}) {
  const theme = useTheme()
  const fieldGroups = useMemo(
    () => buildDraftFieldGroups(activeCandidate?.draft.fields ?? []),
    [activeCandidate?.draft.fields]
  )

  if (!activeCandidate) {
    return (
      <Typography color="text.secondary" variant="body2">
        Select a candidate to view field-level evidence anchors.
      </Typography>
    )
  }

  if (fieldGroups.length === 0) {
    return (
      <Typography color="text.secondary" variant="body2">
        No draft fields are available for this candidate yet.
      </Typography>
    )
  }

  return (
    <Stack spacing={1.5}>
      {fieldGroups.map((group) => (
        <Stack key={group.key} spacing={1}>
          <Typography color="text.secondary" variant="overline">
            {group.label}
          </Typography>

          {group.fields.map((field) => (
            <Box
              key={field.field_key}
              sx={{
                borderRadius: 1.5,
                border: `1px solid ${alpha(theme.palette.divider, 0.72)}`,
                backgroundColor: alpha(theme.palette.background.paper, 0.52),
                px: 1.5,
                py: 1.25,
              }}
            >
              <Stack spacing={0.9}>
                <Stack
                  alignItems={{ xs: 'flex-start', md: 'center' }}
                  direction={{ xs: 'column', md: 'row' }}
                  justifyContent="space-between"
                  spacing={1}
                >
                  <Typography sx={{ fontWeight: 600 }} variant="body2">
                    {field.label}
                  </Typography>
                  <EvidenceChipGroup
                    evidenceAnchorIds={field.evidence_anchor_ids}
                    evidenceByAnchorId={evidenceNavigation.evidenceByAnchorId}
                    hoverEvidence={evidenceNavigation.hoverEvidence}
                    hoveredEvidence={evidenceNavigation.hoveredEvidence}
                    selectEvidence={evidenceNavigation.selectEvidence}
                    selectedEvidence={evidenceNavigation.selectedEvidence}
                  />
                </Stack>

                <Typography variant="body2">
                  {formatDraftFieldValue(field.value ?? field.seed_value)}
                </Typography>
              </Stack>
            </Box>
          ))}
        </Stack>
      ))}
    </Stack>
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
  const workspaceDocument = workspace.session.document
  const workspaceDocumentId = workspaceDocument.document_id
  const workspaceDocumentPdfUrl = workspaceDocument.pdf_url
  const workspaceDocumentTitle = workspaceDocument.title
  const workspaceDocumentViewerUrl = workspaceDocument.viewer_url

  useEffect(() => {
    const pdfUrl = workspaceDocumentPdfUrl ?? workspaceDocumentViewerUrl
    if (!hydration.isHydrated || !workspaceDocumentId || !pdfUrl) {
      return
    }

    dispatchPDFDocumentChanged(
      workspaceDocumentId,
      pdfUrl,
      workspaceDocumentTitle,
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
    workspaceDocumentId,
    workspaceDocumentPdfUrl,
    workspaceDocumentTitle,
    workspaceDocumentViewerUrl,
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
      description="Field-level evidence chips stay synchronized with the evidence panel and PDF viewer in this temporary editor placeholder."
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

      <Divider />

      <DraftFieldEvidencePreview
        activeCandidate={activeCandidate}
        evidenceNavigation={evidenceNavigation}
      />

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
      hoverEvidence={evidenceNavigation.hoverEvidence}
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
