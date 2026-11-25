import React from 'react';
import { Link as RouterLink, useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  LinearProgress,
  Link,
  Paper,
  Stack,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
  alpha,
  useTheme,
} from '@mui/material';
import {
  Article as ArticleIcon,
  CheckCircle as CheckCircleIcon,
  CloudUpload as CloudUploadIcon,
  ContentCopy as ContentCopyIcon,
  ErrorOutlined as ErrorOutlineIcon,
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  FileOpen as FileOpenIcon,
  HourglassTop as HourglassTopIcon,
  PlayArrow as PlayArrowIcon,
  Refresh as RefreshIcon,
  Search as SearchIcon,
  WarningAmber as WarningAmberIcon,
} from '@mui/icons-material';
import { UNNAMED_FILE_LABEL } from '@/components/pdfViewer/viewerFiles';
import {
  uploadPdfDocument,
  validatePdfSelection,
} from '@/features/documents/pdfUploadFlow';
import type {
  IdentifierSourceFile,
  IdentifierSourceListedFile,
  IdentifierSourceListing,
  ListingRefusalCode,
} from '@/features/documents/paperSourceFiles';
import {
  PDF_JOB_FALLBACK_POLL_INTERVAL_MS,
  PDF_JOB_LIMIT,
  PDF_JOB_WINDOW_DAYS,
} from '@/features/documents/documentIntakeConfig';
import { buildPdfTerminalNotification } from '@/features/documents/pdfTerminalNotifications';
import { emitGlobalToast } from '@/lib/globalNotifications';
import PdfJobsPanel from '../../components/weaviate/PdfJobsPanel';
import {
  ADDABLE_TEXT,
  ADD_LITERATURE_TEXT,
  DEFAULT_PROVIDER_LABEL,
  NOT_TICKABLE_REASON_TEXT,
  OBSERVATION_TEXT,
  TRY_AGAIN_BUTTON_TEXT,
  addFilesRefusalText,
  duplicateCopiesText,
  fewCharactersNote,
  fileNoLongerChoosableText,
  importWithSupplementsButtonText,
  listingRefusalText,
  notShownText,
  resolveProviderShortLabel,
  sourceFileStatusText,
  unconvertedTickableSupplementText,
} from '../../components/weaviate/paperFileReasonText';
import {
  cancelPdfJob,
  fetchDocumentSourceFiles,
  fetchDocumentSourceProviderConfiguration,
  fetchPdfJobs,
  importSourceIdentifiers,
  resolveSourceIdentifiers,
  SourceIdentifierImportError,
  type ChosenSupplementInput,
  type DocumentSourceProviderMetadata,
  type LiteratureImportResult,
  type LiteratureImportStatus,
  type PdfProcessingJob,
} from '../../services/weaviate';

type ImportMode = 'identifiers' | 'upload';
type PendingAction = 'resolve' | 'import' | null;
type UploadStatus = 'idle' | 'uploading' | 'complete' | 'error';
type IdentifierFeedback = {
  severity: 'info' | 'success' | 'warning' | 'error';
  message: string;
};
type PaperFileListingEntry =
  | { status: 'loading' }
  | { status: 'loaded'; listing: IdentifierSourceListing }
  | { status: 'refused'; code: ListingRefusalCode; httpStatus: number }
  | { status: 'error' };

const statusTone: Record<
  LiteratureImportStatus,
  {
    label: string;
    chipColor: 'success' | 'info' | 'warning' | 'error' | 'default';
    icon: React.ReactElement;
  }
> = {
  resolved: {
    label: 'Resolved',
    chipColor: 'success',
    icon: <CheckCircleIcon fontSize="small" />,
  },
  imported: {
    label: 'Imported',
    chipColor: 'success',
    icon: <CheckCircleIcon fontSize="small" />,
  },
  duplicate: {
    label: 'Duplicate',
    chipColor: 'info',
    icon: <ContentCopyIcon fontSize="small" />,
  },
  invalid: {
    label: 'Invalid',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
  access_denied: {
    label: 'Access denied',
    chipColor: 'warning',
    icon: <WarningAmberIcon fontSize="small" />,
  },
  conversion_running: {
    label: 'Conversion running',
    chipColor: 'warning',
    icon: <HourglassTopIcon fontSize="small" />,
  },
  conversion_failed: {
    label: 'Conversion failed',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
  needs_selection: {
    label: 'Needs selection',
    chipColor: 'warning',
    icon: <WarningAmberIcon fontSize="small" />,
  },
  no_source_pdf: {
    label: 'No source PDF',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
  no_converted_text: {
    label: 'No converted text',
    chipColor: 'warning',
    icon: <WarningAmberIcon fontSize="small" />,
  },
  provider_unavailable: {
    label: 'Provider unavailable',
    chipColor: 'error',
    icon: <ErrorOutlineIcon fontSize="small" />,
  },
};

const areJsonValuesEquivalent = (previous: unknown, next: unknown): boolean => {
  if (Object.is(previous, next)) {
    return true;
  }
  if (typeof previous !== 'object' || typeof next !== 'object' || previous === null || next === null) {
    return false;
  }
  if (Array.isArray(previous) || Array.isArray(next)) {
    if (!Array.isArray(previous) || !Array.isArray(next) || previous.length !== next.length) {
      return false;
    }
    return previous.every((entry, index) => areJsonValuesEquivalent(entry, next[index]));
  }
  const prevRecord = previous as Record<string, unknown>;
  const nextRecord = next as Record<string, unknown>;
  const prevKeys = Object.keys(prevRecord);
  const nextKeys = Object.keys(nextRecord);
  if (prevKeys.length !== nextKeys.length) {
    return false;
  }
  return prevKeys.every(
    (key) => Object.prototype.hasOwnProperty.call(nextRecord, key)
      && areJsonValuesEquivalent(prevRecord[key], nextRecord[key]),
  );
};

const areJobMetadataEquivalent = (
  previous: PdfProcessingJob['metadata'],
  next: PdfProcessingJob['metadata'],
): boolean => {
  if (previous === next) {
    return true;
  }
  if (!previous || !next) {
    return (previous ?? null) === (next ?? null);
  }
  return areJsonValuesEquivalent(previous, next);
};
const areJobsEquivalent = (previous: PdfProcessingJob[], next: PdfProcessingJob[]): boolean => {
  if (previous === next) {
    return true;
  }
  if (previous.length !== next.length) {
    return false;
  }

  for (let index = 0; index < previous.length; index += 1) {
    const prevJob = previous[index];
    const nextJob = next[index];

    if (
      prevJob.job_id !== nextJob.job_id ||
      prevJob.status !== nextJob.status ||
      prevJob.progress_percentage !== nextJob.progress_percentage ||
      prevJob.current_stage !== nextJob.current_stage ||
      prevJob.message !== nextJob.message ||
      prevJob.error_message !== nextJob.error_message ||
      prevJob.cancel_requested !== nextJob.cancel_requested ||
      prevJob.updated_at !== nextJob.updated_at ||
      prevJob.completed_at !== nextJob.completed_at ||
      !areJobMetadataEquivalent(prevJob.metadata, nextJob.metadata)
    ) {
      return false;
    }
  }

  return true;
};

const convertedSourceLabel = (result: LiteratureImportResult): string => {
  if (!result.source) {
    return '';
  }
  if (result.source.convertedArtifactId) {
    return `MD ${result.source.convertedArtifactId}`;
  }
  return result.source.provider === 'manual_upload' ? 'MD pending' : 'MD not available';
};

const libraryDocumentPath = (documentId: string): string => (
  `/weaviate/documents/${encodeURIComponent(documentId)}`
);

const formatObservedDate = (value: string): string => {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
};

const isListingAlreadyInLibrary = (listing: IdentifierSourceListing): boolean => (
  listing.existing_document_id !== null
  || listing.files.some((file) => file.recognized && file.not_tickable_reason === 'paper_already_in_library')
);

const isListingPaperNotImportable = (listing: IdentifierSourceListing): boolean => (
  (listing.main_status !== 'ready' && listing.main_status !== 'conversion_running')
  || (
    listing.files.length > 0
    && listing.files.every((file) => file.recognized && file.not_tickable_reason === 'paper_not_importable')
  )
);

const canChooseSupplementsForResult = (result: LiteratureImportResult): boolean => (
  result.status === 'resolved' || result.errorCode === 'file_not_tickable'
);

const mergeImportResults = (
  previousResults: LiteratureImportResult[],
  incomingResults: LiteratureImportResult[],
): LiteratureImportResult[] => {
  const incomingQueueByIdentifier = new Map<string, LiteratureImportResult[]>();
  for (const item of incomingResults) {
    const queue = incomingQueueByIdentifier.get(item.identifier);
    if (queue) {
      queue.push(item);
    } else {
      incomingQueueByIdentifier.set(item.identifier, [item]);
    }
  }
  const merged: LiteratureImportResult[] = [];
  const replacedIdentifiers = new Set<string>();
  for (const existing of previousResults) {
    if (existing.errorCode !== 'file_not_tickable') {
      merged.push(existing);
      continue;
    }
    const queue = incomingQueueByIdentifier.get(existing.identifier);
    if (queue && queue.length > 0) {
      merged.push(queue.shift()!);
      replacedIdentifiers.add(existing.identifier);
    } else if (!replacedIdentifiers.has(existing.identifier)) {
      merged.push(existing);
    }
  }
  for (const remaining of incomingQueueByIdentifier.values()) {
    merged.push(...remaining);
  }
  return merged;
};

const isAbortError = (error: unknown): boolean => (
  typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError'
);

interface PaperSupplementsPanelProps {
  panelId: string;
  identifier: string;
  entry: PaperFileListingEntry | undefined;
  displayProvider: string;
  selectedByFileId: Record<string, ChosenSupplementInput> | undefined;
  disabled: boolean;
  onToggleFile: (identifier: string, file: IdentifierSourceFile) => void;
  onRetry: (identifier: string) => void;
}

const PaperSupplementsPanel: React.FC<PaperSupplementsPanelProps> = ({
  panelId,
  identifier,
  entry,
  displayProvider,
  selectedByFileId,
  disabled,
  onToggleFile,
  onRetry,
}) => {
  if (!entry || entry.status === 'loading') {
    return (
      <Stack id={panelId} direction="row" spacing={1} sx={{ alignItems: 'center', py: 0.5 }}>
        <CircularProgress size={16} />
        <Typography variant="body2">Loading this paper&apos;s files...</Typography>
      </Stack>
    );
  }

  if (entry.status === 'error') {
    return (
      <Box id={panelId}>
        <Alert
          severity="warning"
          action={(
            <Button
              color="inherit"
              size="small"
              onClick={() => onRetry(identifier)}
              disabled={disabled}
            >
              {TRY_AGAIN_BUTTON_TEXT}
            </Button>
          )}
        >
          {ADD_LITERATURE_TEXT.listingFailedImportMainOnly}
        </Alert>
      </Box>
    );
  }

  if (entry.status === 'refused') {
    const refusalText = listingRefusalText(entry.code, entry.httpStatus, displayProvider);
    if (refusalText === null) {
      return null;
    }
    return (
      <Box id={panelId}>
        <Alert
          severity="warning"
          action={(
            <Button
              color="inherit"
              size="small"
              onClick={() => onRetry(identifier)}
              disabled={disabled}
            >
              {TRY_AGAIN_BUTTON_TEXT}
            </Button>
          )}
        >
          {refusalText}
        </Alert>
      </Box>
    );
  }

  const { listing } = entry;
  if (isListingAlreadyInLibrary(listing)) {
    return null;
  }

  if (isListingPaperNotImportable(listing)) {
    return (
      <Box id={panelId}>
        <Alert severity="warning">
          {NOT_TICKABLE_REASON_TEXT.paper_not_importable(displayProvider)}
        </Alert>
      </Box>
    );
  }

  const hasNonMainFiles = listing.files.some((file) => !(file.recognized && file.file_role === 'main'));
  const hasOtherFiles = hasNonMainFiles
    || listing.not_shown_count > 0
    || listing.duplicate_copies_count > 0;

  const renderListedFileRow = (file: IdentifierSourceListedFile) => {
    const fileName = file.display_name || UNNAMED_FILE_LABEL;
    const isMainFile = file.recognized && file.file_role === 'main';

    if (isMainFile) {
      return (
        <Box component="li" key={file.provider_file_id} sx={{ py: 0.5, listStyle: 'none' }}>
          <FormControlLabel
            control={<Checkbox size="small" checked disabled />}
            label={(
              <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
                {fileName}
              </Typography>
            )}
            sx={{ m: 0, alignItems: 'center' }}
          />
          <Typography variant="body2" color="text.secondary" sx={{ pl: 4 }}>
            {ADD_LITERATURE_TEXT.mainPaperAlwaysImported}
          </Typography>
        </Box>
      );
    }

    const observation = file.recognized ? file.last_provider_observation : null;
    const fewCharacters = file.recognized ? file.check.few_characters_count : null;

    if (file.recognized && file.tickable) {
      const isSelected = Boolean(selectedByFileId?.[file.provider_file_id]);
      const isUnconverted = file.status === 'no_converted_text';
      const description = isUnconverted
        ? unconvertedTickableSupplementText(displayProvider)
        : ADDABLE_TEXT(displayProvider);
      const descriptionId = isUnconverted
        ? `${panelId}-file-${file.provider_file_id}-desc`
        : undefined;

      return (
        <Box component="li" key={file.provider_file_id} sx={{ py: 0.5, listStyle: 'none' }}>
          <FormControlLabel
            control={(
              <Checkbox
                size="small"
                checked={isSelected}
                disabled={disabled}
                onChange={() => onToggleFile(identifier, file)}
                slotProps={descriptionId ? { input: { 'aria-describedby': descriptionId } } : undefined}
              />
            )}
            label={(
              <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
                {fileName}
              </Typography>
            )}
            sx={{ m: 0, alignItems: 'center' }}
          />
          <Typography id={descriptionId} variant="body2" color="text.secondary" sx={{ pl: 4 }}>
            {description}
          </Typography>
          {fewCharacters !== null ? (
            <Typography variant="body2" color="text.secondary" sx={{ pl: 4 }}>
              {fewCharactersNote(fewCharacters)}
            </Typography>
          ) : null}
          {observation ? (
            <Typography variant="body2" color="text.secondary" sx={{ pl: 4 }}>
              {OBSERVATION_TEXT[observation.code](displayProvider, formatObservedDate(observation.observed_at))}
            </Typography>
          ) : null}
        </Box>
      );
    }

    return (
      <Box component="li" key={file.provider_file_id} sx={{ py: 0.5, pl: 4, listStyle: 'none' }}>
        <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
          {fileName}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {sourceFileStatusText(file, displayProvider)}
        </Typography>
        {fewCharacters !== null ? (
          <Typography variant="body2" color="text.secondary">
            {fewCharactersNote(fewCharacters)}
          </Typography>
        ) : null}
        {observation ? (
          <Typography variant="body2" color="text.secondary">
            {OBSERVATION_TEXT[observation.code](displayProvider, formatObservedDate(observation.observed_at))}
          </Typography>
        ) : null}
      </Box>
    );
  };

  return (
    <Stack id={panelId} spacing={0.75} aria-label={`Supplements for ${identifier}`}>
      <Box component="ul" aria-label={`Files for ${identifier}`} sx={{ m: 0, p: 0 }}>
        {listing.files.map(renderListedFileRow)}
      </Box>
      {!hasOtherFiles ? (
        <Typography variant="body2" color="text.secondary" sx={{ pl: 4 }}>
          {`${displayProvider} has no other files for this paper.`}
        </Typography>
      ) : null}
      {listing.not_shown_count > 0 ? (
        <Typography variant="body2" color="text.secondary">
          {notShownText(listing.not_shown_count, displayProvider)}
        </Typography>
      ) : null}
      {listing.duplicate_copies_count > 0 ? (
        <Typography variant="body2" color="text.secondary">
          {duplicateCopiesText(listing.duplicate_copies_count)}
        </Typography>
      ) : null}
    </Stack>
  );
};

const AddLiteraturePage: React.FC = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const panelIdPrefix = React.useId();
  const [mode, setMode] = React.useState<ImportMode>('identifiers');
  const [identifiers, setIdentifiers] = React.useState('');
  const [results, setResults] = React.useState<LiteratureImportResult[]>([]);
  const [pendingAction, setPendingAction] = React.useState<PendingAction>(null);
  const [identifierFeedback, setIdentifierFeedback] = React.useState<IdentifierFeedback | null>(null);
  const [uploadStatus, setUploadStatus] = React.useState<UploadStatus>('idle');
  const [uploadMessage, setUploadMessage] = React.useState<string | null>(null);
  const [jobs, setJobs] = React.useState<PdfProcessingJob[]>([]);
  const [jobsLoading, setJobsLoading] = React.useState(false);
  const [providerPresentation, setProviderPresentation] =
    React.useState<DocumentSourceProviderMetadata | null>(null);
  const [providerPresentationError, setProviderPresentationError] =
    React.useState<string | null>(null);
  const [expandedIdentifiers, setExpandedIdentifiers] = React.useState<Record<string, boolean>>({});
  const [paperListings, setPaperListings] = React.useState<Record<string, PaperFileListingEntry>>({});
  const [selectedSupplementsByIdentifier, setSelectedSupplementsByIdentifier] = React.useState<
    Record<string, Record<string, ChosenSupplementInput>>
  >({});
  const [retryIdentifiers, setRetryIdentifiers] = React.useState<string[]>([]);
  const intakeGenerationRef = React.useRef(0);
  const supplementSessionRef = React.useRef(0);
  const listingAbortControllersRef = React.useRef<Map<string, AbortController>>(new Map());
  const listingRequestVersionRef = React.useRef<Record<string, number>>({});
  const jobsEventSourceRef = React.useRef<EventSource | null>(null);
  const jobsPollingRef = React.useRef<number | null>(null);
  const seenTerminalNotificationsRef = React.useRef<Set<string>>(new Set());
  const seededTerminalNotificationsRef = React.useRef(false);
  const isWorking = pendingAction !== null;
  const isUploading = uploadStatus === 'uploading';
  const displayProvider = providerPresentation?.displayLabel?.trim() || DEFAULT_PROVIDER_LABEL;
  const shortProvider = resolveProviderShortLabel({
    providerShortLabel: providerPresentation?.shortLabel,
    providerLabel: providerPresentation?.displayLabel,
  });
  const identifierPlaceholder = providerPresentation?.identifierExamples?.join('\n');
  const identifierHelp = providerPresentationError
    ? undefined
    : providerPresentation?.identifierHelpLabel
      ?? 'Identifier guidance is not configured for the current document source; use commas or new lines to separate values.';

  const clearSupplementState = React.useCallback(() => {
    supplementSessionRef.current += 1;
    for (const controller of listingAbortControllersRef.current.values()) {
      controller.abort();
    }
    listingAbortControllersRef.current.clear();
    listingRequestVersionRef.current = {};
    setExpandedIdentifiers({});
    setPaperListings({});
    setSelectedSupplementsByIdentifier({});
    setRetryIdentifiers([]);
  }, []);

  const retainSupplementStateForIdentifiers = React.useCallback((keep: ReadonlySet<string>) => {
    for (const [identifier, controller] of listingAbortControllersRef.current.entries()) {
      if (!keep.has(identifier)) {
        controller.abort();
        listingAbortControllersRef.current.delete(identifier);
      }
    }
    for (const identifier of Object.keys(listingRequestVersionRef.current)) {
      if (!keep.has(identifier)) {
        delete listingRequestVersionRef.current[identifier];
      }
    }
    setPaperListings((previous) => {
      const next: Record<string, PaperFileListingEntry> = {};
      for (const [identifier, entry] of Object.entries(previous)) {
        if (keep.has(identifier)) {
          next[identifier] = entry;
        }
      }
      return next;
    });
    setSelectedSupplementsByIdentifier((previous) => {
      const next: Record<string, Record<string, ChosenSupplementInput>> = {};
      for (const [identifier, byFileId] of Object.entries(previous)) {
        if (keep.has(identifier)) {
          next[identifier] = byFileId;
        }
      }
      return next;
    });
    setExpandedIdentifiers(() => {
      const next: Record<string, boolean> = {};
      for (const identifier of keep) {
        next[identifier] = true;
      }
      return next;
    });
  }, []);

  React.useEffect(() => () => {
    supplementSessionRef.current += 1;
    for (const controller of listingAbortControllersRef.current.values()) {
      controller.abort();
    }
    listingAbortControllersRef.current.clear();
  }, []);

  const loadPaperSourceFiles = React.useCallback(async (identifier: string) => {
    const supplementSession = supplementSessionRef.current;
    listingAbortControllersRef.current.get(identifier)?.abort();
    const controller = new AbortController();
    listingAbortControllersRef.current.set(identifier, controller);
    const requestVersion = (listingRequestVersionRef.current[identifier] ?? 0) + 1;
    listingRequestVersionRef.current[identifier] = requestVersion;

    setPaperListings((previous) => ({
      ...previous,
      [identifier]: { status: 'loading' },
    }));

    try {
      const result = await fetchDocumentSourceFiles(identifier, controller.signal);
      if (
        controller.signal.aborted
        || supplementSessionRef.current !== supplementSession
        || listingRequestVersionRef.current[identifier] !== requestVersion
      ) {
        return;
      }

      if (result.kind === 'refused') {
        setPaperListings((previous) => ({
          ...previous,
          [identifier]: { status: 'refused', code: result.code, httpStatus: result.httpStatus },
        }));
        if (result.code === 'document_source_import_disabled') {
          setExpandedIdentifiers((previous) => {
            if (!previous[identifier]) {
              return previous;
            }
            const next = { ...previous };
            delete next[identifier];
            return next;
          });
          setSelectedSupplementsByIdentifier((previous) => {
            if (!previous[identifier]) {
              return previous;
            }
            const next = { ...previous };
            delete next[identifier];
            return next;
          });
        }
        return;
      }

      setPaperListings((previous) => ({
        ...previous,
        [identifier]: { status: 'loaded', listing: result.listing },
      }));

      const duplicateInLibrary = isListingAlreadyInLibrary(result.listing);
      if (duplicateInLibrary || isListingPaperNotImportable(result.listing)) {
        if (duplicateInLibrary) {
          setExpandedIdentifiers((previous) => {
            if (!previous[identifier]) {
              return previous;
            }
            const next = { ...previous };
            delete next[identifier];
            return next;
          });
        }
        setSelectedSupplementsByIdentifier((previous) => {
          if (!previous[identifier]) {
            return previous;
          }
          const next = { ...previous };
          delete next[identifier];
          return next;
        });
      }
    } catch (error) {
      if (
        isAbortError(error)
        || controller.signal.aborted
        || supplementSessionRef.current !== supplementSession
        || listingRequestVersionRef.current[identifier] !== requestVersion
      ) {
        return;
      }
      setPaperListings((previous) => ({
        ...previous,
        [identifier]: { status: 'error' },
      }));
    } finally {
      if (listingAbortControllersRef.current.get(identifier) === controller) {
        listingAbortControllersRef.current.delete(identifier);
      }
    }
  }, []);

  const handleToggleChooseSupplements = React.useCallback(
    (identifier: string) => {
      const nextExpanded = !expandedIdentifiers[identifier];
      setExpandedIdentifiers((previous) => ({
        ...previous,
        [identifier]: nextExpanded,
      }));
      if (nextExpanded && !paperListings[identifier]) {
        void loadPaperSourceFiles(identifier);
      }
    },
    [expandedIdentifiers, loadPaperSourceFiles, paperListings],
  );

  const handleToggleSupplementSelection = React.useCallback(
    (identifier: string, file: IdentifierSourceFile) => {
      if (!file.tickable || file.file_role === 'main') {
        return;
      }
      setSelectedSupplementsByIdentifier((previous) => {
        const existingForIdentifier = previous[identifier] ?? {};
        if (existingForIdentifier[file.provider_file_id]) {
          const nextForIdentifier = { ...existingForIdentifier };
          delete nextForIdentifier[file.provider_file_id];
          const next = { ...previous };
          if (Object.keys(nextForIdentifier).length === 0) {
            delete next[identifier];
          } else {
            next[identifier] = nextForIdentifier;
          }
          return next;
        }
        return {
          ...previous,
          [identifier]: {
            ...existingForIdentifier,
            [file.provider_file_id]: {
              identifier,
              provider_file_id: file.provider_file_id,
              md5: file.md5,
              converted_md5: file.converted_md5,
            },
          },
        };
      });
    },
    [],
  );

  const selectedSupplements = React.useMemo<ChosenSupplementInput[]>(() => {
    const selectableIdentifiers = new Set(
      results
        .filter(canChooseSupplementsForResult)
        .map((result) => result.identifier),
    );
    const chosen: ChosenSupplementInput[] = [];
    for (const [identifier, byFileId] of Object.entries(selectedSupplementsByIdentifier)) {
      if (!selectableIdentifiers.has(identifier)) {
        continue;
      }
      const listingEntry = paperListings[identifier];
      if (
        listingEntry?.status === 'refused'
        && listingEntry.code === 'document_source_import_disabled'
      ) {
        continue;
      }
      if (
        listingEntry?.status === 'loaded'
        && (isListingAlreadyInLibrary(listingEntry.listing) || isListingPaperNotImportable(listingEntry.listing))
      ) {
        continue;
      }
      chosen.push(...Object.values(byFileId));
    }
    return chosen;
  }, [paperListings, results, selectedSupplementsByIdentifier]);

  const selectedSupplementCount = selectedSupplements.length;

  React.useEffect(() => {
    let active = true;
    void fetchDocumentSourceProviderConfiguration()
      .then((configuration) => {
        if (active) {
          setProviderPresentationError(null);
          setProviderPresentation(
            configuration.importEnabled ? configuration.presentation : null,
          );
        }
      })
      .catch((error) => {
        console.error('Failed to load document-source presentation:', error);
        if (active) {
          setProviderPresentation(null);
          setProviderPresentationError(
            'Identifier guidance could not be loaded. Try refreshing the page.',
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const notifyTerminalJobTransitions = React.useCallback((nextJobs: PdfProcessingJob[]) => {
    const seedOnly = !seededTerminalNotificationsRef.current;

    for (const job of nextJobs) {
      const notification = buildPdfTerminalNotification(job);
      if (!notification) {
        continue;
      }

      const key = notification.key;
      if (seenTerminalNotificationsRef.current.has(key)) {
        continue;
      }
      seenTerminalNotificationsRef.current.add(key);
      if (seedOnly) {
        continue;
      }

      emitGlobalToast({ message: notification.message, severity: notification.severity });
    }

    if (!seededTerminalNotificationsRef.current) {
      seededTerminalNotificationsRef.current = true;
    }
  }, []);

  const applyJobsUpdate = React.useCallback(
    (nextJobs: PdfProcessingJob[]) => {
      setJobs((previousJobs) => (areJobsEquivalent(previousJobs, nextJobs) ? previousJobs : nextJobs));
      notifyTerminalJobTransitions(nextJobs);
    },
    [notifyTerminalJobTransitions],
  );

  const refreshJobs = React.useCallback(async (silent = false) => {
    if (!silent) {
      setJobsLoading(true);
    }

    try {
      const payload = await fetchPdfJobs({
        windowDays: PDF_JOB_WINDOW_DAYS,
        limit: PDF_JOB_LIMIT,
        offset: 0,
      });
      applyJobsUpdate(payload.jobs);
    } catch (error) {
      console.error('Error fetching PDF jobs:', error);
    } finally {
      if (!silent) {
        setJobsLoading(false);
      }
    }
  }, [applyJobsUpdate]);

  React.useEffect(() => {
    const startJobsPolling = () => {
      if (jobsPollingRef.current !== null) {
        return;
      }
      jobsPollingRef.current = window.setInterval(() => {
        void refreshJobs(true);
      }, PDF_JOB_FALLBACK_POLL_INTERVAL_MS);
    };

    void refreshJobs();

    try {
      const streamQuery = new URLSearchParams({
        window_days: String(PDF_JOB_WINDOW_DAYS),
        limit: String(PDF_JOB_LIMIT),
      });
      const source = new EventSource(`/api/weaviate/pdf-jobs/stream?${streamQuery.toString()}`);
      jobsEventSourceRef.current = source;

      source.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as { jobs?: PdfProcessingJob[]; final?: boolean };
          if (payload.final) {
            return;
          }
          if (Array.isArray(payload.jobs)) {
            applyJobsUpdate(payload.jobs);
          }
        } catch (parseError) {
          console.error('Failed to parse PDF jobs stream payload:', parseError);
        }
      };

      source.onerror = () => {
        source.close();
        jobsEventSourceRef.current = null;
        startJobsPolling();
      };
    } catch (error) {
      console.error('Failed to open PDF jobs stream:', error);
      startJobsPolling();
    }

    return () => {
      if (jobsEventSourceRef.current) {
        jobsEventSourceRef.current.close();
        jobsEventSourceRef.current = null;
      }
      if (jobsPollingRef.current !== null) {
        window.clearInterval(jobsPollingRef.current);
        jobsPollingRef.current = null;
      }
    };
  }, [applyJobsUpdate, refreshJobs]);

  const handleCancelJob = React.useCallback(async (jobId: string) => {
    try {
      const response = await cancelPdfJob(jobId);
      emitGlobalToast({ message: response.message, severity: 'info' });
      await refreshJobs(true);
    } catch (error) {
      console.error('Failed to cancel PDF job:', error);
      emitGlobalToast({
        message: error instanceof Error ? error.message : 'Failed to cancel job',
        severity: 'error',
      });
    }
  }, [refreshJobs]);

  const handleResolve = React.useCallback(() => {
    const requestVersion = intakeGenerationRef.current + 1;
    intakeGenerationRef.current = requestVersion;
    clearSupplementState();
    setPendingAction('resolve');
    setIdentifierFeedback({ severity: 'info', message: 'Resolving identifiers…' });
    resolveSourceIdentifiers(identifiers)
      .then((payload) => {
        if (intakeGenerationRef.current !== requestVersion) {
          return;
        }
        const nextResults = payload.results;
        setResults(nextResults);
        const unresolvedCount = nextResults.filter((result) => result.status !== 'resolved').length;
        setIdentifierFeedback({
          severity: nextResults.length > 0 && unresolvedCount === 0 ? 'success' : 'warning',
          message: nextResults.length === 0
            ? 'No identifiers were returned. Check the identifier format and try again.'
            : `Resolved ${nextResults.length - unresolvedCount} of ${nextResults.length} identifier${nextResults.length === 1 ? '' : 's'}. Review Identifier Results below; no imports were started.`,
        });
      })
      .catch((error) => {
        if (intakeGenerationRef.current !== requestVersion) {
          return;
        }
        const message = error instanceof Error ? error.message : 'Failed to resolve source identifiers.';
        setResults([{
          identifier: identifiers,
          normalizedIdentifier: null,
          status: 'provider_unavailable',
          message,
        }]);
        setIdentifierFeedback({ severity: 'error', message });
      })
      .finally(() => {
        if (intakeGenerationRef.current !== requestVersion) {
          return;
        }
        setPendingAction(null);
      });
  }, [clearSupplementState, identifiers]);

  const handleResolveAndImport = React.useCallback(() => {
    const requestVersion = intakeGenerationRef.current + 1;
    intakeGenerationRef.current = requestVersion;
    const isRecoveryImport = retryIdentifiers.length > 0;
    const requestIdentifiers = isRecoveryImport ? retryIdentifiers.join('\n') : identifiers;
    const retryIdentifierSet = isRecoveryImport ? new Set(retryIdentifiers) : null;
    const requestSupplements = retryIdentifierSet
      ? selectedSupplements.filter((choice) => retryIdentifierSet.has(choice.identifier))
      : selectedSupplements;
    setPendingAction('import');
    setIdentifierFeedback({ severity: 'info', message: 'Resolving identifiers and starting import jobs…' });
    importSourceIdentifiers(requestIdentifiers, requestSupplements)
      .then((payload) => {
        if (intakeGenerationRef.current !== requestVersion) {
          return;
        }
        const mergedResults = isRecoveryImport
          ? mergeImportResults(results, payload.results)
          : payload.results;
        const importedCount = payload.importedCount;
        const refusedTickableIdentifiers = Array.from(
          new Set(
            mergedResults
              .filter((result) => result.errorCode === 'file_not_tickable')
              .map((result) => result.identifier),
          ),
        );
        if (refusedTickableIdentifiers.length === 0) {
          clearSupplementState();
        } else {
          retainSupplementStateForIdentifiers(new Set(refusedTickableIdentifiers));
          setRetryIdentifiers(refusedTickableIdentifiers);
        }
        setResults(mergedResults);
        setIdentifierFeedback({
          severity: importedCount > 0 ? 'success' : 'warning',
          message: importedCount > 0
            ? `Started ${importedCount} import job${importedCount === 1 ? '' : 's'}. Progress remains visible in PDF Jobs below.`
            : 'No import jobs were started. Review Identifier Results below for details.',
        });
        if (importedCount > 0) {
          void refreshJobs(true);
        }
      })
      .catch((error) => {
        if (intakeGenerationRef.current !== requestVersion) {
          return;
        }
        if (error instanceof SourceIdentifierImportError && error.code) {
          if (error.code === 'document_source_import_disabled') {
            setIdentifierFeedback({
              severity: 'error',
              message: error.message || 'Document-source import is disabled.',
            });
            return;
          }
          const refusalMessage = addFilesRefusalText(error.code, {
            provider: displayProvider,
            providerShortLabel: shortProvider,
            httpStatus: error.status,
            limitValue: error.limitValue,
          });
          setIdentifierFeedback({
            severity: 'error',
            message: refusalMessage ?? error.message,
          });
          return;
        }
        const message = error instanceof Error ? error.message : 'Failed to import source identifiers.';
        if (isRecoveryImport) {
          setIdentifierFeedback({ severity: 'error', message });
          return;
        }
        clearSupplementState();
        setResults([{
          identifier: identifiers,
          normalizedIdentifier: null,
          status: 'provider_unavailable',
          message,
        }]);
        setIdentifierFeedback({ severity: 'error', message });
      })
      .finally(() => {
        if (intakeGenerationRef.current !== requestVersion) {
          return;
        }
        setPendingAction(null);
      });
  }, [
    clearSupplementState,
    displayProvider,
    identifiers,
    refreshJobs,
    results,
    retainSupplementStateForIdentifiers,
    retryIdentifiers,
    selectedSupplements,
    shortProvider,
  ]);

  const handleReset = React.useCallback(() => {
    setIdentifiers('');
    setResults([]);
    clearSupplementState();
    setPendingAction(null);
    setIdentifierFeedback(null);
    intakeGenerationRef.current += 1;
    setUploadStatus('idle');
    setUploadMessage(null);
  }, [clearSupplementState]);

  const handleModeChange = React.useCallback((_event: React.SyntheticEvent, nextMode: ImportMode) => {
    setMode(nextMode);
    intakeGenerationRef.current += 1;
    clearSupplementState();
    setPendingAction(null);
    setIdentifierFeedback(null);
    setResults([]);
    setUploadStatus('idle');
    setUploadMessage(null);
  }, [clearSupplementState]);

  const handleViewDocument = React.useCallback(
    (documentId: string | undefined, isDuplicate: boolean) => {
      if (!documentId) {
        return;
      }
      if (isDuplicate) {
        navigate(libraryDocumentPath(documentId));
        return;
      }
      navigate('/weaviate/documents');
    },
    [navigate],
  );

  const handleIdentifierChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      setIdentifiers(event.target.value);
      setResults([]);
      clearSupplementState();
      setIdentifierFeedback(null);
      intakeGenerationRef.current += 1;
      setPendingAction(null);
    },
    [clearSupplementState],
  );

  const handlePdfFiles = React.useCallback(async (selectedFiles: File[]) => {
    const validation = validatePdfSelection(selectedFiles, { allowMultiple: true });

    if (!validation.ok) {
      setUploadStatus('error');
      setUploadMessage(validation.error ?? 'Please select PDF files only.');
      return;
    }

    const intakeGeneration = intakeGenerationRef.current + 1;
    intakeGenerationRef.current = intakeGeneration;
    setPendingAction(null);
    setUploadStatus('uploading');
    setUploadMessage(`Uploading ${validation.files.length} PDF${validation.files.length === 1 ? '' : 's'}...`);

    let succeeded = 0;
    const failures: string[] = [];
    const uploadResults: LiteratureImportResult[] = [];

    for (const file of validation.files) {
      try {
        const documentId = await uploadPdfDocument(file);
        succeeded += 1;
        uploadResults.push({
          identifier: file.name,
          normalizedIdentifier: null,
          status: 'imported',
          message: 'Queued for background processing',
          documentId,
          filename: file.name,
          source: {
            provider: 'manual_upload',
            viewerMode: 'local_pdf',
            pdfArtifactId: 'uploaded PDF',
            sourceMd5: 'pending',
          },
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Upload failed';
        failures.push(`${file.name}: ${message}`);
        uploadResults.push({
          identifier: file.name,
          normalizedIdentifier: null,
          status: 'invalid',
          message,
        });
      }
    }

    if (succeeded > 0) {
      emitGlobalToast({
        message: 'Your PDFs are processing in the background. You can safely navigate away.',
        severity: 'info',
        autoHideDurationMs: 6000,
        anchorOrigin: { vertical: 'bottom', horizontal: 'left' },
      });
      void refreshJobs(true);
    }

    if (intakeGenerationRef.current !== intakeGeneration) {
      return;
    }

    setResults(uploadResults);

    if (failures.length > 0) {
      setUploadStatus(succeeded > 0 ? 'complete' : 'error');
      setUploadMessage(`Queued ${succeeded}/${validation.files.length} PDFs. ${failures.slice(0, 2).join(' | ')}`);
      return;
    }

    setUploadStatus('complete');
    setUploadMessage(`Queued ${succeeded} PDF${succeeded === 1 ? '' : 's'} for background processing.`);
  }, [refreshJobs]);

  const handleFileSelect = React.useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      await handlePdfFiles(Array.from(event.target.files ?? []));
      event.target.value = '';
    },
    [handlePdfFiles],
  );

  const handleUploadDrop = React.useCallback(
    async (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      await handlePdfFiles(Array.from(event.dataTransfer.files ?? []));
    },
    [handlePdfFiles],
  );

  return (
    <Box sx={{ flex: '1 1 auto', minHeight: 0, overflow: 'auto', pr: { xs: 0, md: 1 } }}>
      <Stack spacing={2.5} sx={{ pb: 3 }}>
        <Paper
          variant="outlined"
          sx={{
            p: { xs: 2, md: 2.5 },
            borderRadius: 1,
            borderColor: alpha(theme.palette.primary.main, 0.22),
            background: `linear-gradient(135deg, ${alpha(theme.palette.background.paper, 0.98)}, ${alpha(theme.palette.primary.main, 0.04)})`,
          }}
        >
          <Stack spacing={2}>
            <Stack
              direction={{ xs: 'column', md: 'row' }}
              spacing={1.5}
              sx={{
                justifyContent: "space-between",
                alignItems: { xs: 'flex-start', md: 'center' }
              }}>
              <Box>
                <Typography variant="h4" component="h1" sx={{ fontWeight: 700 }}>
                  Add Literature
                </Typography>
                <Typography
                  variant="body2"
                  sx={{
                    color: "text.secondary",
                    mt: 0.5,
                    maxWidth: 720
                  }}>
                  Add PDFs directly, or resolve source identifiers and retrieve the PDF when the catalog can provide it.
                </Typography>
              </Box>
              <Chip
                icon={<ArticleIcon />}
                label="Library intake"
                color="success"
                variant="outlined"
                sx={{ borderRadius: 1 }}
              />
            </Stack>

            <Tabs
              value={mode}
              onChange={handleModeChange}
              aria-label="Literature import mode"
              variant="fullWidth"
            >
              <Tab value="identifiers" icon={<SearchIcon fontSize="small" />} iconPosition="start" label="Identifiers" />
              <Tab value="upload" icon={<CloudUploadIcon fontSize="small" />} iconPosition="start" label="Upload PDFs" />
            </Tabs>

            {mode === 'identifiers' ? (
              <>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                    Add Publication by Identifiers
                  </Typography>
                  <Typography variant="body2" sx={{
                    color: "text.secondary"
                  }}>
                    Resolve identifiers first, then import the resolved PDF-backed publications when the batch looks right.
                  </Typography>
                </Box>
                {providerPresentationError && (
                  <Alert severity="error">{providerPresentationError}</Alert>
                )}
                <TextField
                  label="Source identifiers"
                  value={identifiers}
                  onChange={handleIdentifierChange}
                  multiline
                  minRows={5}
                  fullWidth
                  placeholder={identifierPlaceholder}
                  helperText={identifierHelp}
                  slotProps={{
                    input: {
                      sx: {
                        alignItems: 'flex-start',
                        fontFamily: 'monospace',
                        fontSize: '0.92rem',
                      },
                    }
                  }}
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{
                  alignItems: { xs: 'stretch', sm: 'center' }
                }}>
                  <Button
                    variant="outlined"
                    startIcon={<SearchIcon />}
                    onClick={handleResolve}
                    disabled={isWorking || !identifiers.trim()}
                    sx={{ minWidth: 120 }}
                  >
                    Resolve
                  </Button>
                  <Button
                    variant="contained"
                    startIcon={<PlayArrowIcon />}
                    onClick={handleResolveAndImport}
                    disabled={isWorking || !identifiers.trim()}
                    sx={{ minWidth: 160 }}
                  >
                    {selectedSupplementCount > 0
                      ? importWithSupplementsButtonText(selectedSupplementCount)
                      : 'Resolve and Import'}
                  </Button>
                  <Button variant="outlined" startIcon={<RefreshIcon />} onClick={handleReset} disabled={isWorking}>
                    Reset
                  </Button>
                </Stack>
                {identifierFeedback && (
                  <Alert
                    severity={identifierFeedback.severity}
                    role="status"
                    aria-live="polite"
                    icon={isWorking ? false : undefined}
                    sx={{ borderRadius: 1 }}
                  >
                    <Stack spacing={0.75}>
                      <Typography variant="body2">{identifierFeedback.message}</Typography>
                      {isWorking && <LinearProgress aria-label="Identifier request progress" />}
                    </Stack>
                  </Alert>
                )}
                <Alert severity="info" sx={{ borderRadius: 1 }}>
                  Resolve is a dry run. Resolve and Import starts PDF-backed jobs that continue in the background and remain visible in PDF Jobs.
                </Alert>
              </>
            ) : (
              <Paper
                variant="outlined"
                sx={{
                  p: 2,
                  borderRadius: 1,
                  borderStyle: 'dashed',
                  borderColor: alpha(theme.palette.primary.main, 0.45),
                  bgcolor: alpha(theme.palette.primary.main, 0.04),
                }}
              >
                <Stack spacing={1.5}>
                  <Box>
                    <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                      Upload PDFs
                    </Typography>
                    <Typography variant="body2" sx={{
                      color: "text.secondary"
                    }}>
                      Uploaded PDFs still use the existing processing path when no source md5 match is found.
                    </Typography>
                  </Box>
                  <Paper
                    variant="outlined"
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={handleUploadDrop}
                    sx={{
                      p: { xs: 2, md: 3 },
                      borderRadius: 1,
                      borderStyle: 'dashed',
                      borderColor: alpha(theme.palette.primary.main, 0.55),
                      bgcolor: alpha(theme.palette.background.paper, 0.55),
                      textAlign: 'center',
                    }}
                  >
                    <Stack spacing={1.25} sx={{
                      alignItems: "center"
                    }}>
                      <CloudUploadIcon color="primary" />
                      <Typography variant="body2" sx={{ fontWeight: 700 }}>
                        Drop PDFs here or choose files
                      </Typography>
                      <Typography
                        variant="caption"
                        sx={{
                          color: "text.secondary",
                          maxWidth: 560
                        }}>
                        Processing continues in the background, with progress visible in PDF Jobs and final documents available in the Library.
                      </Typography>
                      <Button variant="contained" component="label" startIcon={<CloudUploadIcon />} disabled={isUploading}>
                        Choose PDFs
                        <input hidden multiple type="file" accept="application/pdf" onChange={handleFileSelect} />
                      </Button>
                    </Stack>
                  </Paper>
                  {uploadMessage && (
                    <Alert
                      severity={uploadStatus === 'error' ? 'error' : uploadStatus === 'complete' ? 'success' : 'info'}
                      sx={{ borderRadius: 1 }}
                    >
                      {uploadMessage}
                    </Alert>
                  )}
                </Stack>
              </Paper>
            )}
          </Stack>
        </Paper>

        <Paper variant="outlined" sx={{ borderRadius: 1, overflow: 'hidden' }}>
          <Box sx={{ px: 2, py: 1.5, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {mode === 'identifiers' ? 'Identifier Results' : 'Upload Results'}
              </Typography>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                PDF-backed source retrievals and upload jobs land in the Library.
              </Typography>
            </Box>
            <Chip
              label={`${results.length} result${results.length === 1 ? '' : 's'}`}
              variant="outlined"
              sx={{ borderRadius: 1 }}
            />
          </Box>
          <Divider />

          <TableContainer sx={{ maxHeight: { xs: 520, md: 620 } }}>
            <Table stickyHeader size="small" aria-label="Literature import results">
              <TableHead>
                <TableRow>
                  <TableCell>Identifier</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Document</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {results.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5}>
                      <Typography variant="body2" sx={{
                        color: "text.secondary"
                      }}>
                        No import results yet.
                      </Typography>
                    </TableCell>
                  </TableRow>
                ) : results.map((result, rowIndex) => {
                  const rowKey = `${rowIndex}-${result.identifier}-${result.status}-${result.documentId ?? result.jobId ?? result.message}`;
                  const panelId = `${panelIdPrefix}-supplements-${rowIndex}`;
                  const listingEntry = paperListings[result.identifier];
                  const listingDuplicate = listingEntry?.status === 'loaded'
                    && isListingAlreadyInLibrary(listingEntry.listing);
                  const listingImportDisabled = listingEntry?.status === 'refused'
                    && listingRefusalText(listingEntry.code, listingEntry.httpStatus, displayProvider) === null;
                  const isDuplicateRow = result.status === 'duplicate' || listingDuplicate;
                  const effectiveDocumentId = result.documentId
                    ?? (listingDuplicate ? (listingEntry.listing.existing_document_id ?? undefined) : undefined);
                  const canOpen = Boolean(effectiveDocumentId) && result.status !== 'conversion_running';
                  const tone = isDuplicateRow ? statusTone.duplicate : statusTone[result.status];
                  const canChooseSupplements = canChooseSupplementsForResult(result)
                    && !listingDuplicate
                    && !listingImportDisabled;
                  const isExpanded = canChooseSupplements && Boolean(expandedIdentifiers[result.identifier]);
                  const selectedForPaper = selectedSupplementsByIdentifier[result.identifier];
                  const selectedForPaperCount = selectedForPaper ? Object.keys(selectedForPaper).length : 0;
                  const statusMessage = result.errorCode === 'file_not_tickable'
                    ? fileNoLongerChoosableText(shortProvider)
                    : result.message;

                  return (
                    <React.Fragment key={rowKey}>
                      <TableRow hover>
                        <TableCell>
                          <Stack spacing={0.25}>
                            <Typography variant="body2" sx={{ fontFamily: 'monospace', fontWeight: 700 }}>
                              {result.identifier}
                            </Typography>
                            <Typography variant="caption" sx={{
                              color: "text.secondary"
                            }}>
                              {result.normalizedIdentifier ?? 'No normalized identifier'}
                            </Typography>
                          </Stack>
                        </TableCell>
                        <TableCell>
                          <Stack spacing={0.75}>
                            <Chip
                              icon={tone.icon}
                              label={tone.label}
                              color={tone.chipColor}
                              size="small"
                              variant={!isDuplicateRow && (result.status === 'imported' || result.status === 'resolved') ? 'filled' : 'outlined'}
                              sx={{ alignSelf: 'flex-start', borderRadius: 1 }}
                            />
                            {isDuplicateRow ? (
                              <Stack spacing={0.25}>
                                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                                  {NOT_TICKABLE_REASON_TEXT.paper_already_in_library(displayProvider)}
                                </Typography>
                                {effectiveDocumentId ? (
                                  <Link
                                    component={RouterLink}
                                    to={libraryDocumentPath(effectiveDocumentId)}
                                    variant="caption"
                                    underline="hover"
                                    sx={{ alignSelf: 'flex-start' }}
                                  >
                                    {ADD_LITERATURE_TEXT.openInLibraryToAddFiles}
                                  </Link>
                                ) : null}
                              </Stack>
                            ) : (
                              <Typography variant="caption" sx={{
                                color: "text.secondary"
                              }}>
                                {statusMessage}
                              </Typography>
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell>
                          {result.filename ? (
                            <Stack spacing={0.25}>
                              <Typography variant="body2" sx={{ fontWeight: 700 }}>
                                {result.filename}
                              </Typography>
                              <Typography variant="caption" sx={{
                                color: "text.secondary"
                              }}>
                                {result.jobId
                                  ? `Job ${result.jobId}`
                                  : isDuplicateRow
                                    ? 'Existing document'
                                    : result.status === 'resolved' && !listingImportDisabled
                                      ? 'Ready to import'
                                      : result.status === 'imported'
                                        ? result.source?.provider === 'manual_upload'
                                          ? 'Queued upload'
                                          : 'Queued document'
                                        : effectiveDocumentId
                                          ? 'Document available'
                                          : 'Document pending'}
                              </Typography>
                            </Stack>
                          ) : (
                            <Typography variant="body2" sx={{
                              color: "text.secondary"
                            }}>
                              Not queued
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          {result.source ? (
                            <Stack spacing={0.25}>
                              <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                                PDF {result.source.pdfArtifactId} / {convertedSourceLabel(result)}
                              </Typography>
                              <Typography variant="caption" sx={{
                                color: "text.secondary"
                              }}>
                                {result.source.viewerMode}
                              </Typography>
                            </Stack>
                          ) : (
                            <Typography variant="body2" sx={{
                              color: "text.secondary"
                            }}>
                              No local PDF
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell align="right">
                          {canChooseSupplements ? (
                            <Stack direction="row" spacing={1} sx={{ justifyContent: 'flex-end', alignItems: 'center' }}>
                              {selectedForPaperCount > 0 ? (
                                <Chip
                                  size="small"
                                  color="primary"
                                  variant="outlined"
                                  label={`${selectedForPaperCount} selected`}
                                />
                              ) : null}
                              <Button
                                size="small"
                                variant="outlined"
                                endIcon={isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                                aria-label={`${ADD_LITERATURE_TEXT.chooseSupplements} for ${result.identifier}`}
                                aria-expanded={isExpanded}
                                aria-controls={panelId}
                                onClick={() => handleToggleChooseSupplements(result.identifier)}
                                disabled={isWorking}
                              >
                                {ADD_LITERATURE_TEXT.chooseSupplements}
                              </Button>
                            </Stack>
                          ) : (
                            <Tooltip
                              title={
                                canOpen
                                  ? 'View in Library'
                                  : result.status === 'resolved' && !isDuplicateRow && !listingImportDisabled
                                    ? 'Ready to import'
                                    : 'No PDF-backed document available'
                              }
                            >
                              <span>
                                <IconButton
                                  size="small"
                                  color="success"
                                  disabled={!canOpen}
                                  onClick={() => handleViewDocument(effectiveDocumentId, isDuplicateRow)}
                                  aria-label={`View ${result.filename ?? result.identifier} in Library`}
                                >
                                  <FileOpenIcon fontSize="small" />
                                </IconButton>
                              </span>
                            </Tooltip>
                          )}
                        </TableCell>
                      </TableRow>
                      {isExpanded ? (
                        <TableRow>
                          <TableCell
                            colSpan={5}
                            sx={{
                              bgcolor: alpha(theme.palette.primary.main, 0.02),
                              py: 1.5,
                              px: 2,
                            }}
                          >
                            <PaperSupplementsPanel
                              panelId={panelId}
                              identifier={result.identifier}
                              entry={listingEntry}
                              displayProvider={displayProvider}
                              selectedByFileId={selectedForPaper}
                              disabled={isWorking}
                              onToggleFile={handleToggleSupplementSelection}
                              onRetry={(targetIdentifier) => {
                                void loadPaperSourceFiles(targetIdentifier);
                              }}
                            />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>

        <PdfJobsPanel
          jobs={jobs}
          loading={jobsLoading}
          onCancelJob={handleCancelJob}
          providerShortLabel={providerPresentation?.shortLabel}
          providerLabel={providerPresentation?.displayLabel}
        />
      </Stack>
    </Box>
  );
};

export default AddLiteraturePage;
