import { Stack, Typography } from '@mui/material'

import {
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  type ChatHistoryListKind,
  type ChatHistorySessionSummary,
} from '@/services/chatHistoryApi'

import ConversationCard from './ConversationCard'
import ConversationTranscriptView from './ConversationTranscriptView'

interface ConversationListProps {
  chatKind: ChatHistoryListKind
  expandedSessionIds: Set<string>
  onDeleteSession: (session: ChatHistorySessionSummary) => void
  onRenameSession: (session: ChatHistorySessionSummary) => void
  onRestoreSession: (session: ChatHistorySessionSummary) => void
  onSelectSession: (sessionId: string, selected: boolean) => void
  onToggleExpandSession: (sessionId: string) => void
  searchQuery: string
  selectedSessionIds: Set<string>
  sessions: ChatHistorySessionSummary[]
}

function getConversationScopeLabel(chatKind: ChatHistoryListKind): string {
  if (chatKind === AGENT_STUDIO_CHAT_HISTORY_KIND) {
    return 'Agent Studio chats'
  }

  if (chatKind === 'assistant_chat') {
    return 'AI assistant chats'
  }

  return 'conversations'
}

export default function ConversationList({
  chatKind,
  expandedSessionIds,
  onDeleteSession,
  onRenameSession,
  onRestoreSession,
  onSelectSession,
  onToggleExpandSession,
  searchQuery,
  selectedSessionIds,
  sessions,
}: ConversationListProps) {
  const conversationScopeLabel = getConversationScopeLabel(chatKind)

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
          {searchQuery
            ? `No ${conversationScopeLabel} matched your search.`
            : `No stored ${conversationScopeLabel} yet.`}
        </Typography>
        <Typography color="text.secondary" variant="body2">
          {searchQuery
            ? 'Try a shorter or more specific title search.'
            : `Completed ${conversationScopeLabel} will appear here once they are stored in history.`}
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
          onRestore={() => onRestoreSession(session)}
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
