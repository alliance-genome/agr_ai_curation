import { useState } from 'react'
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

/**
 * Get display label for file format
 */
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
function getFormatColor(format: string): string {
  const colors: Record<string, string> = {
    csv: '#4caf50',  // green
    tsv: '#2196f3',  // blue
    json: '#ff9800', // orange
  }
  return colors[format.toLowerCase()] || '#9e9e9e'
}

/**
 * FileDownloadCard component
 *
 * Renders a downloadable file card in chat messages.
 * Shows filename, format badge, size, and a download button.
 * Handles download state with loading indicator and error handling.
 */
function FileDownloadCard({ file }: FileDownloadCardProps) {
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadComplete, setDownloadComplete] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      setTimeout(() => setDownloadComplete(false), 2000)
    } catch (err) {
      console.error('[FileDownloadCard] Download error:', err)
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setIsDownloading(false)
    }
  }

  const formatColor = getFormatColor(file.format)

  return (
    <>
      <Card
        sx={{
          mt: 1,
          mb: 1,
          backgroundColor: 'rgba(255, 255, 255, 0.05)',
          border: '1px solid rgba(255, 255, 255, 0.12)',
          borderRadius: 2,
          maxWidth: 400,
        }}
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
                backgroundColor: `${formatColor}20`,
              }}
            >
              <InsertDriveFileIcon sx={{ color: formatColor, fontSize: 24 }} />
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
                  color: 'rgba(255, 255, 255, 0.9)',
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
                    backgroundColor: `${formatColor}30`,
                    color: formatColor,
                    fontWeight: 600,
                    fontSize: '0.7rem',
                  }}
                >
                  {getFormatLabel(file.format)}
                </Typography>
                {file.size_bytes && (
                  <Typography
                    variant="caption"
                    sx={{ color: 'rgba(255, 255, 255, 0.5)' }}
                  >
                    {formatFileSize(file.size_bytes)}
                  </Typography>
                )}
              </Box>
            </Box>

            {/* Download button */}
            <Button
              variant="contained"
              size="small"
              onClick={handleDownload}
              disabled={isDownloading}
              sx={{
                minWidth: 36,
                height: 36,
                borderRadius: 1,
                backgroundColor: downloadComplete ? '#4caf50' : 'primary.main',
                '&:hover': {
                  backgroundColor: downloadComplete ? '#45a049' : 'primary.dark',
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
          </Box>
        </CardContent>
      </Card>

      {/* Error snackbar */}
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
    </>
  )
}

export default FileDownloadCard
