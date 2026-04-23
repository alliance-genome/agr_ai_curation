import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import LinkIcon from '@mui/icons-material/Link'
import RestoreOutlinedIcon from '@mui/icons-material/RestoreOutlined'
import {
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  Divider,
  Stack,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import type { ReactNode } from 'react'

import {
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  type ChatHistorySessionSummary,
  type PersistedChatHistoryKind,
} from '@/services/chatHistoryApi'

import formatConversationTitle from './formatConversationTitle'

interface ConversationCardProps {
  children?: ReactNode
  isExpanded: boolean
  onRestore: () => void
  isSelected: boolean
  onDelete: () => void
  onRename: () => void
  onSelectChange: (selected: boolean) => void
  onToggleExpand: () => void
  session: ChatHistorySessionSummary
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return 'Unavailable'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return 'Unavailable'
  }

  return date.toLocaleString()
}

function getChatKindLabel(chatKind: PersistedChatHistoryKind): string {
  return chatKind === AGENT_STUDIO_CHAT_HISTORY_KIND
    ? 'Agent Studio chat'
    : 'AI assistant chat'
}

function getRestoreLabel(chatKind: PersistedChatHistoryKind): string {
  return chatKind === AGENT_STUDIO_CHAT_HISTORY_KIND
    ? 'Open in Agent Studio'
    : 'Resume chat'
}

export default function ConversationCard({
  children,
  isExpanded,
  onRestore,
  isSelected,
  onDelete,
  onRename,
  onSelectChange,
  onToggleExpand,
  session,
}: ConversationCardProps) {
  return (
    <Card
      data-testid={`conversation-card-${session.session_id}`}
      sx={{
        borderRadius: 3,
        border: '1px solid',
        borderColor: isSelected ? 'primary.main' : 'divider',
        boxShadow: 'none',
      }}
    >
      <CardContent sx={{ p: 0 }}>
        <Stack direction="row" spacing={2} alignItems="flex-start" sx={{ px: 2.5, py: 2.5 }}>
          <Checkbox
            checked={isSelected}
            inputProps={{
              'aria-label': `Select conversation ${formatConversationTitle(session)}`,
            }}
            onChange={(event) => onSelectChange(event.target.checked)}
            sx={{ mt: 0.25 }}
          />

          <Stack spacing={1.25} sx={{ flex: 1, minWidth: 0 }}>
            <Stack
              direction={{ xs: 'column', md: 'row' }}
              spacing={1}
              justifyContent="space-between"
              alignItems={{ md: 'flex-start' }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Stack
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  flexWrap="wrap"
                  useFlexGap
                >
                  <Typography variant="h6" sx={{ wordBreak: 'break-word' }}>
                    {formatConversationTitle(session)}
                  </Typography>
                  <Chip
                    label={getChatKindLabel(session.chat_kind)}
                    size="small"
                    variant="outlined"
                    sx={(theme) => ({
                      borderColor:
                        session.chat_kind === AGENT_STUDIO_CHAT_HISTORY_KIND
                          ? alpha(theme.palette.warning.main, 0.3)
                          : alpha(theme.palette.primary.main, 0.3),
                      bgcolor:
                        session.chat_kind === AGENT_STUDIO_CHAT_HISTORY_KIND
                          ? alpha(theme.palette.warning.main, 0.12)
                          : alpha(theme.palette.primary.main, 0.12),
                      color:
                        session.chat_kind === AGENT_STUDIO_CHAT_HISTORY_KIND
                          ? theme.palette.warning.dark
                          : theme.palette.primary.dark,
                    })}
                  />
                </Stack>
                <Typography color="text.secondary" variant="body2" sx={{ mt: 0.5 }}>
                  Updated {formatDateTime(session.recent_activity_at)}
                </Typography>
              </Box>

              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {session.active_document_id ? (
                  <Chip
                    icon={<LinkIcon />}
                    label="Document linked"
                    size="small"
                    variant="outlined"
                  />
                ) : null}
                <Chip
                  label={`Created ${formatDateTime(session.created_at)}`}
                  size="small"
                  variant="outlined"
                />
                {session.last_message_at ? (
                  <Chip
                    label={`Last message ${formatDateTime(session.last_message_at)}`}
                    size="small"
                    variant="outlined"
                  />
                ) : null}
              </Stack>
            </Stack>

            <Typography color="text.secondary" variant="caption">
              Session ID: {session.session_id}
            </Typography>

            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <Button onClick={onRestore} startIcon={<RestoreOutlinedIcon />} variant="contained">
                {getRestoreLabel(session.chat_kind)}
              </Button>
              <Button
                onClick={onToggleExpand}
                startIcon={isExpanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                variant={isExpanded ? 'contained' : 'outlined'}
              >
                {isExpanded ? 'Hide transcript' : 'Show transcript'}
              </Button>
              <Button onClick={onRename} startIcon={<EditOutlinedIcon />} variant="outlined">
                Rename
              </Button>
              <Button
                color="error"
                onClick={onDelete}
                startIcon={<DeleteOutlineIcon />}
                variant="outlined"
              >
                Delete
              </Button>
            </Stack>
          </Stack>
        </Stack>

        {isExpanded ? (
          <>
            <Divider />
            <Box sx={{ px: { xs: 2, md: 2.5 }, py: 2 }}>{children}</Box>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}
