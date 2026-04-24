import { Box } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import type { Theme } from '@mui/material/styles'

import EvidenceCard from '@/components/Chat/EvidenceCard'
import FileDownloadCard, { type FileInfo } from '@/components/Chat/FileDownloadCard'
import FlowStepEvidenceCard from '@/components/Chat/FlowStepEvidenceCard'
import type { EvidenceRecord } from '@/features/curation/types'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

export type TranscriptMessageRole = 'user' | 'assistant' | 'flow'
type TranscriptFileFormat = 'csv' | 'tsv' | 'json'

export interface TranscriptMessageRecord {
  id?: string
  role: TranscriptMessageRole
  content: string
  timestamp?: string | Date | null
  type?: 'text' | 'file_download'
  fileData?: FileInfo | null
  flowStepEvidence?: FlowStepEvidenceDetails | null
  evidenceRecords?: EvidenceRecord[] | null
}

interface TranscriptMessageProps {
  message: TranscriptMessageRecord
}

function assertUnreachable(value: never, context: string): never {
  throw new Error(`Unhandled ${context}: ${String(value)}`)
}

function parseTranscriptFileFormat(format: string): TranscriptFileFormat {
  switch (format.toLowerCase()) {
    case 'csv':
      return 'csv'
    case 'tsv':
      return 'tsv'
    case 'json':
      return 'json'
    default:
      throw new Error(`Unsupported transcript file format: ${format}`)
  }
}

function getRoleLabel(role: TranscriptMessageRole): string {
  switch (role) {
    case 'assistant':
      return 'AI Assistant'
    case 'flow':
      return 'Flow'
    case 'user':
      return 'You'
  }

  return assertUnreachable(role, 'transcript message role')
}

function getBubbleStyles(
  role: TranscriptMessageRole,
  hasEvidenceCard: boolean,
  theme: Theme,
): {
  alignSelf: 'flex-start' | 'flex-end'
  maxWidth: string
  backgroundColor: string
  color: string
  borderRadius: string
  boxShadow: string
} {
  const messageShadow = `0 1px 3px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.28 : 0.12)}`

  switch (role) {
    case 'user':
      return {
        alignSelf: 'flex-end',
        maxWidth: '75%',
        backgroundColor: theme.palette.mode === 'dark' ? theme.palette.grey[800] : theme.palette.grey[100],
        color: theme.palette.text.primary,
        borderRadius: '18px 18px 4px 18px',
        boxShadow: messageShadow,
      }
    case 'flow':
      return {
        alignSelf: 'flex-start',
        maxWidth: '85%',
        backgroundColor: theme.palette.mode === 'dark'
          ? alpha(theme.palette.common.white, 0.08)
          : alpha(theme.palette.text.primary, 0.06),
        color: theme.palette.text.primary,
        borderRadius: '18px 18px 18px 4px',
        boxShadow: messageShadow,
      }
    case 'assistant':
      return {
        alignSelf: 'flex-start',
        maxWidth: '85%',
        backgroundColor: theme.palette.secondary.main,
        color: theme.palette.secondary.contrastText,
        borderRadius: hasEvidenceCard ? '18px 18px 4px 4px' : '18px 18px 18px 4px',
        boxShadow: messageShadow,
      }
  }

  return assertUnreachable(role, 'transcript message role')
}

export default function TranscriptMessage({ message }: TranscriptMessageProps) {
  const theme = useTheme()

  if (message.role === 'flow' && message.flowStepEvidence) {
    return (
      <FlowStepEvidenceCard
        containerTestId="transcript-flow-step-evidence-card"
        details={message.flowStepEvidence}
        emptyStateTestId="transcript-flow-step-evidence-empty"
        interactionMode="readOnly"
      />
    )
  }

  const hasEvidenceCard = (message.evidenceRecords?.length ?? 0) > 0
  const bubbleStyles = getBubbleStyles(message.role, hasEvidenceCard, theme)
  const transcriptFile =
    message.type === 'file_download' && message.fileData
      ? (() => {
          parseTranscriptFileFormat(message.fileData.format)
          return message.fileData
        })()
      : null

  return (
    <Box
      data-testid={`transcript-message-${message.role}`}
      sx={{
        alignSelf: bubbleStyles.alignSelf,
        maxWidth: bubbleStyles.maxWidth,
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
      }}
    >
      <Box
        sx={{
          backgroundColor: bubbleStyles.backgroundColor,
          color: bubbleStyles.color,
          padding: '1rem 1.5rem',
          borderRadius: bubbleStyles.borderRadius,
          boxShadow: bubbleStyles.boxShadow,
        }}
      >
        <Box
          sx={{
            fontSize: '0.75rem',
            fontWeight: 600,
            opacity: 0.8,
            mb: '0.5rem',
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
          }}
        >
          {getRoleLabel(message.role)}
        </Box>

        <Box
          sx={{
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {transcriptFile ? (
            <FileDownloadCard
              allowDownload={false}
              cardTestId="transcript-file-card"
              file={transcriptFile}
            />
          ) : (
            message.content
          )}
        </Box>
      </Box>

      {message.role === 'assistant' && hasEvidenceCard ? (
        <EvidenceCard
          containerTestId="transcript-evidence-card"
          headerIconTestId="transcript-evidence-card-header-icon"
          evidenceRecords={message.evidenceRecords ?? []}
          interactionMode="readOnly"
          quoteTestId="transcript-evidence-quote"
        />
      ) : null}
    </Box>
  )
}
