import { useEffect, useRef, useState } from 'react'
import {
  Card,
  CardContent,
  Typography,
  Button,
  Box,
  CircularProgress,
  Snackbar,
  Alert
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import type { Theme } from '@mui/material/styles'
import DownloadIcon from '@mui/icons-material/Download'
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'

/**
 * FileInfo structure matching backend FileInfo model
 */
export interface FileInfo {
  file_id: string
  filename: string
  format: string
  size_bytes?: number
  mime_type?: string
  download_url: string
  created_at?: string
}

interface FileDownloadCardProps {
  file: FileInfo
  allowDownload?: boolean
  cardTestId?: string
}

/**
 * Format file size in human-readable units
 */
function formatFileSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function getFormatLabel(format: string): string {
  const labels: Record<string, string> = {
    csv: 'CSV',
    tsv: 'TSV',
    json: 'JSON',
  }
  return labels[format.toLowerCase()] || format.toUpperCase()
}

/**
 * Get icon color based on file format
 */
function getFormatColor(theme: Theme, format: string): string {
  const isDark = theme.palette.mode === 'dark'
  const colors: Record<string, string> = {
    csv: isDark ? theme.palette.success.light : theme.palette.success.dark,
    tsv: isDark ? theme.palette.info.light : theme.palette.info.dark,
    json: isDark ? theme.palette.warning.light : theme.palette.warning.dark,
  }
  return colors[format.toLowerCase()] || theme.palette.text.secondary
}

/**
 * FileDownloadCard component
 *
 * Renders a downloadable file card in chat messages.
 * Shows filename, format badge, size, and a download button.
 * Handles download state with loading indicator and error handling.
 */
function FileDownloadCard({
  file,
  allowDownload = true,
  cardTestId,
}: FileDownloadCardProps) {
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadComplete, setDownloadComplete] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const resetDownloadCompleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (resetDownloadCompleteTimerRef.current !== null) {
        clearTimeout(resetDownloadCompleteTimerRef.current)
      }
    }
  }, [])

  const handleDownload = async () => {
    setIsDownloading(true)
    setError(null)

    try {
      // Fetch the file with credentials (for authenticated sessions)
      const response = await fetch(file.download_url, {
        credentials: 'include',
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `Download failed: ${response.statusText}`)
      }

      // Get the blob from response
      const blob = await response.blob()

      // Create download link and trigger download
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = file.filename
      document.body.appendChild(a)
      a.click()

      // Cleanup
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)

      // Show success state briefly
      setDownloadComplete(true)
      if (resetDownloadCompleteTimerRef.current !== null) {
        clearTimeout(resetDownloadCompleteTimerRef.current)
      }
      resetDownloadCompleteTimerRef.current = setTimeout(() => {
        setDownloadComplete(false)
        resetDownloadCompleteTimerRef.current = null
      }, 2000)
    } catch (err) {
      console.error('[FileDownloadCard] Download error:', err)
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setIsDownloading(false)
    }
  }

  return (
    <>
      <Card
        data-testid={cardTestId}
        sx={(theme) => ({
          mt: 1,
          mb: 1,
          backgroundColor: theme.palette.mode === 'dark'
            ? alpha(theme.palette.common.white, 0.08)
            : alpha(theme.palette.background.paper, 0.9),
          border: `1px solid ${theme.palette.mode === 'dark'
            ? alpha(theme.palette.common.white, 0.12)
            : alpha(theme.palette.primary.dark, 0.16)}`,
          borderRadius: 2,
          maxWidth: 400,
        })}
      >
        <CardContent sx={{ py: 1.5, px: 2, '&:last-child': { pb: 1.5 } }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            {/* File icon with format-colored background */}
            <Box
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 40,
                height: 40,
                borderRadius: 1,
                backgroundColor: (theme) => alpha(getFormatColor(theme, file.format), 0.14),
              }}
            >
              <InsertDriveFileIcon sx={{ color: (theme) => getFormatColor(theme, file.format), fontSize: 24 }} />
            </Box>

            {/* File info */}
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                variant="body2"
                sx={{
                  fontWeight: 500,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  color: 'text.primary',
                }}
                title={file.filename}
              >
                {file.filename}
              </Typography>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.25 }}>
                <Typography
                  variant="caption"
                  sx={{
                    px: 0.75,
                    py: 0.125,
                    borderRadius: 0.5,
                    backgroundColor: (theme) => alpha(getFormatColor(theme, file.format), 0.18),
                    color: (theme) => getFormatColor(theme, file.format),
                    fontWeight: 600,
                    fontSize: '0.7rem',
                  }}
                >
                  {getFormatLabel(file.format)}
                </Typography>
                {file.size_bytes && (
                  <Typography
                    variant="caption"
                    sx={{ color: 'text.secondary' }}
                  >
                    {formatFileSize(file.size_bytes)}
                  </Typography>
                )}
              </Box>
            </Box>

            {/* Download button */}
            {allowDownload ? (
              <Button
                variant="contained"
                size="small"
                onClick={handleDownload}
                disabled={isDownloading}
                sx={{
                  minWidth: 36,
                  height: 36,
                  borderRadius: 1,
                  backgroundColor: downloadComplete ? 'success.main' : 'primary.main',
                  '&:hover': {
                    backgroundColor: downloadComplete ? 'success.dark' : 'primary.dark',
                  },
                }}
              >
                {isDownloading ? (
                  <CircularProgress size={18} color="inherit" />
                ) : downloadComplete ? (
                  <CheckCircleIcon sx={{ fontSize: 20 }} />
                ) : (
                  <DownloadIcon sx={{ fontSize: 20 }} />
                )}
              </Button>
            ) : null}
          </Box>
        </CardContent>
      </Card>

      {allowDownload ? (
        <Snackbar
          open={!!error}
          autoHideDuration={5000}
          onClose={() => setError(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        >
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        </Snackbar>
      ) : null}
    </>
  )
}

export default FileDownloadCard
