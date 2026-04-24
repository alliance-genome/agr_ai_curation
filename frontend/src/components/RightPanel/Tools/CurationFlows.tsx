/**
 * CurationFlows Component
 *
 * Displays user's saved curation flows in the Tools panel.
 * Allows viewing flow details, executing flows, and creating new flows.
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Box,
  Button,
  Typography,
  CircularProgress,
  IconButton,
  Tooltip,
  Collapse,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import StopIcon from '@mui/icons-material/Stop'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import RefreshIcon from '@mui/icons-material/Refresh'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import type { SSEEvent } from '@/hooks/useChatStream'
import { getStreamEventSessionId } from '@/lib/streamEventSession'
import FlowRunCompletionCard, { type FlowRunCompletionSummary } from './FlowRunCompletionCard'
import { subscribeToFlowListInvalidation } from '@/features/flows/flowListInvalidation'
import { listFlows, type FlowSummaryResponse } from '@/services/agentStudioService'
import logger from '@/services/logger'

/**
 * Props for CurationFlows component
 */
export interface CurationFlowsProps {
  /** Current chat session ID */
  sessionId: string | null
  /** Shared SSE event stream for reacting to flow completion */
  sseEvents: SSEEvent[]
  /** Callback to execute a flow */
  onExecuteFlow: (flowId: string, documentId?: string, userQuery?: string) => Promise<void>
  /** Callback to stop currently executing flow/chat stream */
  onStopFlow?: () => void | Promise<void>
  /** Whether a flow is currently executing */
  isExecuting?: boolean
  /** Current document loaded in PDF viewer */
  currentDocumentId?: string
}

/**
 * CurationFlows component for the Tools panel.
 *
 * Displays a list of user's curation flows with options to:
 * - View flow details (name, description, step count)
 * - Execute flows with a single click
 * - Navigate to Agent Studio to create new flows
 */
const CurationFlows: React.FC<CurationFlowsProps> = ({
  sessionId,
  sseEvents,
  onExecuteFlow,
  onStopFlow,
  isExecuting = false,
  currentDocumentId,
}) => {
  const navigate = useNavigate()
  const theme = useTheme()
  const [flows, setFlows] = useState<FlowSummaryResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFlowId, setExpandedFlowId] = useState<string | null>(null)
  const [executingFlowId, setExecutingFlowId] = useState<string | null>(null)
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [flowToDelete, setFlowToDelete] = useState<FlowSummaryResponse | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  const latestCompletedRun = useMemo<FlowRunCompletionSummary | null>(() => {
    for (let index = sseEvents.length - 1; index >= 0; index -= 1) {
      const event = sseEvents[index]
      if (event.type !== 'FLOW_FINISHED') {
        continue
      }

      const eventSessionId = getStreamEventSessionId(event)

      if (sessionId && eventSessionId && eventSessionId !== sessionId) {
        continue
      }

      const flowRunId = typeof event.flow_run_id === 'string'
        ? event.flow_run_id.trim()
        : ''
      const flowName = typeof event.flow_name === 'string'
        ? event.flow_name.trim()
        : ''
      const status = typeof event.status === 'string' ? event.status.trim() : ''
      const totalEvidenceRecords = Number(event.total_evidence_records)

      if (!flowRunId || !flowName || !status || !Number.isFinite(totalEvidenceRecords)) {
        continue
      }

      return {
        adapterKeys: Array.isArray(event.adapter_keys)
          ? event.adapter_keys.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
          : [],
        documentId: typeof event.document_id === 'string' && event.document_id.trim()
          ? event.document_id
          : null,
        flowId: typeof event.flow_id === 'string' ? event.flow_id : null,
        flowName,
        flowRunId,
        originSessionId: typeof event.origin_session_id === 'string' && event.origin_session_id.trim()
          ? event.origin_session_id
          : null,
        status,
        failureReason: typeof event.failure_reason === 'string' && event.failure_reason.trim()
          ? event.failure_reason
          : null,
        totalEvidenceRecords,
      }
    }

    return null
  }, [sessionId, sseEvents])

  /**
   * Fetch flows from API
   */
  const fetchFlows = useCallback(async () => {
    setIsLoading(true)
    setError(null)

    try {
      const data = await listFlows()
      setFlows(data.flows)
    } catch (err) {
      const error = err as Error
      logger.error('Failed to load flows', error, { component: 'CurationFlows' })
      setError(error.message)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Fetch flows on mount
  useEffect(() => {
    void fetchFlows()
  }, [fetchFlows])

  useEffect(() => {
    return subscribeToFlowListInvalidation(() => {
      void fetchFlows()
    })
  }, [fetchFlows])

  /**
   * Handle flow execution
   */
  const handleExecuteFlow = async (flow: FlowSummaryResponse) => {
    if (!sessionId) {
      console.error('Cannot execute flow: no session ID')
      return
    }

    setExecutingFlowId(flow.id)

    try {
      await onExecuteFlow(flow.id, currentDocumentId)
    } catch (err) {
      console.error('Error executing flow:', err)
    } finally {
      setExecutingFlowId(null)
    }
  }

  /**
   * Stop the currently running flow execution stream
   */
  const handleStopExecution = async () => {
    if (!onStopFlow) return
    try {
      await onStopFlow()
    } catch (err) {
      console.error('Error stopping flow execution:', err)
    }
  }

  /**
   * Navigate to Agent Studio to create new flow
   */
  const handleCreateNewFlow = () => {
    navigate('/agent-studio')
  }

  /**
   * Toggle flow details expansion
   */
  const toggleFlowExpanded = (flowId: string) => {
    setExpandedFlowId(prev => (prev === flowId ? null : flowId))
  }

  /**
   * Open delete confirmation dialog
   */
  const handleDeleteClick = (flow: FlowSummaryResponse, e: React.MouseEvent) => {
    e.stopPropagation()
    setFlowToDelete(flow)
    setDeleteDialogOpen(true)
  }

  /**
   * Close delete dialog
   */
  const handleDeleteCancel = () => {
    setDeleteDialogOpen(false)
    setFlowToDelete(null)
  }

  /**
   * Confirm and execute delete
   */
  const handleDeleteConfirm = async () => {
    if (!flowToDelete) return

    setIsDeleting(true)

    try {
      const response = await fetch(`/api/flows/${flowToDelete.id}`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        console.error('Failed to delete flow:', response.status)
        return
      }

      // Remove from local state
      setFlows(prev => prev.filter(f => f.id !== flowToDelete.id))
      setDeleteDialogOpen(false)
      setFlowToDelete(null)
    } catch (err) {
      console.error('Error deleting flow:', err)
    } finally {
      setIsDeleting(false)
    }
  }

  /**
   * Format relative time
   */
  const formatRelativeTime = (dateString: string | null): string => {
    if (!dateString) return 'Never'

    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)

    if (diffMins < 1) return 'Just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    if (diffDays < 7) return `${diffDays}d ago`
    return date.toLocaleDateString()
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'transparent',
        borderRadius: '8px',
        border: `1px solid ${theme.palette.divider}`,
        overflow: 'hidden',
        mb: 2,
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          borderBottom: isCollapsed ? 'none' : `1px solid ${theme.palette.divider}`,
          backgroundColor: alpha(theme.palette.background.paper, 0.52),
          cursor: 'pointer',
          '&:hover': {
            backgroundColor: theme.palette.action.hover,
          },
        }}
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DescriptionOutlinedIcon
            sx={{ fontSize: '1.1rem', color: theme.palette.text.secondary }}
          />
          <Typography
            variant="subtitle2"
            sx={{
              fontWeight: 600,
              color: theme.palette.text.primary,
              letterSpacing: '0.02em',
            }}
          >
            Curation Flows
          </Typography>
          {flows.length > 0 && (
            <Typography
              variant="caption"
              sx={{
                color: theme.palette.text.secondary,
                ml: 0.5,
              }}
            >
              ({flows.length})
            </Typography>
          )}
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          {!isCollapsed && (
            <Tooltip title="Refresh flows">
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation()
                  void fetchFlows()
                }}
                disabled={isLoading}
                sx={{
                  color: theme.palette.text.secondary,
                  '&:hover': {
                    color: theme.palette.text.primary,
                    backgroundColor: theme.palette.action.hover,
                  },
                }}
              >
                <RefreshIcon sx={{ fontSize: '1rem' }} />
              </IconButton>
            </Tooltip>
          )}
          <IconButton
            size="small"
            sx={{
              color: theme.palette.text.secondary,
              padding: '4px',
            }}
          >
            {isCollapsed ? (
              <ExpandMoreIcon sx={{ fontSize: '1.2rem' }} />
            ) : (
              <ExpandLessIcon sx={{ fontSize: '1.2rem' }} />
            )}
          </IconButton>
        </Box>
      </Box>

      {/* Collapsible Content */}
      <Collapse in={!isCollapsed}>
        <Box sx={{ padding: '12px 16px' }}>
          {/* New Flow Button */}
          <Button
            variant="outlined"
            size="small"
            startIcon={<AddIcon />}
            onClick={handleCreateNewFlow}
            sx={{
              width: '100%',
              mb: 2,
              textTransform: 'none',
              fontSize: '0.8rem',
              fontWeight: 500,
              borderColor: alpha(theme.palette.primary.main, 0.5),
              color: theme.palette.primary.main,
              backgroundColor: alpha(theme.palette.primary.main, 0.08),
              '&:hover': {
                borderColor: theme.palette.primary.main,
                backgroundColor: alpha(theme.palette.primary.main, 0.15),
              },
            }}
          >
            New Flow
          </Button>

          {latestCompletedRun && (
            <FlowRunCompletionCard run={latestCompletedRun} />
          )}

          {/* Loading State */}
          {isLoading && (
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                py: 3,
              }}
            >
              <CircularProgress
                size={24}
                sx={{ color: theme.palette.text.secondary }}
              />
            </Box>
          )}

          {/* Error State */}
          {error && !isLoading && (
            <Box
              sx={{
                py: 2,
                px: 1,
                textAlign: 'center',
              }}
            >
              <Typography
                variant="body2"
                sx={{
                  color: theme.palette.error.main,
                  fontSize: '0.8rem',
                }}
              >
                {error}
              </Typography>
              <Button
                size="small"
                onClick={() => void fetchFlows()}
                sx={{
                  mt: 1,
                  textTransform: 'none',
                  fontSize: '0.75rem',
                  color: theme.palette.text.secondary,
                }}
              >
                Retry
              </Button>
            </Box>
          )}

          {/* Empty State */}
          {!isLoading && !error && flows.length === 0 && (
            <Box
              sx={{
                py: 3,
                textAlign: 'center',
              }}
            >
              <Typography
                variant="body2"
                sx={{
                  color: theme.palette.text.secondary,
                  fontStyle: 'italic',
                  fontSize: '0.85rem',
                  mb: 1,
                }}
              >
                No flows yet
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: theme.palette.text.secondary,
                  display: 'block',
                }}
              >
                Create your first flow in Agent Studio
              </Typography>
            </Box>
          )}

          {/* Flow List */}
          {!isLoading && !error && flows.length > 0 && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
              {flows.map((flow) => {
                const isExpanded = expandedFlowId === flow.id
                const isThisFlowExecuting = executingFlowId === flow.id
                const canStopThisFlow = Boolean(isThisFlowExecuting && isExecuting && onStopFlow)
                const canExecute = !isExecuting && !isThisFlowExecuting && sessionId

                return (
                  <Box
                    key={flow.id}
                    sx={{
                      border: `1px solid ${theme.palette.divider}`,
                      borderRadius: '6px',
                      backgroundColor: alpha(theme.palette.background.paper, 0.48),
                      transition: 'all 0.2s ease',
                      '&:hover': {
                        borderColor: alpha(theme.palette.primary.main, 0.3),
                        backgroundColor: theme.palette.action.hover,
                      },
                    }}
                  >
                    {/* Flow Card Header */}
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '10px 12px',
                        gap: 1,
                      }}
                    >
                      {/* Flow Info */}
                      <Box
                        sx={{
                          flex: 1,
                          minWidth: 0,
                          cursor: 'pointer',
                        }}
                        onClick={() => toggleFlowExpanded(flow.id)}
                      >
                        <Box
                          sx={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                          }}
                        >
                          <Typography
                            sx={{
                              fontSize: '0.85rem',
                              fontWeight: 500,
                              color: theme.palette.text.primary,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            📋 {flow.name}
                          </Typography>
                        </Box>
                        <Typography
                          variant="caption"
                          sx={{
                            color: theme.palette.text.secondary,
                            display: 'block',
                            mt: 0.25,
                          }}
                        >
                          {flow.step_count} step{flow.step_count !== 1 ? 's' : ''}
                          {flow.execution_count > 0 && (
                            <span style={{ marginLeft: '8px' }}>
                              • Run {flow.execution_count}×
                            </span>
                          )}
                        </Typography>
                      </Box>

                      {/* Action Buttons */}
                      <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'center' }}>
                        {canStopThisFlow ? (
                          <Tooltip title="Stop running flow">
                            <Button
                              variant="outlined"
                              size="small"
                              onClick={() => void handleStopExecution()}
                              sx={{
                                minWidth: 'auto',
                                px: 1.5,
                                py: 0.5,
                                fontSize: '0.75rem',
                                fontWeight: 500,
                                textTransform: 'none',
                                borderColor: alpha(theme.palette.error.main, 0.6),
                                color: theme.palette.error.main,
                                '&:hover': {
                                  borderColor: theme.palette.error.main,
                                  backgroundColor: alpha(theme.palette.error.main, 0.1),
                                },
                              }}
                            >
                              <StopIcon sx={{ fontSize: '0.95rem', mr: 0.4 }} />
                              Stop
                            </Button>
                          </Tooltip>
                        ) : (
                          /* Run Button */
                          <Tooltip
                            title={
                              !sessionId
                                ? 'No active session'
                                : isExecuting
                                  ? 'Another flow is running'
                                  : 'Run this flow'
                            }
                          >
                            <span>
                              <Button
                                variant="contained"
                                size="small"
                                disabled={!canExecute}
                                onClick={() => void handleExecuteFlow(flow)}
                                sx={{
                                  minWidth: 'auto',
                                  px: 1.5,
                                  py: 0.5,
                                  fontSize: '0.75rem',
                                  fontWeight: 500,
                                  textTransform: 'none',
                                  backgroundColor: theme.palette.primary.main,
                                  color: theme.palette.primary.contrastText,
                                  boxShadow: 'none',
                                  '&:hover': {
                                    backgroundColor: theme.palette.primary.dark,
                                    boxShadow: `0 2px 8px ${alpha(theme.palette.primary.main, 0.3)}`,
                                  },
                                  '&:disabled': {
                                    backgroundColor: theme.palette.action.disabledBackground,
                                    color: theme.palette.action.disabled,
                                  },
                                }}
                              >
                                {isThisFlowExecuting ? (
                                  <CircularProgress
                                    size={14}
                                    sx={{ color: 'inherit' }}
                                  />
                                ) : (
                                  <>
                                    <PlayArrowIcon
                                      sx={{ fontSize: '1rem', mr: 0.25 }}
                                    />
                                    Run
                                  </>
                                )}
                              </Button>
                            </span>
                          </Tooltip>
                        )}

                        {/* Delete Button */}
                        <Tooltip title="Delete this flow">
                          <IconButton
                            size="small"
                            onClick={(e) => handleDeleteClick(flow, e)}
                            sx={{
                              color: alpha(theme.palette.error.main, 0.74),
                              padding: '4px',
                              '&:hover': {
                                color: theme.palette.error.main,
                                backgroundColor: alpha(theme.palette.error.main, 0.1),
                              },
                            }}
                          >
                            <DeleteIcon sx={{ fontSize: '1.1rem' }} />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </Box>

                    {/* Expanded Details */}
                    <Collapse in={isExpanded}>
                      <Box
                        sx={{
                          px: 1.5,
                          pb: 1.5,
                          pt: 0,
                          borderTop: `1px solid ${theme.palette.divider}`,
                        }}
                      >
                        {flow.description && (
                          <Typography
                            variant="body2"
                            sx={{
                              color: theme.palette.text.secondary,
                              fontSize: '0.8rem',
                              lineHeight: 1.5,
                              mt: 1,
                              mb: 1,
                            }}
                          >
                            {flow.description}
                          </Typography>
                        )}
                        <Box
                          sx={{
                            display: 'flex',
                            gap: 2,
                            flexWrap: 'wrap',
                            mt: 1,
                          }}
                        >
                          <Typography
                            variant="caption"
                            sx={{ color: theme.palette.text.secondary }}
                          >
                            Created: {formatRelativeTime(flow.created_at)}
                          </Typography>
                          {flow.last_executed_at && (
                            <Typography
                              variant="caption"
                              sx={{ color: theme.palette.text.secondary }}
                            >
                              Last run: {formatRelativeTime(flow.last_executed_at)}
                            </Typography>
                          )}
                        </Box>
                      </Box>
                    </Collapse>
                  </Box>
                )
              })}
            </Box>
          )}
        </Box>
      </Collapse>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteDialogOpen}
        onClose={handleDeleteCancel}
        PaperProps={{
          sx: {
            backgroundColor: theme.palette.background.paper,
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: '8px',
          },
        }}
      >
        <DialogTitle sx={{ color: theme.palette.text.primary }}>
          Delete Flow
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ color: theme.palette.text.secondary }}>
            Are you sure you want to delete &ldquo;{flowToDelete?.name}&rdquo;? This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={handleDeleteCancel}
            disabled={isDeleting}
            sx={{
              color: theme.palette.text.secondary,
              '&:hover': {
                backgroundColor: theme.palette.action.hover,
              },
            }}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleDeleteConfirm()}
            disabled={isDeleting}
            variant="contained"
            sx={{
              backgroundColor: theme.palette.error.main,
              color: theme.palette.error.contrastText,
              '&:hover': {
                backgroundColor: theme.palette.error.dark,
              },
              '&:disabled': {
                backgroundColor: alpha(theme.palette.error.main, 0.5),
              },
            }}
          >
            {isDeleting ? (
              <CircularProgress size={20} sx={{ color: 'inherit' }} />
            ) : (
              'Delete'
            )}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default CurationFlows
