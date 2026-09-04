/**
 * ClaudeRail
 *
 * The 44px rail that stays on the right edge of Agent Studio while AI Chat
 * panel is collapsed. It holds the Show control, an unread badge, a streaming
 * ring, and a vertical label.
 */

import { forwardRef } from 'react'
import { Badge, Box, CircularProgress, IconButton, Tooltip, Typography } from '@mui/material'
import { styled } from '@mui/material/styles'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'

export const CLAUDE_RAIL_WIDTH = 44

export function formatUnreadDescription(unreadCount: number): string {
  return `${unreadCount} new message${unreadCount === 1 ? '' : 's'} from AI Chat`
}

const RailRoot = styled(Box)(({ theme }) => ({
  width: CLAUDE_RAIL_WIDTH,
  flex: 'none',
  height: '100%',
  marginLeft: theme.spacing(1),
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  gap: theme.spacing(1.25),
  paddingTop: theme.spacing(0.75),
  paddingBottom: theme.spacing(0.75),
  backgroundColor: theme.palette.background.paper,
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.shape.borderRadius * 2,
}))

const ShowButton = styled(IconButton)(({ theme }) => ({
  width: 28,
  height: 28,
  borderRadius: 6,
  color: theme.palette.primary.main,
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: 1,
  },
}))

const VerticalLabel = styled(Typography)(({ theme }) => ({
  writingMode: 'vertical-rl',
  transform: 'rotate(180deg)',
  fontSize: '0.72rem',
  fontWeight: 500,
  letterSpacing: '0.04em',
  color: theme.palette.text.secondary,
  userSelect: 'none',
}))

const VisuallyHidden = styled('span')({
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
})

export interface ClaudeRailProps {
  /** DOM id of the collapsed panel container (aria-controls) */
  panelId: string
  unreadCount: number
  isStreaming: boolean
  onShow: () => void
}

const UNREAD_DESCRIPTION_ID = 'agent-studio-claude-rail-unread'

const ClaudeRail = forwardRef<HTMLButtonElement, ClaudeRailProps>(function ClaudeRail(
  { panelId, unreadCount, isStreaming, onShow },
  ref,
) {
  const hasUnread = unreadCount > 0

  return (
    <RailRoot>
      <Badge
        variant="dot"
        color="warning"
        overlap="circular"
        invisible={!hasUnread}
        sx={{
          '& .MuiBadge-badge': {
            width: 8,
            height: 8,
            minWidth: 8,
            border: '2px solid',
            borderColor: 'background.paper',
            top: 3,
            right: 3,
          },
        }}
      >
        <Tooltip title="Show AI Chat (Ctrl+.)" placement="left">
          <ShowButton
            ref={ref}
            size="small"
            aria-label="Show AI Chat"
            aria-expanded="false"
            aria-controls={panelId}
            aria-describedby={hasUnread ? UNREAD_DESCRIPTION_ID : undefined}
            onClick={onShow}
          >
            <AutoAwesomeIcon sx={{ fontSize: 18 }} />
          </ShowButton>
        </Tooltip>
      </Badge>
      {hasUnread && (
        <VisuallyHidden id={UNREAD_DESCRIPTION_ID}>{formatUnreadDescription(unreadCount)}</VisuallyHidden>
      )}
      {isStreaming && (
        <CircularProgress size={14} thickness={4} aria-label="AI Chat is responding" />
      )}
      <VerticalLabel aria-hidden="true">AI Chat</VerticalLabel>
      <Box sx={{ flex: 1 }} />
      <ChevronLeftIcon aria-hidden="true" sx={{ fontSize: 20, color: 'text.secondary' }} />
    </RailRoot>
  )
})

export default ClaudeRail
