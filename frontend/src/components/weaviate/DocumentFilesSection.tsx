import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { showPaperFilesInViewer } from '@/components/pdfViewer/pdfEvents'
import {
  UNNAMED_FILE_LABEL,
  fetchViewerFiles,
  fileDisplayLabel,
  isPdfFile,
  type ViewerFile,
  type ViewerFilesPayload,
} from '@/components/pdfViewer/viewerFiles'
import {
  PDF_JOB_FALLBACK_POLL_INTERVAL_MS,
  PDF_PROGRESS_POLL_INTERVAL_MS,
} from '@/features/documents/documentIntakeConfig'
import {
  PaperSourceFilesContractError,
  fetchPaperSourceFiles,
  type PaperSourceFile,
  type PaperSourceListedFile,
} from '@/features/documents/paperSourceFiles'
import {
  parseSupplementJobProgress,
  type DocumentFileRemovalCheckResponse,
} from '@/features/documents/supplementProgress'
import {
  AddDocumentSourceFilesError,
  ApiHttpError,
  AuthenticationError,
  DocumentFileRemovalError,
  addDocumentSourceFiles,
  checkDocumentFileRemoval,
  dispatchDocumentsListInvalidated,
  fetchActivePdfJobsForDocument,
  fetchDocumentSourceProviderConfiguration,
  fetchPdfJob,
  isActivePdfJobStatus,
  removeDocumentFile,
  type DocumentSourceProvenance,
  type PdfProcessingJob,
} from '@/services/weaviate'

import SupplementFilesList from './SupplementFilesList'
import {
  LIBRARY_ADD_TEXT,
  LIBRARY_REMOVE_TEXT,
  LISTING_UNREADABLE_TEXT,
  OBSERVATION_TEXT,
  TRY_AGAIN_BUTTON_TEXT,
  addFilesButtonText,
  addFilesRefusalText,
  duplicateCopiesText,
  fewCharactersNote,
  fileRemovalHttpErrorText,
  fileRemovalRefusalText,
  listingRefusalText,
  notShownText,
  removeFileAriaLabel,
  removeFileDialogTitle,
  sourceFileStatusText,
  uploadedPaperFilesText,
  type FileRemovalErrorDisplay,
} from './paperFileReasonText'

/**
 * The paper's files in its details dialog (ALL-1460 / ALL-1747 / ALL-1749, P10b-ui).
 *
 * The paper's own files come from the viewer's files route. Supplements on papers with
 * another ready file can be removed here after a read-only pre-check and confirmation,
 * refreshing the paper's files, source listing, Library row, and any open viewer tab.
 * For a paper imported from a provider, the provider's other files for it come from the
 * per-paper listing, each with why it is not in the paper (codes worded in
 * `paperFileReasonText.ts`), and converted supplements can be ticked and added here.
 */

/** Query keys, shared with the dialog's "Refresh details". */
export const paperFilesQueryKey = (documentId: string) => ['paper-files', documentId] as const
export const paperSourceFilesQueryKey = (documentId: string) => ['paper-source-files', documentId] as const
export const paperActiveJobsQueryKey = (documentId: string) => ['paper-active-jobs', documentId] as const
export const documentSourceProviderConfigQueryKey = () =>
  ['document-source-provider-configuration'] as const

/**
 * Reopening the same paper within this time shows the stored listing without asking the
 * provider again (each listing reads the provider and can download up to 20 converted
 * texts). "Refresh details" always asks again.
 */
export const PAPER_SOURCE_FILES_STALE_MS = 5 * 60 * 1000

interface PendingAddJobEntry {
  jobId: string
  recordedAt: number
}

/**
 * Retained in-flight add job ids keyed by document id so closing the dialog mid-add and
 * reopening the same paper within `PAPER_SOURCE_FILES_STALE_MS` reconciles the completed
 * job and busts the 5-minute listing cache.
 */
const pendingAddJobIdsByDocument = new Map<string, PendingAddJobEntry>()
/**
 * Documents whose active-job poll hit 404 and whose follow-up active-jobs lookup failed.
 * Retained across dialog close/reopen within `PAPER_SOURCE_FILES_STALE_MS` so reopening
 * still runs the deferred `/files` + `/source-files` refresh once active jobs settle empty.
 */
const pending404RefreshByDocument = new Map<string, number>()
interface PendingViewerRefreshEntry {
  generation: number
  recordedAt: number
}

interface ActiveViewerRecoveryEntry {
  generation: number
  promise: Promise<ViewerFilesPayload>
}

/**
 * Per-document direct `/files` refresh sequence, abort controllers, and shared in-flight
 * recovery promises so an older post-mutation refresh finishing late can never overwrite a
 * newer removal, recovery survives dialog close/unmount, and reopening during recovery
 * reuses the same GET.
 */
let nextViewerRefreshGeneration = 0
const latestViewerRefreshGenerationByDocument = new Map<string, number>()
const activeViewerRefreshControllerByDocument = new Map<string, AbortController>()
const activeViewerRecoveryByDocument = new Map<string, ActiveViewerRecoveryEntry>()

/**
 * Documents whose post-removal `/files` fetch failed and still need to publish a
 * `refreshOnlyIfOpen` viewer refresh once a recovery `/files` fetch succeeds.
 * Bounded by `PAPER_SOURCE_FILES_STALE_MS` so inactive documents are never retained indefinitely.
 */
const pendingViewerRefreshByDocument = new Map<string, PendingViewerRefreshEntry>()

function pruneViewerRefreshGenerationIfIdle(documentId: string): void {
  if (
    !activeViewerRefreshControllerByDocument.has(documentId)
    && !activeViewerRecoveryByDocument.has(documentId)
    && !pendingViewerRefreshByDocument.has(documentId)
  ) {
    latestViewerRefreshGenerationByDocument.delete(documentId)
  }
}

function pruneStalePendingAddJobs(now = Date.now()): void {
  for (const [docId, entry] of pendingAddJobIdsByDocument.entries()) {
    if (now - entry.recordedAt > PAPER_SOURCE_FILES_STALE_MS) {
      pendingAddJobIdsByDocument.delete(docId)
    }
  }
}

function pruneStalePending404Refreshes(now = Date.now()): void {
  for (const [docId, recordedAt] of pending404RefreshByDocument.entries()) {
    if (now - recordedAt > PAPER_SOURCE_FILES_STALE_MS) {
      pending404RefreshByDocument.delete(docId)
    }
  }
}

function pruneStalePendingViewerRefreshes(now = Date.now()): void {
  for (const [docId, entry] of pendingViewerRefreshByDocument.entries()) {
    if (now - entry.recordedAt > PAPER_SOURCE_FILES_STALE_MS) {
      pendingViewerRefreshByDocument.delete(docId)
      pruneViewerRefreshGenerationIfIdle(docId)
    }
  }
}

function getRetainedAddJobId(documentId: string): string | null {
  pruneStalePendingAddJobs()
  return pendingAddJobIdsByDocument.get(documentId)?.jobId ?? null
}

function hasRetainedAddJobId(documentId: string, jobId: string): boolean {
  return pendingAddJobIdsByDocument.get(documentId)?.jobId === jobId
}

function setRetainedAddJobId(documentId: string, jobId: string): void {
  const now = Date.now()
  pruneStalePendingAddJobs(now)
  pendingAddJobIdsByDocument.set(documentId, { jobId, recordedAt: now })
}

function deleteRetainedAddJobIdIfMatches(documentId: string, jobId: string): void {
  if (pendingAddJobIdsByDocument.get(documentId)?.jobId === jobId) {
    pendingAddJobIdsByDocument.delete(documentId)
  }
}

function hasPending404Refresh(documentId: string): boolean {
  pruneStalePending404Refreshes()
  return pending404RefreshByDocument.has(documentId)
}

function markPending404Refresh(documentId: string): void {
  const now = Date.now()
  pruneStalePending404Refreshes(now)
  pending404RefreshByDocument.set(documentId, now)
}

function clearPending404Refresh(documentId: string): void {
  pending404RefreshByDocument.delete(documentId)
}

function markPendingViewerRefresh(documentId: string, generation: number): void {
  const now = Date.now()
  pruneStalePendingViewerRefreshes(now)
  pendingViewerRefreshByDocument.set(documentId, { generation, recordedAt: now })
}

function clearPendingViewerRefresh(documentId: string): void {
  pendingViewerRefreshByDocument.delete(documentId)
  pruneViewerRefreshGenerationIfIdle(documentId)
}

function consumePendingViewerRefresh(documentId: string, expectedGeneration: number): boolean {
  pruneStalePendingViewerRefreshes()
  const entry = pendingViewerRefreshByDocument.get(documentId)
  if (!entry || entry.generation !== expectedGeneration) {
    return false
  }
  if ((latestViewerRefreshGenerationByDocument.get(documentId) ?? 0) !== expectedGeneration) {
    return false
  }
  pendingViewerRefreshByDocument.delete(documentId)
  pruneViewerRefreshGenerationIfIdle(documentId)
  return true
}

/** Whether a document currently has a TTL-bounded pending viewer refresh marker (for tests). */
export function hasPendingViewerRefresh(documentId: string): boolean {
  pruneStalePendingViewerRefreshes()
  return pendingViewerRefreshByDocument.has(documentId)
}

/** Whether a document currently has a tracked viewer refresh generation entry (for tests). */
export function hasTrackedViewerRefreshGeneration(documentId: string): boolean {
  pruneStalePendingViewerRefreshes()
  return latestViewerRefreshGenerationByDocument.has(documentId)
}

/** Clear retained add job ids and deferred refresh markers (used for test isolation). */
export function clearPendingAddJobIds(): void {
  pendingAddJobIdsByDocument.clear()
  pending404RefreshByDocument.clear()
  pendingViewerRefreshByDocument.clear()
  activeViewerRecoveryByDocument.clear()
  latestViewerRefreshGenerationByDocument.clear()
  for (const controller of activeViewerRefreshControllerByDocument.values()) {
    controller.abort()
  }
  activeViewerRefreshControllerByDocument.clear()
}

/**
 * The provider's label for the Files section: only its configured label. The label is
 * required when a provider is registered (the backend refuses to start without one), so
 * a paper whose provider has no label came from a provider this server does not have; the
 * listing would refuse it (`not_imported_from_provider`), so nothing is asked of the
 * provider listing. Never the raw provider id.
 */
export function listedProviderLabel(
  source: Pick<DocumentSourceProvenance, 'provider' | 'providerMetadata'> | null,
): string | null {
  if (!source?.provider) {
    return null
  }
  return source.providerMetadata?.displayLabel ?? null
}

const ROLE_TEXT: Record<ViewerFile['role'], string> = {
  main: 'Main paper',
  supplement: 'Supplement',
}

const STATE_TEXT: Record<ViewerFile['status'], string> = {
  ready: 'Ready',
  pending: 'Being added',
  failed: 'Could not be added',
}

function formatObservedDate(value: string): string {
  return new Date(value).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object'
    && error !== null
    && 'name' in error
    && (error as { name?: unknown }).name === 'AbortError'
  )
}

function isSupplementAddJob(job: PdfProcessingJob | null): boolean {
  return job !== null && job.metadata?.job_kind === 'supplement_add'
}

type TickablePaperSourceFile = Extract<PaperSourceFile, { tickable: true }>

function isTickableOtherFile(file: PaperSourceListedFile): file is TickablePaperSourceFile {
  return file.recognized && file.tickable && !file.in_this_paper
}

interface AddRefusalBanner {
  code: string | null
  text: string
  retryable: boolean
}

interface TrackedJobState {
  job: PdfProcessingJob
  origin: 'user_add' | 'server_active'
}

export interface DocumentFilesSectionProps {
  documentId: string
  /** The provider's display label when the paper was imported from one; null for an upload. */
  providerLabel: string | null
  /** The provider's short label when configured (e.g. `providerMetadata.shortLabel`); falls back to `providerLabel`. */
  providerShortLabel?: string | null
  /** Optional override for the server's configured provider short label on uploaded papers (Card 34). */
  configuredProviderShortLabel?: string | null
  /** The paper's processing status; Library Add fails closed unless explicitly `completed`. */
  processingStatus: string | null
  open: boolean
  /** Called after an active or submitted supplement job finishes so parent document details can refresh. */
  onFilesChanged?: () => void
  /** Optional poll interval override (defaults to env-configured `PDF_PROGRESS_POLL_INTERVAL_MS`). */
  pollIntervalMs?: number
}

function canShowRemoveForFile(file: ViewerFile, allFiles: readonly ViewerFile[]): boolean {
  if (file.role !== 'supplement' || file.status === 'pending') {
    return false
  }
  if (allFiles.length <= 1) {
    return false
  }
  return allFiles.some((other) => other.file_id !== file.file_id && other.status === 'ready')
}

type RemovalDialogState =
  | null
  | {
      file: ViewerFile
      phase: 'checking'
    }
  | {
      file: ViewerFile
      phase: 'check_error'
      text: string
      retryable: boolean
    }
  | {
      file: ViewerFile
      phase: 'refused'
      code: string | null
      text: string
    }
  | {
      file: ViewerFile
      phase: 'confirm'
      check: DocumentFileRemovalCheckResponse
      submitting: boolean
      error: FileRemovalErrorDisplay | null
    }

function PaperFileRow({
  file,
  showRemove,
  removeDisabled,
  onRequestRemove,
}: {
  file: ViewerFile
  showRemove: boolean
  removeDisabled: boolean
  onRequestRemove: (file: ViewerFile) => void
}) {
  const displayName = fileDisplayLabel(file)
  return (
    <Box component="li" sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', py: 0.5 }}>
      <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
        {displayName}
      </Typography>
      <Chip size="small" variant="outlined" label={ROLE_TEXT[file.role]} />
      <Chip size="small" variant="outlined" label={isPdfFile(file) ? 'PDF' : 'Text'} />
      <Chip
        size="small"
        label={STATE_TEXT[file.status]}
        color={file.status === 'ready' ? 'success' : file.status === 'failed' ? 'error' : 'default'}
      />
      {showRemove ? (
        <Button
          size="small"
          color="error"
          variant="text"
          disabled={removeDisabled}
          aria-label={removeFileAriaLabel(displayName)}
          onClick={() => onRequestRemove(file)}
          sx={{ ml: 'auto', minWidth: 'auto', px: 1 }}
        >
          {LIBRARY_REMOVE_TEXT.buttonLabel}
        </Button>
      ) : null}
    </Box>
  )
}

function FileRemovalDialog({
  state,
  actionDisabled,
  onClose,
  onRetryPrecheck,
  onConfirm,
}: {
  state: RemovalDialogState
  actionDisabled: boolean
  onClose: () => void
  onRetryPrecheck: () => void
  onConfirm: () => void
}) {
  const displayName = state ? fileDisplayLabel(state.file) : ''
  const isSubmittingDelete = state?.phase === 'confirm' && state.submitting

  return (
    <Dialog
      open={state !== null}
      onClose={isSubmittingDelete ? undefined : onClose}
      aria-labelledby="remove-paper-file-dialog-title"
      maxWidth="xs"
      fullWidth
    >
      <DialogTitle id="remove-paper-file-dialog-title">
        {state ? removeFileDialogTitle(displayName) : ''}
      </DialogTitle>
      <DialogContent>
        {state?.phase === 'checking' ? (
          <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center', py: 1 }}>
            <CircularProgress size={16} />
            <Typography variant="body2">Checking whether this file can be removed...</Typography>
          </Stack>
        ) : state?.phase === 'check_error' ? (
          <Alert severity="warning">{state.text}</Alert>
        ) : state?.phase === 'refused' ? (
          <Alert severity="warning">{state.text}</Alert>
        ) : state?.phase === 'confirm' ? (
          <Stack spacing={1.5} sx={{ pt: 0.5 }}>
            <Typography variant="body2">{LIBRARY_REMOVE_TEXT.confirmBody}</Typography>
            {state.check.review_and_curate_would_refuse ? (
              <Alert severity="warning">{LIBRARY_REMOVE_TEXT.newestResultQuotesFileWarning}</Alert>
            ) : null}
            {state.error ? (
              <Alert severity="warning">{state.error.text}</Alert>
            ) : null}
          </Stack>
        ) : null}
      </DialogContent>
      <DialogActions>
        {state?.phase === 'checking' ? (
          <Button onClick={onClose}>{LIBRARY_REMOVE_TEXT.cancelButton}</Button>
        ) : state?.phase === 'check_error' ? (
          <>
            <Button onClick={onClose}>Close</Button>
            {state.retryable ? (
              <Button variant="contained" disabled={actionDisabled} onClick={onRetryPrecheck}>
                {TRY_AGAIN_BUTTON_TEXT}
              </Button>
            ) : null}
          </>
        ) : state?.phase === 'refused' ? (
          <Button onClick={onClose}>Close</Button>
        ) : state?.phase === 'confirm' ? (
          <>
            <Button onClick={onClose} disabled={state.submitting}>
              {LIBRARY_REMOVE_TEXT.cancelButton}
            </Button>
            <Button
              variant="contained"
              color="error"
              disabled={state.submitting || actionDisabled}
              onClick={onConfirm}
              startIcon={state.submitting ? <CircularProgress size={14} color="inherit" /> : undefined}
            >
              {state.submitting
                ? 'Removing...'
                : state.error?.retryable
                  ? TRY_AGAIN_BUTTON_TEXT
                  : LIBRARY_REMOVE_TEXT.confirmButton}
            </Button>
          </>
        ) : null}
      </DialogActions>
    </Dialog>
  )
}

function SourceFileRow({
  file,
  provider,
  canSelect,
  selected,
  selectionDisabled,
  onToggleSelect,
}: {
  file: PaperSourceListedFile
  provider: string
  canSelect: boolean
  selected: boolean
  selectionDisabled: boolean
  onToggleSelect: (providerFileId: string) => void
}) {
  const fileName = file.display_name || UNNAMED_FILE_LABEL
  const observation = file.recognized ? file.last_provider_observation : null
  const fewCharacters = file.recognized ? file.check.few_characters_count : null
  return (
    <Box component="li" sx={{ py: 0.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        {canSelect ? (
          <Checkbox
            size="small"
            checked={selected}
            disabled={selectionDisabled}
            onChange={() => onToggleSelect(file.provider_file_id)}
            slotProps={{ input: { 'aria-label': fileName } }}
            sx={{ p: 0.25 }}
          />
        ) : null}
        <Typography variant="body2" sx={{ fontWeight: 600, overflowWrap: 'anywhere' }}>
          {fileName}
        </Typography>
        {file.recognized && file.file_role === 'main' ? (
          <Chip size="small" variant="outlined" label={ROLE_TEXT.main} />
        ) : null}
      </Box>
      <Typography variant="body2" color="text.secondary">{sourceFileStatusText(file, provider)}</Typography>
      {fewCharacters !== null ? (
        <Typography variant="body2" color="text.secondary">{fewCharactersNote(fewCharacters)}</Typography>
      ) : null}
      {observation ? (
        <Typography variant="body2" color="text.secondary">
          {OBSERVATION_TEXT[observation.code](provider, formatObservedDate(observation.observed_at))}
        </Typography>
      ) : null}
    </Box>
  )
}

function NonProviderActiveJobsPart({
  documentId,
  open,
  pollIntervalMs,
  onBusyChange,
}: {
  documentId: string
  open: boolean
  pollIntervalMs: number
  onBusyChange: (busy: boolean) => void
}) {
  const activeJobsQuery = useQuery({
    queryKey: paperActiveJobsQueryKey(documentId),
    queryFn: ({ signal }) => fetchActivePdfJobsForDocument(documentId, { signal }),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      if (!open) {
        return false
      }
      const jobs = query.state.data
      const hasActive =
        query.state.status === 'success'
        && Array.isArray(jobs)
        && jobs.some((job) => isActivePdfJobStatus(job.status))
      return hasActive ? pollIntervalMs : false
    },
  })

  const activeJob = useMemo(
    () => (
      activeJobsQuery.isError
        ? null
        : (activeJobsQuery.data?.find((job) => isActivePdfJobStatus(job.status)) ?? null)
    ),
    [activeJobsQuery.data, activeJobsQuery.isError],
  )

  const activeJobsSettled =
    activeJobsQuery.isSuccess && activeJobsQuery.fetchStatus === 'idle'
  const busy = !open || !activeJobsSettled || activeJob !== null

  useEffect(() => {
    onBusyChange(busy)
  }, [busy, onBusyChange])

  useEffect(() => () => {
    onBusyChange(true)
  }, [onBusyChange])

  if (activeJob !== null) {
    return <Alert severity="info">{LIBRARY_ADD_TEXT.importStillRunning}</Alert>
  }
  if (activeJobsQuery.isError) {
    return (
      <Alert
        severity="warning"
        action={(
          <Button size="small" color="inherit" onClick={() => void activeJobsQuery.refetch()}>
            {TRY_AGAIN_BUTTON_TEXT}
          </Button>
        )}
      >
        Could not check whether this paper has an active import.
      </Alert>
    )
  }
  return null
}

function UploadedPaperFilesNotice({
  configuredProviderShortLabel,
  open,
}: {
  configuredProviderShortLabel?: string | null
  open: boolean
}) {
  const providerConfigQuery = useQuery({
    queryKey: documentSourceProviderConfigQueryKey(),
    queryFn: ({ signal }) => fetchDocumentSourceProviderConfiguration({ signal }),
    enabled: open && configuredProviderShortLabel === undefined,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: PAPER_SOURCE_FILES_STALE_MS,
  })

  if (configuredProviderShortLabel !== undefined) {
    return configuredProviderShortLabel ? (
      <Typography variant="body2" color="text.secondary">
        {uploadedPaperFilesText(configuredProviderShortLabel)}
      </Typography>
    ) : null
  }

  if (!providerConfigQuery.data?.importEnabled) {
    return null
  }
  const shortLabel =
    providerConfigQuery.data.presentation?.shortLabel
    ?? providerConfigQuery.data.presentation?.displayLabel
  if (!shortLabel) {
    return null
  }
  return (
    <Typography variant="body2" color="text.secondary">
      {uploadedPaperFilesText(shortLabel)}
    </Typography>
  )
}

function ProviderFilesPart({
  documentId,
  provider,
  providerShortLabel,
  processingStatus,
  open,
  onFilesChanged,
  pollIntervalMs,
  isRemovalBusy,
  onAddBusyChange,
  refreshPaperSurfaces,
  removalResetSeq,
}: {
  documentId: string
  provider: string
  providerShortLabel?: string | null
  processingStatus: string | null
  open: boolean
  onFilesChanged?: () => void
  pollIntervalMs: number
  isRemovalBusy: boolean
  onAddBusyChange: (busy: boolean) => void
  refreshPaperSurfaces: (
    targetDocumentId: string,
    options?: { invalidateFilesOnFailure?: boolean },
  ) => Promise<void>
  removalResetSeq: number
}) {
  const queryClient = useQueryClient()
  const shortProvider = providerShortLabel?.trim() || provider
  const isPaperCompleted = processingStatus?.toLowerCase() === 'completed'

  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set())
  const [submitting, setSubmitting] = useState(false)
  const [addRefusal, setAddRefusal] = useState<AddRefusalBanner | null>(null)
  const [importDisabled, setImportDisabled] = useState(false)
  const [trackedJobState, setTrackedJobState] = useState<TrackedJobState | null>(null)
  const [reconcilingRetainedJob, setReconcilingRetainedJob] = useState(
    () => open && getRetainedAddJobId(documentId) !== null,
  )
  const [retainedReconcileFailed, setRetainedReconcileFailed] = useState(false)
  const [retainedRetryCount, setRetainedRetryCount] = useState(0)
  const [pending404Refresh, setPending404Refresh] = useState(
    () => open && hasPending404Refresh(documentId),
  )
  const [refreshingAfterAdd, setRefreshingAfterAdd] = useState(false)
  const [pollError, setPollError] = useState<string | null>(null)
  const [pollRetryCount, setPollRetryCount] = useState(0)

  const mountedRef = useRef(true)
  const activeDocumentIdRef = useRef(documentId)
  activeDocumentIdRef.current = documentId
  const onFilesChangedRef = useRef(onFilesChanged)
  onFilesChangedRef.current = onFilesChanged
  const submitAbortControllerRef = useRef<AbortController | null>(null)
  const lastRemovalResetSeqRef = useRef(removalResetSeq)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      submitAbortControllerRef.current?.abort()
      submitAbortControllerRef.current = null
    }
  }, [])

  // Reset transient selection, progress, and refusal state when switching papers or closing the dialog.
  useEffect(() => {
    submitAbortControllerRef.current?.abort()
    submitAbortControllerRef.current = null
    setSelectedIds(new Set())
    setSubmitting(false)
    setAddRefusal(null)
    setImportDisabled(false)
    setTrackedJobState(null)
    setRetainedReconcileFailed(false)
    setPending404Refresh(open && hasPending404Refresh(documentId))
    setRefreshingAfterAdd(false)
    setPollError(null)
    setPollRetryCount(0)
  }, [documentId, open])

  // Clear a stale terminal add summary after a subsequent file removal refreshes this paper,
  // without clobbering an actively running job or falsely clearing on mount/paper switch.
  useEffect(() => {
    if (lastRemovalResetSeqRef.current === removalResetSeq) {
      return
    }
    lastRemovalResetSeqRef.current = removalResetSeq
    setTrackedJobState((current) => {
      if (!current || isActivePdfJobStatus(current.job.status)) {
        return current
      }
      return null
    })
  }, [removalResetSeq])

  const listingQuery = useQuery({
    queryKey: paperSourceFilesQueryKey(documentId),
    queryFn: ({ signal }) => fetchPaperSourceFiles(documentId, signal),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: PAPER_SOURCE_FILES_STALE_MS,
  })

  const activeJobsQuery = useQuery({
    queryKey: paperActiveJobsQueryKey(documentId),
    queryFn: ({ signal }) => fetchActivePdfJobsForDocument(documentId, { signal }),
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const refetchActiveJobsRef = useRef(activeJobsQuery.refetch)
  refetchActiveJobsRef.current = activeJobsQuery.refetch

  const serverActiveJob = useMemo(
    () => (
      activeJobsQuery.isError
        ? null
        : (activeJobsQuery.data?.find((job) => isActivePdfJobStatus(job.status)) ?? null)
    ),
    [activeJobsQuery.data, activeJobsQuery.isError],
  )

  const handleTrackedJobFinished = useCallback(async (targetDocumentId: string) => {
    clearPending404Refresh(targetDocumentId)
    if (mountedRef.current && activeDocumentIdRef.current === targetDocumentId) {
      setPending404Refresh(false)
      setRefreshingAfterAdd(true)
    }

    try {
      await refreshPaperSurfaces(targetDocumentId)
    } finally {
      if (mountedRef.current && activeDocumentIdRef.current === targetDocumentId) {
        setRefreshingAfterAdd(false)
        setReconcilingRetainedJob(false)
        setRetainedReconcileFailed(false)
        setPollError(null)
        setAddRefusal((prev) => (prev?.code === 'import_in_progress' ? null : prev))
        dispatchDocumentsListInvalidated(onFilesChangedRef.current)
      } else {
        dispatchDocumentsListInvalidated()
      }
    }
  }, [refreshPaperSurfaces])

  const handleTrackedJobFinishedRef = useRef(handleTrackedJobFinished)
  handleTrackedJobFinishedRef.current = handleTrackedJobFinished

  // Reconcile a retained add job started before the dialog closed so reopening within the
  // 5-minute listing cache window never re-offers files that already finished adding.
  useEffect(() => {
    if (!open) {
      setReconcilingRetainedJob(false)
      setRetainedReconcileFailed(false)
      return
    }
    const retainedJobId = getRetainedAddJobId(documentId)
    if (!retainedJobId) {
      setReconcilingRetainedJob(false)
      setRetainedReconcileFailed(false)
      return
    }
    let cancelled = false
    const controller = new AbortController()
    const targetDocumentId = documentId
    setReconcilingRetainedJob(true)
    setRetainedReconcileFailed(false)

    void (async () => {
      try {
        const latest = await fetchPdfJob(retainedJobId, { signal: controller.signal })
        if (
          cancelled
          || controller.signal.aborted
          || !hasRetainedAddJobId(targetDocumentId, retainedJobId)
        ) {
          return
        }
        if (latest.document_id !== targetDocumentId) {
          deleteRetainedAddJobIdIfMatches(targetDocumentId, retainedJobId)
          setRetainedReconcileFailed(false)
          return
        }
        setAddRefusal((prev) => (prev?.code === 'import_in_progress' ? null : prev))
        setPollError(null)
        setRetainedReconcileFailed(false)
        setTrackedJobState({ job: latest, origin: 'user_add' })
        if (!isActivePdfJobStatus(latest.status)) {
          deleteRetainedAddJobIdIfMatches(targetDocumentId, retainedJobId)
          await handleTrackedJobFinishedRef.current(targetDocumentId)
        }
      } catch (error) {
        if (
          cancelled
          || controller.signal.aborted
          || isAbortError(error)
          || !hasRetainedAddJobId(targetDocumentId, retainedJobId)
        ) {
          return
        }
        if (error instanceof ApiHttpError && error.httpStatus === 404) {
          deleteRetainedAddJobIdIfMatches(targetDocumentId, retainedJobId)
          setRetainedReconcileFailed(false)
          await handleTrackedJobFinishedRef.current(targetDocumentId)
        } else {
          // Fail closed while retained job status is unknown so stale 5-minute cached files are never re-offered.
          setRetainedReconcileFailed(true)
          setPollError('Could not check progress for adding files.')
        }
      } finally {
        if (!cancelled && !controller.signal.aborted) {
          setReconcilingRetainedJob(false)
        }
      }
    })()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [open, documentId, retainedRetryCount])

  // Adopt an existing server-side active job (e.g. waiting_for_supplements or an add started earlier).
  useEffect(() => {
    if (!open || !serverActiveJob) {
      return
    }
    setRetainedReconcileFailed(false)
    setPollError(null)
    setAddRefusal((prev) => (prev?.code === 'import_in_progress' ? null : prev))
    setTrackedJobState((current) => {
      if (current && current.job.job_id === serverActiveJob.job_id) {
        return current
      }
      if (current && isActivePdfJobStatus(current.job.status)) {
        return current
      }
      return {
        job: serverActiveJob,
        origin: isSupplementAddJob(serverActiveJob) ? 'user_add' : 'server_active',
      }
    })
  }, [open, serverActiveJob])

  const trackedJob = trackedJobState?.job ?? null
  const trackedJobOrigin = trackedJobState?.origin ?? null
  const trackedJobId = trackedJob?.job_id ?? null
  const isTrackedJobActive = trackedJob !== null && isActivePdfJobStatus(trackedJob.status)

  useEffect(() => {
    if (!open || !trackedJobId || !isTrackedJobActive) {
      return
    }
    let cancelled = false
    let timerId: number | null = null
    const controller = new AbortController()
    const polledDocumentId = documentId

    const pollOnce = async () => {
      try {
        const latest = await fetchPdfJob(trackedJobId, { signal: controller.signal })
        if (cancelled || controller.signal.aborted) {
          return
        }
        if (latest.document_id !== polledDocumentId) {
          deleteRetainedAddJobIdIfMatches(polledDocumentId, trackedJobId)
          return
        }
        setTrackedJobState((current) =>
          current && current.job.job_id === latest.job_id
            ? { ...current, job: latest }
            : current,
        )
        setPollError(null)
        if (!isActivePdfJobStatus(latest.status)) {
          deleteRetainedAddJobIdIfMatches(polledDocumentId, trackedJobId)
          await handleTrackedJobFinishedRef.current(polledDocumentId)
          return
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted || isAbortError(error)) {
          return
        }
        if (error instanceof ApiHttpError && error.httpStatus === 404) {
          // Persist before awaiting active-jobs refetch so closing the dialog mid-refetch
          // still triggers a full refresh when reopening sees no active job.
          markPending404Refresh(polledDocumentId)
          const refetched = await refetchActiveJobsRef.current({ throwOnError: false })
          if (cancelled || controller.signal.aborted) {
            return
          }
          if (refetched.isError) {
            // Active-jobs refetch failed after 404: fail closed via activeJobsQuery.isError
            // and trigger a full refresh if a subsequent "Try again" or reopen confirms no active job remains.
            setPending404Refresh(true)
            setTrackedJobState(null)
            setPollError(null)
            return
          }
          deleteRetainedAddJobIdIfMatches(polledDocumentId, trackedJobId)
          const remainingActive =
            refetched.data?.find((job) => isActivePdfJobStatus(job.status)) ?? null
          if (remainingActive && remainingActive.job_id !== trackedJobId) {
            clearPending404Refresh(polledDocumentId)
            setPending404Refresh(false)
            setTrackedJobState({
              job: remainingActive,
              origin: isSupplementAddJob(remainingActive) ? 'user_add' : 'server_active',
            })
            setPollError(null)
          } else {
            setTrackedJobState(null)
            setPollError(null)
            await handleTrackedJobFinishedRef.current(polledDocumentId)
          }
          return
        }
        setPollError('Could not check progress for adding files.')
        return
      }
      if (!cancelled) {
        timerId = window.setTimeout(() => {
          void pollOnce()
        }, pollIntervalMs)
      }
    }

    timerId = window.setTimeout(() => {
      void pollOnce()
    }, pollIntervalMs)

    return () => {
      cancelled = true
      controller.abort()
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
    }
  }, [open, documentId, trackedJobId, isTrackedJobActive, pollIntervalMs, pollRetryCount])

  const listingResult = listingQuery.data
  const others = useMemo(
    () => (listingResult?.kind === 'listing' ? listingResult.listing.files.filter((file) => !file.in_this_paper) : []),
    [listingResult],
  )
  const tickableOthers = useMemo(
    () => others.filter(isTickableOtherFile),
    [others],
  )

  const selectedFiles = useMemo(() => {
    const seen = new Set<string>()
    const chosen: TickablePaperSourceFile[] = []
    for (const file of tickableOthers) {
      if (selectedIds.has(file.provider_file_id) && !seen.has(file.provider_file_id)) {
        seen.add(file.provider_file_id)
        chosen.push(file)
      }
    }
    return chosen
  }, [selectedIds, tickableOthers])

  const isAddTrackedJob =
    trackedJob !== null
    && (trackedJobOrigin === 'user_add' || isSupplementAddJob(trackedJob))
  const serverJobStillActive =
    serverActiveJob !== null
    && !(
      trackedJob !== null
      && trackedJob.job_id === serverActiveJob.job_id
      && !isTrackedJobActive
    )
  const hasActiveJobForPaper = serverJobStillActive || isTrackedJobActive
  const showImportRunningNotice =
    (serverJobStillActive && !isSupplementAddJob(serverActiveJob))
    || (trackedJob !== null && !isAddTrackedJob && isTrackedJobActive)
  const activeJobsSettled =
    activeJobsQuery.isSuccess && activeJobsQuery.fetchStatus === 'idle'
  const paperBusyForRemoval =
    !open
    || !activeJobsSettled
    || hasActiveJobForPaper
    || submitting
    || reconcilingRetainedJob
    || retainedReconcileFailed
    || pending404Refresh
    || refreshingAfterAdd

  useEffect(() => {
    onAddBusyChange(paperBusyForRemoval)
  }, [paperBusyForRemoval, onAddBusyChange])

  useEffect(() => () => {
    onAddBusyChange(true)
  }, [onAddBusyChange])

  const showTickboxes =
    isPaperCompleted
    && !hasActiveJobForPaper
    && activeJobsSettled
    && !listingQuery.isFetching
    && !reconcilingRetainedJob
    && !retainedReconcileFailed
    && !pending404Refresh
    && !refreshingAfterAdd

  // Complete a deferred 404 reconciliation once a subsequent active-jobs retry or reopen succeeds empty.
  useEffect(() => {
    if (!open || !pending404Refresh || !activeJobsSettled) {
      return
    }
    clearPending404Refresh(documentId)
    setPending404Refresh(false)
    if (!hasActiveJobForPaper) {
      pendingAddJobIdsByDocument.delete(documentId)
      void handleTrackedJobFinishedRef.current(documentId)
    }
  }, [open, pending404Refresh, activeJobsSettled, hasActiveJobForPaper, documentId])

  // Clear N35 (409 import_in_progress) whenever an authoritative active-jobs lookup settles empty
  // (immediately after 409 or after a 500 -> "Try again" retry), so N35 never sits beside enabled tickboxes.
  useEffect(() => {
    if (
      open
      && activeJobsSettled
      && !hasActiveJobForPaper
      && !submitting
      && addRefusal?.code === 'import_in_progress'
    ) {
      setAddRefusal(null)
    }
  }, [open, activeJobsSettled, hasActiveJobForPaper, submitting, addRefusal])

  const handleToggleSelect = useCallback((providerFileId: string) => {
    setAddRefusal(null)
    setSelectedIds((previous) => {
      const next = new Set(previous)
      if (next.has(providerFileId)) {
        next.delete(providerFileId)
      } else {
        next.add(providerFileId)
      }
      return next
    })
  }, [])

  const handleAddSelected = useCallback(async () => {
    if (
      !isPaperCompleted
      || selectedFiles.length === 0
      || submitting
      || isRemovalBusy
      || hasActiveJobForPaper
      || !activeJobsSettled
      || listingQuery.isFetching
      || reconcilingRetainedJob
      || retainedReconcileFailed
      || pending404Refresh
      || refreshingAfterAdd
    ) {
      return
    }
    submitAbortControllerRef.current?.abort()
    const controller = new AbortController()
    submitAbortControllerRef.current = controller

    setSubmitting(true)
    setAddRefusal(null)
    setPollError(null)

    try {
      const acceptedJob = await addDocumentSourceFiles(
        documentId,
        {
          files: selectedFiles.map(({ provider_file_id, md5, converted_md5 }) => ({
            provider_file_id,
            md5,
            converted_md5,
          })),
        },
        { signal: controller.signal },
      )
      if (controller.signal.aborted || !mountedRef.current || activeDocumentIdRef.current !== documentId) {
        return
      }
      setSelectedIds(new Set())
      setTrackedJobState({ job: acceptedJob, origin: 'user_add' })
      setSubmitting(false)
      if (isActivePdfJobStatus(acceptedJob.status)) {
        setRetainedAddJobId(documentId, acceptedJob.job_id)
      } else {
        pendingAddJobIdsByDocument.delete(documentId)
        await handleTrackedJobFinishedRef.current(documentId)
      }
    } catch (error) {
      if (
        controller.signal.aborted
        || !mountedRef.current
        || activeDocumentIdRef.current !== documentId
        || isAbortError(error)
      ) {
        return
      }
      if (error instanceof AuthenticationError) {
        setSubmitting(false)
        const signInText =
          listingRefusalText('document_source_curator_token_unavailable', 409, provider)
          ?? 'Sign in again to add files to this paper.'
        setAddRefusal({ code: 'authentication_required', text: signInText, retryable: false })
        return
      }
      if (error instanceof AddDocumentSourceFilesError) {
        if (error.code === 'document_source_import_disabled') {
          setSubmitting(false)
          setImportDisabled(true)
          void queryClient.invalidateQueries({ queryKey: paperSourceFilesQueryKey(documentId) })
          return
        }
        if (error.code !== null) {
          const text = addFilesRefusalText(error.code, {
            provider,
            providerShortLabel: shortProvider,
            httpStatus: error.httpStatus,
            limitValue: error.limitValue,
          })
          if (error.code === 'import_in_progress') {
            if (text !== null) {
              setAddRefusal({ code: error.code, text, retryable: false })
            }
            // Keep submitting=true across the follow-up active-jobs lookup so the clearing effect
            // only runs against the settled follow-up result (empty clears N35; 500 keeps N35 until Try again).
            await refetchActiveJobsRef.current({ throwOnError: false })
            if (
              !controller.signal.aborted
              && mountedRef.current
              && activeDocumentIdRef.current === documentId
            ) {
              setSubmitting(false)
            }
            return
          }
          setSubmitting(false)
          if (text !== null) {
            const retryable =
              error.code === 'file_removal_unfinished'
              || (error.code === 'document_source_curator_token_unavailable' && error.httpStatus === 503)
            setAddRefusal({ code: error.code, text, retryable })
          }
          return
        }
      }
      setSubmitting(false)
      setAddRefusal({ code: null, text: 'Could not add files to this paper.', retryable: true })
    } finally {
      if (submitAbortControllerRef.current === controller) {
        submitAbortControllerRef.current = null
      }
    }
  }, [
    activeJobsSettled,
    documentId,
    hasActiveJobForPaper,
    isPaperCompleted,
    isRemovalBusy,
    listingQuery.isFetching,
    pending404Refresh,
    provider,
    queryClient,
    reconcilingRetainedJob,
    refreshingAfterAdd,
    retainedReconcileFailed,
    selectedFiles,
    shortProvider,
    submitting,
  ])

  const handleRetryPoll = useCallback(() => {
    setPollError(null)
    setPollRetryCount((count) => count + 1)
  }, [])

  const handleRetryRetainedOrActiveJobs = useCallback(() => {
    setPollError(null)
    setRetainedReconcileFailed(false)
    setRetainedRetryCount((count) => count + 1)
    void activeJobsQuery.refetch()
  }, [activeJobsQuery])

  const activeJobsErrorAlert =
    activeJobsQuery.isError && !hasActiveJobForPaper && !pollError ? (
      <Alert
        severity="warning"
        action={(
          <Button size="small" color="inherit" onClick={() => void activeJobsQuery.refetch()}>
            {TRY_AGAIN_BUTTON_TEXT}
          </Button>
        )}
      >
        Could not check whether this paper has an active import.
      </Alert>
    ) : null

  if (importDisabled) {
    return activeJobsErrorAlert
  }

  if (listingQuery.isPending) {
    return (
      <Stack spacing={1.5}>
        {activeJobsErrorAlert}
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
          <CircularProgress size={14} />
          <Typography variant="body2">{`Checking ${provider} for this paper's files...`}</Typography>
        </Stack>
      </Stack>
    )
  }
  if (listingQuery.isError) {
    const isContractError = listingQuery.error instanceof PaperSourceFilesContractError
    return (
      <Stack spacing={1.5}>
        {activeJobsErrorAlert}
        <Alert
          severity="warning"
          action={
            isContractError ? undefined : (
              <Button size="small" color="inherit" onClick={() => void listingQuery.refetch()}>
                {TRY_AGAIN_BUTTON_TEXT}
              </Button>
            )
          }
        >
          {isContractError
            ? LISTING_UNREADABLE_TEXT(provider)
            : `Could not check ${provider} for this paper's files.`}
        </Alert>
      </Stack>
    )
  }

  const result = listingQuery.data
  if (result.kind === 'refused') {
    const text = result.code === 'not_imported_from_provider'
      ? uploadedPaperFilesText(shortProvider)
      : listingRefusalText(result.code, result.httpStatus, provider)
    if (!text && !activeJobsErrorAlert) {
      return null
    }
    return (
      <Stack spacing={1.5}>
        {activeJobsErrorAlert}
        {text ? <Alert severity="info">{text}</Alert> : null}
      </Stack>
    )
  }

  const trackedProgress = trackedJob
    ? parseSupplementJobProgress(trackedJob.metadata, { currentStage: trackedJob.current_stage })
    : null
  const showRefusalAlert =
    addRefusal !== null
    && !(showImportRunningNotice && addRefusal.code === 'import_in_progress')
  const canRetryAddRefusal = Boolean(
    addRefusal?.retryable && selectedFiles.length > 0 && showTickboxes && !submitting && !isRemovalBusy,
  )
  const terminalAddAlertText =
    isAddTrackedJob && !isTrackedJobActive && (trackedJob.status === 'failed' || trackedJob.status === 'cancelled')
      ? trackedJob.error_message || trackedJob.message || (trackedJob.status === 'cancelled' ? 'Cancelled' : 'Adding files failed.')
      : null

  return (
    <Stack spacing={1.5}>
      {showImportRunningNotice ? (
        <Alert severity="info">{LIBRARY_ADD_TEXT.importStillRunning}</Alert>
      ) : null}

      {activeJobsErrorAlert}

      {trackedJob ? (
        isAddTrackedJob ? (
          <Stack spacing={1} aria-live="polite">
            <Typography variant="subtitle2">
              {LIBRARY_ADD_TEXT.addingFilesHeading}
            </Typography>
            {isTrackedJobActive ? (
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <CircularProgress size={14} />
                <Typography variant="body2" color="text.secondary">
                  {trackedJob.message || 'Adding files...'}
                </Typography>
              </Stack>
            ) : terminalAddAlertText ? (
              <Alert severity={trackedJob.status === 'failed' ? 'warning' : 'info'}>
                {terminalAddAlertText}
              </Alert>
            ) : trackedJob.message ? (
              <Typography variant="body2" color="text.secondary">
                {trackedJob.message}
              </Typography>
            ) : null}
            <SupplementFilesList
              progress={trackedProgress}
              currentStage={trackedJob.current_stage}
              providerShortLabel={shortProvider}
              providerLabel={provider}
            />
            {pollError ? (
              <Alert
                severity="warning"
                action={(
                  <Button size="small" color="inherit" onClick={handleRetryPoll}>
                    {TRY_AGAIN_BUTTON_TEXT}
                  </Button>
                )}
              >
                {pollError}
              </Alert>
            ) : null}
          </Stack>
        ) : (trackedProgress || pollError) ? (
          <Stack spacing={1}>
            <SupplementFilesList
              progress={trackedProgress}
              currentStage={trackedJob.current_stage}
              providerShortLabel={shortProvider}
              providerLabel={provider}
            />
            {pollError ? (
              <Alert
                severity="warning"
                action={(
                  <Button size="small" color="inherit" onClick={handleRetryPoll}>
                    {TRY_AGAIN_BUTTON_TEXT}
                  </Button>
                )}
              >
                {pollError}
              </Alert>
            ) : null}
          </Stack>
        ) : null
      ) : pollError ? (
        <Alert
          severity="warning"
          action={(
            <Button size="small" color="inherit" onClick={handleRetryRetainedOrActiveJobs}>
              {TRY_AGAIN_BUTTON_TEXT}
            </Button>
          )}
        >
          {pollError}
        </Alert>
      ) : null}

      {showRefusalAlert ? (
        <Alert
          severity="warning"
          action={
            canRetryAddRefusal ? (
              <Button size="small" color="inherit" onClick={() => void handleAddSelected()}>
                {TRY_AGAIN_BUTTON_TEXT}
              </Button>
            ) : undefined
          }
        >
          {addRefusal.text}
        </Alert>
      ) : null}

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          {`Also in ${provider}`}
        </Typography>
        {others.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {`${provider} has no other files for this paper.`}
          </Typography>
        ) : (
          <Box
            component="ul"
            aria-label={`Files in ${provider} not in this paper`}
            sx={{ listStyle: 'none', m: 0, pl: 0 }}
          >
            {others.map((file) => {
              const canSelect = showTickboxes && isTickableOtherFile(file)
              return (
                <SourceFileRow
                  key={file.provider_file_id}
                  file={file}
                  provider={provider}
                  canSelect={canSelect}
                  selected={canSelect && selectedIds.has(file.provider_file_id)}
                  selectionDisabled={submitting || isRemovalBusy}
                  onToggleSelect={handleToggleSelect}
                />
              )
            })}
          </Box>
        )}
        {showTickboxes && tickableOthers.length > 0 ? (
          <Box sx={{ mt: 1.5 }}>
            <Button
              variant="contained"
              size="small"
              disabled={selectedFiles.length === 0 || submitting || isRemovalBusy}
              onClick={() => void handleAddSelected()}
            >
              {addFilesButtonText(selectedFiles.length)}
            </Button>
          </Box>
        ) : null}
        {result.listing.not_shown_count > 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {notShownText(result.listing.not_shown_count, provider)}
          </Typography>
        ) : null}
        {result.listing.duplicate_copies_count > 0 ? (
          <Typography variant="body2" color="text.secondary">
            {duplicateCopiesText(result.listing.duplicate_copies_count)}
          </Typography>
        ) : null}
      </Box>
    </Stack>
  )
}

export default function DocumentFilesSection({
  documentId,
  providerLabel,
  providerShortLabel,
  configuredProviderShortLabel,
  processingStatus,
  open,
  onFilesChanged,
  pollIntervalMs,
}: DocumentFilesSectionProps) {
  const effectivePollIntervalMs = pollIntervalMs ?? PDF_PROGRESS_POLL_INTERVAL_MS
  const nonProviderPollIntervalMs = pollIntervalMs ?? PDF_JOB_FALLBACK_POLL_INTERVAL_MS
  const queryClient = useQueryClient()
  const isPaperCompleted = processingStatus?.toLowerCase() === 'completed'

  const [providerPaperBusy, setProviderPaperBusy] = useState(true)
  const [nonProviderPaperBusy, setNonProviderPaperBusy] = useState(true)
  const [removalDialog, setRemovalDialog] = useState<RemovalDialogState>(null)
  const [refreshingAfterRemoval, setRefreshingAfterRemoval] = useState(false)
  const [removalResetSeq, setRemovalResetSeq] = useState(0)

  const mountedRef = useRef(true)
  const activeDocumentIdRef = useRef(documentId)
  activeDocumentIdRef.current = documentId
  const onFilesChangedRef = useRef(onFilesChanged)
  onFilesChangedRef.current = onFilesChanged
  const precheckAbortControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      precheckAbortControllerRef.current?.abort()
      precheckAbortControllerRef.current = null
    }
  }, [])

  useEffect(() => {
    precheckAbortControllerRef.current?.abort()
    precheckAbortControllerRef.current = null
    setRemovalDialog(null)
    setRefreshingAfterRemoval(false)
  }, [documentId, open])

  const filesQuery = useQuery({
    queryKey: paperFilesQueryKey(documentId),
    queryFn: async ({ signal }) => {
      const activeRecovery = activeViewerRecoveryByDocument.get(documentId)
      if (
        activeRecovery
        && latestViewerRefreshGenerationByDocument.get(documentId) === activeRecovery.generation
      ) {
        return await activeRecovery.promise
      }
      const expectedGeneration = latestViewerRefreshGenerationByDocument.get(documentId) ?? 0
      const payload = await fetchViewerFiles(documentId, signal)
      if (consumePendingViewerRefresh(documentId, expectedGeneration)) {
        showPaperFilesInViewer(documentId, payload.files, { refreshOnlyIfOpen: true })
      }
      return payload
    },
    enabled: open,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const paperFiles = useMemo(() => filesQuery.data?.files ?? [], [filesQuery.data?.files])
  const hasRemovableSupplement = useMemo(
    () => paperFiles.some((file) => canShowRemoveForFile(file, paperFiles)),
    [paperFiles],
  )
  const needsNonProviderActiveJobs = !providerLabel && hasRemovableSupplement

  const refreshPaperSurfaces = useCallback(
    async (
      targetDocumentId: string,
      options?: { invalidateFilesOnFailure?: boolean },
    ) => {
      // Track a monotonic generation and abort any older direct `/files` refresh for this
      // paper so an earlier mutation's slow response can never overwrite a newer removal in
      // query cache or the open viewer.
      const refreshGeneration = ++nextViewerRefreshGeneration
      latestViewerRefreshGenerationByDocument.set(targetDocumentId, refreshGeneration)
      activeViewerRefreshControllerByDocument.get(targetDocumentId)?.abort()
      activeViewerRecoveryByDocument.delete(targetDocumentId)
      const directRefreshController = new AbortController()
      activeViewerRefreshControllerByDocument.set(targetDocumentId, directRefreshController)
      const isLatestGeneration = () =>
        latestViewerRefreshGenerationByDocument.get(targetDocumentId) === refreshGeneration

      // Cancel any older in-flight viewer /files query first so it cannot resolve later and
      // overwrite the fresher payload while the dialog stays open, then fetch fresh viewer
      // files directly (not bound to an unmount-cancelling query observer) so closing the
      // dialog mid-completion still populates the cache and publishes the viewer refresh.
      const viewerRefreshPromise = (async () => {
        try {
          try {
            await queryClient.cancelQueries({ queryKey: paperFilesQueryKey(targetDocumentId) })
          } catch {
            // Best-effort cancellation; still fetch the fresh viewer file list.
          }
          if (directRefreshController.signal.aborted || !isLatestGeneration()) {
            return
          }
          try {
            const payload = await fetchViewerFiles(targetDocumentId, directRefreshController.signal)
            if (directRefreshController.signal.aborted || !isLatestGeneration()) {
              return
            }
            clearPendingViewerRefresh(targetDocumentId)
            queryClient.setQueryData(paperFilesQueryKey(targetDocumentId), payload)
            showPaperFilesInViewer(targetDocumentId, payload.files, { refreshOnlyIfOpen: true })
          } catch {
            if (
              directRefreshController.signal.aborted
              || !isLatestGeneration()
              || !options?.invalidateFilesOnFailure
            ) {
              return
            }
            markPendingViewerRefresh(targetDocumentId, refreshGeneration)
            // Share ONE unmount-surviving recovery request per (documentId, generation) bound
            // only to `directRefreshController.signal` (never to dialog unmount/close), so
            // closing the dialog mid-recovery still updates cache + open viewer and reopening
            // during recovery awaits the same GET without issuing a duplicate request.
            const recoveryPromise = Promise.resolve().then(async (): Promise<ViewerFilesPayload> => {
              try {
                const recoveredPayload = await fetchViewerFiles(
                  targetDocumentId,
                  directRefreshController.signal,
                )
                if (!directRefreshController.signal.aborted && isLatestGeneration()) {
                  clearPendingViewerRefresh(targetDocumentId)
                  queryClient.setQueryData(paperFilesQueryKey(targetDocumentId), recoveredPayload)
                  showPaperFilesInViewer(targetDocumentId, recoveredPayload.files, {
                    refreshOnlyIfOpen: true,
                  })
                }
                return recoveredPayload
              } finally {
                if (activeViewerRecoveryByDocument.get(targetDocumentId)?.promise === recoveryPromise) {
                  activeViewerRecoveryByDocument.delete(targetDocumentId)
                  pruneViewerRefreshGenerationIfIdle(targetDocumentId)
                }
              }
            })
            activeViewerRecoveryByDocument.set(targetDocumentId, {
              generation: refreshGeneration,
              promise: recoveryPromise,
            })

            const hasActiveFilesObserver =
              queryClient.getQueryCache().find({
                queryKey: paperFilesQueryKey(targetDocumentId),
                type: 'active',
              }) !== undefined
            if (hasActiveFilesObserver) {
              await Promise.allSettled([
                queryClient.invalidateQueries({ queryKey: paperFilesQueryKey(targetDocumentId) }),
                recoveryPromise,
              ])
            } else {
              await Promise.allSettled([
                queryClient.invalidateQueries({
                  queryKey: paperFilesQueryKey(targetDocumentId),
                  refetchType: 'none',
                }),
                recoveryPromise,
              ])
            }
          }
        } finally {
          if (
            activeViewerRefreshControllerByDocument.get(targetDocumentId)
            === directRefreshController
          ) {
            activeViewerRefreshControllerByDocument.delete(targetDocumentId)
          }
          pruneViewerRefreshGenerationIfIdle(targetDocumentId)
        }
      })()

      // Cancel any older in-flight /source-files query before invalidating so a slow
      // pre-mutation listing cannot land afterwards and sit fresh for PAPER_SOURCE_FILES_STALE_MS.
      const sourceFilesRefreshPromise = (async () => {
        try {
          await queryClient.cancelQueries({ queryKey: paperSourceFilesQueryKey(targetDocumentId) })
        } catch {
          // Best-effort cancellation; still invalidate cached source listing.
        }
        await queryClient.invalidateQueries({ queryKey: paperSourceFilesQueryKey(targetDocumentId) })
      })()

      await Promise.allSettled([
        viewerRefreshPromise,
        sourceFilesRefreshPromise,
        queryClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(targetDocumentId) }),
        queryClient.invalidateQueries({ queryKey: ['documents'] }),
        queryClient.invalidateQueries({ queryKey: ['document', targetDocumentId] }),
      ])
    },
    [queryClient],
  )

  const runPostRemovalRefresh = useCallback(
    async (targetDocumentId: string, notifyFilesChanged?: () => void) => {
      if (mountedRef.current && activeDocumentIdRef.current === targetDocumentId) {
        setRefreshingAfterRemoval(true)
        setRemovalResetSeq((count) => count + 1)
      }
      try {
        await refreshPaperSurfaces(targetDocumentId, { invalidateFilesOnFailure: true })
      } finally {
        dispatchDocumentsListInvalidated(notifyFilesChanged)
        if (mountedRef.current && activeDocumentIdRef.current === targetDocumentId) {
          setRefreshingAfterRemoval(false)
        }
      }
    },
    [refreshPaperSurfaces],
  )

  const isRemovalBusy = removalDialog !== null || refreshingAfterRemoval
  const activeJobsOrAddBusy = providerLabel
    ? providerPaperBusy
    : (needsNonProviderActiveJobs && nonProviderPaperBusy)
  const baseRemovalBlocked =
    !isPaperCompleted
    || activeJobsOrAddBusy
    || refreshingAfterRemoval
    || filesQuery.isFetching
  const removeDisabled = baseRemovalBlocked || removalDialog !== null

  const runRemovalPrecheck = useCallback(
    async (file: ViewerFile) => {
      const targetDocumentId = documentId
      const notifyFilesChanged = onFilesChangedRef.current
      precheckAbortControllerRef.current?.abort()
      const controller = new AbortController()
      precheckAbortControllerRef.current = controller

      setRemovalDialog({ file, phase: 'checking' })

      try {
        const check = await checkDocumentFileRemoval(targetDocumentId, file.file_id, controller.signal)
        if (
          controller.signal.aborted
          || !mountedRef.current
          || activeDocumentIdRef.current !== targetDocumentId
        ) {
          return
        }
        if (!check.removable) {
          setRemovalDialog({
            file,
            phase: 'refused',
            code: check.error,
            text: fileRemovalRefusalText(check.error, { sessionCount: check.session_ids?.length }),
          })
          if (check.error === 'file_not_in_paper') {
            void runPostRemovalRefresh(targetDocumentId, notifyFilesChanged)
          } else if (check.error === 'import_in_progress') {
            void queryClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(targetDocumentId) })
          }
          return
        }
        setRemovalDialog({
          file,
          phase: 'confirm',
          check,
          submitting: false,
          error: null,
        })
      } catch (error) {
        if (
          controller.signal.aborted
          || !mountedRef.current
          || activeDocumentIdRef.current !== targetDocumentId
          || isAbortError(error)
        ) {
          return
        }
        if (error instanceof AuthenticationError) {
          setRemovalDialog({
            file,
            phase: 'check_error',
            text: 'Sign in again to remove files from this paper.',
            retryable: false,
          })
          return
        }
        if (error instanceof DocumentFileRemovalError) {
          const mapped = fileRemovalHttpErrorText({
            httpStatus: error.httpStatus,
            code: error.code,
            sessionCount: error.sessionIds.length,
            fallbackText: 'Could not check whether this file can be removed.',
          })
          if (mapped.code !== null) {
            setRemovalDialog({
              file,
              phase: 'refused',
              code: mapped.code,
              text: mapped.text,
            })
            if (mapped.code === 'file_not_in_paper') {
              void runPostRemovalRefresh(targetDocumentId, notifyFilesChanged)
            } else if (mapped.code === 'import_in_progress') {
              void queryClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(targetDocumentId) })
            }
            return
          }
          setRemovalDialog({
            file,
            phase: 'check_error',
            text: mapped.text,
            retryable: mapped.retryable,
          })
          return
        }
        setRemovalDialog({
          file,
          phase: 'check_error',
          text: 'Could not check whether this file can be removed.',
          retryable: true,
        })
      } finally {
        if (precheckAbortControllerRef.current === controller) {
          precheckAbortControllerRef.current = null
        }
      }
    },
    [documentId, queryClient, runPostRemovalRefresh],
  )

  const handleRequestRemove = useCallback(
    (file: ViewerFile) => {
      if (removeDisabled) {
        return
      }
      void runRemovalPrecheck(file)
    },
    [removeDisabled, runRemovalPrecheck],
  )

  const handleRetryPrecheck = useCallback(() => {
    if (removalDialog?.phase !== 'check_error' || baseRemovalBlocked) {
      return
    }
    void runRemovalPrecheck(removalDialog.file)
  }, [baseRemovalBlocked, removalDialog, runRemovalPrecheck])

  const handleConfirmRemove = useCallback(async () => {
    if (removalDialog?.phase !== 'confirm' || removalDialog.submitting || baseRemovalBlocked) {
      return
    }
    const targetFile = removalDialog.file
    const targetDocumentId = documentId
    const notifyFilesChanged = onFilesChangedRef.current

    precheckAbortControllerRef.current?.abort()
    precheckAbortControllerRef.current = null

    setRemovalDialog((current) =>
      current?.phase === 'confirm' && current.file.file_id === targetFile.file_id
        ? { ...current, submitting: true, error: null }
        : current,
    )

    try {
      // Once issued, do NOT tie DELETE to an unmount/dialog-switch AbortSignal so Phase 2
      // server cleanup and 3-surface post-removal reconciliation always finish cleanly.
      // Card 3 (W-D2): `cleanup_finished: false` intentionally shows nothing extra in the UI;
      // backend reports cleanup failures to Sentry.
      await removeDocumentFile(targetDocumentId, targetFile.file_id)
      if (mountedRef.current && activeDocumentIdRef.current === targetDocumentId) {
        setRemovalDialog((current) =>
          current?.phase === 'confirm' && current.file.file_id === targetFile.file_id
            ? null
            : current,
        )
      }
      await runPostRemovalRefresh(targetDocumentId, notifyFilesChanged)
    } catch (error) {
      if (error instanceof DocumentFileRemovalError) {
        const mapped = fileRemovalHttpErrorText({
          httpStatus: error.httpStatus,
          code: error.code,
          sessionCount: error.sessionIds.length,
          fallbackText: 'Could not remove this file.',
        })
        if (mapped.code === 'file_not_in_paper') {
          void runPostRemovalRefresh(targetDocumentId, notifyFilesChanged)
        } else if (mapped.code === 'import_in_progress') {
          void queryClient.invalidateQueries({ queryKey: paperActiveJobsQueryKey(targetDocumentId) })
        }
        if (!mountedRef.current || activeDocumentIdRef.current !== targetDocumentId) {
          return
        }
        if (mapped.code !== null) {
          setRemovalDialog((current) =>
            current?.phase === 'confirm' && current.file.file_id === targetFile.file_id
              ? {
                  file: targetFile,
                  phase: 'refused',
                  code: mapped.code,
                  text: mapped.text,
                }
              : current,
          )
          return
        }
        setRemovalDialog((current) =>
          current?.phase === 'confirm' && current.file.file_id === targetFile.file_id
            ? { ...current, submitting: false, error: mapped }
            : current,
        )
        return
      }
      if (!mountedRef.current || activeDocumentIdRef.current !== targetDocumentId) {
        return
      }
      if (error instanceof AuthenticationError) {
        setRemovalDialog((current) =>
          current?.phase === 'confirm' && current.file.file_id === targetFile.file_id
            ? {
                file: targetFile,
                phase: 'refused',
                code: 'authentication_required',
                text: 'Sign in again to remove files from this paper.',
              }
            : current,
        )
        return
      }
      setRemovalDialog((current) =>
        current?.phase === 'confirm' && current.file.file_id === targetFile.file_id
          ? {
              ...current,
              submitting: false,
              error: { text: 'Could not remove this file.', retryable: true, code: null },
            }
          : current,
      )
    }
  }, [baseRemovalBlocked, documentId, queryClient, removalDialog, runPostRemovalRefresh])

  const handleCloseRemovalDialog = useCallback(() => {
    if (removalDialog?.phase === 'confirm' && removalDialog.submitting) {
      return
    }
    precheckAbortControllerRef.current?.abort()
    precheckAbortControllerRef.current = null
    setRemovalDialog(null)
  }, [removalDialog])

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        Files
      </Typography>
      <Divider sx={{ mb: 2 }} />
      <Stack spacing={2}>
        {filesQuery.isPending ? (
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <CircularProgress size={14} />
            <Typography variant="body2">Loading this paper&apos;s files...</Typography>
          </Stack>
        ) : filesQuery.isError ? (
          <Alert
            severity="warning"
            action={(
              <Button size="small" color="inherit" onClick={() => void filesQuery.refetch()}>
                {TRY_AGAIN_BUTTON_TEXT}
              </Button>
            )}
          >
            Could not load this paper&apos;s files.
          </Alert>
        ) : (
          <Box component="ul" aria-label="Files in this paper" sx={{ m: 0, pl: 2.5 }}>
            {paperFiles.map((file) => (
              <PaperFileRow
                key={file.file_id}
                file={file}
                showRemove={canShowRemoveForFile(file, paperFiles)}
                removeDisabled={removeDisabled}
                onRequestRemove={handleRequestRemove}
              />
            ))}
          </Box>
        )}
        {needsNonProviderActiveJobs ? (
          <NonProviderActiveJobsPart
            key={documentId}
            documentId={documentId}
            open={open}
            pollIntervalMs={nonProviderPollIntervalMs}
            onBusyChange={setNonProviderPaperBusy}
          />
        ) : null}
        {providerLabel ? (
          <ProviderFilesPart
            key={documentId}
            documentId={documentId}
            provider={providerLabel}
            providerShortLabel={providerShortLabel}
            processingStatus={processingStatus}
            open={open}
            onFilesChanged={onFilesChanged}
            pollIntervalMs={effectivePollIntervalMs}
            isRemovalBusy={isRemovalBusy}
            onAddBusyChange={setProviderPaperBusy}
            refreshPaperSurfaces={refreshPaperSurfaces}
            removalResetSeq={removalResetSeq}
          />
        ) : (
          <UploadedPaperFilesNotice
            configuredProviderShortLabel={configuredProviderShortLabel}
            open={open}
          />
        )}
      </Stack>
      <FileRemovalDialog
        state={removalDialog}
        actionDisabled={baseRemovalBlocked}
        onClose={handleCloseRemovalDialog}
        onRetryPrecheck={handleRetryPrecheck}
        onConfirm={() => void handleConfirmRemove()}
      />
    </Paper>
  )
}
