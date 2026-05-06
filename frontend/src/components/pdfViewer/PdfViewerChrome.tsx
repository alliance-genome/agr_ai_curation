import type { ChangeEvent, DragEventHandler, KeyboardEvent, Ref } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  InputAdornment,
  Paper,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import {
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  FitScreen as FitScreenIcon,
  Search as SearchIcon,
  ZoomIn as ZoomInIcon,
  ZoomOut as ZoomOutIcon,
} from '@mui/icons-material'
import UploadProgressDialog from '@/components/weaviate/UploadProgressDialog'
import {
  formatLocatorQualityLabel,
  getNavigationBadgeColor,
  getNavigationBannerSeverity,
  type PdfViewerNavigationResult,
} from './pdfEvidenceNavigation'
import type { ViewerDocument, ViewerStatus } from './pdfViewerTypes'
import type { UploadDialogState } from './usePdfViewerUpload'

interface PdfViewerChromeProps {
  activeDocument: ViewerDocument | null
  status: ViewerStatus
  error: string | null
  retryKey: number
  viewerSrc: string
  iframeRef: Ref<HTMLIFrameElement>
  highlightTerms: string[]
  navigationResult: PdfViewerNavigationResult | null
  navigationBannerMessage: string | null
  dragActive: boolean
  uploadInFlight: boolean
  dropError: string | null
  uploadDialog: UploadDialogState
  currentPage: number
  zoomLevel: number
  searchQuery: string
  searchCurrent: number | null
  searchTotal: number | null
  searchNotFound: boolean
  onDragEnter: DragEventHandler<HTMLDivElement>
  onDragOver: DragEventHandler<HTMLDivElement>
  onDragLeave: DragEventHandler<HTMLDivElement>
  onDrop: DragEventHandler<HTMLDivElement>
  onRetry: () => void
  onCloseUploadDialog: () => void
  onPreviousPage: () => void
  onNextPage: () => void
  onZoomOut: () => void
  onZoomIn: () => void
  onZoomAuto: () => void
  onSearchQueryChange: (query: string) => void
  onSearchNext: () => void
  onSearchPrevious: () => void
}

export function PdfViewerChrome({
  activeDocument,
  status,
  error,
  retryKey,
  viewerSrc,
  iframeRef,
  highlightTerms,
  navigationResult,
  navigationBannerMessage,
  dragActive,
  uploadInFlight,
  dropError,
  uploadDialog,
  currentPage,
  zoomLevel,
  searchQuery,
  searchCurrent,
  searchTotal,
  searchNotFound,
  onDragEnter,
  onDragOver,
  onDragLeave,
  onDrop,
  onRetry,
  onCloseUploadDialog,
  onPreviousPage,
  onNextPage,
  onZoomOut,
  onZoomIn,
  onZoomAuto,
  onSearchQueryChange,
  onSearchNext,
  onSearchPrevious,
}: PdfViewerChromeProps) {
  const theme = useTheme()
  const controlsDisabled = !activeDocument || status !== 'ready'
  const searchDisabled = controlsDisabled || searchQuery.trim().length === 0
  const searchMatchLabel = searchNotFound
    ? 'No matches'
    : searchTotal !== null && searchTotal > 0
      ? `${searchCurrent ?? 0} / ${searchTotal}`
      : ''
  const handleSearchChange = (event: ChangeEvent<HTMLInputElement>) => {
    onSearchQueryChange(event.target.value)
  }
  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' && !searchDisabled) {
      event.preventDefault()
      if (event.shiftKey) {
        onSearchPrevious()
      } else {
        onSearchNext()
      }
    }
  }

  return (
    <Paper
      elevation={3}
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'background.paper',
      }}
    >
      <Box sx={{ padding: 2, borderBottom: 1, borderColor: 'divider' }}>
        {activeDocument ? (
          <Stack spacing={0.5}>
            <Typography variant="h6">{activeDocument.filename}</Typography>
            <Typography variant="body2" color="text.secondary">
              {activeDocument.pageCount} pages · Serving from {activeDocument.viewerUrl}
            </Typography>
            {navigationResult && (
              <>
                <Stack direction="row" spacing={1} flexWrap="wrap">
                  <Chip
                    size="small"
                    color={getNavigationBadgeColor(navigationResult)}
                    label={formatLocatorQualityLabel(navigationResult.locatorQuality)}
                    sx={{ marginTop: 0.5 }}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={navigationResult.mode === 'hover' ? 'Hover sync' : 'Selection sync'}
                    sx={{ marginTop: 0.5 }}
                  />
                  {navigationResult.matchedPage !== null && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={`Page ${navigationResult.matchedPage}`}
                      sx={{ marginTop: 0.5 }}
                    />
                  )}
                </Stack>
                <Typography
                  variant="body2"
                  color={navigationResult.degraded ? 'warning.main' : 'text.secondary'}
                >
                  {navigationBannerMessage}
                </Typography>
              </>
            )}
            {highlightTerms.length > 0 && (
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {highlightTerms.map((term) => (
                  <Chip key={term} size="small" label={term} color="secondary" sx={{ marginTop: 0.5 }} />
                ))}
              </Stack>
            )}
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={1}
              alignItems={{ xs: 'stretch', sm: 'center' }}
              sx={{ pt: 0.75 }}
            >
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Tooltip title="Previous page">
                  <span>
                    <IconButton
                      aria-label="Previous PDF page"
                      size="small"
                      disabled={controlsDisabled || currentPage <= 1}
                      onClick={onPreviousPage}
                    >
                      <ChevronLeftIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 72, textAlign: 'center' }}
                >
                  {currentPage} / {activeDocument.pageCount}
                </Typography>
                <Tooltip title="Next page">
                  <span>
                    <IconButton
                      aria-label="Next PDF page"
                      size="small"
                      disabled={controlsDisabled || currentPage >= activeDocument.pageCount}
                      onClick={onNextPage}
                    >
                      <ChevronRightIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
              <Divider orientation="vertical" flexItem sx={{ display: { xs: 'none', sm: 'block' } }} />
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Tooltip title="Zoom out">
                  <span>
                    <IconButton
                      aria-label="Zoom out"
                      size="small"
                      disabled={controlsDisabled || zoomLevel <= 10}
                      onClick={onZoomOut}
                    >
                      <ZoomOutIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 48, textAlign: 'center' }}
                >
                  {zoomLevel}%
                </Typography>
                <Tooltip title="Zoom in">
                  <span>
                    <IconButton
                      aria-label="Zoom in"
                      size="small"
                      disabled={controlsDisabled || zoomLevel >= 500}
                      onClick={onZoomIn}
                    >
                      <ZoomInIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                <Tooltip title="Automatic zoom">
                  <span>
                    <IconButton
                      aria-label="Automatic zoom"
                      size="small"
                      disabled={controlsDisabled}
                      onClick={onZoomAuto}
                    >
                      <FitScreenIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
              <TextField
                size="small"
                value={searchQuery}
                onChange={handleSearchChange}
                onKeyDown={handleSearchKeyDown}
                disabled={controlsDisabled}
                placeholder="Find in PDF"
                inputProps={{ 'aria-label': 'Find in PDF' }}
                sx={{
                  flex: 1,
                  minWidth: { xs: '100%', sm: 180 },
                }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon fontSize="small" color="action" />
                    </InputAdornment>
                  ),
                  endAdornment: searchMatchLabel ? (
                    <InputAdornment position="end">
                      <Typography
                        variant="caption"
                        color={searchNotFound ? 'error.main' : 'text.secondary'}
                        sx={{ whiteSpace: 'nowrap' }}
                      >
                        {searchMatchLabel}
                      </Typography>
                    </InputAdornment>
                  ) : undefined,
                }}
              />
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Tooltip title="Previous match">
                  <span>
                    <IconButton
                      aria-label="Previous PDF search match"
                      size="small"
                      disabled={searchDisabled}
                      onClick={onSearchPrevious}
                    >
                      <ChevronLeftIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                <Tooltip title="Next match">
                  <span>
                    <IconButton
                      aria-label="Next PDF search match"
                      size="small"
                      disabled={searchDisabled}
                      onClick={onSearchNext}
                    >
                      <ChevronRightIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
            </Stack>
          </Stack>
        ) : (
          <Typography variant="h6">No document loaded</Typography>
        )}
      </Box>

      <Box sx={{ flex: 1, position: 'relative' }}>
        {activeDocument && navigationResult?.degraded && navigationBannerMessage && (
          <Box
            sx={{
              position: 'absolute',
              top: 12,
              left: 12,
              right: 12,
              zIndex: 3,
            }}
          >
            <Alert severity={getNavigationBannerSeverity(navigationResult)} variant="filled">
              {navigationBannerMessage}
            </Alert>
          </Box>
        )}

        {!activeDocument && (
          <Box
            role="region"
            aria-label="PDF drop zone"
            onDragEnter={onDragEnter}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'text.secondary',
              textAlign: 'center',
              px: 3,
              border: '2px dashed',
              borderColor: dragActive ? 'primary.main' : 'divider',
              backgroundColor: dragActive ? 'action.hover' : 'transparent',
              transition: 'border-color 120ms ease, background-color 120ms ease',
            }}
          >
            <Typography variant="body1" sx={{ mb: 2 }}>
              {dragActive ? 'Drop PDF to upload and load for chat' : 'Drag and drop a PDF here to upload'}
            </Typography>
            {uploadInFlight && (
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                <CircularProgress size={16} />
                <Typography variant="body2" color="text.secondary">
                  Upload in progress...
                </Typography>
              </Stack>
            )}
            {error && (
              <Alert severity="error" sx={{ mb: 2, maxWidth: 640 }}>
                {error}
              </Alert>
            )}
            {dropError && (
              <Alert severity="error" sx={{ mb: 2, maxWidth: 640 }}>
                {dropError}
              </Alert>
            )}
            <Typography variant="body2" component="div">
              <ul style={{ textAlign: 'left', margin: 0, paddingLeft: '1.5rem' }}>
                <li style={{ marginBottom: '0.75rem' }}>
                  Drop a PDF here to upload and load it for chat.
                </li>
                <li style={{ marginBottom: '0.75rem' }}>
                  For one or multiple uploads, open <strong>Documents</strong> and use Upload.
                </li>
                <li>To load a PDF you already uploaded, open <strong>Documents</strong> and click the green file icon in that row.</li>
              </ul>
            </Typography>
          </Box>
        )}

        {activeDocument && status === 'loading' && (
          <Box
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.background.default, theme.palette.mode === 'dark' ? 0.72 : 0.64),
              color: theme.palette.text.primary,
              zIndex: 1,
            }}
          >
            <Stack spacing={2} alignItems="center">
              <CircularProgress color="inherit" size={48} />
              <Typography variant="body2" color="inherit">
                Loading PDF...
              </Typography>
            </Stack>
          </Box>
        )}

        {activeDocument && status === 'error' && (
          <Box
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.background.default, 0.92),
              zIndex: 2,
              padding: 3,
            }}
          >
            <Alert
              severity="error"
              sx={{ width: '100%' }}
              action={(
                <Button
                  color="inherit"
                  size="small"
                  onClick={onRetry}
                >
                  Retry
                </Button>
              )}
            >
              {error ?? 'Something went wrong while loading the PDF viewer.'}
            </Alert>
          </Box>
        )}

        <iframe
          key={`${activeDocument?.documentId}::${retryKey}`}
          ref={iframeRef}
          title="PDF Viewer"
          src={viewerSrc}
          style={{
            border: 'none',
            width: '100%',
            height: '100%',
            backgroundColor: theme.palette.background.default,
          }}
        />
      </Box>
      <UploadProgressDialog
        open={uploadDialog.open}
        fileName={uploadDialog.fileName}
        stage={uploadDialog.stage}
        progress={uploadDialog.progress}
        message={uploadDialog.message}
        documentId={uploadDialog.documentId}
        onClose={onCloseUploadDialog}
      />
    </Paper>
  )
}
