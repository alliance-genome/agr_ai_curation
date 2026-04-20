import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import LinkIcon from '@mui/icons-material/Link'
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
import type { ReactNode } from 'react'

import type { ChatHistorySessionSummary } from '@/services/chatHistoryApi'

import formatConversationTitle from './formatConversationTitle'

interface ConversationCardProps {
  children?: ReactNode
  isExpanded: boolean
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

export default function ConversationCard({
  children,
  isExpanded,
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
                <Typography variant="h6" sx={{ wordBreak: 'break-word' }}>
                  {formatConversationTitle(session)}
                </Typography>
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
