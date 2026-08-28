import React, { useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  columnOrderingFeature,
  columnResizingFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  createColumnHelper,
  createSortedRowModel,
  rowPaginationFeature,
  rowSelectionFeature,
  rowSortingFeature,
  tableFeatures,
  type ColumnSizingState,
  type ColumnVisibilityState,
  type PaginationState,
  type RowSelectionState,
  type SortingState,
  type Updater,
  useTable,
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
  TablePagination,
  TableSortLabel,
  ToggleButton,
  ToggleButtonGroup,
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
  DocumentSourceProvenance,
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
  DocumentPaginationModel,
  DocumentSortModel,
} from '@/features/documents/documentTableTypes';

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
  paginationModel?: DocumentPaginationModel;
  /** Called when the user requests another page or page size. */
  onPaginationModelChange?: (model: DocumentPaginationModel) => void;
  /** Server-backed sort state for document fields supported by the API. */
  sortModel?: DocumentSortModel;
  /** Called when the user changes sort order. */
  onSortModelChange?: (model: DocumentSortModel) => void;
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
const DOCUMENT_PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
const DOCUMENT_SELECTION_COLUMN_ID = '_selection';
const COLUMN_RESIZE_KEYBOARD_STEP = 10;

const DOCUMENT_TABLE_FEATURES = tableFeatures({
  columnOrderingFeature,
  columnSizingFeature,
  columnResizingFeature,
  columnVisibilityFeature,
  rowPaginationFeature,
  rowSelectionFeature,
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
});

const documentColumnHelper = createColumnHelper<typeof DOCUMENT_TABLE_FEATURES, DocumentSummary>();

const applyUpdater = <T,>(updater: Updater<T>, current: T): T => (
  typeof updater === 'function'
    ? (updater as (previous: T) => T)(current)
    : updater
);

const toTanStackSorting = (sortModel: DocumentSortModel): SortingState => (
  sortModel.map(({ field, sort }) => ({ id: field, desc: sort === 'desc' }))
);

const fromTanStackSorting = (sorting: SortingState): DocumentSortModel => (
  sorting.map(({ id, desc }) => ({ field: id, sort: desc ? 'desc' : 'asc' }))
);

const toRowSelection = (ids: readonly string[]): RowSelectionState => Object.fromEntries(
  ids.map((id) => [id, true] as const),
);

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

const isDocumentBusy = (document: DocumentSummary): boolean => {
  const processingStatus = String(document.processingStatus || '').toLowerCase();
  const embeddingStatus = String(document.embeddingStatus || '').toLowerCase();
  return isActiveDocumentStatus(processingStatus) || embeddingStatus === 'processing';
};

const formatFileSize = (bytes: number | null | undefined): string => {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
};

const getStatusColor = (
  status: string,
): 'default' | 'primary' | 'success' | 'error' | 'warning' => {
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
  const draggedColumnIdRef = useRef<string | null>(null);
  const [internalPaginationModel, setInternalPaginationModel] = useState<DocumentPaginationModel>({
    page: 0,
    pageSize: 20,
  });
  const [internalSortModel, setInternalSortModel] = useState<DocumentSortModel>([]);
  const [internalSelectedIds, setInternalSelectedIds] = useState<string[]>([]);
  const [detailsDialogOpen, setDetailsDialogOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const [selectedDocument, setSelectedDocument] = useState<DocumentSummary | null>(null);
  const [downloadDialogOpen, setDownloadDialogOpen] = useState(false);
  const [downloadDocumentId, setDownloadDocumentId] = useState<string | null>(null);
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editDocument, setEditDocument] = useState<DocumentSummary | null>(null);
  const [layoutMenuAnchor, setLayoutMenuAnchor] = useState<HTMLElement | null>(null);
  const [tablePreferences, setTablePreferences] = useState<DocumentTablePreferences>(() => (
    loadDocumentTablePreferences(preferenceUserId, DOCUMENT_COLUMN_FIELDS)
  ));

  const paginationModel = controlledPaginationModel ?? internalPaginationModel;
  const sortModel = controlledSortModel ?? internalSortModel;
  const handlePaginationModelChange = onPaginationModelChange ?? setInternalPaginationModel;
  const handleSortModelChange = onSortModelChange ?? setInternalSortModel;
  const serverSorting = onSortModelChange !== undefined;
  const selectionIsControlled = selectedIds !== undefined;
  const selectedDocumentIds = selectedIds ?? internalSelectedIds;
  const handleSelectedIdsChange = React.useCallback((nextIds: string[]) => {
    if (!selectionIsControlled) {
      setInternalSelectedIds(nextIds);
    }
    onSelectionChange?.(nextIds);
  }, [onSelectionChange, selectionIsControlled]);

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

  const handleColumnVisibilityModelChange = React.useCallback((
    updater: Updater<ColumnVisibilityState>,
  ) => {
    updateTablePreferences((current) => normalizeDocumentTablePreferences({
      ...current,
      columnVisibilityModel: applyUpdater(updater, current.columnVisibilityModel),
    }, DOCUMENT_COLUMN_FIELDS));
  }, [updateTablePreferences]);

  const handleColumnOrderChange = React.useCallback((updater: Updater<string[]>) => {
    updateTablePreferences((current) => normalizeDocumentTablePreferences({
      ...current,
      columnOrder: applyUpdater(updater, current.columnOrder).filter(
        (field) => field !== DOCUMENT_SELECTION_COLUMN_ID,
      ),
    }, DOCUMENT_COLUMN_FIELDS));
  }, [updateTablePreferences]);

  const handleColumnSizingChange = React.useCallback((updater: Updater<ColumnSizingState>) => {
    updateTablePreferences((current) => normalizeDocumentTablePreferences({
      ...current,
      columnSizing: applyUpdater(updater, current.columnSizing),
    }, DOCUMENT_COLUMN_FIELDS));
  }, [updateTablePreferences]);

  const handleDensityChange = React.useCallback((density: DocumentTableDensity | null) => {
    if (!density) {
      return;
    }
    updateTablePreferences((current) => ({ ...current, density }));
  }, [updateTablePreferences]);

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

  const handleDropColumn = React.useCallback((field: string, targetField: string) => {
    updateTablePreferences((current) => {
      const oldIndex = current.columnOrder.indexOf(field);
      const targetIndex = current.columnOrder.indexOf(targetField);
      if (oldIndex < 0 || targetIndex < 0) {
        return current;
      }
      return {
        ...current,
        columnOrder: reorderDocumentTableColumns(
          current.columnOrder,
          field,
          oldIndex,
          targetIndex,
        ),
      };
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

  const handleViewDetails = React.useCallback((id: string) => {
    const doc = documents.find((item) => item.id === id) || null;
    setSelectedDocument(doc);
    setSelectedDocumentId(id);
    setDetailsDialogOpen(true);
  }, [documents]);

  const handleCloseDetails = () => {
    setDetailsDialogOpen(false);
    setSelectedDocumentId(null);
    setSelectedDocument(null);
  };

  const handleOpenDownload = React.useCallback((id: string) => {
    setDownloadDocumentId(id);
    setDownloadDialogOpen(true);
  }, []);

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

  const handleLoadFromTable = React.useCallback((summary: DocumentSummary) => {
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
  }, [navigate]);

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

  const columns = useMemo(() => documentColumnHelper.columns([
    ...(checkboxSelection ? [documentColumnHelper.display({
      id: DOCUMENT_SELECTION_COLUMN_ID,
      header: ({ table }) => (
        <Checkbox
          size="small"
          checked={table.getIsAllPageRowsSelected()}
          indeterminate={table.getIsSomePageRowsSelected() && !table.getIsAllPageRowsSelected()}
          onChange={table.getToggleAllPageRowsSelectedHandler()}
          inputProps={{ 'aria-label': 'Select all documents on this page' }}
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          size="small"
          checked={row.getIsSelected()}
          disabled={!row.getCanSelect()}
          onClick={row.getToggleSelectedHandler()}
          onChange={() => undefined}
          inputProps={{ 'aria-label': `Select ${row.original.filename}` }}
        />
      ),
      size: 52,
      minSize: 52,
      maxSize: 52,
      enableHiding: false,
      enableResizing: false,
      enableSorting: false,
    })] : []),
    documentColumnHelper.accessor('filename', {
      id: 'filename',
      header: 'Filename',
      cell: ({ getValue }) => getValue(),
      size: 240,
      minSize: 150,
      sortFn: (rowA, rowB, columnId) => compareTextValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
    }),
    documentColumnHelper.accessor('title', {
      id: 'title',
      header: 'Title',
      cell: ({ getValue }) => getValue() || '—',
      size: 180,
      minSize: 120,
      enableSorting: false,
    }),
    documentColumnHelper.accessor('sourceProvenance', {
      id: 'sourceProvenance',
      header: 'Source',
      size: 320,
      minSize: 280,
      enableSorting: false,
      cell: ({ getValue, row }) => {
        const source: DocumentSourceProvenance | null = getValue() ?? row.original.sourceProvenance ?? null;
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
    }),
    documentColumnHelper.accessor('fileSize', {
      id: 'fileSize',
      header: 'Size',
      cell: ({ getValue }) => formatFileSize(getValue()),
      size: 100,
      minSize: 90,
      sortFn: (rowA, rowB, columnId) => compareNumberValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
      sortUndefined: false,
    }),
    documentColumnHelper.accessor('creationDate', {
      id: 'creationDate',
      header: 'Created',
      cell: ({ getValue }) => {
        const value = getValue();
        return value ? new Date(value).toLocaleDateString() : '—';
      },
      size: 140,
      minSize: 120,
      sortFn: (rowA, rowB, columnId) => compareDateValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
      sortUndefined: false,
    }),
    documentColumnHelper.accessor('processingStatus', {
      id: 'processingStatus',
      header: 'Status',
      size: 220,
      minSize: 180,
      enableSorting: false,
      cell: ({ row }) => {
        const document = row.original;
        const status = documentDisplayStatus(document);
        return (
          <Stack spacing={0.25} sx={{ minWidth: 0, py: 0.5 }}>
            <Chip
              label={status}
              size="small"
              color={getStatusColor(status)}
              variant={status === 'processing' ? 'outlined' : 'filled'}
              sx={{ alignSelf: 'flex-start' }}
            />
            {document.errorMessage && (
              <Tooltip title={document.errorMessage}>
                <Typography variant="caption" color="error" noWrap>
                  {document.errorMessage}
                </Typography>
              </Tooltip>
            )}
          </Stack>
        );
      },
    }),
    documentColumnHelper.accessor('vectorCount', {
      id: 'vectorCount',
      header: 'Vectors',
      size: 90,
      minSize: 80,
      sortFn: (rowA, rowB, columnId) => compareNumberValues(
        rowA.getValue(columnId),
        rowB.getValue(columnId),
      ),
      sortUndefined: false,
    }),
    documentColumnHelper.accessor('chunkCount', {
      id: 'chunkCount',
      header: 'Chunks',
      size: 90,
      minSize: 80,
      enableSorting: false,
    }),
    documentColumnHelper.display({
      id: 'actions',
      header: 'Actions',
      size: onTitleUpdate ? 280 : 240,
      minSize: onTitleUpdate ? 280 : 240,
      enableSorting: false,
      cell: ({ row }) => {
        const summary = row.original;
        const disableLoad = summary.embeddingStatus !== 'completed';

        return (
          <Box sx={{ display: 'flex', alignItems: 'center' }}>
            <Tooltip title="View Details">
              <IconButton size="small" onClick={() => handleViewDetails(summary.id)}>
                <Visibility fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Load for Chat">
              <span>
                <IconButton
                  size="small"
                  onClick={() => handleLoadFromTable(summary)}
                  disabled={disableLoad}
                  color="success"
                >
                  <FileOpen fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <PreparedReviewAndCurateButton
              documentId={summary.id}
              disabled={disableLoad}
              iconOnly={true}
              size="small"
            />
            <Tooltip title="Download">
              <IconButton size="small" onClick={() => handleOpenDownload(summary.id)}>
                <Download fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title="Re-embed">
              <span>
                <IconButton
                  size="small"
                  onClick={() => onReembed(summary.id)}
                  disabled={isDocumentBusy(summary)}
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
                  onClick={() => onDelete(summary.id)}
                  disabled={isDocumentBusy(summary)}
                >
                  <Delete fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        );
      },
    }),
  ]), [
    checkboxSelection,
    handleLoadFromTable,
    handleOpenDownload,
    handleViewDetails,
    onDelete,
    onReembed,
    onTitleUpdate,
  ]);

  const tanStackSorting = useMemo(() => toTanStackSorting(sortModel), [sortModel]);
  const rowSelection = useMemo(
    () => toRowSelection(selectedDocumentIds),
    [selectedDocumentIds],
  );
  const tanStackPagination = useMemo<PaginationState>(() => ({
    pageIndex: paginationModel.page,
    pageSize: paginationModel.pageSize,
  }), [paginationModel.page, paginationModel.pageSize]);

  const handleSortingChange = React.useCallback((updater: Updater<SortingState>) => {
    handleSortModelChange(fromTanStackSorting(applyUpdater(updater, tanStackSorting)));
  }, [handleSortModelChange, tanStackSorting]);

  const handlePaginationChange = React.useCallback((updater: Updater<PaginationState>) => {
    const next = applyUpdater(updater, tanStackPagination);
    handlePaginationModelChange({ page: next.pageIndex, pageSize: next.pageSize });
  }, [handlePaginationModelChange, tanStackPagination]);

  const handleRowSelectionChange = React.useCallback((updater: Updater<RowSelectionState>) => {
    const next = applyUpdater(updater, rowSelection);
    handleSelectedIdsChange(Object.keys(next).filter((id) => next[id]));
  }, [handleSelectedIdsChange, rowSelection]);

  const table = useTable({
    features: DOCUMENT_TABLE_FEATURES,
    columns,
    data: documents,
    defaultColumn: {
      maxSize: 800,
    },
    getRowId: (document) => document.id,
    state: {
      columnOrder: checkboxSelection
        ? [DOCUMENT_SELECTION_COLUMN_ID, ...tablePreferences.columnOrder]
        : tablePreferences.columnOrder,
      columnSizing: tablePreferences.columnSizing,
      columnVisibility: tablePreferences.columnVisibilityModel,
      pagination: tanStackPagination,
      rowSelection,
      sorting: tanStackSorting,
    },
    onColumnOrderChange: handleColumnOrderChange,
    onColumnSizingChange: handleColumnSizingChange,
    onColumnVisibilityChange: handleColumnVisibilityModelChange,
    onPaginationChange: handlePaginationChange,
    onRowSelectionChange: handleRowSelectionChange,
    onSortingChange: handleSortingChange,
    columnResizeMode: 'onEnd',
    enableMultiSort: false,
    enableSortingRemoval: false,
    manualPagination: true,
    manualSorting: serverSorting,
    rowCount: totalCount,
    sortDescFirst: false,
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
          <Box sx={{ px: 2, pt: 1.5 }}>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.75 }}>
              Row density
            </Typography>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={tablePreferences.density}
              onChange={(_event, density: DocumentTableDensity | null) => handleDensityChange(density)}
              aria-label="Documents table row density"
              fullWidth
            >
              <ToggleButton value="compact" aria-label="Compact row density">
                Compact
              </ToggleButton>
              <ToggleButton value="standard" aria-label="Standard row density">
                Standard
              </ToggleButton>
            </ToggleButtonGroup>
          </Box>
          <List
            dense
            aria-label="Documents table columns"
            sx={{ minWidth: 320 }}
          >
            {tablePreferences.columnOrder.map((field, index) => {
              const label = columnLabels.get(field) ?? field;
              const visible = tablePreferences.columnVisibilityModel[field] !== false;
              return (
                <ListItem key={field}>
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
            data-testid="documents-table-root"
            data-sorting-mode={serverSorting ? 'server' : 'client'}
            data-pagination-mode="server"
            data-filter-mode="server"
            data-row-count={totalCount}
            data-loading={loading}
            data-page={paginationModel.page}
            data-page-size={paginationModel.pageSize}
            sx={{
              display: 'flex',
              flexDirection: 'column',
              height: '100%',
              minHeight: 0,
              overflow: 'hidden',
            }}
          >
            <TableContainer
              data-testid="documents-table-scroller"
              sx={{ flex: '1 1 auto', minHeight: 0, overflow: 'auto' }}
            >
              <Table
                stickyHeader
                size="small"
                aria-label="Documents table"
                sx={{
                  tableLayout: 'fixed',
                  minWidth: '100%',
                  width: table.getTotalSize(),
                }}
              >
                <TableHead>
                  {table.getHeaderGroups().map((headerGroup) => (
                    <TableRow key={headerGroup.id}>
                      {headerGroup.headers.map((header) => {
                        const columnId = header.column.id;
                        const isSelectionColumn = columnId === DOCUMENT_SELECTION_COLUMN_ID;
                        const sorted = header.column.getIsSorted();
                        const canSort = header.column.getCanSort();
                        const canResize = header.column.getCanResize();
                        const columnLabel = columnLabels.get(columnId) ?? columnId;
                        return (
                          <TableCell
                            key={header.id}
                            component="th"
                            scope="col"
                            onDragOver={(event) => {
                              if (!isSelectionColumn) event.preventDefault();
                            }}
                            onDrop={(event) => {
                              event.preventDefault();
                              const draggedColumnId = draggedColumnIdRef.current
                                ?? event.dataTransfer.getData('text/plain');
                              if (draggedColumnId && !isSelectionColumn && draggedColumnId !== columnId) {
                                handleDropColumn(draggedColumnId, columnId);
                              }
                              draggedColumnIdRef.current = null;
                            }}
                            onDragEnd={() => {
                              draggedColumnIdRef.current = null;
                            }}
                            aria-label={isSelectionColumn ? undefined : columnLabel}
                            aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined}
                            title={isSelectionColumn ? undefined : `Drag ${columnLabel} to reorder. Use Table layout for keyboard reordering.`}
                            sx={{
                              position: 'relative',
                              width: header.getSize(),
                              minWidth: header.column.columnDef.minSize,
                              maxWidth: header.column.columnDef.maxSize,
                              bgcolor: 'background.paper',
                              borderBottomColor: 'divider',
                              px: isSelectionColumn ? 0.5 : 2,
                              textAlign: columnId === 'actions' ? 'center' : 'left',
                              userSelect: 'none',
                            }}
                          >
                            {!isSelectionColumn && (
                              <Box
                                component="span"
                                draggable
                                aria-label={`Drag ${columnLabel} column`}
                                title={`Drag ${columnLabel} to reorder`}
                                onClick={(event) => event.stopPropagation()}
                                onDragStart={(event) => {
                                  draggedColumnIdRef.current = columnId;
                                  event.dataTransfer.effectAllowed = 'move';
                                  event.dataTransfer.setData('text/plain', columnId);
                                }}
                                onDragEnd={() => {
                                  draggedColumnIdRef.current = null;
                                }}
                                sx={{
                                  display: 'inline-block',
                                  width: 10,
                                  height: 18,
                                  mr: 0.75,
                                  verticalAlign: 'middle',
                                  cursor: 'grab',
                                  backgroundImage: 'radial-gradient(circle, currentColor 1px, transparent 1.5px)',
                                  backgroundSize: '4px 4px',
                                  backgroundPosition: 'center',
                                  opacity: 0.45,
                                }}
                              />
                            )}
                            {header.isPlaceholder ? null : canSort ? (
                              <TableSortLabel
                                active={Boolean(sorted)}
                                direction={sorted || 'asc'}
                                onClick={header.column.getToggleSortingHandler()}
                              >
                                <table.FlexRender header={header} />
                              </TableSortLabel>
                            ) : (
                              <table.FlexRender header={header} />
                            )}
                            {canResize && (
                              <Box
                                component="span"
                                role="separator"
                                tabIndex={0}
                                aria-label={`Resize ${columnLabel} column`}
                                aria-orientation="vertical"
                                aria-valuemin={header.column.columnDef.minSize ?? 20}
                                aria-valuemax={header.column.columnDef.maxSize ?? 1000}
                                aria-valuenow={header.getSize()}
                                onMouseDown={(event) => {
                                  event.stopPropagation();
                                  header.getResizeHandler()(event);
                                }}
                                onTouchStart={(event) => {
                                  event.stopPropagation();
                                  header.getResizeHandler()(event);
                                }}
                                onDoubleClick={() => header.column.resetSize()}
                                onKeyDown={(event) => {
                                  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
                                  event.preventDefault();
                                  event.stopPropagation();
                                  const direction = event.key === 'ArrowRight' ? 1 : -1;
                                  const minSize = header.column.columnDef.minSize ?? 20;
                                  const maxSize = header.column.columnDef.maxSize ?? 800;
                                  const nextSize = Math.max(
                                    minSize,
                                    Math.min(
                                      maxSize,
                                      header.getSize() + direction * COLUMN_RESIZE_KEYBOARD_STEP,
                                    ),
                                  );
                                  handleColumnSizingChange((current) => ({
                                    ...current,
                                    [columnId]: nextSize,
                                  }));
                                }}
                                sx={{
                                  position: 'absolute',
                                  top: 0,
                                  right: -4,
                                  width: 8,
                                  height: '100%',
                                  cursor: 'col-resize',
                                  touchAction: 'none',
                                  zIndex: 1,
                                  '&::after': {
                                    content: '""',
                                    position: 'absolute',
                                    top: '20%',
                                    bottom: '20%',
                                    left: '3px',
                                    width: '2px',
                                    bgcolor: header.column.getIsResizing() ? 'primary.main' : 'divider',
                                  },
                                  '&:focus-visible': {
                                    outline: '2px solid',
                                    outlineColor: 'primary.main',
                                    outlineOffset: '-2px',
                                  },
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
                    <TableRow
                      key={row.id}
                      data-testid="document-table-row"
                      selected={row.getIsSelected()}
                      sx={{
                        cursor: 'pointer',
                        '&:hover': { bgcolor: 'action.hover' },
                      }}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <TableCell
                          key={cell.id}
                          sx={{
                            width: cell.column.getSize(),
                            maxWidth: cell.column.getSize(),
                            overflow: 'hidden',
                            borderBottomColor: 'divider',
                            px: cell.column.id === DOCUMENT_SELECTION_COLUMN_ID ? 0.5 : 2,
                            py: tablePreferences.density === 'compact' ? 0.5 : 1.25,
                            textOverflow: 'ellipsis',
                            whiteSpace: cell.column.id === 'actions' ? 'nowrap' : 'normal',
                          }}
                        >
                          <table.FlexRender cell={cell} />
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
              rowsPerPageOptions={DOCUMENT_PAGE_SIZE_OPTIONS}
              onPageChange={(_event, page) => {
                handlePaginationModelChange({ ...paginationModel, page });
              }}
              onRowsPerPageChange={(event) => {
                handlePaginationModelChange({ page: 0, pageSize: Number(event.target.value) });
              }}
              sx={{
                flex: '0 0 auto',
                borderTop: 1,
                borderColor: 'divider',
              }}
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
