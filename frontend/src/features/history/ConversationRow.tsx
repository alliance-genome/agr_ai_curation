import ContentCopyOutlinedIcon from '@mui/icons-material/ContentCopyOutlined'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import LinkIcon from '@mui/icons-material/Link'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import RestoreOutlinedIcon from '@mui/icons-material/RestoreOutlined'
import {
  Box,
  Checkbox,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
  Typography,
} from '@mui/material'
import { alpha } from '@mui/material/styles'
import { useId, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'

import {
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  type ChatHistorySessionSummary,
} from '@/services/chatHistoryApi'

import formatConversationTitle, { hasConversationTitle } from './formatConversationTitle'
import formatRelativeTime, { formatExactTime } from './formatRelativeTime'
import { getChatKindTagLabel, getRestoreLabel } from './historyLabels'
import {
  HISTORY_KIND_TAG_WIDTH,
  HISTORY_MONO_FONT_FAMILY,
  HISTORY_ROW_GRID_COLUMNS,
  HISTORY_ROW_MIN_HEIGHT,
  HISTORY_VISUALLY_HIDDEN,
} from './historyLayout'

interface ConversationRowProps {
  children?: ReactNode
  isExpanded: boolean
  isSelected: boolean
  onCopySessionId: () => void
  onDelete: () => void
  onRename: () => void
  onRestore: () => void
  onSelectChange: (selected: boolean) => void
  onToggleExpand: () => void
  session: ChatHistorySessionSummary
}

const NARROW_ROW_QUERY = '@media (max-width: 720px)'

export default function ConversationRow({
  children,
  isExpanded,
  isSelected,
  onCopySessionId,
  onDelete,
  onRename,
  onRestore,
  onSelectChange,
  onToggleExpand,
  session,
}: ConversationRowProps) {
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null)
  const idPrefix = useId()
  const titleId = `${idPrefix}-title`
  const kindId = `${idPrefix}-kind`
  const documentId = `${idPrefix}-document`
  const timeId = `${idPrefix}-time`
  const menuId = `${idPrefix}-menu`

  const title = formatConversationTitle(session)
  const isUntitled = !hasConversationTitle(session)
  const isStudio = session.chat_kind === AGENT_STUDIO_CHAT_HISTORY_KIND
  const restoreLabel = getRestoreLabel(session.chat_kind)
  const menuOpen = Boolean(menuAnchor)

  const closeMenu = () => setMenuAnchor(null)

  const runMenuAction = (action: () => void) => () => {
    closeMenu()
    action()
  }

  const handleRowKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      onToggleExpand()
      return
    }

    if (event.key === ' ') {
      event.preventDefault()
      onSelectChange(!isSelected)
    }
  }

  const openMenu = (event: MouseEvent<HTMLButtonElement>) => {
    setMenuAnchor(event.currentTarget)
  }

  return (
    <Box
      component="li"
      data-testid={`conversation-row-${session.session_id}`}
      sx={{
        listStyle: 'none',
        position: 'relative',
        borderBottom: '1px solid',
        borderColor: 'divider',
        bgcolor: isSelected ? 'action.selected' : 'transparent',
        '&:last-of-type': { borderBottom: 0 },
        '&::before': isSelected
          ? {
              content: '""',
              position: 'absolute',
              left: 0,
              top: 0,
              bottom: 0,
              width: 3,
              zIndex: 1,
              bgcolor: 'primary.main',
            }
          : undefined,
      }}
    >
      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: HISTORY_ROW_GRID_COLUMNS,
          alignItems: 'center',
          minHeight: HISTORY_ROW_MIN_HEIGHT,
          pl: '10px',
          pr: 1,
          '&:hover': { bgcolor: 'action.hover' },
          [NARROW_ROW_QUERY]: {
            gridTemplateColumns: '36px minmax(0, 1fr) 84px',
          },
        }}
      >
        <Checkbox
          checked={isSelected}
          inputProps={{ 'aria-label': `Select ${title}` }}
          onChange={(event) => onSelectChange(event.target.checked)}
          size="small"
          sx={{ p: 0.5, justifySelf: 'start' }}
        />

        <Box
          aria-describedby={`${kindId}${session.active_document_id ? ` ${documentId}` : ''} ${timeId}`}
          aria-expanded={isExpanded}
          aria-labelledby={titleId}
          onClick={onToggleExpand}
          onKeyDown={handleRowKeyDown}
          role="button"
          tabIndex={0}
          sx={{
            gridColumn: '2 / 4',
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) 96px',
            alignItems: 'center',
            minHeight: HISTORY_ROW_MIN_HEIGHT,
            minWidth: 0,
            cursor: 'pointer',
            outline: 'none',
            '&:focus-visible': {
              outline: '2px solid',
              outlineColor: 'primary.main',
              outlineOffset: -2,
              borderRadius: '4px',
            },
            [NARROW_ROW_QUERY]: {
              gridColumn: '2 / 3',
              gridTemplateColumns: 'minmax(0, 1fr)',
              py: 0.5,
            },
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
            <Box
              component="span"
              id={kindId}
              sx={(theme) => ({
                flex: 'none',
                width: HISTORY_KIND_TAG_WIDTH,
                textAlign: 'center',
                fontSize: '10.5px',
                fontWeight: 600,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                lineHeight: 1.5,
                px: '6px',
                py: '2px',
                borderRadius: '4px',
                bgcolor: alpha(
                  isStudio ? theme.palette.warning.main : theme.palette.primary.main,
                  theme.palette.mode === 'dark' ? 0.16 : 0.12,
                ),
                color: isStudio
                  ? (theme.palette.mode === 'dark' ? theme.palette.warning.light : theme.palette.warning.dark)
                  : (theme.palette.mode === 'dark' ? theme.palette.primary.light : theme.palette.primary.dark),
              })}
            >
              {getChatKindTagLabel(session.chat_kind)}
            </Box>

            <Typography
              component="span"
              id={titleId}
              title={title}
              sx={{
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontSize: '0.875rem',
                fontWeight: isUntitled ? 400 : 500,
                fontStyle: isUntitled ? 'italic' : 'normal',
                color: isUntitled ? 'text.secondary' : 'text.primary',
              }}
            >
              {title}
            </Typography>

            {session.active_document_id ? (
              <Tooltip title="Document linked">
                <Box
                  component="span"
                  id={documentId}
                  sx={{ display: 'inline-flex', color: 'text.secondary', flex: 'none' }}
                >
                  <LinkIcon sx={{ fontSize: 15 }} />
                  <Box component="span" sx={HISTORY_VISUALLY_HIDDEN}>Document linked</Box>
                </Box>
              </Tooltip>
            ) : null}
          </Box>

          <Tooltip title={formatExactTime(session.recent_activity_at)}>
            <Typography
              component="span"
              id={timeId}
              sx={{
                color: 'text.secondary',
                fontSize: '12.5px',
                fontFamily: HISTORY_MONO_FONT_FAMILY,
                fontVariantNumeric: 'tabular-nums',
                textAlign: 'right',
                pr: '10px',
                whiteSpace: 'nowrap',
                [NARROW_ROW_QUERY]: {
                  textAlign: 'left',
                  pl: `${HISTORY_KIND_TAG_WIDTH + 10}px`,
                  pr: 0,
                },
              }}
            >
              {formatRelativeTime(session.recent_activity_at)}
            </Typography>
          </Tooltip>
        </Box>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: '2px' }}>
          <Tooltip title={restoreLabel}>
            <IconButton aria-label={restoreLabel} color="primary" onClick={onRestore} size="small">
              <RestoreOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={isExpanded ? 'Hide transcript' : 'Show transcript'}>
            <IconButton
              aria-expanded={isExpanded}
              aria-label={isExpanded ? 'Hide transcript' : 'Show transcript'}
              onClick={onToggleExpand}
              size="small"
            >
              <ExpandMoreIcon
                fontSize="small"
                sx={{
                  transform: isExpanded ? 'rotate(180deg)' : 'none',
                  transition: 'transform 120ms ease-out',
                }}
              />
            </IconButton>
          </Tooltip>
          <IconButton
            aria-controls={menuOpen ? menuId : undefined}
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            aria-label={`More actions for ${title}`}
            onClick={openMenu}
            size="small"
          >
            <MoreVertIcon fontSize="small" />
          </IconButton>
        </Box>
      </Box>

      <Menu
        anchorEl={menuAnchor}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        id={menuId}
        MenuListProps={{ 'aria-label': `Actions for ${title}`, dense: true }}
        onClose={closeMenu}
        open={menuOpen}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <MenuItem onClick={runMenuAction(onRename)}>
          <ListItemIcon><EditOutlinedIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem onClick={runMenuAction(onCopySessionId)}>
          <ListItemIcon><ContentCopyOutlinedIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Copy session ID</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={runMenuAction(onDelete)} sx={{ color: 'error.main' }}>
          <ListItemIcon><DeleteOutlineIcon color="error" fontSize="small" /></ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {isExpanded ? children : null}
    </Box>
  )
}
