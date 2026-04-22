import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from '@mui/material'

import { DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT } from '@/lib/chatCacheKeys'
import { buildRestorableChatMessages } from '@/services/chatHistoryApi'

import TranscriptMessage, { type TranscriptMessageRecord } from './TranscriptMessage'
import { useChatHistoryDetailQuery } from './useChatHistoryQuery'

interface ConversationTranscriptViewProps {
  expanded: boolean
  sessionId: string
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

  const transcriptMessages: TranscriptMessageRecord[] = buildRestorableChatMessages(
    detail.messages,
    { onUnknownRole: 'throw' },
  )

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

      {transcriptMessages.length === 0 ? (
        <Alert severity="info">This conversation does not have any stored transcript messages yet.</Alert>
      ) : (
        <Stack spacing={1.5}>
          {transcriptMessages.map((message) => (
            <TranscriptMessage
              key={message.id ?? `${message.role}-${message.timestamp ?? 'unknown'}`}
              message={message}
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
