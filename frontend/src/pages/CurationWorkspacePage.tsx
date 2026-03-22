import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link as RouterLink, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material'

import PdfViewer from '@/components/pdfViewer/PdfViewer'
import { dispatchPDFDocumentChanged } from '@/components/pdfViewer/pdfEvents'
import { fetchCurationWorkspace } from '@/features/curation/services/curationWorkspaceService'
import type {
  CurationCandidate,
  CurationWorkspace,
} from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
} from '@/features/curation/workspace/CurationWorkspaceContext'

function formatStatusLabel(value: string): string {
  return value
    .split('_')
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' ')
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

export function resolveActiveCandidateId(
  workspace: CurationWorkspace,
  candidateIdParam?: string | null,
): string | null {
  const candidates = workspace.candidates
  const routeCandidate = findCandidate(candidates, candidateIdParam)
  if (routeCandidate) {
    return routeCandidate.candidate_id
  }

  const firstPendingCandidate = candidates.find((candidate) => candidate.status === 'pending')
  if (firstPendingCandidate) {
    return firstPendingCandidate.candidate_id
  }

  const workspaceActiveCandidate = findCandidate(candidates, workspace.active_candidate_id)
  if (workspaceActiveCandidate) {
    return workspaceActiveCandidate.candidate_id
  }

  const sessionActiveCandidate = findCandidate(
    candidates,
    workspace.session.current_candidate_id,
  )
  if (sessionActiveCandidate) {
    return sessionActiveCandidate.candidate_id
  }

  return candidates[0]?.candidate_id ?? null
}

function CurationWorkspacePage() {
  const navigate = useNavigate()
  const { sessionId, candidateId } = useParams<{
    sessionId: string
    candidateId?: string
  }>()
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null)

  const workspaceQuery = useQuery({
    queryKey: ['curation-workspace', sessionId],
    queryFn: () => fetchCurationWorkspace(sessionId as string),
    enabled: typeof sessionId === 'string' && sessionId.length > 0,
  })

  const workspace = workspaceQuery.data ?? null
  const resolvedCandidateId = useMemo(
    () => (workspace ? resolveActiveCandidateId(workspace, candidateId) : null),
    [candidateId, workspace],
  )

  useEffect(() => {
    setActiveCandidateId(resolvedCandidateId)
  }, [resolvedCandidateId])

  useEffect(() => {
    if (!sessionId || !workspace) {
      return
    }

    if (resolvedCandidateId && candidateId !== resolvedCandidateId) {
      navigate(`/curation/${sessionId}/${resolvedCandidateId}`, { replace: true })
      return
    }

    if (!resolvedCandidateId && candidateId) {
      navigate(`/curation/${sessionId}`, { replace: true })
    }
  }, [candidateId, navigate, resolvedCandidateId, sessionId, workspace])

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
        { replace: options?.replace ?? false },
      )
    },
    [navigate, sessionId],
  )

  const activeCandidate = useMemo(
    () => findCandidate(workspace?.candidates ?? [], activeCandidateId),
    [activeCandidateId, workspace?.candidates],
  )

  useEffect(() => {
    const document = workspace?.session.document
    const pdfUrl = document?.pdf_url ?? document?.viewer_url
    if (!document?.document_id || !pdfUrl) {
      return
    }

    dispatchPDFDocumentChanged(
      document.document_id,
      pdfUrl,
      document.title,
      0,
    )
  }, [
    workspace?.session.document.document_id,
    workspace?.session.document.pdf_url,
    workspace?.session.document.title,
    workspace?.session.document.viewer_url,
  ])

  const contextValue = useMemo(() => {
    if (!workspace) {
      return null
    }

    return {
      workspace,
      session: workspace.session,
      candidates: workspace.candidates,
      activeCandidateId,
      activeCandidate,
      setActiveCandidate,
    }
  }, [activeCandidate, activeCandidateId, setActiveCandidate, workspace])

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
      <Box
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          p: 2,
          gap: 2,
          overflow: 'hidden',
        }}
      >
        <Stack
          direction={{ xs: 'column', md: 'row' }}
          spacing={2}
          alignItems={{ xs: 'flex-start', md: 'center' }}
          justifyContent="space-between"
        >
          <Stack spacing={0.5}>
            <Button
              component={RouterLink}
              to="/curation"
              startIcon={<ArrowBackRoundedIcon />}
              sx={{ alignSelf: 'flex-start', px: 0 }}
            >
              Back to Inventory
            </Button>
            <Typography variant="h4">
              {workspace.session.document.title}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Session {workspace.session.session_id} • {formatStatusLabel(workspace.session.status)}
            </Typography>
          </Stack>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Chip label={`${workspace.candidates.length} candidates`} />
            <Chip label={`${workspace.session.progress.pending_candidates} pending`} />
            <Chip
              label={`${workspace.session.progress.reviewed_candidates} reviewed`}
              variant="outlined"
            />
          </Stack>
        </Stack>

        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 2fr) minmax(320px, 1fr)' },
          }}
        >
          <Card
            sx={{
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <Box
              sx={{
                flex: 1,
                minHeight: { xs: 420, lg: 0 },
                '& > *': {
                  height: '100%',
                },
              }}
            >
              <PdfViewer />
            </Box>
          </Card>

          <Stack spacing={2} sx={{ minWidth: 0, overflow: 'auto' }}>
            <Card variant="outlined">
              <CardContent>
                <Stack spacing={1.5}>
                  <Typography variant="overline" color="text.secondary">
                    Active Candidate
                  </Typography>
                  <Typography variant="h5">
                    {activeCandidate?.display_label ?? 'No candidate available'}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {activeCandidate
                      ? `Candidate ${activeCandidate.candidate_id}`
                      : 'This session does not currently expose a candidate queue.'}
                  </Typography>
                  <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                    <Chip
                      label={`Status: ${formatStatusLabel(activeCandidate?.status ?? 'pending')}`}
                      size="small"
                    />
                    <Chip
                      label={`Evidence anchors: ${activeCandidate?.evidence_anchors.length ?? 0}`}
                      size="small"
                      variant="outlined"
                    />
                  </Stack>
                  {activeCandidate?.conversation_summary ? (
                    <Typography variant="body2" color="text.secondary">
                      {activeCandidate.conversation_summary}
                    </Typography>
                  ) : null}
                </Stack>
              </CardContent>
            </Card>

            <Card variant="outlined">
              <CardContent>
                <Stack spacing={1.5}>
                  <Typography variant="overline" color="text.secondary">
                    Session Context
                  </Typography>
                  <Typography variant="body2">
                    Adapter: {workspace.session.adapter.display_label ?? workspace.session.adapter.adapter_key}
                  </Typography>
                  <Typography variant="body2">
                    PDF source: {workspace.session.document.pdf_url ?? 'Unavailable'}
                  </Typography>
                  <Divider />
                  <Typography variant="body2" color="text.secondary">
                    This page owns session loading, route-driven candidate focus, workspace
                    context, and PDF viewer initialization. Workspace shell composition,
                    queue rendering, and autosave remain with sibling tickets.
                  </Typography>
                </Stack>
              </CardContent>
            </Card>

            <Alert severity="info">
              Child workspace panels will consume this page&apos;s context in later waves.
            </Alert>
          </Stack>
        </Box>
      </Box>
    </CurationWorkspaceProvider>
  )
}

export default CurationWorkspacePage
