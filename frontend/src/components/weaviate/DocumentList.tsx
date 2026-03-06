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
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
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
import DocumentDetailsDialog from './DocumentDetailsDialog';
import DocumentDownloadDialog from './DocumentDownloadDialog';
import EditDocumentDialog from './EditDocumentDialog';
import {
  DocumentSummary,
  usePdfExtractionHealth,
} from '../../services/weaviate';
import { emitGlobalToast } from '../../lib/globalNotifications';

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

const MAX_UPLOAD_FILES_PER_SELECTION = 10;
const PDF_BACKGROUND_PROCESSING_TOAST =
  'Your PDFs are processing in the background. You can safely navigate away.';

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
  const extractionHealthQuery = usePdfExtractionHealth();
  const extractionHealth = extractionHealthQuery.data;
  const navigate = useNavigate();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [paginationModel, setPaginationModel] = useState<GridPaginationModel>({
    page: 0,
    pageSize: 20,
  });
  const [sortModel, setSortModel] = useState<GridSortModel>([]);
  const [filterModel, setFilterModel] = useState<GridFilterModel>({ items: [] });
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<DocumentSummary | null>(null);
  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false);
  const [downloadDocumentId, setDownloadDocumentId] = useState<string | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editDocument, setEditDocument] = useState<DocumentSummary | null>(null);

  React.useEffect(() => {
    if (!selectedDocumentId) {
      return;
    }
    const match = documents.find((doc) => doc.id === selectedDocumentId) || null;
    setSelectedDocument(match);
  }, [documents, selectedDocumentId]);

  const extractionHealthy = extractionHealth?.status === 'healthy';
  const uploadBlockedByExtraction =
    extractionHealthQuery.isError ||
    (extractionHealth != null && !extractionHealthy);

  const uploadBlockedReason =
    extractionHealthQuery.isError
      ? 'Unable to reach PDF extraction service.'
      : extractionHealth && !extractionHealthy
        ? extractionHealth.error || 'PDF extraction service is not healthy.'
        : null;

  const isDocumentBusy = (doc: DocumentSummary): boolean => {
    const processingStatus = String(doc.processingStatus || '').toLowerCase();
    const embeddingStatus = String(doc.embeddingStatus || '').toLowerCase();
    const activeProcessingStatuses = new Set(['processing', 'parsing', 'chunking', 'embedding', 'storing']);
    return activeProcessingStatuses.has(processingStatus) || embeddingStatus === 'processing';
  };

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

  const notifyBackgroundProcessingStarted = React.useCallback(() => {
    emitGlobalToast({
      message: PDF_BACKGROUND_PROCESSING_TOAST,
      severity: 'info',
      autoHideDurationMs: 8000,
      anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
    });
  }, []);

  const uploadDocumentFile = React.useCallback(async (file: File): Promise<string> => {
    const formData = new FormData();
    formData.append('file', file);

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
    return result.document_id as string;
  }, []);

  const uploadMultipleFiles = React.useCallback(
    async (files: File[]) => {
      const total = files.length;
      let succeeded = 0;
      const failures: string[] = [];
      onPipelineStateChange?.(true, `Uploading ${total} PDFs...`);

      for (let index = 0; index < total; index += 1) {
        const file = files[index];
        onPipelineStateChange?.(true, `Uploading ${index + 1}/${total}: ${file.name}`);

        try {
          await uploadDocumentFile(file);
          succeeded += 1;
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Upload failed';
          failures.push(`${file.name}: ${message}`);
        }
      }

      if (succeeded > 0) {
        notifyBackgroundProcessingStarted();
      }

      onRefresh();
      onPipelineStateChange?.(false);

      if (failures.length > 0) {
        const preview = failures.slice(0, 2).join(' | ');
        const overflow = failures.length > 2 ? ` (+${failures.length - 2} more)` : '';
        window.alert(`Queued ${succeeded}/${total} PDFs. Failed ${failures.length}: ${preview}${overflow}`);
      }
    },
    [notifyBackgroundProcessingStarted, onPipelineStateChange, onRefresh, uploadDocumentFile]
  );

  const uploadSingleFile = React.useCallback(async (file: File) => {
    onPipelineStateChange?.(true, `Uploading “${file.name}”…`);

    try {
      await uploadDocumentFile(file);
      notifyBackgroundProcessingStarted();
      onRefresh();
    } catch (error) {
      console.error('Error uploading file:', error);
      const errorMessage = error instanceof Error ? error.message : 'Failed to upload file';
      window.alert(errorMessage);
    } finally {
      onPipelineStateChange?.(false);
    }
  }, [notifyBackgroundProcessingStarted, onPipelineStateChange, onRefresh, uploadDocumentFile]);

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = Array.from(event.target.files ?? []);
    if (selectedFiles.length === 0) {
      return;
    }

    const nonPdfFile = selectedFiles.find((file) => !file.name.toLowerCase().endsWith('.pdf'));
    if (nonPdfFile) {
      alert('Please select PDF files only');
      if (event.target) {
        event.target.value = '';
      }
      return;
    }

    if (selectedFiles.length > MAX_UPLOAD_FILES_PER_SELECTION) {
      alert(`Please select up to ${MAX_UPLOAD_FILES_PER_SELECTION} PDF files at a time`);
      if (event.target) {
        event.target.value = '';
      }
      return;
    }

    if (selectedFiles.length === 1) {
      await uploadSingleFile(selectedFiles[0]);
    } else {
      await uploadMultipleFiles(selectedFiles);
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
        const disableLoad = params.row.embeddingStatus !== 'completed';

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
                  disabled={isDocumentBusy(params.row)}
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
                    setEditDocument(summary ?? null);
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
                  disabled={isDocumentBusy(params.row)}
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

  const hasDocuments = documents.length > 0;

  return (
    <Box sx={{ flexGrow: 1, minHeight: 0, width: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={1}
        alignItems={{ xs: 'stretch', sm: 'center' }}
        justifyContent="space-between"
        sx={{ mb: 2 }}
      >
        {extractionHealthQuery.isLoading ? (
          <Alert severity="info" sx={{ flex: 1 }}>
            Checking PDF extraction service health…
          </Alert>
        ) : extractionHealthQuery.isError ? (
          <Alert severity="error" sx={{ flex: 1 }}>
            Unable to reach PDF extraction service: {(extractionHealthQuery.error as Error).message}
          </Alert>
        ) : extractionHealth ? (
          <Alert
            severity={
              extractionHealth.status === 'healthy'
                ? 'success'
                : extractionHealth.status === 'degraded'
                  ? 'warning'
                  : 'error'
            }
            sx={{ flex: 1 }}
          >
            PDF extraction service: {extractionHealth.status}
            {extractionHealth.last_checked && (
              <Typography component="span" variant="caption" sx={{ ml: 1 }}>
                · Checked {new Date(extractionHealth.last_checked).toLocaleTimeString()}
              </Typography>
            )}
            {extractionHealth.error && extractionHealth.status !== 'healthy' && (
              <Typography component="span" variant="caption" sx={{ ml: 1 }}>
                ({extractionHealth.error})
              </Typography>
            )}
          </Alert>
        ) : (
          <Alert severity="warning" sx={{ flex: 1 }}>
            PDF extraction service status unavailable.
          </Alert>
        )}

        <Button
          size="small"
          variant="outlined"
          onClick={() => extractionHealthQuery.refetch()}
          disabled={extractionHealthQuery.isFetching}
          startIcon={extractionHealthQuery.isFetching ? <CircularProgress size={14} /> : undefined}
        >
          Refresh Status
        </Button>
      </Stack>

      {uploadBlockedReason && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {uploadBlockedReason}
        </Alert>
      )}

      <Stack direction="row" spacing={2} sx={{ mb: 2 }}>
        <Button
          variant="contained"
          startIcon={<CloudUpload />}
          onClick={handleUploadClick}
          disabled={loading || pipelineBusy || uploadBlockedByExtraction}
        >
          UPLOAD DOCUMENT(S)
        </Button>
        <Button
          variant="outlined"
          startIcon={<Refresh />}
          onClick={onRefresh}
          disabled={loading || pipelineBusy}
        >
          Refresh
        </Button>
        {pipelineBusy && (
          <Stack direction="row" spacing={1} alignItems="center">
            <CircularProgress size={16} thickness={5} />
            <Typography variant="body2" color="text.secondary">
              {pipelineMessage ||
                'Processing in progress.'}
            </Typography>
          </Stack>
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          multiple
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

      <Box sx={{ flexGrow: hasDocuments ? 1 : 0, minHeight: 0, overflowX: 'hidden' }}>
        {!hasDocuments ? (
          <TableContainer component={Paper} variant="outlined" sx={{ maxWidth: '100%', overflowX: 'hidden' }}>
            <Table size="small" sx={{ tableLayout: 'fixed' }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ width: '35%' }}>Filename</TableCell>
                  <TableCell sx={{ width: '35%' }}>Title</TableCell>
                  <TableCell sx={{ width: '15%' }}>Status</TableCell>
                  <TableCell sx={{ width: '15%' }} align="center">
                    Actions
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                <TableRow>
                  <TableCell colSpan={4} align="center" sx={{ py: 3 }}>
                    <Stack direction="row" spacing={1} alignItems="center" justifyContent="center">
                      {loading && <CircularProgress size={16} />}
                      <Typography variant="body2" color="text.secondary">
                        {loading ? 'Loading documents…' : 'No documents yet. Upload a PDF to get started.'}
                      </Typography>
                    </Stack>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
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
        )}
      </Box>

      <DocumentDetailsDialog
        open={detailsDialogOpen}
        documentId={selectedDocumentId}
        documentSummary={toDocumentSummary(selectedDocument)}
        onClose={handleCloseDetails}
        onDelete={onDelete ? (id) => Promise.resolve(onDelete(id)) : undefined}
        onReembed={onReembed ? (id) => Promise.resolve(onReembed(id)) : undefined}
        onRefreshRequested={() => Promise.resolve(onRefresh())}
        disableActions={false}
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

export default React.memo(DocumentList);
