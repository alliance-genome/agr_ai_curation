import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  Divider,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  CircularProgress,
  LinearProgress,
  Chip,
  IconButton,
  Tooltip,
  Snackbar,
  Menu,
} from '@mui/material';
import {
  Description as DocumentIcon,
  PlayArrow as StartIcon,
  Stop as CancelIcon,
  Download as DownloadIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  Schedule as PendingIcon,
  Refresh as ProcessingIcon,
  Add as AddIcon,
  MoreVert as MoreVertIcon,
  ContentCopy as ContentCopyIcon,
  RateReview as RateReviewIcon,
} from '@mui/icons-material';
import { useLocation, useNavigate } from 'react-router-dom';
import AuditPanel from '../components/AuditPanel';
import { AuditEvent, AuditEventType } from '../types/AuditEvent';
import FeedbackDialog from '../components/Chat/FeedbackDialog';
import { submitFeedback } from '../services/feedbackService';
import { useAuth } from '../contexts/AuthContext';

// Valid audit event types that should be shown in the batch audit panel
// NOTE: Excludes AGENT_THINKING because it emits per-token events that
// flood the panel. AGENT_GENERATING is kept as it's emitted once per phase.
const VALID_AUDIT_EVENT_TYPES: Set<string> = new Set([
  'SUPERVISOR_START',
  'SUPERVISOR_DISPATCH',
  'CREW_START',
  'AGENT_COMPLETE',
  'AGENT_GENERATING',
  // 'AGENT_THINKING' - excluded: per-token events create noise in batch mode
  'TOOL_START',
  'TOOL_COMPLETE',
  'LLM_CALL',
  'SUPERVISOR_RESULT',
  'SUPERVISOR_COMPLETE',
  'SUPERVISOR_ERROR',
  'SPECIALIST_RETRY',
  'SPECIALIST_RETRY_SUCCESS',
  'SPECIALIST_ERROR',
  'FORMATTER_PROCESSING',
  'DOMAIN_PLAN_CREATED',
  'DOMAIN_PLANNING',
  'DOMAIN_EXECUTION_START',
  'DOMAIN_COMPLETED',
  'DOMAIN_CATEGORY_ERROR',
  'DOMAIN_SKIPPED',
  'FILE_READY',
]);

// Helper to check if an event type is a valid audit event
const isValidAuditEventType = (type: string): type is AuditEventType => {
  return VALID_AUDIT_EVENT_TYPES.has(type);
};

// Batch status types
type BatchStatus = 'setup' | 'running' | 'completed' | 'cancelled';
type DocumentStatus = 'pending' | 'processing' | 'completed' | 'failed';

interface BatchDocument {
  id: string;
  document_id: string;
  title: string;
  status: DocumentStatus;
  result_file_path?: string;
  error_message?: string;
  processing_time_ms?: number;
  trace_id?: string;  // Langfuse trace ID for debugging
}

interface Flow {
  id: string;
  name: string;
  description?: string;
}

interface RecentBatch {
  id: string;
  flow_id: string;
  flow_name?: string;
  status: 'pending' | 'running' | 'completed' | 'cancelled';
  total_documents: number;
  completed_documents: number;
  failed_documents: number;
  created_at: string;
  completed_at?: string;
}

interface BatchState {
  status: BatchStatus;
  documents: BatchDocument[];
  selectedFlowId: string | null;
  flowValidation: { valid: boolean; errors: string[] } | null;
  completedCount: number;
  failedCount: number;
  totalCount: number;
}

const BatchPage: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();

  // Get selected document IDs from navigation state
  const selectedDocumentIds: string[] = location.state?.selectedDocumentIds || [];
  const selectedDocuments: { id: string; title: string }[] = location.state?.selectedDocuments || [];

  // Batch state
  const [batchState, setBatchState] = useState<BatchState>({
    status: 'setup',
    documents: selectedDocuments.map((doc, index) => ({
      id: `bd-${index}`,
      document_id: doc.id,
      title: doc.title,
      status: 'pending',
    })),
    selectedFlowId: null,
    flowValidation: null,
    completedCount: 0,
    failedCount: 0,
    totalCount: selectedDocuments.length,
  });

  // Available flows
  const [flows, setFlows] = useState<Flow[]>([]);
  const [loadingFlows, setLoadingFlows] = useState(true);

  // Recent batches
  const [recentBatches, setRecentBatches] = useState<RecentBatch[]>([]);
  const [loadingRecentBatches, setLoadingRecentBatches] = useState(true);

  // Audit events - with localStorage persistence
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);

  // Helper to save audit events to localStorage
  const saveAuditEventsToStorage = (batchId: string, events: AuditEvent[]) => {
    try {
      localStorage.setItem(`batch_audit_${batchId}`, JSON.stringify(events));
    } catch (e) {
      console.error('Failed to save audit events to localStorage:', e);
    }
  };

  // Helper to load audit events from localStorage
  const loadAuditEventsFromStorage = (batchId: string): AuditEvent[] => {
    try {
      const stored = localStorage.getItem(`batch_audit_${batchId}`);
      if (stored) {
        const events = JSON.parse(stored);
        // Restore Date objects
        return events.map((e: any) => ({
          ...e,
          timestamp: new Date(e.timestamp),
        }));
      }
    } catch (e) {
      console.error('Failed to load audit events from localStorage:', e);
    }
    return [];
  };

  // Helper to clear audit events from localStorage
  const clearAuditEventsFromStorage = (batchId: string) => {
    try {
      localStorage.removeItem(`batch_audit_${batchId}`);
    } catch (e) {
      console.error('Failed to clear audit events from localStorage:', e);
    }
  };

  // Toast notification state
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'info';
  }>({ open: false, message: '', severity: 'info' });

  // Document actions menu state
  const [menuAnchorEl, setMenuAnchorEl] = useState<null | HTMLElement>(null);
  const [menuDocument, setMenuDocument] = useState<BatchDocument | null>(null);

  // Feedback dialog state
  const [feedbackDialogOpen, setFeedbackDialogOpen] = useState(false);
  const [feedbackDocument, setFeedbackDocument] = useState<BatchDocument | null>(null);

  // Load available flows
  useEffect(() => {
    const fetchFlows = async () => {
      try {
        const response = await fetch('/api/flows', {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          setFlows(data.flows || []);
        }
      } catch (error) {
        console.error('Failed to load flows:', error);
      } finally {
        setLoadingFlows(false);
      }
    };
    fetchFlows();
  }, []);

  // Track if we've already auto-resumed a batch (to prevent re-triggering)
  const [hasAutoResumed, setHasAutoResumed] = useState(false);

  // Load recent batches
  useEffect(() => {
    const fetchRecentBatches = async () => {
      try {
        const response = await fetch('/api/batches', {
          credentials: 'include',
        });
        if (response.ok) {
          const data = await response.json();
          // Take only the 5 most recent batches
          setRecentBatches((data.batches || []).slice(0, 5));
        }
      } catch (error) {
        console.error('Failed to load recent batches:', error);
      } finally {
        setLoadingRecentBatches(false);
      }
    };
    fetchRecentBatches();
  }, []);

  // Validate flow when selected
  const handleFlowChange = async (flowId: string) => {
    setBatchState(prev => ({
      ...prev,
      selectedFlowId: flowId,
      flowValidation: null,
    }));

    if (flowId) {
      try {
        const response = await fetch(`/api/flows/${flowId}/validate-batch`, {
          method: 'GET',
          credentials: 'include',
        });
        if (response.ok) {
          const validation = await response.json();
          setBatchState(prev => ({
            ...prev,
            flowValidation: validation,
          }));
        }
      } catch (error) {
        console.error('Failed to validate flow:', error);
        setBatchState(prev => ({
          ...prev,
          flowValidation: { valid: false, errors: ['Failed to validate flow'] },
        }));
      }
    }
  };

  // Track active batch ID and EventSource for SSE
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [eventSource, setEventSource] = useState<EventSource | null>(null);

  // Cleanup EventSource on unmount
  useEffect(() => {
    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [eventSource]);

  // Helper to add an audit event
  // Add audit event and persist to localStorage
  const addAuditEvent = (event: Omit<AuditEvent, 'id'>, batchId?: string) => {
    const newEvent: AuditEvent = {
      ...event,
      id: crypto.randomUUID(),
    };
    setAuditEvents(prev => {
      const updated = [...prev, newEvent];
      // Persist to localStorage if we have a batch ID
      const storageKey = batchId || activeBatchId;
      if (storageKey) {
        saveAuditEventsToStorage(storageKey, updated);
      }
      return updated;
    });
  };

  // Connect to SSE stream for batch progress
  const connectToSSE = (batchId: string) => {
    // CR-6: Close existing connection before opening a new one to prevent memory leaks
    if (eventSource) {
      eventSource.close();
      setEventSource(null);
    }

    const es = new EventSource(`/api/batches/${batchId}/stream`, {
      withCredentials: true,
    });

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Handle batch-specific events (from DB polling)
        switch (data.type) {
          case 'BATCH_STATUS':
            setBatchState(prev => ({
              ...prev,
              completedCount: data.completed_documents ?? data.data?.completed_documents ?? prev.completedCount,
              failedCount: data.failed_documents ?? data.data?.failed_documents ?? prev.failedCount,
            }));
            break;

          case 'DOCUMENT_STATUS': {
            const docStatus = (data.status ?? data.data?.status) as DocumentStatus;
            const docId = data.document_id ?? data.data?.document_id;
            const docPosition = data.position ?? data.data?.position ?? 0;
            const docTitle = `Document ${docPosition + 1}`; // Default title

            // CR-7: Add audit events outside state setter to avoid stale closure issues
            // State setter callbacks should be pure functions
            if (docStatus === 'processing') {
              addAuditEvent({
                type: 'TOOL_START',
                timestamp: new Date(),
                sessionId: batchId,
                details: {
                  toolName: 'batch_document_processor',
                  friendlyName: `Processing: ${docTitle}`,
                  agent: 'Batch Processor',
                },
              });
            } else if (docStatus === 'completed') {
              addAuditEvent({
                type: 'TOOL_COMPLETE',
                timestamp: new Date(),
                sessionId: batchId,
                details: {
                  toolName: 'batch_document_processor',
                  friendlyName: `Completed: ${docTitle}`,
                  success: true,
                  agent: 'Batch Processor',
                },
              });
            } else if (docStatus === 'failed') {
              addAuditEvent({
                type: 'TOOL_COMPLETE',
                timestamp: new Date(),
                sessionId: batchId,
                details: {
                  toolName: 'batch_document_processor',
                  friendlyName: `Failed: ${docTitle}`,
                  success: false,
                  error: data.error_message ?? data.data?.error_message ?? 'Unknown error',
                  agent: 'Batch Processor',
                },
              });
            }

            // Update document state separately
            setBatchState(prev => ({
              ...prev,
              documents: prev.documents.map(d => {
                if (d.document_id === docId) {
                  return {
                    ...d,
                    status: docStatus,
                    result_file_path: data.result_file_path ?? data.data?.result_file_path,
                    error_message: data.error_message ?? data.data?.error_message,
                    processing_time_ms: data.processing_time_ms ?? data.data?.processing_time_ms,
                  };
                }
                return d;
              }),
            }));
            break;
          }

          case 'BATCH_COMPLETE': {
            const status = data.status ?? data.data?.status;
            const completedDocs = data.completed_documents ?? data.data?.completed_documents ?? 0;
            const failedDocs = data.failed_documents ?? data.data?.failed_documents ?? 0;
            const totalDocs = data.total_documents ?? data.data?.total_documents ?? 0;

            // Add completion audit event
            addAuditEvent({
              type: 'SUPERVISOR_COMPLETE',
              timestamp: new Date(),
              sessionId: batchId,
              details: {
                message: status === 'cancelled'
                  ? 'Batch processing cancelled'
                  : `Batch completed: ${completedDocs} successful, ${failedDocs} failed`,
                totalSteps: totalDocs,
              },
            });

            setBatchState(prev => ({
              ...prev,
              status: status === 'cancelled' ? 'cancelled' : 'completed',
              completedCount: completedDocs,
              failedCount: failedDocs,
            }));
            es.close();
            setEventSource(null);
            // Show toast notification
            if (status === 'cancelled') {
              setSnackbar({
                open: true,
                message: 'Batch processing cancelled',
                severity: 'info',
              });
            } else if (failedDocs > 0) {
              setSnackbar({
                open: true,
                message: `Batch completed: ${completedDocs} succeeded, ${failedDocs} failed`,
                severity: 'error',
              });
            } else {
              setSnackbar({
                open: true,
                message: `Batch completed successfully: ${completedDocs} documents processed`,
                severity: 'success',
              });
            }
            break;
          }

          case 'ERROR':
            // Add error audit event
            addAuditEvent({
              type: 'SUPERVISOR_ERROR',
              timestamp: new Date(),
              sessionId: batchId,
              details: {
                error: data.message ?? data.data?.message ?? 'Unknown error',
                context: 'Batch processing stream error',
              },
            });
            console.error('Batch stream error:', data.message ?? data.data?.message);
            es.close();
            setEventSource(null);
            break;

          default:
            // Capture trace_id from RUN_STARTED event and associate with document
            if (data.type === 'RUN_STARTED' && data.trace_id && data.document_id) {
              setBatchState(prev => ({
                ...prev,
                documents: prev.documents.map(d => {
                  if (d.document_id === data.document_id) {
                    return { ...d, trace_id: data.trace_id };
                  }
                  return d;
                }),
              }));
            }

            // Forward only valid audit events from flow execution to the panel
            // Filter out streaming text chunks, progress events, etc.
            if (data.type && data.timestamp && isValidAuditEventType(data.type)) {
              addAuditEvent({
                type: data.type as AuditEventType,
                timestamp: new Date(data.timestamp),
                sessionId: batchId,
                details: data.details || {},
              });
            }
            break;
        }
      } catch (e) {
        console.error('Failed to parse SSE event:', e);
      }
    };

    es.onerror = (error) => {
      console.error('SSE connection error:', error);
      es.close();
      setEventSource(null);
    };

    setEventSource(es);
  };

  // Auto-resume running batch on mount (must be after connectToSSE is defined)
  useEffect(() => {
    // Only auto-resume once, and only if we're not already viewing a batch
    if (hasAutoResumed || loadingRecentBatches || activeBatchId || batchState.status !== 'setup') {
      return;
    }

    // Find any running or pending batch
    const runningBatch = recentBatches.find(b => b.status === 'running' || b.status === 'pending');
    if (runningBatch) {
      setHasAutoResumed(true);
      // Auto-resume this batch
      (async () => {
        try {
          const response = await fetch(`/api/batches/${runningBatch.id}`, {
            credentials: 'include',
          });
          if (!response.ok) return;
          const batch = await response.json();

          setActiveBatchId(runningBatch.id);
          setBatchState({
            status: 'running',
            documents: batch.documents.map((doc: any) => ({
              id: doc.id,
              document_id: doc.document_id,
              title: doc.document_title || `Document ${doc.position + 1}`,
              status: doc.status,
              result_file_path: doc.result_file_path,
              error_message: doc.error_message,
              processing_time_ms: doc.processing_time_ms,
            })),
            selectedFlowId: batch.flow_id,
            flowValidation: null,
            completedCount: batch.completed_documents,
            failedCount: batch.failed_documents,
            totalCount: batch.total_documents,
          });

          // Load saved audit events from localStorage
          const savedEvents = loadAuditEventsFromStorage(runningBatch.id);
          if (savedEvents.length > 0) {
            setAuditEvents(savedEvents);
          }

          // Connect to SSE for live updates
          connectToSSE(runningBatch.id);
        } catch (error) {
          console.error('Failed to auto-resume batch:', error);
        }
      })();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentBatches, loadingRecentBatches, hasAutoResumed, activeBatchId, batchState.status]);

  // Start batch processing
  const handleStartBatch = async () => {
    if (!batchState.selectedFlowId || !batchState.flowValidation?.valid) {
      return;
    }

    setBatchState(prev => ({ ...prev, status: 'running' }));

    try {
      const response = await fetch('/api/batches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          flow_id: batchState.selectedFlowId,
          document_ids: selectedDocumentIds,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to start batch');
      }

      const batch = await response.json();

      // Store batch ID and connect to SSE for real-time updates
      setActiveBatchId(batch.id);

      // Add initial audit event for batch start
      addAuditEvent({
        type: 'SUPERVISOR_START',
        timestamp: new Date(),
        sessionId: batch.id,
        details: {
          message: `Starting batch processing: ${selectedDocumentIds.length} documents`,
        },
      });

      connectToSSE(batch.id);

    } catch (error) {
      console.error('Failed to start batch:', error);
      setBatchState(prev => ({ ...prev, status: 'setup' }));
      // CR-8: Show user-facing error message
      setSnackbar({
        open: true,
        message: error instanceof Error ? error.message : 'Failed to start batch processing',
        severity: 'error',
      });
    }
  };

  // Cancel batch
  const handleCancelBatch = async () => {
    if (!activeBatchId) {
      return;
    }

    try {
      const response = await fetch(`/api/batches/${activeBatchId}/cancel`, {
        method: 'POST',
        credentials: 'include',
      });

      if (!response.ok) {
        const error = await response.json();
        console.error('Failed to cancel batch:', error.detail);
        return;
      }

      // SSE will send BATCH_COMPLETE event with cancelled status
    } catch (error) {
      console.error('Failed to cancel batch:', error);
    }
  };

  // Navigate to documents page to select documents
  const handleChangeDocuments = () => {
    navigate('/weaviate/documents');
  };

  // Download a single result file
  const handleDownloadFile = (doc: BatchDocument) => {
    if (!doc.result_file_path) {
      console.error('No result file path for document:', doc.document_id);
      return;
    }
    // result_file_path is the download URL from the file output tool
    window.open(doc.result_file_path, '_blank');
  };

  // Download all results as ZIP
  const handleDownloadZip = async () => {
    if (!activeBatchId) {
      console.error('No active batch ID for ZIP download');
      return;
    }
    try {
      const response = await fetch(`/api/batches/${activeBatchId}/download-zip`, {
        credentials: 'include',
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to download ZIP');
      }
      // Get the blob and trigger download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `batch_${activeBatchId}_results.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error('Error downloading ZIP:', error);
      setSnackbar({
        open: true,
        message: error instanceof Error ? error.message : 'Failed to download ZIP',
        severity: 'error',
      });
    }
  };

  // Document actions menu handlers
  const handleDocumentMenuOpen = (event: React.MouseEvent<HTMLElement>, doc: BatchDocument) => {
    setMenuAnchorEl(event.currentTarget);
    setMenuDocument(doc);
  };

  const handleDocumentMenuClose = () => {
    setMenuAnchorEl(null);
    setMenuDocument(null);
  };

  const handleCopyTraceId = () => {
    if (!menuDocument?.trace_id) {
      setSnackbar({
        open: true,
        message: 'No trace available yet. Document must be processing or completed.',
        severity: 'error',
      });
      handleDocumentMenuClose();
      return;
    }
    navigator.clipboard.writeText(menuDocument.trace_id).then(() => {
      setSnackbar({
        open: true,
        message: `Trace ID copied: ${menuDocument.trace_id!.slice(0, 12)}...`,
        severity: 'info',
      });
    }).catch(err => {
      console.error('Failed to copy trace ID:', err);
    });
    handleDocumentMenuClose();
  };

  const handleProvideFeedback = () => {
    if (!menuDocument?.trace_id) {
      setSnackbar({
        open: true,
        message: 'No trace available yet. Document must be processing or completed.',
        severity: 'error',
      });
      handleDocumentMenuClose();
      return;
    }
    setFeedbackDocument(menuDocument);
    setFeedbackDialogOpen(true);
    handleDocumentMenuClose();
  };

  const handleFeedbackDialogClose = () => {
    setFeedbackDialogOpen(false);
    setFeedbackDocument(null);
  };

  const handleFeedbackSubmit = async (feedback: {
    session_id: string;
    curator_id: string;
    feedback_text: string;
    trace_ids: string[];
  }) => {
    try {
      await submitFeedback(feedback);
    } catch (error) {
      setSnackbar({
        open: true,
        message: 'Failed to submit feedback. Please try again.',
        severity: 'error',
      });
      throw error; // Re-throw so FeedbackDialog can handle it
    }
  };

  // View a recent batch (load its details)
  const handleViewRecentBatch = async (batchId: string) => {
    try {
      const response = await fetch(`/api/batches/${batchId}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error('Failed to load batch');
      }
      const batch = await response.json();

      // Set the active batch ID for downloads
      setActiveBatchId(batchId);

      // Update batch state with the loaded batch
      setBatchState({
        status: batch.status === 'running' ? 'running' :
                batch.status === 'cancelled' ? 'cancelled' : 'completed',
        documents: batch.documents.map((doc: any) => ({
          id: doc.id,
          document_id: doc.document_id,
          title: doc.document_title || `Document ${doc.position + 1}`,
          status: doc.status,
          result_file_path: doc.result_file_path,
          error_message: doc.error_message,
          processing_time_ms: doc.processing_time_ms,
        })),
        selectedFlowId: batch.flow_id,
        flowValidation: null,
        completedCount: batch.completed_documents,
        failedCount: batch.failed_documents,
        totalCount: batch.total_documents,
      });

      // Load saved audit events from localStorage
      const savedEvents = loadAuditEventsFromStorage(batchId);
      setAuditEvents(savedEvents);

      // If the batch is still running, connect to SSE
      if (batch.status === 'running' || batch.status === 'pending') {
        connectToSSE(batchId);
      }
    } catch (error) {
      console.error('Error loading batch:', error);
      setSnackbar({
        open: true,
        message: 'Failed to load batch details',
        severity: 'error',
      });
    }
  };

  // Get status chip color for batch status
  const getBatchStatusColor = (status: string): 'default' | 'primary' | 'success' | 'error' | 'warning' => {
    switch (status) {
      case 'completed':
        return 'success';
      case 'running':
      case 'pending':
        return 'primary';
      case 'cancelled':
        return 'warning';
      default:
        return 'default';
    }
  };

  // Render document status icon
  const getStatusIcon = (status: DocumentStatus) => {
    switch (status) {
      case 'completed':
        return <SuccessIcon color="success" />;
      case 'failed':
        return <ErrorIcon color="error" />;
      case 'processing':
        return <ProcessingIcon color="primary" sx={{ animation: 'spin 1s linear infinite' }} />;
      default:
        return <PendingIcon color="disabled" />;
    }
  };

  // Calculate progress percentage
  const progressPercent = batchState.totalCount > 0
    ? ((batchState.completedCount + batchState.failedCount) / batchState.totalCount) * 100
    : 0;

  // Render setup panel (left side)
  const renderSetupPanel = () => (
    <Paper sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Typography variant="h6" gutterBottom>
        Batch Setup
      </Typography>

      {/* Documents Section */}
      <Box sx={{ mb: 3 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="subtitle2" color="text.secondary">
            Documents ({batchState.documents.length} selected)
          </Typography>
          <Button size="small" onClick={handleChangeDocuments}>
            Change
          </Button>
        </Box>

        {batchState.documents.length === 0 ? (
          <Alert severity="info" sx={{ mb: 2 }}>
            No documents selected. Go to the Documents page to select documents for batch processing.
            <Button
              size="small"
              startIcon={<AddIcon />}
              onClick={handleChangeDocuments}
              sx={{ mt: 1, display: 'block' }}
            >
              Select Documents
            </Button>
          </Alert>
        ) : (
          <Paper variant="outlined" sx={{ maxHeight: 200, overflow: 'auto' }}>
            <List dense>
              {batchState.documents.map((doc) => (
                <ListItem
                  key={doc.id}
                  secondaryAction={
                    <IconButton
                      edge="end"
                      size="small"
                      onClick={(e) => handleDocumentMenuOpen(e, doc)}
                      aria-label="document actions"
                    >
                      <MoreVertIcon fontSize="small" />
                    </IconButton>
                  }
                >
                  <ListItemIcon sx={{ minWidth: 36 }}>
                    <DocumentIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText
                    primary={doc.title}
                    primaryTypographyProps={{ noWrap: true }}
                    sx={{ pr: 2 }}
                  />
                </ListItem>
              ))}
            </List>
          </Paper>
        )}
      </Box>

      <Divider sx={{ my: 2 }} />

      {/* Flow Selection */}
      <Box sx={{ mb: 3 }}>
        <Typography variant="subtitle2" color="text.secondary" gutterBottom>
          Flow
        </Typography>
        <FormControl fullWidth size="small">
          <InputLabel>Select a flow</InputLabel>
          <Select
            value={batchState.selectedFlowId || ''}
            label="Select a flow"
            onChange={(e) => handleFlowChange(e.target.value)}
            disabled={loadingFlows || batchState.documents.length === 0}
          >
            {flows.map((flow) => (
              <MenuItem key={flow.id} value={flow.id}>
                {flow.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>

        {/* Flow Validation Status */}
        {batchState.flowValidation && (
          <Box sx={{ mt: 1 }}>
            {batchState.flowValidation.valid ? (
              <Alert severity="success" sx={{ py: 0.5 }}>
                Valid: PDF input, file output
              </Alert>
            ) : (
              <Alert severity="error" sx={{ py: 0.5 }}>
                {batchState.flowValidation.errors.map((err, i) => (
                  <div key={i}>{err}</div>
                ))}
              </Alert>
            )}
          </Box>
        )}
      </Box>

      {/* Recent Batches Section */}
      {recentBatches.length > 0 && (
        <>
          <Divider sx={{ my: 2 }} />
          <Box sx={{ mb: 2 }}>
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Recent Batches
            </Typography>
            <Paper variant="outlined" sx={{ maxHeight: 180, overflow: 'auto' }}>
              <List dense>
                {recentBatches.map((batch) => (
                  <ListItem
                    key={batch.id}
                    onClick={() => handleViewRecentBatch(batch.id)}
                    sx={{
                      cursor: 'pointer',
                      '&:hover': { bgcolor: 'action.hover' },
                    }}
                  >
                    <ListItemText
                      primary={
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                          <Typography variant="body2" noWrap sx={{ flexGrow: 1 }}>
                            {batch.flow_name || `Batch ${batch.id.slice(0, 8)}`}
                          </Typography>
                          <Chip
                            label={batch.status}
                            size="small"
                            color={getBatchStatusColor(batch.status)}
                            sx={{ height: 20, fontSize: '0.7rem' }}
                          />
                        </Box>
                      }
                      secondary={
                        <Typography variant="caption" color="text.secondary">
                          {batch.completed_documents}/{batch.total_documents} docs •{' '}
                          {new Date(batch.created_at).toLocaleDateString()}
                        </Typography>
                      }
                    />
                  </ListItem>
                ))}
              </List>
            </Paper>
          </Box>
        </>
      )}

      <Box sx={{ flexGrow: 1 }} />

      {/* Start Button */}
      <Button
        variant="contained"
        size="large"
        startIcon={<StartIcon />}
        onClick={handleStartBatch}
        disabled={
          !batchState.selectedFlowId ||
          !batchState.flowValidation?.valid ||
          batchState.documents.length === 0
        }
        fullWidth
      >
        Start Batch
      </Button>
    </Paper>
  );

  // Render running panel (left side during processing)
  const renderRunningPanel = () => (
    <Paper sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Typography variant="h6" gutterBottom>
        Progress
      </Typography>

      {/* Progress Bar */}
      <Box sx={{ mb: 3 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="body2">
            {batchState.completedCount + batchState.failedCount} / {batchState.totalCount}
          </Typography>
          <Typography variant="body2">{Math.round(progressPercent)}%</Typography>
        </Box>
        <LinearProgress variant="determinate" value={progressPercent} />
      </Box>

      {/* Document Status List */}
      <Paper variant="outlined" sx={{ flexGrow: 1, overflow: 'auto', mb: 2 }}>
        <List dense>
          {batchState.documents.map((doc) => (
            <ListItem
              key={doc.id}
              secondaryAction={
                <IconButton
                  edge="end"
                  size="small"
                  onClick={(e) => handleDocumentMenuOpen(e, doc)}
                  aria-label="document actions"
                >
                  <MoreVertIcon fontSize="small" />
                </IconButton>
              }
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                {getStatusIcon(doc.status)}
              </ListItemIcon>
              <ListItemText
                primary={doc.title}
                secondary={
                  doc.status === 'processing' ? 'Processing...' :
                  doc.status === 'completed' ? `${doc.processing_time_ms}ms` :
                  doc.status === 'failed' ? doc.error_message :
                  'Pending'
                }
                primaryTypographyProps={{ noWrap: true }}
                sx={{ pr: 2 }}
              />
            </ListItem>
          ))}
        </List>
      </Paper>

      {/* Cancel Button */}
      <Button
        variant="outlined"
        color="error"
        startIcon={<CancelIcon />}
        onClick={handleCancelBatch}
        fullWidth
      >
        Cancel Batch
      </Button>
    </Paper>
  );

  // Render completed panel (left side when done)
  const renderCompletedPanel = () => (
    <Paper sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Typography variant="h6" gutterBottom>
        Batch Complete
      </Typography>

      {/* Summary */}
      <Box sx={{ mb: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Flow: {flows.find(f => f.id === batchState.selectedFlowId)?.name || 'Unknown'}
        </Typography>
        <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
          <Chip
            icon={<SuccessIcon />}
            label={`${batchState.completedCount} successful`}
            color="success"
            size="small"
          />
          {batchState.failedCount > 0 && (
            <Chip
              icon={<ErrorIcon />}
              label={`${batchState.failedCount} failed`}
              color="error"
              size="small"
            />
          )}
        </Box>
      </Box>

      {/* Results List */}
      <Typography variant="subtitle2" color="text.secondary" gutterBottom>
        Results
      </Typography>
      <Paper variant="outlined" sx={{ flexGrow: 1, overflow: 'auto', mb: 2 }}>
        <List dense>
          {batchState.documents.map((doc) => (
            <ListItem
              key={doc.id}
              secondaryAction={
                <Box sx={{ display: 'flex', gap: 0.5 }}>
                  <IconButton
                    size="small"
                    onClick={(e) => handleDocumentMenuOpen(e, doc)}
                    aria-label="document actions"
                  >
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                  {doc.status === 'completed' && doc.result_file_path && (
                    <Tooltip title="Download">
                      <IconButton
                        edge="end"
                        size="small"
                        onClick={() => handleDownloadFile(doc)}
                      >
                        <DownloadIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  )}
                </Box>
              }
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                {getStatusIcon(doc.status)}
              </ListItemIcon>
              <ListItemText
                primary={doc.title}
                secondary={
                  doc.status === 'failed' ? doc.error_message : undefined
                }
                primaryTypographyProps={{ noWrap: true }}
                sx={{ pr: 2 }}
              />
            </ListItem>
          ))}
        </List>
      </Paper>

      {/* Action Buttons */}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        <Button
          variant="contained"
          startIcon={<DownloadIcon />}
          disabled={batchState.completedCount === 0}
          onClick={handleDownloadZip}
          fullWidth
        >
          Download ZIP
        </Button>
        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={() => setBatchState(prev => ({
            ...prev,
            status: 'setup',
            documents: [],
            selectedFlowId: null,
            flowValidation: null,
            completedCount: 0,
            failedCount: 0,
            totalCount: 0,
          }))}
          fullWidth
        >
          Start New Batch
        </Button>
      </Box>
    </Paper>
  );

  // Render appropriate left panel based on status
  const renderLeftPanel = () => {
    switch (batchState.status) {
      case 'running':
        return renderRunningPanel();
      case 'completed':
      case 'cancelled':
        return renderCompletedPanel();
      default:
        return renderSetupPanel();
    }
  };

  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', width: '100%', height: '100%', p: 2 }}>
      <Box sx={{ display: 'flex', gap: 2, width: '100%', maxWidth: 1200 }}>
        {/* Left Panel - Setup/Progress/Results */}
        <Box sx={{ width: 400, flexShrink: 0 }}>
          {renderLeftPanel()}
        </Box>

        {/* Right Panel - Audit Log */}
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
            <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
              <Typography variant="h6">Audit Log</Typography>
            </Box>
            <Box sx={{ flexGrow: 1, minHeight: 0 }}>
              {/* CR-1: Use correct AuditPanel props interface */}
              <AuditPanel
                sessionId={activeBatchId}
                sseEvents={[]}
                initialEvents={auditEvents}
                onClear={() => {
                  setAuditEvents([]);
                  if (activeBatchId) {
                    clearAuditEventsFromStorage(activeBatchId);
                  }
                }}
                onStop={handleCancelBatch}
                isStreaming={batchState.status === 'running'}
              />
            </Box>
          </Paper>
        </Box>
      </Box>

      {/* Toast Notification */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={5000}
        onClose={() => setSnackbar(prev => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert
          onClose={() => setSnackbar(prev => ({ ...prev, open: false }))}
          severity={snackbar.severity}
          variant="filled"
        >
          {snackbar.message}
        </Alert>
      </Snackbar>

      {/* Document Actions Menu */}
      <Menu
        anchorEl={menuAnchorEl}
        open={Boolean(menuAnchorEl)}
        onClose={handleDocumentMenuClose}
        anchorOrigin={{
          vertical: 'bottom',
          horizontal: 'right',
        }}
        transformOrigin={{
          vertical: 'top',
          horizontal: 'right',
        }}
      >
        <MenuItem onClick={handleProvideFeedback}>
          <RateReviewIcon fontSize="small" sx={{ mr: 1 }} />
          Provide Feedback
        </MenuItem>
        <Divider />
        <MenuItem onClick={handleCopyTraceId}>
          <ContentCopyIcon fontSize="small" sx={{ mr: 1 }} />
          Copy Trace ID
        </MenuItem>
      </Menu>

      {/* Feedback Dialog */}
      <FeedbackDialog
        open={feedbackDialogOpen}
        onClose={handleFeedbackDialogClose}
        sessionId={activeBatchId}
        traceIds={feedbackDocument?.trace_id ? [feedbackDocument.trace_id] : []}
        curatorId={user?.email || 'unknown@example.com'}
        onSubmit={handleFeedbackSubmit}
      />
    </Box>
  );
};

export default BatchPage;
