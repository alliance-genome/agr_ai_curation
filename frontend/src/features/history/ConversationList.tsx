import { Box, Checkbox, Skeleton } from '@mui/material'
import { alpha } from '@mui/material/styles'

import type { ChatHistorySessionSummary } from '@/services/chatHistoryApi'

import ConversationRow from './ConversationRow'
import ConversationTranscriptView from './ConversationTranscriptView'
import {
  HISTORY_KIND_TAG_WIDTH,
  HISTORY_LIST_HEADER_HEIGHT,
  HISTORY_ROW_GRID_COLUMNS,
  HISTORY_ROW_MIN_HEIGHT,
} from './historyLayout'

interface ConversationListProps {
  expandedSessionIds: Set<string>
  isLoading: boolean
  onCopySessionId: (session: ChatHistorySessionSummary) => void
  onDeleteSession: (session: ChatHistorySessionSummary) => void
  onRenameSession: (session: ChatHistorySessionSummary) => void
  onRestoreSession: (session: ChatHistorySessionSummary) => void
  onSelectSession: (sessionId: string, selected: boolean) => void
  onToggleExpandSession: (sessionId: string) => void
  onToggleSelectAll: (selected: boolean) => void
  selectedSessionIds: Set<string>
  sessions: ChatHistorySessionSummary[]
}

const SKELETON_ROW_COUNT = 6

function SkeletonRows() {
  return (
    <Box aria-busy="true" aria-label="Loading conversations" role="status">
      {Array.from({ length: SKELETON_ROW_COUNT }, (_value, index) => (
        <Box
          key={index}
          sx={{
            display: 'grid',
            gridTemplateColumns: HISTORY_ROW_GRID_COLUMNS,
            alignItems: 'center',
            minHeight: HISTORY_ROW_MIN_HEIGHT,
            pl: '10px',
            pr: 1,
            borderBottom: '1px solid',
            borderColor: 'divider',
            '&:last-of-type': { borderBottom: 0 },
          }}
        >
          <Skeleton height={16} variant="rounded" width={16} />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <Skeleton height={18} variant="rounded" width={HISTORY_KIND_TAG_WIDTH} />
            <Skeleton height={14} variant="rounded" width={`${45 + (index * 13) % 40}%`} />
          </Box>
          <Skeleton height={12} sx={{ justifySelf: 'end', mr: '10px' }} variant="rounded" width={56} />
          <span />
        </Box>
      ))}
    </Box>
  )
}

export default function ConversationList({
  expandedSessionIds,
  isLoading,
  onCopySessionId,
  onDeleteSession,
  onRenameSession,
  onRestoreSession,
  onSelectSession,
  onToggleExpandSession,
  onToggleSelectAll,
  selectedSessionIds,
  sessions,
}: ConversationListProps) {
  const selectedVisibleCount = sessions.filter((session) => selectedSessionIds.has(session.session_id)).length
  const allSelected = sessions.length > 0 && selectedVisibleCount === sessions.length
  const someSelected = selectedVisibleCount > 0 && !allSelected

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        overflow: 'auto',
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 2,
        bgcolor: 'background.paper',
      }}
    >
      <Box
        sx={(theme) => ({
          position: 'sticky',
          top: 0,
          zIndex: 1,
          display: 'grid',
          gridTemplateColumns: HISTORY_ROW_GRID_COLUMNS,
          alignItems: 'center',
          height: HISTORY_LIST_HEADER_HEIGHT,
          pl: '10px',
          pr: 1,
          fontSize: '11px',
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: 'text.secondary',
          bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.12 : 0.06),
          backdropFilter: 'blur(4px)',
          borderBottom: '1px solid',
          borderColor: 'divider',
        })}
      >
        <Checkbox
          checked={allSelected}
          disabled={isLoading || sessions.length === 0}
          indeterminate={someSelected}
          inputProps={{
            'aria-label': 'Select all conversations',
            'aria-checked': someSelected ? 'mixed' : allSelected,
          }}
          onChange={(event) => onToggleSelectAll(event.target.checked)}
          size="small"
          sx={{ p: 0.5, justifySelf: 'start' }}
        />
        <span>Conversation</span>
        <Box component="span" sx={{ textAlign: 'right', pr: '10px' }}>Activity</Box>
        <span />
      </Box>

      {isLoading ? (
        <SkeletonRows />
      ) : (
        <Box aria-label="Conversations" component="ul" role="list" sx={{ m: 0, p: 0 }}>
          {sessions.map((session) => {
            const isExpanded = expandedSessionIds.has(session.session_id)

            return (
              <ConversationRow
                key={session.session_id}
                isExpanded={isExpanded}
                isSelected={selectedSessionIds.has(session.session_id)}
                onCopySessionId={() => onCopySessionId(session)}
                onDelete={() => onDeleteSession(session)}
                onRename={() => onRenameSession(session)}
                onRestore={() => onRestoreSession(session)}
                onSelectChange={(selected) => onSelectSession(session.session_id, selected)}
                onToggleExpand={() => onToggleExpandSession(session.session_id)}
                session={session}
              >
                <ConversationTranscriptView
                  expanded={isExpanded}
                  onCopySessionId={() => onCopySessionId(session)}
                  onRestore={() => onRestoreSession(session)}
                  session={session}
                />
              </ConversationRow>
            )
          })}
        </Box>
      )}
    </Box>
  )
}
