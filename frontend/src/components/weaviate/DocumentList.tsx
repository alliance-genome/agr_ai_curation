import React, { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnOrderState,
  type ColumnSizingState,
  type RowSelectionState,
  type SortingState,
  type Updater,
  type VisibilityState,
} from '@tanstack/react-table';
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
  TablePagination,
  TableSortLabel,
  Tooltip,
  LinearProgress,
  Button,
  Checkbox,
  List,
  ListItem,
  ListItemText,
  Popover,
  Stack,
  Typography,
  CircularProgress,
  Divider,
  FormControlLabel,
  Radio,
  RadioGroup,
} from '@mui/material';
import {
  Delete,
  Refresh,
  Visibility,
  CloudUpload,
  FileOpen,
  Download,
  Edit,
  ArrowDownward,
  ArrowUpward,
  RestartAlt,
  ViewColumn,
} from '@mui/icons-material';
import DocumentDetailsDialog from './DocumentDetailsDialog';
import DocumentDownloadDialog from './DocumentDownloadDialog';
import EditDocumentDialog from './EditDocumentDialog';
import {
  DocumentSummary,
  isActiveDocumentStatus,
  usePdfExtractionHealth,
} from '../../services/weaviate';
import { emitGlobalToast } from '../../lib/globalNotifications';
import {
  documentSourceProviderLabel,
  documentSourceReferenceLabel,
} from '../../utils/documentSourcePresentation';
import {
  uploadPdfDocument,
  validatePdfSelection,
} from '@/features/documents/pdfUploadFlow';
import { startDocumentLoad } from '@/features/documents/documentLoadEvents';
import PreparedReviewAndCurateButton from '@/features/curation/components/PreparedReviewAndCurateButton';
import { useAuth } from '@/contexts/AuthContext';
import {
  clearDocumentTablePreferences,
  defaultDocumentTablePreferences,
  hasCustomDocumentTablePreferences,
  loadDocumentTablePreferences,
  normalizeDocumentTablePreferences,
  reorderDocumentTableColumns,
  saveDocumentTablePreferences,
  type DocumentTablePreferences,
  type DocumentTableDensity,
} from '@/features/documents/documentTablePreferences';
import type {
  DocumentTablePaginationModel,
  DocumentTableSortModel,
} from '@/features/documents/documentTableModels';

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
  /** Show PDF upload and extraction-health controls above the inventory table */
  showUploadControls?: boolean;
  /** Server-backed page state for large document libraries. */
  paginationModel?: DocumentTablePaginationModel;
  /** Called when the user requests another page or page size. */
  onPaginationModelChange?: (model: DocumentTablePaginationModel) => void;
  /** Server-backed sort state for document fields supported by the API. */
  sortModel?: DocumentTableSortModel;
  /** Called when the user changes sort order. */
  onSortModelChange?: (model: DocumentTableSortModel) => void;
}

const PDF_BACKGROUND_PROCESSING_TOAST =
  'Your PDFs are processing in the background. You can safely navigate away.';
const PDF_BACKGROUND_PROCESSING_TOAST_AUTO_HIDE_MS = 6000;

const DOCUMENT_COLUMN_OPTIONS = [
  { field: 'filename', label: 'Filename' },
  { field: 'title', label: 'Title' },
  { field: 'sourceProvenance', label: 'Source' },
  { field: 'fileSize', label: 'Size' },
  { field: 'creationDate', label: 'Created' },
  { field: 'processingStatus', label: 'Status' },
  { field: 'vectorCount', label: 'Vectors' },
  { field: 'chunkCount', label: 'Chunks' },
  { field: 'actions', label: 'Actions' },
] as const;
const DOCUMENT_COLUMN_FIELDS = DOCUMENT_COLUMN_OPTIONS.map(({ field }) => field);
const DOCUMENT_SELECTION_COLUMN_WIDTH = 48;

const compareTextValues = (left: unknown, right: unknown): number => {
  const leftValue = left == null ? '' : String(left);
  const rightValue = right == null ? '' : String(right);

  return leftValue.localeCompare(rightValue, undefined, {
    numeric: true,
    sensitivity: 'base',
  });
};

export const documentDisplayStatus = (document: DocumentSummary): string => {
  const processingStatus = String(document.processingStatus || '').toLowerCase();
  const embeddingStatus = String(document.embeddingStatus || '').toLowerCase();

  if (processingStatus === 'failed') {
    return 'failed';
  }
  if (isActiveDocumentStatus(processingStatus)) {
    return processingStatus;
  }
  if (embeddingStatus === 'failed' || embeddingStatus === 'partial') {
    return embeddingStatus;
  }
  if (isActiveDocumentStatus(embeddingStatus)) {
    return embeddingStatus;
  }
  return processingStatus || embeddingStatus || 'pending';
};

const compareNumberValues = (left: unknown, right: unknown): number => {
  const toComparableNumber = (value: unknown): number | null => {
    if (value === null || value === undefined) {
      return null;
    }

    if (typeof value === 'string' && value.trim() === '') {
      return null;
    }

    const numberValue = typeof value === 'number' ? value : Number(value);
    return Number.isFinite(numberValue) ? numberValue : null;
  };
  const leftComparable = toComparableNumber(left);
  const rightComparable = toComparableNumber(right);

  if (leftComparable === null && rightComparable === null) return 0;
  if (leftComparable === null) return 1;
  if (rightComparable === null) return -1;

  return leftComparable - rightComparable;
};

const compareDateValues = (left: unknown, right: unknown): number => {
  const toTimestamp = (value: unknown): number | null => {
    if (value instanceof Date) {
      return value.getTime();
    }

    if (typeof value !== 'string' || value.trim() === '') {
      return null;
    }

    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) ? timestamp : null;
  };

  return compareNumberValues(toTimestamp(left), toTimestamp(right));
};

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
  checkboxSelection = false,
  selectedIds,
  onSelectionChange,
  onTitleUpdate,
  filterBar,
  showUploadControls = true,
  paginationModel: controlledPaginationModel,
  onPaginationModelChange,
  sortModel: controlledSortModel,
  onSortModelChange,
}) => {
  const extractionHealthQuery = usePdfExtractionHealth({ enabled: showUploadControls });
  const extractionHealth = extractionHealthQuery.data;
  const navigate = useNavigate();
  const { user } = useAuth();
  const preferenceUserId = user?.uid ?? null;

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [internalPaginationModel, setInternalPaginationModel] = useState<DocumentTablePaginationModel>({
    page: 0,
    pageSize: 20,
  });
  const [internalSortModel, setInternalSortModel] = useState<DocumentTableSortModel>([]);
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<DocumentSummary | null>(null);
  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false);
  const [downloadDocumentId, setDownloadDocumentId] = useState<string | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editDocument, setEditDocument] = useState<DocumentSummary | null>(null);
  const [layoutMenuAnchor, setLayoutMenuAnchor] = useState<HTMLElement | null>(null);
  const draggedColumnRef = useRef<string | null>(null);
  const [tablePreferences, setTablePreferences] = useState<DocumentTablePreferences>(() => (
    loadDocumentTablePreferences(preferenceUserId, DOCUMENT_COLUMN_FIELDS)
  ));

  const paginationModel = controlledPaginationModel ?? internalPaginationModel;
  const sortModel = controlledSortModel ?? internalSortModel;
  const handlePaginationModelChange = onPaginationModelChange ?? setInternalPaginationModel;
  const handleSortModelChange = onSortModelChange ?? setInternalSortModel;
  const serverSorting = onSortModelChange !== undefined;
  const sorting: SortingState = sortModel.map(({ field, sort }) => ({
    id: field,
    desc: sort === 'desc',
  }));
  const rowSelection: RowSelectionState = Object.fromEntries(
    (selectedIds ?? []).map((id) => [id, true]),
  );

  React.useEffect(() => {
    setTablePreferences(loadDocumentTablePreferences(preferenceUserId, DOCUMENT_COLUMN_FIELDS));
  }, [preferenceUserId]);

  const updateTablePreferences = React.useCallback((
    update: (current: DocumentTablePreferences) => DocumentTablePreferences,
  ) => {
    setTablePreferences((current) => saveDocumentTablePreferences(
      preferenceUserId,
      update(current),
      DOCUMENT_COLUMN_FIELDS,
    ));
  }, [preferenceUserId]);

  const handleColumnVisibilityChange = React.useCallback((
    updater: Updater<VisibilityState>,
  ) => {
    updateTablePreferences((current) => normalizeDocumentTablePreferences({
      ...current,
      columnVisibilityModel: typeof updater === 'function'
        ? updater(current.columnVisibilityModel)
        : updater,
    }, DOCUMENT_COLUMN_FIELDS));
  }, [updateTablePreferences]);

  const handleColumnOrderChange = React.useCallback((updater: Updater<ColumnOrderState>) => {
    updateTablePreferences((current) => ({
      ...current,
      columnOrder: typeof updater === 'function' ? updater(current.columnOrder) : updater,
    }));
  }, [updateTablePreferences]);

  const handleColumnSizingChange = React.useCallback((updater: Updater<ColumnSizingState>) => {
    updateTablePreferences((current) => normalizeDocumentTablePreferences({
      ...current,
      columnWidths: typeof updater === 'function' ? updater(current.columnWidths) : updater,
    }, DOCUMENT_COLUMN_FIELDS));
  }, [updateTablePreferences]);

  const handleDensityChange = React.useCallback((density: DocumentTableDensity) => {
    updateTablePreferences((current) => ({ ...current, density }));
  }, [updateTablePreferences]);

  const handleSortingChange = React.useCallback((updater: Updater<SortingState>) => {
    const nextSorting = typeof updater === 'function' ? updater(sorting) : updater;
    handleSortModelChange(nextSorting.slice(0, 1).map(({ id, desc }) => ({
      field: id,
      sort: desc ? 'desc' : 'asc',
    })));
  }, [handleSortModelChange, sorting]);

  const handleRowSelectionChange = React.useCallback((updater: Updater<RowSelectionState>) => {
    const nextSelection = typeof updater === 'function' ? updater(rowSelection) : updater;
    onSelectionChange?.(Object.keys(nextSelection).filter((id) => nextSelection[id]));
  }, [onSelectionChange, rowSelection]);

  const handleToggleColumnVisibility = React.useCallback((field: string) => {
    updateTablePreferences((current) => {
      const columnVisibilityModel = { ...current.columnVisibilityModel };
      if (columnVisibilityModel[field] === false) {
        delete columnVisibilityModel[field];
      } else {
        columnVisibilityModel[field] = false;
      }
      return normalizeDocumentTablePreferences({
        ...current,
        columnVisibilityModel,
      }, DOCUMENT_COLUMN_FIELDS);
    });
  }, [updateTablePreferences]);

  const handleMoveColumn = React.useCallback((field: string, offset: -1 | 1) => {
    updateTablePreferences((current) => {
      const oldIndex = current.columnOrder.indexOf(field);
      return {
        ...current,
        columnOrder: reorderDocumentTableColumns(
          current.columnOrder,
          field,
          oldIndex,
          oldIndex + offset,
        ),
      };
    });
  }, [updateTablePreferences]);

  const handleDropColumn = React.useCallback((targetField: string) => {
    const draggedField = draggedColumnRef.current;
    draggedColumnRef.current = null;
    if (!draggedField || draggedField === targetField) {
      return;
    }
    updateTablePreferences((current) => {
      const nextOrder = current.columnOrder.filter((field) => field !== draggedField);
      const targetIndex = nextOrder.indexOf(targetField);
      nextOrder.splice(targetIndex, 0, draggedField);
      return { ...current, columnOrder: nextOrder };
    });
  }, [updateTablePreferences]);

  const handleResetTableLayout = React.useCallback(() => {
    clearDocumentTablePreferences(preferenceUserId);
    setTablePreferences(defaultDocumentTablePreferences(DOCUMENT_COLUMN_FIELDS));
  }, [preferenceUserId]);

  React.useEffect(() => {
    if (!selectedDocumentId) {
      return;
    }
    const match = documents.find((doc) => doc.id === selectedDocumentId) || null;
    setSelectedDocument(match);
  }, [documents, selectedDocumentId]);

  const extractionHealthy = extractionHealth?.status === 'healthy';
  const uploadBlockedByExtraction =
    showUploadControls && (
      extractionHealthQuery.isError ||
      (extractionHealth != null && !extractionHealthy)
    );

  const uploadBlockedReason =
    extractionHealthQuery.isError
      ? 'Unable to reach PDF extraction service.'
      : extractionHealth && !extractionHealthy
        ? extractionHealth.error || 'PDF extraction service is not healthy.'
        : null;

  const isDocumentBusy = (doc: DocumentSummary): boolean => {
    const processingStatus = String(doc.processingStatus || '').toLowerCase();
    const embeddingStatus = String(doc.embeddingStatus || '').toLowerCase();
    return isActiveDocumentStatus(processingStatus) || embeddingStatus === 'processing';
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
    startDocumentLoad({
      documentId: summary.id,
      filename: summary.filename,
      message: `Loading ${summary.filename || 'document'} for chat...`,
    });

    navigate('/', {
      state: {
        loadForChatDocument: {
          id: summary.id,
          filename: summary.filename,
        },
      },
    });
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
      autoHideDurationMs: PDF_BACKGROUND_PROCESSING_TOAST_AUTO_HIDE_MS,
      anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
    });
  }, []);

  const uploadDocumentFile = React.useCallback(async (file: File): Promise<string> => {
    return uploadPdfDocument(file);
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

    const validation = validatePdfSelection(selectedFiles, { allowMultiple: true });
    if (!validation.ok) {
      alert(validation.error ?? 'Please select PDF files only');
      if (event.target) {
        event.target.value = '';
      }
      return;
    }

    if (validation.files.length === 1) {
      await uploadSingleFile(validation.files[0]);
    } else {
      await uploadMultipleFiles(validation.files);
    }

    // Reset file input
    if (event.target) {
      event.target.value = '';
    }
  };

  const columns: ColumnDef<DocumentSummary>[] = [
    {
      accessorKey: 'filename',
      header: 'Filename',
      size: 240,
      minSize: 150,
      sortingFn: (rowA, rowB, columnId) => compareTextValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
    },
    {
      accessorKey: 'title',
      header: 'Title',
      size: 180,
      minSize: 120,
      enableSorting: false,
      cell: ({ getValue }) => getValue<string | null>() || '—',
    },
    {
      accessorKey: 'sourceProvenance',
      header: 'Source',
      size: 280,
      minSize: 280,
      enableSorting: false,
      cell: ({ row }) => {
        const source = row.original.sourceProvenance ?? null;
        const providerLabel = documentSourceProviderLabel(source);
        const referenceLabel = documentSourceReferenceLabel(source);
        const statusLabel = source?.importStatus ?? source?.artifactStatus ?? null;

        return (
          <Stack spacing={0.25} sx={{ minWidth: 0, py: 0.5 }}>
            <Stack direction="row" spacing={0.5} alignItems="center" sx={{ minWidth: 0 }}>
              <Chip
                label={providerLabel}
                size="small"
                variant={source ? 'filled' : 'outlined'}
                color={source ? 'primary' : 'default'}
                sx={{ flexShrink: 0 }}
              />
              {statusLabel && (
                <Chip label={statusLabel} size="small" variant="outlined" sx={{ flexShrink: 0 }} />
              )}
            </Stack>
            <Tooltip title={referenceLabel}>
              <Typography
                variant="caption"
                color="text.secondary"
                noWrap
                sx={{ display: 'block', maxWidth: '100%' }}
              >
                {referenceLabel}
              </Typography>
            </Tooltip>
          </Stack>
        );
      },
    },
    {
      accessorKey: 'fileSize',
      header: 'Size',
      size: 100,
      minSize: 90,
      sortingFn: (rowA, rowB, columnId) => compareNumberValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
      cell: ({ getValue }) => formatFileSize(getValue<number | null>()),
    },
    {
      accessorKey: 'creationDate',
      header: 'Created',
      size: 140,
      minSize: 120,
      sortingFn: (rowA, rowB, columnId) => compareDateValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
      cell: ({ getValue }) => {
        const value = getValue<string | null>();
        return value ? new Date(value).toLocaleDateString() : '—';
      },
    },
    {
      id: 'processingStatus',
      accessorFn: documentDisplayStatus,
      header: 'Status',
      size: 220,
      minSize: 180,
      enableSorting: false,
      cell: ({ row, getValue }) => {
        const status = getValue<string>();
        return (
          <Stack spacing={0.25} sx={{ minWidth: 0, py: 0.5 }}>
            <Chip
              label={status}
              size="small"
              color={getStatusColor(status)}
              variant={status === 'processing' ? 'outlined' : 'filled'}
              sx={{ alignSelf: 'flex-start' }}
            />
            {row.original.errorMessage && (
              <Tooltip title={row.original.errorMessage}>
                <Typography variant="caption" color="error" noWrap>
                  {row.original.errorMessage}
                </Typography>
              </Tooltip>
            )}
          </Stack>
        );
      },
    },
    {
      accessorKey: 'vectorCount',
      header: 'Vectors',
      size: 90,
      minSize: 80,
      sortingFn: (rowA, rowB, columnId) => compareNumberValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
    },
    {
      accessorKey: 'chunkCount',
      header: 'Chunks',
      size: 90,
      minSize: 80,
      enableSorting: false,
    },
    {
      id: 'actions',
      header: 'Actions',
      size: onTitleUpdate ? 280 : 240,
      minSize: 240,
      enableSorting: false,
      enableResizing: false,
      cell: ({ row }) => {
        const summary = toDocumentSummary(row.original) ?? undefined;
        const disableLoad = row.original.embeddingStatus !== 'completed';

        return (
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Tooltip title="View Details">
              <IconButton
                size="small"
                onClick={() => handleViewDetails(row.original.id)}
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
            {summary && (
              <PreparedReviewAndCurateButton
                documentId={summary.id}
                disabled={disableLoad || !summary}
                iconOnly={true}
                size="small"
              />
            )}
            <Tooltip title="Download">
              <IconButton
                size="small"
                onClick={() => handleOpenDownload(row.original.id)}
              >
                <Download fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Re-embed">
              <span>
                <IconButton
                  size="small"
                  onClick={() => onReembed(row.original.id)}
                  disabled={isDocumentBusy(row.original)}
                >
                  <Refresh fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            {onTitleUpdate && (
              <Tooltip title="Edit display title">
                <IconButton
                  size="small"
                  onClick={() => {
                    const summary = toDocumentSummary(row.original);
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
                  onClick={() => onDelete(row.original.id)}
                  disabled={isDocumentBusy(row.original)}
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

  const table = useReactTable({
    data: documents,
    columns,
    defaultColumn: {
      sortDescFirst: false,
    },
    state: {
      sorting,
      pagination: {
        pageIndex: paginationModel.page,
        pageSize: paginationModel.pageSize,
      },
      rowSelection,
      columnVisibility: tablePreferences.columnVisibilityModel,
      columnOrder: tablePreferences.columnOrder,
      columnSizing: tablePreferences.columnWidths,
    },
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: serverSorting ? undefined : getSortedRowModel(),
    manualSorting: serverSorting,
    manualPagination: true,
    rowCount: totalCount,
    enableMultiRowSelection: checkboxSelection,
    enableRowSelection: checkboxSelection,
    enableSortingRemoval: false,
    columnResizeMode: 'onEnd',
    onSortingChange: handleSortingChange,
    onRowSelectionChange: handleRowSelectionChange,
    onColumnVisibilityChange: handleColumnVisibilityChange,
    onColumnOrderChange: handleColumnOrderChange,
    onColumnSizingChange: handleColumnSizingChange,
  });

  const hasCustomTablePreferences = hasCustomDocumentTablePreferences(
    tablePreferences,
    DOCUMENT_COLUMN_FIELDS,
  );
  const columnLabels = new Map<string, string>(
    DOCUMENT_COLUMN_OPTIONS.map(({ field, label }) => [field, label]),
  );

  const hasDocuments = documents.length > 0;

  return (
    <Box
      sx={{
        flex: '1 1 auto',
        minHeight: 0,
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {showUploadControls && (
        <>
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
        </>
      )}

      <Stack
        direction="row"
        useFlexGap
        spacing={2}
        sx={{ mb: 2, flexWrap: 'wrap' }}
      >
        {showUploadControls && (
          <Button
            variant="contained"
            startIcon={<CloudUpload />}
            onClick={handleUploadClick}
            disabled={loading || pipelineBusy || uploadBlockedByExtraction}
          >
            UPLOAD DOCUMENT(S)
          </Button>
        )}
        <Button
          variant="outlined"
          startIcon={<Refresh />}
          onClick={onRefresh}
          disabled={loading || pipelineBusy}
        >
          Refresh
        </Button>
        <Button
          variant="outlined"
          startIcon={<ViewColumn />}
          onClick={(event) => setLayoutMenuAnchor(event.currentTarget)}
          aria-controls={layoutMenuAnchor ? 'documents-table-layout-menu' : undefined}
          aria-haspopup="dialog"
          aria-expanded={layoutMenuAnchor ? 'true' : undefined}
        >
          Table layout
        </Button>
        <Button
          variant="outlined"
          startIcon={<RestartAlt />}
          onClick={handleResetTableLayout}
          disabled={!hasCustomTablePreferences}
        >
          Reset table layout
        </Button>
        <Popover
          id="documents-table-layout-menu"
          anchorEl={layoutMenuAnchor}
          open={Boolean(layoutMenuAnchor)}
          onClose={() => setLayoutMenuAnchor(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
          PaperProps={{ role: 'dialog', 'aria-label': 'Documents table layout' }}
        >
          <List
            dense
            aria-label="Documents table columns"
            sx={{ minWidth: 320 }}
          >
            {tablePreferences.columnOrder.map((field, index) => {
              const label = columnLabels.get(field) ?? field;
              const visible = tablePreferences.columnVisibilityModel[field] !== false;
              return (
                <ListItem
                  key={field}
                  draggable
                  onDragStart={() => {
                    draggedColumnRef.current = field;
                  }}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => handleDropColumn(field)}
                  sx={{ cursor: 'grab' }}
                >
                <Checkbox
                  checked={visible}
                  size="small"
                  autoFocus={index === 0}
                  onChange={() => handleToggleColumnVisibility(field)}
                  inputProps={{ 'aria-label': `Show ${label} column` }}
                />
                <ListItemText primary={label} />
                <Tooltip title="Move earlier">
                  <span>
                    <IconButton
                      size="small"
                      aria-label={`Move ${label} column earlier`}
                      disabled={index === 0}
                      onClick={() => {
                        handleMoveColumn(field, -1);
                      }}
                    >
                      <ArrowUpward fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                <Tooltip title="Move later">
                  <span>
                    <IconButton
                      size="small"
                      aria-label={`Move ${label} column later`}
                      disabled={index === tablePreferences.columnOrder.length - 1}
                      onClick={() => {
                        handleMoveColumn(field, 1);
                      }}
                    >
                      <ArrowDownward fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                </ListItem>
              );
            })}
            <Divider component="li" sx={{ my: 1 }} />
            <ListItem>
              <RadioGroup
                row
                value={tablePreferences.density}
                onChange={(event) => handleDensityChange(event.target.value as DocumentTableDensity)}
                aria-label="Documents table density"
              >
                <FormControlLabel value="compact" control={<Radio size="small" />} label="Compact" />
                <FormControlLabel value="standard" control={<Radio size="small" />} label="Standard" />
              </RadioGroup>
            </ListItem>
          </List>
        </Popover>
        {pipelineBusy && (
          <Stack direction="row" spacing={1} alignItems="center">
            <CircularProgress size={16} thickness={5} />
            <Typography variant="body2" color="text.secondary">
              {pipelineMessage ||
                'Processing in progress.'}
            </Typography>
          </Stack>
        )}
        {showUploadControls && (
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            multiple
            style={{ display: 'none' }}
            onChange={handleFileSelect}
          />
        )}
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

      <Box
        data-testid="documents-table-scroll-region"
        sx={{
          flex: hasDocuments ? '1 1 0' : '0 0 auto',
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
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
                        {loading ? 'Loading documents…' : showUploadControls ? 'No documents yet. Upload a PDF to get started.' : 'No library documents yet.'}
                      </Typography>
                    </Stack>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </TableContainer>
        ) : (
          <Paper
            variant="outlined"
            data-testid="documents-table"
            sx={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}
          >
            <TableContainer sx={{ flex: '1 1 0', minHeight: 0, overflow: 'auto' }}>
              <Table
                stickyHeader
                size={tablePreferences.density === 'compact' ? 'small' : 'medium'}
                aria-label="Documents"
                sx={{
                  width: table.getTotalSize() + (checkboxSelection ? DOCUMENT_SELECTION_COLUMN_WIDTH : 0),
                  minWidth: '100%',
                  tableLayout: 'fixed',
                }}
              >
                <TableHead>
                  {table.getHeaderGroups().map((headerGroup) => (
                    <TableRow key={headerGroup.id}>
                      {checkboxSelection && (
                        <TableCell padding="checkbox" sx={{ width: DOCUMENT_SELECTION_COLUMN_WIDTH }}>
                          <Checkbox
                            size="small"
                            checked={table.getIsAllPageRowsSelected()}
                            indeterminate={table.getIsSomePageRowsSelected()}
                            onChange={table.getToggleAllPageRowsSelectedHandler()}
                            inputProps={{ 'aria-label': 'Select all documents on this page' }}
                          />
                        </TableCell>
                      )}
                      {headerGroup.headers.map((header) => {
                        const sorted = header.column.getIsSorted();
                        const label = columnLabels.get(header.column.id) ?? header.column.id;
                        return (
                          <TableCell
                            key={header.id}
                            component="th"
                            scope="col"
                            draggable
                            onDragStart={() => {
                              draggedColumnRef.current = header.column.id;
                            }}
                            onDragOver={(event) => event.preventDefault()}
                            onDrop={() => handleDropColumn(header.column.id)}
                            aria-label={label}
                            aria-sort={sorted ? (sorted === 'asc' ? 'ascending' : 'descending') : undefined}
                            sx={{
                              width: header.getSize(),
                              position: 'relative',
                              bgcolor: 'background.paper',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {header.column.getCanSort() ? (
                              <TableSortLabel
                                active={Boolean(sorted)}
                                direction={sorted || 'asc'}
                                onClick={header.column.getToggleSortingHandler()}
                              >
                                {flexRender(header.column.columnDef.header, header.getContext())}
                              </TableSortLabel>
                            ) : (
                              flexRender(header.column.columnDef.header, header.getContext())
                            )}
                            {header.column.getCanResize() && (
                              <Box
                                role="separator"
                                aria-orientation="vertical"
                                aria-label={`Resize ${label} column`}
                                tabIndex={0}
                                onMouseDown={header.getResizeHandler()}
                                onTouchStart={header.getResizeHandler()}
                                onKeyDown={(event) => {
                                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                                    event.preventDefault();
                                    handleColumnSizingChange((current) => ({
                                      ...current,
                                      [header.column.id]: header.column.getSize()
                                        + (event.key === 'ArrowRight' ? 8 : -8),
                                    }));
                                  }
                                }}
                                sx={{
                                  position: 'absolute',
                                  insetBlock: 0,
                                  right: -4,
                                  width: 8,
                                  cursor: 'col-resize',
                                  touchAction: 'none',
                                  zIndex: 1,
                                  '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.main' },
                                }}
                              />
                            )}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  ))}
                </TableHead>
                <TableBody>
                  {table.getRowModel().rows.map((row) => (
                    <TableRow key={row.id} hover sx={{ cursor: 'pointer' }}>
                      {checkboxSelection && (
                        <TableCell padding="checkbox">
                          <Checkbox
                            size="small"
                            checked={row.getIsSelected()}
                            onChange={row.getToggleSelectedHandler()}
                            inputProps={{ 'aria-label': `Select ${row.original.filename}` }}
                          />
                        </TableCell>
                      )}
                      {row.getVisibleCells().map((cell) => (
                        <TableCell
                          key={cell.id}
                          sx={{
                            width: cell.column.getSize(),
                            py: tablePreferences.density === 'compact' ? 0.5 : 1,
                            overflow: 'hidden',
                          }}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
            <TablePagination
              component="div"
              count={totalCount}
              page={paginationModel.page}
              rowsPerPage={paginationModel.pageSize}
              rowsPerPageOptions={[10, 20, 50, 100]}
              onPageChange={(_event, page) => handlePaginationModelChange({
                page,
                pageSize: paginationModel.pageSize,
              })}
              onRowsPerPageChange={(event) => handlePaginationModelChange({
                page: 0,
                pageSize: Number(event.target.value),
              })}
              labelRowsPerPage="Documents per page:"
            />
          </Paper>
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
          originalFilename={editDocument?.filename ?? null}
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
