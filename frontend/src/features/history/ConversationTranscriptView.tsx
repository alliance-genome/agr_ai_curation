import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material'

import type { FileInfo } from '@/components/Chat/FileDownloadCard'
import type { EvidenceRecord } from '@/features/curation/types'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'
import { DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT } from '@/lib/chatCacheKeys'
import type { ChatHistoryMessage } from '@/services/chatHistoryApi'

import TranscriptMessage, { type TranscriptMessageRecord } from './TranscriptMessage'
import { useChatHistoryDetailQuery } from './useChatHistoryQuery'

interface ConversationTranscriptViewProps {
  expanded: boolean
  sessionId: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function readNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function isEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.entity === 'string' &&
    typeof value.verified_quote === 'string' &&
    typeof value.page === 'number' &&
    Number.isFinite(value.page) &&
    typeof value.section === 'string' &&
    typeof value.chunk_id === 'string'
  )
}

function extractEvidenceRecords(value: unknown): EvidenceRecord[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value.filter(isEvidenceRecord)
}

function extractPayloadRecords(
  payload: Record<string, unknown> | null,
): Array<Record<string, unknown>> {
  if (!payload) {
    return []
  }

  const records = [payload]
  const nestedValues = [payload.details, payload.data, payload.file, payload.fileData]

  nestedValues.forEach((value) => {
    if (isRecord(value)) {
      records.push(value)
    }
  })

  return records
}

function extractFileData(payload: Record<string, unknown> | null): FileInfo | null {
  for (const candidate of extractPayloadRecords(payload)) {
    const fileId = readString(candidate.file_id)
    const filename = readString(candidate.filename)
    const format = readString(candidate.format)
    const downloadUrl = readString(candidate.download_url)

    if (!fileId || !filename || !format || !downloadUrl) {
      continue
    }

    return {
      file_id: fileId,
      filename,
      format,
      download_url: downloadUrl,
      size_bytes: readNumber(candidate.size_bytes) ?? undefined,
      mime_type: readString(candidate.mime_type) ?? undefined,
      created_at: readString(candidate.created_at) ?? undefined,
    }
  }

  return null
}

function extractFlowStepEvidence(payload: Record<string, unknown> | null): FlowStepEvidenceDetails | null {
  for (const candidate of extractPayloadRecords(payload)) {
    const flowId = readString(candidate.flow_id)
    const flowRunId = readString(candidate.flow_run_id)
    const step = readNumber(candidate.step)
    const evidenceCount = readNumber(candidate.evidence_count)
    const totalEvidenceRecords = readNumber(candidate.total_evidence_records)

    if (!flowId || !flowRunId || step == null || evidenceCount == null || totalEvidenceRecords == null) {
      continue
    }

    return {
      flow_id: flowId,
      flow_name: readString(candidate.flow_name),
      flow_run_id: flowRunId,
      step,
      tool_name: readString(candidate.tool_name),
      agent_id: readString(candidate.agent_id),
      agent_name: readString(candidate.agent_name),
      evidence_records: extractEvidenceRecords(
        candidate.evidence_records ?? candidate.evidence_preview,
      ),
      evidence_count: evidenceCount,
      total_evidence_records: totalEvidenceRecords,
    }
  }

  return null
}

function toTranscriptRole(role: string): TranscriptMessageRecord['role'] {
  if (role === 'user' || role === 'assistant' || role === 'flow') {
    return role
  }

  return 'assistant'
}

function toTranscriptMessage(message: ChatHistoryMessage): TranscriptMessageRecord {
  const payload = isRecord(message.payload_json) ? message.payload_json : null
  const evidenceRecords = extractEvidenceRecords(payload?.evidence_records)
  const fileData = extractFileData(payload)
  const flowStepEvidence = extractFlowStepEvidence(payload)

  if (message.message_type === 'file_download' && fileData) {
    return {
      id: message.message_id,
      role: 'assistant',
      content: message.content,
      timestamp: message.created_at,
      type: 'file_download',
      fileData,
    }
  }

  if ((message.message_type === 'flow_step_evidence' || message.role === 'flow') && flowStepEvidence) {
    return {
      id: message.message_id,
      role: 'flow',
      content: message.content,
      timestamp: message.created_at,
      flowStepEvidence,
      evidenceRecords: flowStepEvidence.evidence_records,
    }
  }

  return {
    id: message.message_id,
    role: toTranscriptRole(message.role),
    content: message.content,
    timestamp: message.created_at,
    evidenceRecords,
  }
}

function formatNumber(value?: number | null): string | null {
  if (value == null) {
    return null
  }

  return value.toLocaleString()
}

export default function ConversationTranscriptView({
  expanded,
  sessionId,
}: ConversationTranscriptViewProps) {
  const detailQuery = useChatHistoryDetailQuery(
    {
      sessionId,
      messageLimit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
    },
    {
      enabled: expanded,
    },
  )

  if (!expanded) {
    return null
  }

  if (detailQuery.isLoading) {
    return (
      <Stack direction="row" spacing={1.5} alignItems="center">
        <CircularProgress size={18} />
        <Typography color="text.secondary" variant="body2">
          Loading transcript…
        </Typography>
      </Stack>
    )
  }

  if (detailQuery.error) {
    return <Alert severity="error">{detailQuery.error.message}</Alert>
  }

  const detail = detailQuery.data
  if (!detail) {
    return null
  }

  return (
    <Stack spacing={2}>
      {detail.active_document ? (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Active document
          </Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Chip label={detail.active_document.filename ?? detail.active_document.id} size="small" />
            {formatNumber(detail.active_document.chunk_count) ? (
              <Chip
                label={`${formatNumber(detail.active_document.chunk_count)} chunks`}
                size="small"
                variant="outlined"
              />
            ) : null}
            {formatNumber(detail.active_document.vector_count) ? (
              <Chip
                label={`${formatNumber(detail.active_document.vector_count)} vectors`}
                size="small"
                variant="outlined"
              />
            ) : null}
          </Stack>
        </Box>
      ) : null}

      <Divider />

      {detail.messages.length === 0 ? (
        <Alert severity="info">This conversation does not have any stored transcript messages yet.</Alert>
      ) : (
        <Stack spacing={1.5}>
          {detail.messages.map((message) => (
            <TranscriptMessage
              key={message.message_id}
              message={toTranscriptMessage(message)}
            />
          ))}
        </Stack>
      )}

      {detail.next_message_cursor ? (
        <Typography color="text.secondary" variant="caption">
          Showing the newest stored transcript messages for this conversation.
        </Typography>
      ) : null}
    </Stack>
  )
}
