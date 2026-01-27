/**
 * CurationFlows Component
 *
 * Displays user's saved curation flows in the Tools panel.
 * Allows viewing flow details, executing flows, and creating new flows.
 */

import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Box,
  Button,
  Typography,
  CircularProgress,
  IconButton,
  Tooltip,
  Collapse,
  alpha,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from '@mui/material'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import RefreshIcon from '@mui/icons-material/Refresh'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'

/**
 * Flow summary from API response
 */
interface FlowSummaryResponse {
  id: string
  user_id: number
  name: string
  description: string | null
  step_count: number
  execution_count: number
  last_executed_at: string | null
  created_at: string
  updated_at: string
}

/**
 * API list response
 */
interface FlowListResponse {
  flows: FlowSummaryResponse[]
  total: number
  page: number
  page_size: number
}

/**
 * Props for CurationFlows component
 */
export interface CurationFlowsProps {
  /** Current chat session ID */
  sessionId: string | null
  /** Callback to execute a flow */
  onExecuteFlow: (flowId: string, documentId?: string, userQuery?: string) => Promise<void>
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
  onExecuteFlow,
  isExecuting = false,
  currentDocumentId,
}) => {
  const navigate = useNavigate()
  const [flows, setFlows] = useState<FlowSummaryResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFlowId, setExpandedFlowId] = useState<string | null>(null)
  const [executingFlowId, setExecutingFlowId] = useState<string | null>(null)
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [flowToDelete, setFlowToDelete] = useState<FlowSummaryResponse | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  /**
   * Fetch flows from API
   */
  const fetchFlows = useCallback(async () => {
    setIsLoading(true)
    setError(null)

    try {
      const response = await fetch('/api/flows?page=1&page_size=50')

      if (!response.ok) {
        if (response.status === 401) {
          setError('Please log in to view your flows')
        } else {
          setError(`Failed to load flows (${response.status})`)
        }
        return
      }

      const data: FlowListResponse = await response.json()
      setFlows(data.flows)
    } catch (err) {
      console.error('Error fetching flows:', err)
      setError('Failed to connect to server')
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Fetch flows on mount
  useEffect(() => {
    void fetchFlows()
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
        border: '1px solid rgba(255, 255, 255, 0.08)',
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
          borderBottom: isCollapsed ? 'none' : '1px solid rgba(255, 255, 255, 0.08)',
          backgroundColor: 'rgba(255, 255, 255, 0.02)',
          cursor: 'pointer',
          '&:hover': {
            backgroundColor: 'rgba(255, 255, 255, 0.04)',
          },
        }}
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <DescriptionOutlinedIcon
            sx={{ fontSize: '1.1rem', color: 'rgba(255, 255, 255, 0.7)' }}
          />
          <Typography
            variant="subtitle2"
            sx={{
              fontWeight: 600,
              color: 'rgba(255, 255, 255, 0.9)',
              letterSpacing: '0.02em',
            }}
          >
            Curation Flows
          </Typography>
          {flows.length > 0 && (
            <Typography
              variant="caption"
              sx={{
                color: 'rgba(255, 255, 255, 0.5)',
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
                  color: 'rgba(255, 255, 255, 0.5)',
                  '&:hover': {
                    color: 'rgba(255, 255, 255, 0.8)',
                    backgroundColor: 'rgba(255, 255, 255, 0.08)',
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
              color: 'rgba(255, 255, 255, 0.5)',
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
              borderColor: 'rgba(33, 150, 243, 0.5)',
              color: '#2196f3',
              backgroundColor: 'rgba(33, 150, 243, 0.08)',
              '&:hover': {
                borderColor: '#2196f3',
                backgroundColor: 'rgba(33, 150, 243, 0.15)',
              },
            }}
          >
            New Flow
          </Button>

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
                sx={{ color: 'rgba(255, 255, 255, 0.5)' }}
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
                  color: 'rgba(244, 67, 54, 0.8)',
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
                  color: 'rgba(255, 255, 255, 0.6)',
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
                  color: 'rgba(255, 255, 255, 0.5)',
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
                  color: 'rgba(255, 255, 255, 0.4)',
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
                const canExecute = !isExecuting && !isThisFlowExecuting && sessionId

                return (
                  <Box
                    key={flow.id}
                    sx={{
                      border: '1px solid rgba(255, 255, 255, 0.1)',
                      borderRadius: '6px',
                      backgroundColor: 'rgba(255, 255, 255, 0.02)',
                      transition: 'all 0.2s ease',
                      '&:hover': {
                        borderColor: 'rgba(33, 150, 243, 0.3)',
                        backgroundColor: 'rgba(255, 255, 255, 0.04)',
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
                              color: 'rgba(255, 255, 255, 0.9)',
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
                            color: 'rgba(255, 255, 255, 0.5)',
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
                        {/* Run Button */}
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
                                backgroundColor: alpha('#2196f3', 0.9),
                                color: '#fff',
                                boxShadow: 'none',
                                '&:hover': {
                                  backgroundColor: '#2196f3',
                                  boxShadow: '0 2px 8px rgba(33, 150, 243, 0.3)',
                                },
                                '&:disabled': {
                                  backgroundColor: 'rgba(255, 255, 255, 0.08)',
                                  color: 'rgba(255, 255, 255, 0.3)',
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

                        {/* Delete Button */}
                        <Tooltip title="Delete this flow">
                          <IconButton
                            size="small"
                            onClick={(e) => handleDeleteClick(flow, e)}
                            sx={{
                              color: 'rgba(244, 67, 54, 0.7)',
                              padding: '4px',
                              '&:hover': {
                                color: '#f44336',
                                backgroundColor: 'rgba(244, 67, 54, 0.1)',
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
                          borderTop: '1px solid rgba(255, 255, 255, 0.05)',
                        }}
                      >
                        {flow.description && (
                          <Typography
                            variant="body2"
                            sx={{
                              color: 'rgba(255, 255, 255, 0.6)',
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
                            sx={{ color: 'rgba(255, 255, 255, 0.4)' }}
                          >
                            Created: {formatRelativeTime(flow.created_at)}
                          </Typography>
                          {flow.last_executed_at && (
                            <Typography
                              variant="caption"
                              sx={{ color: 'rgba(255, 255, 255, 0.4)' }}
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
            backgroundColor: '#1e1e1e',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: '8px',
          },
        }}
      >
        <DialogTitle sx={{ color: 'rgba(255, 255, 255, 0.9)' }}>
          Delete Flow
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ color: 'rgba(255, 255, 255, 0.7)' }}>
            Are you sure you want to delete "{flowToDelete?.name}"? This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={handleDeleteCancel}
            disabled={isDeleting}
            sx={{
              color: 'rgba(255, 255, 255, 0.7)',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.08)',
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
              backgroundColor: '#f44336',
              color: '#fff',
              '&:hover': {
                backgroundColor: '#d32f2f',
              },
              '&:disabled': {
                backgroundColor: 'rgba(244, 67, 54, 0.5)',
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
