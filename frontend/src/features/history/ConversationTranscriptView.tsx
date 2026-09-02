import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import RestoreOutlinedIcon from '@mui/icons-material/RestoreOutlined'
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'

import { DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT } from '@/lib/chatCacheKeys'
import {
  buildRestorableChatMessages,
  type ChatHistoryActiveDocument,
  type ChatHistorySessionSummary,
} from '@/services/chatHistoryApi'

import formatConversationTitle from './formatConversationTitle'
import { formatExactTime } from './formatRelativeTime'
import { getRestoreLabel, pluralize } from './historyLabels'
import { HISTORY_MONO_FONT_FAMILY, HISTORY_PANEL_INDENT, HISTORY_TRANSCRIPT_MAX_HEIGHT } from './historyLayout'
import TranscriptMessage, { type TranscriptMessageRecord } from './TranscriptMessage'
import { useChatHistoryDetailQuery } from './useChatHistoryQuery'

interface ConversationTranscriptViewProps {
  expanded: boolean
  onCopySessionId: () => void
  onRestore: () => void
  session: ChatHistorySessionSummary
}

function describeActiveDocument(document: ChatHistoryActiveDocument): string {
  const parts = [document.filename ?? document.id]

  if (document.chunk_count != null) {
    parts.push(`${document.chunk_count.toLocaleString()} ${pluralize(document.chunk_count, 'chunk')}`)
  }

  if (document.vector_count != null) {
    parts.push(`${document.vector_count.toLocaleString()} ${pluralize(document.vector_count, 'vector')}`)
  }

  return parts.join(' · ')
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <Typography component="span" sx={{ fontSize: '12px', color: 'text.secondary' }}>
      {label}{' '}
      <Box component="b" sx={{ color: 'text.primary', fontWeight: 500 }}>{value}</Box>
    </Typography>
  )
}

export default function ConversationTranscriptView({
  expanded,
  onCopySessionId,
  onRestore,
  session,
}: ConversationTranscriptViewProps) {
  const detailQuery = useChatHistoryDetailQuery(
    {
      sessionId: session.session_id,
      messageLimit: DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
    },
    {
      enabled: expanded,
    },
  )

  if (!expanded) {
    return null
  }

  const title = formatConversationTitle(session)
  const restoreLabel = getRestoreLabel(session.chat_kind)
  const detail = detailQuery.data
  const transcriptMessages: TranscriptMessageRecord[] = detail
    ? buildRestorableChatMessages(detail.messages, { onUnknownRole: 'throw' })
    : []

  return (
    <Box
      aria-label={`Transcript for ${title}`}
      role="region"
      sx={{
        borderTop: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.default',
        pt: 1.5,
        pb: 1.5,
        pr: '14px',
        pl: `${HISTORY_PANEL_INDENT}px`,
        '@media (max-width: 720px)': { pl: '14px' },
      }}
    >
      <Box sx={{ maxHeight: HISTORY_TRANSCRIPT_MAX_HEIGHT, overflowY: 'auto', pr: '6px' }}>
        <Stack spacing={1.25}>
          <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ columnGap: 2, rowGap: 0.75, alignItems: 'center' }}>
            <MetaItem label="Created" value={formatExactTime(session.created_at)} />
            <MetaItem label="Last message" value={formatExactTime(session.last_message_at)} />
            <Typography component="span" sx={{ fontSize: '12px', color: 'text.secondary', display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
              Session ID{' '}
              <Box
                component="code"
                sx={{ fontFamily: HISTORY_MONO_FONT_FAMILY, fontSize: '11.5px', color: 'text.primary', wordBreak: 'break-all' }}
              >
                {session.session_id}
              </Box>
              <Tooltip title="Copy session ID">
                <IconButton aria-label="Copy session ID" onClick={onCopySessionId} size="small" sx={{ p: 0.25 }}>
                  <ContentCopyOutlinedIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </Tooltip>
            </Typography>
          </Stack>

          {detail?.active_document ? (
            <Box>
              <Box
                component="span"
                sx={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 0.75,
                  border: '1px solid',
                  borderColor: 'divider',
                  borderRadius: '6px',
                  px: 1,
                  py: '3px',
                  fontSize: '12px',
                  bgcolor: 'background.paper',
                }}
              >
                <DescriptionOutlinedIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
                {describeActiveDocument(detail.active_document)}
              </Box>
            </Box>
          ) : null}

          {detailQuery.isLoading ? (
            <Stack direction="row" spacing={1.5} alignItems="center">
              <CircularProgress size={16} />
              <Typography color="text.secondary" sx={{ fontSize: '12.5px' }}>
                Loading transcript…
              </Typography>
            </Stack>
          ) : null}

          {detailQuery.error ? <Alert severity="error">{detailQuery.error.message}</Alert> : null}

          {detail && transcriptMessages.length === 0 ? (
            <Alert severity="info">This conversation does not have any stored transcript messages yet.</Alert>
          ) : null}

          {transcriptMessages.length > 0 ? (
            <Stack spacing="10px">
              {transcriptMessages.map((message, index) => (
                <TranscriptMessage
                  key={message.id ?? `${message.role}-${message.timestamp ?? 'unknown'}-${index}`}
                  message={message}
                />
              ))}
            </Stack>
          ) : null}
        </Stack>
      </Box>

      <Stack
        direction="row"
        alignItems="center"
        spacing={1.25}
        sx={{ mt: 1.25, pt: 1.25, borderTop: '1px solid', borderColor: 'divider' }}
      >
        <Typography color="text.secondary" sx={{ fontSize: '12.5px', flex: 1, minWidth: 0 }}>
          {detail?.next_message_cursor
            ? `Showing the newest ${transcriptMessages.length} ${pluralize(transcriptMessages.length, 'message')}. Resume the chat to read the full conversation.`
            : ''}
        </Typography>
        <Button
          onClick={onRestore}
          size="small"
          startIcon={<RestoreOutlinedIcon />}
          sx={{ textTransform: 'none', flex: 'none' }}
          variant="contained"
        >
          {restoreLabel}
        </Button>
      </Stack>
    </Box>
  )
}
