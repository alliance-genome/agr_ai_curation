import React, { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  DataGrid,
  GridColDef,
  GridRenderCellParams,
  GridSortModel,
  GridFilterModel,
  GridPaginationModel,
  GridRowSelectionModel,
} from '@mui/x-data-grid';
import {
  Alert,
  Box,
  Chip,
  IconButton,
  Tooltip,
  LinearProgress,
  Button,
  Stack,
  Typography,
  CircularProgress,
} from '@mui/material';
import {
  Delete,
  Refresh,
  Visibility,
  CloudUpload,
  FileOpen,
  Download,
  Edit,
} from '@mui/icons-material';
import UploadProgressDialog from './UploadProgressDialog';
import DocumentDetailsDialog from './DocumentDetailsDialog';
import DocumentDownloadDialog from './DocumentDownloadDialog';
import EditDocumentDialog from './EditDocumentDialog';
import { DocumentSummary, fetchDocumentDetail, useDoclingHealth } from '../../services/weaviate';

interface DocumentListProps {
  documents: DocumentSummary[];
  loading: boolean;
  totalCount: number;
  onDelete: (id: string) => void;
  onReembed: (id: string) => void;
  onRefresh: () => void;
  pipelineBusy?: boolean;
  pipelineMessage?: string;
  onPipelineStateChange?: (busy: boolean, message?: string) => void;
  onLoad?: (summary: DocumentSummary) => void;
  /** Enable checkbox selection for batch processing */
  checkboxSelection?: boolean;
  /** Controlled selection - array of selected document IDs */
  selectedIds?: string[];
  /** Called when selection changes with array of selected IDs */
  onSelectionChange?: (ids: string[]) => void;
  /** Called when document title is updated */
  onTitleUpdate?: (documentId: string, title: string) => Promise<void>;
  /** Optional filter bar component to render above the table */
  filterBar?: React.ReactNode;
}

const DocumentList: React.FC<DocumentListProps> = ({
  documents,
  loading,
  totalCount,
  onDelete,
  onReembed,
  onRefresh,
  pipelineBusy = false,
  pipelineMessage,
  onPipelineStateChange,
  onLoad,
  checkboxSelection = false,
  selectedIds,
  onSelectionChange,
  onTitleUpdate,
  filterBar,
}) => {
  const doclingHealthQuery = useDoclingHealth();
  const doclingHealth = doclingHealthQuery.data;
  const navigate = useNavigate();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({
    page: 0,
    pageSize: 20,
  });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploadingFile, setUploadingFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState({
    stage: '' as string,
    progress: 0,
    message: '',
  });
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<DocumentSummary | null>(null);
  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false);
  const [downloadDocumentId, setDownloadDocumentId] = useState<string | null>(null);
  const [uploadedDocumentId, setUploadedDocumentId] = useState<string | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editDocument, setEditDocument] = useState<DocumentSummary | null>(null);
  const progressEventSourceRef = useRef<EventSource | null>(null);
  const statusPollingRef = useRef<number | null>(null);

  const stopProgressTracking = () => {
    if (progressEventSourceRef.current) {
      progressEventSourceRef.current.close();
      progressEventSourceRef.current = null;
    }
    if (statusPollingRef.current !== null) {
      window.clearInterval(statusPollingRef.current);
      statusPollingRef.current = null;
    }
  };

  React.useEffect(() => {
    return () => {
      stopProgressTracking();
    };
  }, []);

  React.useEffect(() => {
    if (!selectedDocumentId) {
      return;
    }
    const match = documents.find((doc) => doc.id === selectedDocumentId) || null;
    setSelectedDocument(match);
  }, [documents, selectedDocumentId]);

  const formatFileSize = (bytes: number | null | undefined): string => {
    if (bytes === null || bytes === undefined) return '—';
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const handleViewDetails = (id: string) => {
    const doc = documents.find((item) => item.id === id) || null;
    setSelectedDocument(doc);
    setSelectedDocumentId(id);
    setDetailsDialogOpen(true);
  };

  const handleCloseDetails = () => {
    setDetailsDialogOpen(false);
    setSelectedDocumentId(null);
    setSelectedDocument(null);
  };

  const handleOpenDownload = (id: string) => {
    setDownloadDocumentId(id);
    setDownloadDialogOpen(true);
  };

  const handleCloseDownload = () => {
    setDownloadDialogOpen(false);
    setDownloadDocumentId(null);
  };

  const toDocumentSummary = React.useCallback(
    (doc: DocumentSummary | null): DocumentSummary | undefined => {
      if (!doc) {
        return undefined;
      }
      return doc;
    },
    []
  );

  const handleLoadForChat = async (documentId: string) => {
    let summary: DocumentSummary | undefined = undefined;

    // Try to find the document in the current list first
    const doc = documents.find((d) => d.id === documentId);
    if (doc) {
      summary = toDocumentSummary(doc);
    } else {
      try {
        const detail = await fetchDocumentDetail(documentId);
        summary = detail.document;
        // Ensure the documents table eventually reflects the new upload
        onRefresh();
      } catch (error) {
        console.error('Failed to fetch document before loading for chat:', error);
        setUploadProgress((prev) => ({
          ...prev,
          message: 'Unable to load the document. Please refresh the document list and try again.',
        }));
        return;
      }
    }

    if (summary && onLoad) {
      sessionStorage.setItem('document-loading', 'true');
      window.dispatchEvent(new CustomEvent('document-load-start'));
      onLoad(summary);
      setUploadDialogOpen(false);
      navigate('/');
    }
  };

  const handleLoadFromTable = (summary: DocumentSummary) => {
    if (onLoad) {
      // Signal that document loading is starting (persists across navigation)
      sessionStorage.setItem('document-loading', 'true');
      window.dispatchEvent(new CustomEvent('document-load-start'));

      // Load the document for chat
      onLoad(summary);
      // Navigate to home page
      navigate('/');
    }
  };

  const getStatusColor = (status: string): 'default' | 'primary' | 'success' | 'error' | 'warning' => {
    switch (status) {
      case 'completed':
        return 'success';
      case 'failed':
        return 'error';
      case 'processing':
      case 'parsing':
      case 'chunking':
      case 'embedding':
      case 'storing':
        return 'primary';
      case 'partial':
        return 'warning';
      default:
        return 'default';
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('Please select a PDF file');
      return;
    }

    setUploadingFile(file);
    setUploadDialogOpen(true);
    setUploadProgress({
      stage: 'uploading',
      progress: 20,
      message: 'Uploading file...',
    });
    onPipelineStateChange?.(true, `Uploading “${file.name}”…`);

    // Upload the file
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch('/api/weaviate/documents/upload', {
        method: 'POST',
        body: formData,
        credentials: 'include', // Include httpOnly cookies for authentication
      });

      if (!response.ok) {
        // Handle duplicate file error (409 Conflict)
        if (response.status === 409) {
          const errorData = await response.json();
          const uploadDate = errorData.detail?.uploaded_at
            ? new Date(errorData.detail.uploaded_at).toLocaleDateString()
            : 'previously';
          throw new Error(
            `This file was already uploaded ${uploadDate}. ${errorData.detail?.suggestion || 'Delete the existing document and try again.'}`
          );
        }
        throw new Error('Upload failed');
      }

      const result = await response.json();
      const documentId = result.document_id as string;
      setUploadedDocumentId(documentId);

      const handleProgressPayload = (
        stageRaw?: string,
        progressRaw?: number,
        messageRaw?: string,
        isFinal?: boolean
      ) => {
        const stage = (stageRaw || '').toLowerCase() || 'processing';
        const stageProgressFallback: Record<string, number> = {
          upload: 20,
          pending: 20,
          waiting: 20,
          processing: 30,
          parsing: 40,
          chunking: 60,
          storing: 90,
          completed: 100,
          failed: 0,
        };

        const progress = Math.min(
          100,
          Math.max(
            0,
            typeof progressRaw === 'number' && !Number.isNaN(progressRaw)
              ? progressRaw
              : stageProgressFallback[stage] ?? 0
          )
        );

        const displayMessage =
          messageRaw ||
          (stage === 'upload' || stage === 'pending'
            ? 'Uploading file...'
            : stage === 'waiting'
            ? 'Waiting for processing to start...'
            : stage === 'processing'
            ? 'Processing document...'
            : stage === 'parsing'
            ? 'Parsing PDF document...'
            : stage === 'chunking'
            ? 'Chunking document into sections...'
            : stage === 'storing'
            ? 'Storing in Weaviate database...'
            : stage === 'completed'
            ? 'Document uploaded successfully!'
            : stage === 'failed'
            ? 'Upload failed'
            : 'Processing document...');

        const normalizedStage =
          stage === 'upload' || stage === 'pending' || stage === 'processing' || stage === 'waiting'
            ? 'uploading'
            : stage;

        setUploadProgress({ stage: normalizedStage, progress, message: displayMessage });

        if (stage === 'completed' || isFinal) {
          onPipelineStateChange?.(false);
          stopProgressTracking();
          // Refresh document list but keep dialog open so user can click "Load for Chat"
          onRefresh();
        } else if (stage === 'failed' || stage === 'error') {
          onPipelineStateChange?.(false);
          stopProgressTracking();
          setTimeout(() => {
            setUploadDialogOpen(false);
          }, 3000);
        } else {
          onPipelineStateChange?.(true, displayMessage);
        }
      };

      const startStatusPolling = () => {
        const intervalId = window.setInterval(async () => {
          try {
            const statusResponse = await fetch(`/api/weaviate/documents/${documentId}/status`);
            if (statusResponse.ok) {
              const status = await statusResponse.json();
              handleProgressPayload(
                status.pipeline_status?.current_stage || status.processing_status,
                status.pipeline_status?.progress_percentage,
                status.pipeline_status?.message,
                false
              );
            }
          } catch (error) {
            console.error('Error checking status:', error);
          }
        }, 2000);
        statusPollingRef.current = intervalId;
      };

      const startProgressStream = () => {
        try {
          const source = new EventSource(
            `/api/weaviate/documents/${documentId}/progress/stream`
          );
          progressEventSourceRef.current = source;

          source.onmessage = (event) => {
            try {
              const payload = JSON.parse(event.data);
              handleProgressPayload(
                payload.stage,
                payload.progress,
                payload.message,
                payload.final
              );
            } catch (parseError) {
              console.error('Failed to parse progress event:', parseError);
            }
          };

          source.onerror = () => {
            source.close();
            progressEventSourceRef.current = null;
            if (statusPollingRef.current === null) {
              startStatusPolling();
            }
          };
        } catch (streamError) {
          console.error('Failed to open progress stream:', streamError);
          startStatusPolling();
        }
      };

      handleProgressPayload('upload', 20, `Uploading “${file.name}”…`, false);
      startProgressStream();

    } catch (error) {
      console.error('Error uploading file:', error);
      const errorMessage = error instanceof Error ? error.message : 'Failed to upload file';
      setUploadProgress({
        stage: 'error',
        progress: 0,
        message: errorMessage,
      });
      onPipelineStateChange?.(false);
      stopProgressTracking();
      // Dialog stays open until user manually closes it
    }

    // Reset file input
    if (event.target) {
      event.target.value = '';
    }
  };

  const columns: GridColDef[] = [
    {
      field: 'filename',
      headerName: 'Filename',
      flex: 2,
      minWidth: 150,
      sortable: true,
      filterable: true,
    },
    {
      field: 'title',
      headerName: 'Title',
      flex: 1.5,
      minWidth: 120,
      sortable: true,
      filterable: true,
      valueFormatter: (params) => params.value || '—',
    },
    {
      field: 'fileSize',
      headerName: 'Size',
      width: 90,
      sortable: true,
      valueFormatter: (params) => formatFileSize(params.value as number | null),
    },
    {
      field: 'creationDate',
      headerName: 'Created',
      flex: 1,
      minWidth: 120,
      sortable: true,
      valueFormatter: (params) => {
        const value = params.value as string | null;
        return value ? new Date(value).toLocaleDateString() : '—';
      },
    },
    {
      field: 'lastAccessedDate',
      headerName: 'Accessed',
      flex: 1,
      minWidth: 120,
      sortable: true,
      valueFormatter: (params) => {
        const value = params.value as string | null;
        return value ? new Date(value).toLocaleDateString() : '—';
      },
    },
    {
      field: 'embeddingStatus',
      headerName: 'Status',
      width: 120,
      sortable: true,
      filterable: true,
      renderCell: (params: GridRenderCellParams) => (
        <Chip
          label={params.value}
          size="small"
          color={getStatusColor(params.value as string)}
          variant={params.value === 'processing' ? 'outlined' : 'filled'}
        />
      ),
    },
    {
      field: 'vectorCount',
      headerName: 'Vectors',
      width: 80,
      sortable: true,
      type: 'number',
    },
    {
      field: 'chunkCount',
      headerName: 'Chunks',
      width: 80,
      sortable: true,
      type: 'number',
    },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 200,
      sortable: false,
      filterable: false,
      headerAlign: 'center',
      renderCell: (params: GridRenderCellParams) => {
        const summary = toDocumentSummary(params.row) ?? undefined;
        const disableLoad =
          params.row.embeddingStatus !== 'completed' ||
          pipelineBusy ||
          uploadDialogOpen;

        return (
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Tooltip title="View Details">
              <IconButton
                size="small"
                onClick={() => handleViewDetails(params.row.id)}
              >
                <Visibility fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Load for Chat">
              <span>
                <IconButton
                  size="small"
                  onClick={() => summary && handleLoadFromTable(summary)}
                  disabled={disableLoad || !summary}
                  color="success"
                >
                  <FileOpen fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title="Download">
              <IconButton
                size="small"
                onClick={() => handleOpenDownload(params.row.id)}
              >
                <Download fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Re-embed">
              <span>
                <IconButton
                  size="small"
                  onClick={() => onReembed(params.row.id)}
                  disabled={params.row.embeddingStatus === 'processing' || pipelineBusy || uploadDialogOpen}
                >
                  <Refresh fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            {onTitleUpdate && (
              <Tooltip title="Edit Title">
                <IconButton
                  size="small"
                  onClick={() => {
                    const summary = toDocumentSummary(params.row);
                    setEditDocument(summary);
                    setEditDialogOpen(true);
                  }}
                >
                  <Edit fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title="Delete">
              <span>
                <IconButton
                  size="small"
                  onClick={() => onDelete(params.row.id)}
                  disabled={params.row.processingStatus === 'processing' || pipelineBusy || uploadDialogOpen}
                >
                  <Delete fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        );
      },
    },
  ];

  return (
    <Box sx={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={1}
        alignItems={{ xs: 'stretch', sm: 'center' }}
        justifyContent="space-between"
        sx={{ mb: 2 }}
      >
        {doclingHealthQuery.isLoading ? (
          <Alert severity="info" sx={{ flex: 1 }}>
            Checking Docling service health…
          </Alert>
        ) : doclingHealthQuery.isError ? (
          <Alert severity="error" sx={{ flex: 1 }}>
            Unable to reach Docling service: {(doclingHealthQuery.error as Error).message}
          </Alert>
        ) : doclingHealth ? (
          <Alert
            severity={
              doclingHealth.status === 'healthy'
                ? 'success'
                : doclingHealth.status === 'degraded'
                  ? 'warning'
                  : 'error'
            }
            sx={{ flex: 1 }}
          >
            Docling service ({doclingHealth.service_url}): {doclingHealth.status}
            {doclingHealth.last_checked && (
              <Typography component="span" variant="caption" sx={{ ml: 1 }}>
                · Checked {new Date(doclingHealth.last_checked).toLocaleTimeString()}
              </Typography>
            )}
            {doclingHealth.error && doclingHealth.status !== 'healthy' && (
              <Typography component="span" variant="caption" sx={{ ml: 1 }}>
                ({doclingHealth.error})
              </Typography>
            )}
          </Alert>
        ) : (
          <Alert severity="warning" sx={{ flex: 1 }}>
            Docling service status unavailable.
          </Alert>
        )}

        <Button
          size="small"
          variant="outlined"
          onClick={() => doclingHealthQuery.refetch()}
          disabled={doclingHealthQuery.isFetching}
          startIcon={doclingHealthQuery.isFetching ? <CircularProgress size={14} /> : undefined}
        >
          Refresh Status
        </Button>
      </Stack>

      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <Button
          variant="contained"
          startIcon={<CloudUpload />}
          onClick={handleUploadClick}
          disabled={loading || pipelineBusy || uploadDialogOpen}
        >
          Upload Document
        </Button>
        <Button
          variant="outlined"
          startIcon={<Refresh />}
          onClick={onRefresh}
          disabled={loading || pipelineBusy || uploadDialogOpen}
        >
          Refresh
        </Button>
        {pipelineBusy && (
          <Stack direction="row" spacing={1} alignItems="center">
            <CircularProgress size={16} thickness={5} />
            <Typography variant="body2" color="text.secondary">
              {pipelineMessage ||
                'Processing in progress. Upload and refresh will be available when complete.'}
            </Typography>
          </Stack>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          style={{ display: 'none' }}
          onChange={handleFileSelect}
        />
      </Stack>

      {/* Optional filter bar */}
      {filterBar}

      {loading && (
        <LinearProgress
          sx={{
            position: 'absolute',
            top: 60,
            left: 0,
            right: 0,
            zIndex: 1,
          }}
        />
      )}

      <Box sx={{ flexGrow: 1, minHeight: 0 }}>
        <DataGrid
          rows={documents}
          columns={columns}
          rowCount={totalCount}
          loading={loading}
          pageSizeOptions={[10, 20, 50, 100]}
          paginationModel={paginationModel}
          onPaginationModelChange={setPaginationModel}
          sortModel={sortModel}
          onSortModelChange={setSortModel}
          filterModel={filterModel}
          onFilterModelChange={setFilterModel}
          paginationMode="server"
          sortingMode="server"
          filterMode="server"
          disableRowSelectionOnClick
          checkboxSelection={checkboxSelection}
          rowSelectionModel={selectedIds}
          onRowSelectionModelChange={(newSelection: GridRowSelectionModel) => {
            onSelectionChange?.(newSelection as string[]);
          }}
          autoHeight={false}
          sx={{
            height: '100%',
            '& .MuiDataGrid-cell': {
              color: 'text.primary',
              borderBottom: '1px solid',
              borderBottomColor: 'divider',
              '&:focus': {
                outline: 'none',
              },
            },
            '& .MuiDataGrid-columnHeaders': {
              backgroundColor: 'background.paper',
              color: 'text.primary',
              borderBottom: '1px solid',
              borderBottomColor: 'divider',
            },
            '& .MuiDataGrid-row': {
              '&:hover': {
                backgroundColor: 'action.hover',
              },
            },
            '& .MuiDataGrid-footerContainer': {
              borderTop: '1px solid',
              borderTopColor: 'divider',
              backgroundColor: 'background.paper',
            },
            '& .MuiTablePagination-root': {
              color: 'text.primary',
            },
          }}
        />
      </Box>

      <UploadProgressDialog
        open={uploadDialogOpen}
        fileName={uploadingFile?.name || ''}
        stage={uploadProgress.stage}
        progress={uploadProgress.progress}
        message={uploadProgress.message}
        onClose={() => setUploadDialogOpen(false)}
        documentId={uploadedDocumentId || undefined}
        onLoadForChat={handleLoadForChat}
      />
      <DocumentDetailsDialog
        open={detailsDialogOpen}
        documentId={selectedDocumentId}
        documentSummary={toDocumentSummary(selectedDocument)}
        onClose={handleCloseDetails}
        onDelete={onDelete ? (id) => Promise.resolve(onDelete(id)) : undefined}
        onReembed={onReembed ? (id) => Promise.resolve(onReembed(id)) : undefined}
        onRefreshRequested={() => Promise.resolve(onRefresh())}
        disableActions={pipelineBusy || uploadDialogOpen}
      />
      <DocumentDownloadDialog
        open={downloadDialogOpen}
        documentId={downloadDocumentId}
        onClose={handleCloseDownload}
      />
      {onTitleUpdate && (
        <EditDocumentDialog
          open={editDialogOpen}
          documentId={editDocument?.id ?? ''}
          currentTitle={editDocument?.title ?? null}
          onClose={() => {
            setEditDialogOpen(false);
            setEditDocument(null);
          }}
          onSave={async (docId, title) => {
            await onTitleUpdate(docId, title);
            onRefresh();
          }}
        />
      )}
    </Box>
  );
};

export default DocumentList;
