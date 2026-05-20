import { useMemo, useState } from 'react'
import { Link as RouterLink, useLocation, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from 'reactflow'
import 'reactflow/dist/style.css'
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  Divider,
  LinearProgress,
  Stack,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import AccountTreeRoundedIcon from '@mui/icons-material/AccountTreeRounded'
import ArrowBackRoundedIcon from '@mui/icons-material/ArrowBackRounded'
import CheckCircleOutlineRoundedIcon from '@mui/icons-material/CheckCircleOutlineRounded'
import HubRoundedIcon from '@mui/icons-material/HubRounded'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'

import { buildGoFlowDemoGraph } from '@/features/goFlowDemo/buildGoFlowDemoGraph'
import {
  DEFAULT_GO_FLOW_DEMO_SELECTION_ID,
  goFlowDemoGraph,
} from '@/features/goFlowDemo/demoGraph'
import type {
  GoFlowActivityNode,
  GoFlowActivityNodeData,
  GoFlowRelationEdge,
  GoFlowRelationEdgeData,
  GoFlowValidationBadge,
} from '@/features/goFlowDemo/types'
import { fetchCurationWorkspace } from '@/features/curation/services/curationWorkspaceService'
import type { CurationValidationSummary, CurationWorkspace } from '@/features/curation/types'

const WORKSPACE_CONTEXT_STALE_TIME_MS = 60_000

type SelectedGraphItem = {
  kind: 'node' | 'edge'
  id: string
}

interface WorkspaceContextSummary {
  title: string
  pmid: string | null
  doi: string | null
  candidateCount: number
  validation: CurationValidationSummary | null
  evidenceTotal: number | null
  evidenceResolved: number | null
}

function readBackToWorkspacePath(state: unknown): string | null {
  if (!state || typeof state !== 'object') {
    return null
  }

  const routeState = state as {
    backToWorkspacePath?: unknown
    from?: unknown
  }
  const candidate = routeState.backToWorkspacePath ?? routeState.from

  return typeof candidate === 'string' && candidate.startsWith('/')
    ? candidate
    : null
}

function summarizeWorkspace(workspace: CurationWorkspace): WorkspaceContextSummary {
  const document = workspace.session.document
  const evidence = workspace.session.evidence ?? null

  return {
    title: document.title || 'Untitled curation document',
    pmid: document.pmid ? `PMID:${document.pmid.replace(/^PMID:/i, '')}` : null,
    doi: document.doi ? `DOI:${document.doi.replace(/^DOI:/i, '')}` : null,
    candidateCount: workspace.session.progress.total_candidates || workspace.candidates.length,
    validation: workspace.session.validation ?? null,
    evidenceTotal: evidence?.total_anchor_count ?? null,
    evidenceResolved: evidence?.resolved_anchor_count ?? null,
  }
}

function validationTone(status: GoFlowValidationBadge['status']) {
  switch (status) {
    case 'resolved':
      return 'success'
    case 'review':
      return 'warning'
    case 'context':
    default:
      return 'default'
  }
}

function ValidationBadgeList({ badges }: { badges: GoFlowValidationBadge[] }) {
  return (
    <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
      {badges.map((badge) => (
        <Chip
          key={`${badge.status}-${badge.label}`}
          label={badge.label}
          color={validationTone(badge.status)}
          size="small"
          variant={badge.status === 'resolved' ? 'filled' : 'outlined'}
          sx={{
            borderRadius: 1,
            fontSize: '0.7rem',
            height: 24,
            maxWidth: '100%',
            '& .MuiChip-label': {
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            },
          }}
        />
      ))}
    </Stack>
  )
}

function GoActivityNode({ data, selected }: NodeProps<GoFlowActivityNodeData>) {
  const theme = useTheme()
  const activity = data.activity
  const selectedPaper = activity.evidencePosture === 'selected_paper'
  const borderColor = selected
    ? theme.palette.primary.main
    : selectedPaper
      ? alpha(theme.palette.success.main, 0.7)
      : alpha(theme.palette.text.primary, 0.2)
  const surfaceColor = selectedPaper
    ? alpha(theme.palette.success.main, theme.palette.mode === 'dark' ? 0.13 : 0.08)
    : alpha(theme.palette.background.paper, theme.palette.mode === 'dark' ? 0.72 : 0.92)

  return (
    <Box
      data-testid={`go-flow-node-${activity.id}`}
      sx={{
        width: 250,
        minHeight: 158,
        border: '1px solid',
        borderColor,
        borderRadius: 1,
        bgcolor: surfaceColor,
        boxShadow: selected ? `0 0 0 3px ${alpha(theme.palette.primary.main, 0.18)}` : 'none',
        color: 'text.primary',
        overflow: 'hidden',
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ opacity: 0, pointerEvents: 'none' }}
      />
      <Stack spacing={1} sx={{ p: 1.25 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
          <Chip
            label={activity.evidencePostureLabel}
            color={selectedPaper ? 'success' : 'default'}
            size="small"
            variant={selectedPaper ? 'filled' : 'outlined'}
            sx={{
              borderRadius: 1,
              fontSize: '0.66rem',
              height: 22,
              maxWidth: 176,
              '& .MuiChip-label': {
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              },
            }}
          />
          {activity.evidenceCode ? (
            <Typography
              variant="caption"
              sx={{
                color: 'text.secondary',
                fontFamily: 'monospace',
                lineHeight: 1,
              }}
            >
              {activity.evidenceCode.id}
            </Typography>
          ) : null}
        </Stack>
        <Box>
          <Typography
            variant="subtitle2"
            sx={{
              fontWeight: selectedPaper ? 700 : 600,
              lineHeight: 1.2,
              letterSpacing: 0,
              mb: 0.4,
            }}
          >
            {activity.title}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              color: 'text.secondary',
              display: 'block',
              lineHeight: 1.25,
            }}
          >
            {activity.molecularFunction.id} / {activity.molecularFunction.label}
          </Typography>
        </Box>
        <Stack direction="row" spacing={0.6} flexWrap="wrap" useFlexGap>
          <Chip
            label={activity.geneProduct}
            size="small"
            sx={{ borderRadius: 1, height: 22, fontSize: '0.68rem' }}
          />
          {activity.occursIn ? (
            <Chip
              label={activity.occursIn.label}
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1, height: 22, fontSize: '0.68rem' }}
            />
          ) : null}
          {activity.partOf ? (
            <Chip
              label={activity.partOf.label}
              size="small"
              variant="outlined"
              sx={{
                borderRadius: 1,
                height: 22,
                fontSize: '0.68rem',
                maxWidth: 206,
                '& .MuiChip-label': {
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                },
              }}
            />
          ) : null}
        </Stack>
      </Stack>
      <Handle
        type="source"
        position={Position.Right}
        style={{ opacity: 0, pointerEvents: 'none' }}
      />
    </Box>
  )
}

const goFlowNodeTypes = {
  goActivity: GoActivityNode,
}

function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) {
    return null
  }

  return (
    <Box>
      <Typography
        variant="caption"
        sx={{ color: 'text.secondary', display: 'block', lineHeight: 1.1 }}
      >
        {label}
      </Typography>
      <Typography variant="body2" sx={{ lineHeight: 1.35, overflowWrap: 'anywhere' }}>
        {value}
      </Typography>
    </Box>
  )
}

function NodeDetails({ activity }: { activity: GoFlowActivityNode }) {
  return (
    <Stack spacing={1.35}>
      <Box>
        <Typography variant="overline" color="text.secondary">
          Activity unit
        </Typography>
        <Typography variant="h6" sx={{ lineHeight: 1.2, letterSpacing: 0 }}>
          {activity.title}
        </Typography>
      </Box>
      <ValidationBadgeList badges={activity.validationBadges} />
      <Divider />
      <DetailRow label="Enabled by" value={`${activity.geneProduct} / ${activity.geneId}`} />
      <DetailRow
        label="Molecular function"
        value={`${activity.molecularFunction.id} / ${activity.molecularFunction.label}`}
      />
      <DetailRow
        label="Occurs in"
        value={activity.occursIn ? `${activity.occursIn.id} / ${activity.occursIn.label}` : null}
      />
      <DetailRow
        label="Part of"
        value={activity.partOf ? `${activity.partOf.id} / ${activity.partOf.label}` : null}
      />
      <DetailRow
        label="Input or context"
        value={activity.inputContext ? `${activity.inputContext.id} / ${activity.inputContext.label}` : null}
      />
      <DetailRow label="Reference" value={[activity.pmid, activity.doi].filter(Boolean).join(' | ')} />
      <DetailRow label="Evidence code" value={activity.evidenceCode ? `${activity.evidenceCode.id} / ${activity.evidenceCode.label}` : null} />
      <DetailRow label="Evidence posture" value={activity.evidencePostureLabel} />
      <DetailRow label="Source system" value={activity.sourceSystem} />
      <Divider />
      <DetailRow label="Paper evidence" value={activity.paperSnippet} />
      <DetailRow label="Figure or source pointer" value={activity.figurePointer} />
      {activity.processBadges.length > 0 ? (
        <Stack spacing={0.75}>
          <Typography variant="caption" color="text.secondary">
            Process context
          </Typography>
          <Stack direction="row" spacing={0.6} flexWrap="wrap" useFlexGap>
            {activity.processBadges.map((term) => (
              <Chip
                key={term.id}
                label={`${term.id} / ${term.label}`}
                size="small"
                variant="outlined"
                sx={{ borderRadius: 1, maxWidth: '100%' }}
              />
            ))}
          </Stack>
        </Stack>
      ) : null}
    </Stack>
  )
}

function EdgeDetails({ relation }: { relation: GoFlowRelationEdge }) {
  return (
    <Stack spacing={1.35}>
      <Box>
        <Typography variant="overline" color="text.secondary">
          Causal relation
        </Typography>
        <Typography variant="h6" sx={{ lineHeight: 1.2, letterSpacing: 0 }}>
          {relation.predicate.label}
        </Typography>
      </Box>
      <ValidationBadgeList badges={relation.validationBadges} />
      <Divider />
      <DetailRow label="RO predicate" value={`${relation.predicate.id} / ${relation.predicate.label}`} />
      <DetailRow label="Reference" value={[relation.pmid, relation.doi].filter(Boolean).join(' | ')} />
      <DetailRow label="Evidence code" value={relation.evidenceCode ? `${relation.evidenceCode.id} / ${relation.evidenceCode.label}` : null} />
      <DetailRow label="Evidence posture" value={relation.evidencePostureLabel} />
      <DetailRow label="Source system" value={relation.sourceSystem} />
      <Divider />
      <DetailRow label="Paper evidence" value={relation.paperSnippet} />
      <DetailRow label="Figure or source pointer" value={relation.figurePointer} />
    </Stack>
  )
}

function WorkspaceContextPanel({
  errorMessage,
  isLoading,
  sessionId,
  summary,
}: {
  errorMessage: string | null
  isLoading: boolean
  sessionId?: string
  summary: WorkspaceContextSummary | null
}) {
  if (!sessionId) {
    return (
      <Alert severity="info" icon={<InfoOutlinedIcon />}>
        No workspace session attached. The static Shivers et al. 2010 graph is still available.
      </Alert>
    )
  }

  if (isLoading) {
    return (
      <Box
        sx={{
          border: '1px solid',
          borderColor: 'divider',
          borderRadius: 1,
          p: 1.25,
        }}
      >
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Loading workspace context...
        </Typography>
        <LinearProgress />
      </Box>
    )
  }

  if (errorMessage) {
    return (
      <Alert severity="warning">
        Workspace metadata unavailable: {errorMessage}. The static graph remains visible.
      </Alert>
    )
  }

  if (!summary) {
    return null
  }

  const validation = summary.validation
  const validatedCount = validation?.counts.validated ?? null
  const warningCount = validation?.warnings.length ?? null

  return (
    <Box
      sx={{
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 1,
        p: 1.25,
      }}
    >
      <Stack spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center">
          <CheckCircleOutlineRoundedIcon color="success" fontSize="small" />
          <Typography variant="subtitle2">Workspace context</Typography>
        </Stack>
        <Typography variant="body2" sx={{ lineHeight: 1.35 }}>
          {summary.title}
        </Typography>
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {summary.pmid ? <Chip label={summary.pmid} size="small" sx={{ borderRadius: 1 }} /> : null}
          {summary.doi ? <Chip label={summary.doi} size="small" sx={{ borderRadius: 1 }} /> : null}
          <Chip
            label={`${summary.candidateCount} candidate${summary.candidateCount === 1 ? '' : 's'}`}
            size="small"
            variant="outlined"
            sx={{ borderRadius: 1 }}
          />
          {validatedCount !== null ? (
            <Chip
              label={`${validatedCount} validated fields`}
              color="success"
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1 }}
            />
          ) : null}
          {summary.evidenceTotal !== null ? (
            <Chip
              label={`${summary.evidenceResolved ?? 0}/${summary.evidenceTotal} evidence anchors`}
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1 }}
            />
          ) : null}
          {warningCount ? (
            <Chip
              label={`${warningCount} validation warning${warningCount === 1 ? '' : 's'}`}
              color="warning"
              size="small"
              variant="outlined"
              sx={{ borderRadius: 1 }}
            />
          ) : null}
        </Stack>
      </Stack>
    </Box>
  )
}

function ActivityList({
  activities,
  onSelect,
  selected,
}: {
  activities: GoFlowActivityNode[]
  onSelect: (id: string) => void
  selected: SelectedGraphItem
}) {
  return (
    <Stack spacing={0.75}>
      {activities.map((activity) => {
        const active = selected.kind === 'node' && selected.id === activity.id
        return (
          <ButtonBase
            key={activity.id}
            onClick={() => onSelect(activity.id)}
            sx={(theme) => ({
              width: '100%',
              justifyContent: 'flex-start',
              border: '1px solid',
              borderColor: active ? theme.palette.primary.main : theme.palette.divider,
              borderRadius: 1,
              p: 1,
              textAlign: 'left',
              bgcolor: active ? alpha(theme.palette.primary.main, 0.1) : 'transparent',
              transition: theme.transitions.create(['background-color', 'border-color']),
              '&:hover': {
                bgcolor: alpha(theme.palette.primary.main, 0.08),
              },
            })}
          >
            <Stack spacing={0.25} sx={{ minWidth: 0 }}>
              <Typography variant="body2" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
                {activity.geneProduct}
              </Typography>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  lineHeight: 1.25,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  maxWidth: 300,
                }}
              >
                {activity.molecularFunction.label}
              </Typography>
            </Stack>
          </ButtonBase>
        )
      })}
    </Stack>
  )
}

function RelationLegend() {
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box sx={{ width: 30, height: 3, bgcolor: '#047857', borderRadius: 999 }} />
        <Typography variant="caption">direct positive regulation</Typography>
      </Stack>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box sx={{ width: 30, height: 3, bgcolor: '#c2410c', borderRadius: 999 }} />
        <Typography variant="caption">direct negative regulation</Typography>
      </Stack>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box
          sx={{
            width: 30,
            height: 3,
            borderTop: '3px dashed #64748b',
          }}
        />
        <Typography variant="caption">existing GO-CAM context</Typography>
      </Stack>
    </Stack>
  )
}

function GOFlowDemoPage() {
  const { sessionId } = useParams<{ sessionId?: string }>()
  const location = useLocation()
  const backToWorkspacePath = readBackToWorkspacePath(location.state) ?? (
    sessionId ? `/curation/${sessionId}` : null
  )
  const [selection, setSelection] = useState<SelectedGraphItem>({
    kind: 'node',
    id: DEFAULT_GO_FLOW_DEMO_SELECTION_ID,
  })
  const workspaceQuery = useQuery({
    queryKey: ['go-flow-demo-workspace', sessionId],
    queryFn: async () => {
      if (!sessionId) {
        throw new Error('Missing curation session identifier.')
      }

      return fetchCurationWorkspace(sessionId)
    },
    enabled: Boolean(sessionId),
    retry: false,
    staleTime: WORKSPACE_CONTEXT_STALE_TIME_MS,
  })
  const workspaceSummary = useMemo(
    () => workspaceQuery.data ? summarizeWorkspace(workspaceQuery.data) : null,
    [workspaceQuery.data],
  )
  const { nodes, edges } = useMemo(
    () => buildGoFlowDemoGraph(goFlowDemoGraph, selection),
    [selection],
  )
  const selectedActivity = selection.kind === 'node'
    ? goFlowDemoGraph.activities.find((activity) => activity.id === selection.id) ?? null
    : null
  const selectedRelation = selection.kind === 'edge'
    ? goFlowDemoGraph.relations.find((relation) => relation.id === selection.id) ?? null
    : null
  const workspaceErrorMessage = workspaceQuery.error instanceof Error
    ? workspaceQuery.error.message
    : workspaceQuery.error
      ? String(workspaceQuery.error)
      : null

  return (
    <Box
      sx={(theme) => ({
        flex: 1,
        width: '100%',
        minHeight: 0,
        overflow: 'auto',
        bgcolor: theme.palette.background.default,
        color: 'text.primary',
        p: { xs: 1.5, md: 2 },
      })}
    >
      <Stack spacing={1.5} sx={{ minHeight: '100%' }}>
        <Stack
          direction={{ xs: 'column', md: 'row' }}
          alignItems={{ xs: 'flex-start', md: 'center' }}
          justifyContent="space-between"
          spacing={1.5}
        >
          <Stack spacing={0.75} sx={{ minWidth: 0 }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              <AccountTreeRoundedIcon color="primary" />
              <Typography variant="h1" sx={{ letterSpacing: 0 }}>
                Draft GO activity model
              </Typography>
              <Chip
                label="Static demo graph"
                color="warning"
                size="small"
                sx={{ borderRadius: 1 }}
              />
              <Chip
                label="Read-only mockup"
                size="small"
                variant="outlined"
                sx={{ borderRadius: 1 }}
              />
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.35 }}>
              {goFlowDemoGraph.paper.shortLabel} | PMID:{goFlowDemoGraph.paper.pmid} | DOI:{goFlowDemoGraph.paper.doi}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1.4 }}>
              GO-CAM-style review mockup. Workspace metadata can be live; graph extraction is static for this demo.
            </Typography>
          </Stack>
          {backToWorkspacePath ? (
            <Button
              component={RouterLink}
              to={backToWorkspacePath}
              startIcon={<ArrowBackRoundedIcon />}
              variant="outlined"
              size="small"
              sx={{ borderRadius: 1, textTransform: 'none' }}
            >
              Back to workspace
            </Button>
          ) : null}
        </Stack>

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 1fr) 360px' },
            alignItems: 'start',
            gap: 1.5,
          }}
        >
          <Stack spacing={1.5} sx={{ minWidth: 0, minHeight: 0 }}>
            <Box
              aria-label="GO flow canvas"
              sx={(theme) => ({
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                overflow: 'hidden',
                minHeight: { xs: 560, lg: 560 },
                height: { xs: 560, lg: 'calc(100vh - 220px)' },
                maxHeight: { lg: 720 },
                bgcolor: theme.palette.mode === 'dark'
                  ? alpha(theme.palette.common.white, 0.03)
                  : alpha(theme.palette.primary.main, 0.03),
              })}
            >
              <ReactFlowProvider>
                <ReactFlow
                  nodes={nodes as Node<GoFlowActivityNodeData>[]}
                  edges={edges as Edge<GoFlowRelationEdgeData>[]}
                  nodeTypes={goFlowNodeTypes}
                  fitView
                  fitViewOptions={{ padding: 0.18, minZoom: 0.25, maxZoom: 1.05 }}
                  minZoom={0.2}
                  maxZoom={1.35}
                  nodesConnectable={false}
                  nodesDraggable={false}
                  panOnScroll
                  proOptions={{ hideAttribution: true }}
                  onNodeClick={(_, node) => setSelection({ kind: 'node', id: node.id })}
                  onEdgeClick={(_, edge) => setSelection({ kind: 'edge', id: edge.id })}
                >
                  <Background
                    color="#64748b"
                    gap={24}
                    size={1}
                    variant={BackgroundVariant.Dots}
                  />
                  <Controls showInteractive={false} />
                  <MiniMap
                    pannable
                    zoomable
                    nodeStrokeWidth={3}
                    style={{
                      background: 'rgba(15, 23, 42, 0.86)',
                      border: '1px solid rgba(148, 163, 184, 0.32)',
                      borderRadius: 4,
                    }}
                  />
                </ReactFlow>
              </ReactFlowProvider>
            </Box>
          </Stack>

          <Stack spacing={1.5} sx={{ minWidth: 0 }}>
            <Box
              component="aside"
              aria-label="GO flow details"
              sx={{
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                p: 1.5,
                bgcolor: 'background.paper',
                maxHeight: { lg: 430 },
                overflow: 'auto',
              }}
            >
              {selectedRelation ? (
                <EdgeDetails relation={selectedRelation} />
              ) : selectedActivity ? (
                <NodeDetails activity={selectedActivity} />
              ) : (
                <Alert severity="info">Select a node or edge to inspect the GO-CAM activity details.</Alert>
              )}
            </Box>

            <WorkspaceContextPanel
              errorMessage={workspaceErrorMessage}
              isLoading={workspaceQuery.isLoading || workspaceQuery.isFetching}
              sessionId={sessionId}
              summary={workspaceSummary}
            />

            <Box
              component="section"
              aria-label="GO activity list"
              sx={{
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                p: 1.25,
                bgcolor: 'background.paper',
              }}
            >
              <Stack spacing={1}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <HubRoundedIcon fontSize="small" color="primary" />
                  <Typography variant="subtitle2">Activity list</Typography>
                </Stack>
                <ActivityList
                  activities={goFlowDemoGraph.activities}
                  onSelect={(id) => setSelection({ kind: 'node', id })}
                  selected={selection}
                />
              </Stack>
            </Box>

            <Box
              component="section"
              aria-label="GO relation legend"
              sx={{
                border: '1px solid',
                borderColor: 'divider',
                borderRadius: 1,
                p: 1.25,
                bgcolor: 'background.paper',
              }}
            >
              <Stack spacing={1}>
                <Typography variant="subtitle2">Relation legend</Typography>
                <RelationLegend />
              </Stack>
            </Box>
          </Stack>
        </Box>
      </Stack>
    </Box>
  )
}

export default GOFlowDemoPage
