import { Box } from '@mui/material'

import EvidenceCard from '@/components/Chat/EvidenceCard'
import FileDownloadCard, { type FileInfo } from '@/components/Chat/FileDownloadCard'
import FlowStepEvidenceCard from '@/components/Chat/FlowStepEvidenceCard'
import type { EvidenceRecord } from '@/features/curation/types'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

export type TranscriptMessageRole = 'user' | 'assistant' | 'flow'

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
): {
  alignSelf: 'flex-start' | 'flex-end'
  maxWidth: string
  backgroundColor: string
  borderRadius: string
} {
  switch (role) {
    case 'user':
      return {
        alignSelf: 'flex-end',
        maxWidth: '75%',
        backgroundColor: '#424242',
        borderRadius: '18px 18px 4px 18px',
      }
    case 'flow':
      return {
        alignSelf: 'flex-start',
        maxWidth: '85%',
        backgroundColor: 'rgba(255, 255, 255, 0.08)',
        borderRadius: '18px 18px 18px 4px',
      }
    case 'assistant':
      return {
        alignSelf: 'flex-start',
        maxWidth: '85%',
        backgroundColor: '#1565c0',
        borderRadius: hasEvidenceCard ? '18px 18px 4px 4px' : '18px 18px 18px 4px',
      }
  }

  return assertUnreachable(role, 'transcript message role')
}

export default function TranscriptMessage({ message }: TranscriptMessageProps) {
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
  const bubbleStyles = getBubbleStyles(message.role, hasEvidenceCard)

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
          color: '#ffffff',
          padding: '1rem 1.5rem',
          borderRadius: bubbleStyles.borderRadius,
          boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
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
          {message.type === 'file_download' && message.fileData ? (
            <FileDownloadCard
              allowDownload={false}
              cardTestId="transcript-file-card"
              file={message.fileData}
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
