import { useMemo, useState } from 'react'
import CheckCircleOutlineRoundedIcon from '@mui/icons-material/CheckCircleOutlineRounded'
import DownloadRoundedIcon from '@mui/icons-material/DownloadRounded'
import ErrorOutlineRoundedIcon from '@mui/icons-material/ErrorOutlineRounded'
import ExpandMoreRoundedIcon from '@mui/icons-material/ExpandMoreRounded'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from '@mui/material'

export type FlowEvidenceExportFormat = 'csv' | 'tsv' | 'json'

export interface FlowRunCompletionSummary {
  failureReason: string | null
  flowId: string | null
  flowName: string
  flowRunId: string
  status: string
  totalEvidenceRecords: number
}

interface FlowRunCompletionCardProps {
  run: FlowRunCompletionSummary
}

const EXPORT_FORMAT_OPTIONS: Array<{
  description: string
  format: FlowEvidenceExportFormat
  label: string
}> = [
  {
    format: 'csv',
    label: 'Download CSV',
    description: 'Spreadsheet-friendly comma-separated rows.',
  },
  {
    format: 'tsv',
    label: 'Download TSV',
    description: 'Tab-delimited rows for pipeline-friendly copy/paste.',
  },
  {
    format: 'json',
    label: 'Download JSON',
    description: 'Canonical structured evidence payload.',
  },
]

function parseAttachmentFilename(headerValue: string | null): string {
  if (!headerValue) {
    throw new Error('Download response is missing Content-Disposition header.')
  }

  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(headerValue)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch {
      return utf8Match[1]
    }
  }

  const quotedMatch = /filename="([^"]+)"/i.exec(headerValue)
  if (quotedMatch?.[1]) {
    return quotedMatch[1]
  }

  const plainMatch = /filename=([^;]+)/i.exec(headerValue)
  if (plainMatch?.[1]) {
    return plainMatch[1].trim()
  }

  throw new Error('Could not parse attachment filename from download response.')
}

export default function FlowRunCompletionCard({ run }: FlowRunCompletionCardProps) {
  const [menuAnchorEl, setMenuAnchorEl] = useState<HTMLElement | null>(null)
  const [isDownloading, setIsDownloading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const exportReady = run.status === 'completed' && run.totalEvidenceRecords > 0
  const statusChip = useMemo(() => {
    if (run.status === 'completed') {
      return {
        color: '#4caf50',
        icon: <CheckCircleOutlineRoundedIcon sx={{ fontSize: '1rem' }} />,
        label: 'Completed',
      }
    }

    return {
      color: '#f44336',
      icon: <ErrorOutlineRoundedIcon sx={{ fontSize: '1rem' }} />,
      label: 'Failed',
    }
  }, [run.status])

  const handleOpenMenu = (event: React.MouseEvent<HTMLElement>) => {
    setError(null)
    setMenuAnchorEl(event.currentTarget)
  }

  const handleCloseMenu = () => {
    setMenuAnchorEl(null)
  }

  const handleDownload = async (format: FlowEvidenceExportFormat) => {
    setIsDownloading(true)
    setError(null)
    handleCloseMenu()

    try {
      const response = await fetch(
        `/api/flows/runs/${encodeURIComponent(run.flowRunId)}/evidence/export?format=${format}`,
        {
          credentials: 'include',
        },
      )

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => null)
        throw new Error(
          errorPayload?.detail || `Failed to export evidence (${response.status})`,
        )
      }

      const filename = parseAttachmentFilename(response.headers.get('Content-Disposition'))
      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')

      try {
        link.href = objectUrl
        link.download = filename
        document.body.appendChild(link)
        link.click()
      } finally {
        window.URL.revokeObjectURL(objectUrl)
        if (document.body.contains(link)) {
          document.body.removeChild(link)
        }
      }
    } catch (downloadError) {
      setError(
        downloadError instanceof Error
          ? downloadError.message
          : 'Failed to export evidence.',
      )
    } finally {
      setIsDownloading(false)
    }
  }

  return (
    <Box
      sx={{
        mb: 2,
        border: '1px solid rgba(76, 175, 80, 0.24)',
        borderRadius: '8px',
        padding: '12px',
        background: 'linear-gradient(180deg, rgba(76, 175, 80, 0.12), rgba(255, 255, 255, 0.02))',
      }}
    >
      <Stack direction="row" spacing={1.5} alignItems="flex-start" justifyContent="space-between">
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography
              variant="subtitle2"
              sx={{ color: 'rgba(255, 255, 255, 0.92)', fontWeight: 600 }}
            >
              Latest flow run
            </Typography>
            <Chip
              icon={statusChip.icon}
              label={statusChip.label}
              size="small"
              sx={{
                backgroundColor: `${statusChip.color}20`,
                color: statusChip.color,
                border: `1px solid ${statusChip.color}33`,
                '& .MuiChip-icon': {
                  color: statusChip.color,
                },
              }}
            />
          </Stack>

          <Typography
            variant="body2"
            sx={{ color: 'rgba(255, 255, 255, 0.86)', fontWeight: 500 }}
          >
            {run.flowName}
          </Typography>
          <Typography
            variant="caption"
            sx={{
              color: 'rgba(255, 255, 255, 0.55)',
              display: 'block',
              mt: 0.25,
              wordBreak: 'break-all',
            }}
          >
            Run ID: {run.flowRunId}
          </Typography>
          <Typography
            variant="caption"
            sx={{ color: 'rgba(255, 255, 255, 0.7)', display: 'block', mt: 0.5 }}
          >
            {run.status === 'completed'
              ? `${run.totalEvidenceRecords} evidence record${run.totalEvidenceRecords === 1 ? '' : 's'} ready from the latest completed run.`
              : run.failureReason || 'This flow run did not complete successfully.'}
          </Typography>
        </Box>

        <Button
          variant="contained"
          size="small"
          onClick={handleOpenMenu}
          disabled={!exportReady || isDownloading}
          startIcon={
            isDownloading
              ? <CircularProgress size={14} sx={{ color: 'inherit' }} />
              : <DownloadRoundedIcon />
          }
          endIcon={!isDownloading ? <ExpandMoreRoundedIcon /> : undefined}
          sx={{
            minWidth: 'auto',
            px: 1.5,
            py: 0.75,
            textTransform: 'none',
            fontSize: '0.75rem',
            fontWeight: 600,
            boxShadow: 'none',
            backgroundColor: 'rgba(33, 150, 243, 0.92)',
            '&:hover': {
              backgroundColor: '#2196f3',
              boxShadow: '0 2px 8px rgba(33, 150, 243, 0.28)',
            },
            '&:disabled': {
              backgroundColor: 'rgba(255, 255, 255, 0.08)',
              color: 'rgba(255, 255, 255, 0.32)',
            },
          }}
        >
          Export Evidence
        </Button>
      </Stack>

      {!exportReady && run.status === 'completed' && (
        <Alert
          severity="info"
          sx={{
            mt: 1.5,
            backgroundColor: 'rgba(33, 150, 243, 0.08)',
            color: 'rgba(255, 255, 255, 0.78)',
          }}
        >
          This run finished without persisted evidence records to export.
        </Alert>
      )}

      {error && (
        <Alert
          severity="error"
          sx={{
            mt: 1.5,
            backgroundColor: 'rgba(244, 67, 54, 0.12)',
            color: 'rgba(255, 255, 255, 0.88)',
          }}
        >
          {error}
        </Alert>
      )}

      <Menu
        anchorEl={menuAnchorEl}
        open={Boolean(menuAnchorEl)}
        onClose={handleCloseMenu}
        PaperProps={{
          sx: {
            backgroundColor: '#171717',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            minWidth: 220,
          },
        }}
      >
        {EXPORT_FORMAT_OPTIONS.map((option) => (
          <MenuItem
            key={option.format}
            onClick={() => void handleDownload(option.format)}
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              gap: 0.25,
            }}
          >
            <Typography variant="body2">{option.label}</Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {option.description}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </Box>
  )
}
