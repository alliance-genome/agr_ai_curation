import { Stack, Typography } from '@mui/material'

import type { ChatHistorySessionSummary } from '@/services/chatHistoryApi'

import ConversationCard from './ConversationCard'
import ConversationTranscriptView from './ConversationTranscriptView'

interface ConversationListProps {
  expandedSessionIds: Set<string>
  onDeleteSession: (session: ChatHistorySessionSummary) => void
  onRenameSession: (session: ChatHistorySessionSummary) => void
  onSelectSession: (sessionId: string, selected: boolean) => void
  onToggleExpandSession: (sessionId: string) => void
  searchQuery: string
  selectedSessionIds: Set<string>
  sessions: ChatHistorySessionSummary[]
}

export default function ConversationList({
  expandedSessionIds,
  onDeleteSession,
  onRenameSession,
  onSelectSession,
  onToggleExpandSession,
  searchQuery,
  selectedSessionIds,
  sessions,
}: ConversationListProps) {
  if (sessions.length === 0) {
    return (
      <Stack
        alignItems="center"
        justifyContent="center"
        spacing={1}
        sx={{
          border: '1px dashed',
          borderColor: 'divider',
          borderRadius: 3,
          minHeight: 220,
          px: 3,
          py: 4,
          textAlign: 'center',
        }}
      >
        <Typography variant="h6">
          {searchQuery ? 'No conversations matched your search.' : 'No stored conversations yet.'}
        </Typography>
        <Typography color="text.secondary" variant="body2">
          {searchQuery
            ? 'Try a shorter or more specific title search.'
            : 'Completed chat sessions will appear here once they are stored in history.'}
        </Typography>
      </Stack>
    )
  }

  return (
    <Stack spacing={2}>
      {sessions.map((session) => (
        <ConversationCard
          key={session.session_id}
          isExpanded={expandedSessionIds.has(session.session_id)}
          isSelected={selectedSessionIds.has(session.session_id)}
          onDelete={() => onDeleteSession(session)}
          onRename={() => onRenameSession(session)}
          onSelectChange={(selected) => onSelectSession(session.session_id, selected)}
          onToggleExpand={() => onToggleExpandSession(session.session_id)}
          session={session}
        >
          <ConversationTranscriptView
            expanded={expandedSessionIds.has(session.session_id)}
            sessionId={session.session_id}
          />
        </ConversationCard>
      ))}
    </Stack>
  )
}
