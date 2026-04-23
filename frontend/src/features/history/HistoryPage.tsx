import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'
import HistoryIcon from '@mui/icons-material/History'
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Paper,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material'
import { useDeferredValue, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'
import {
  ALL_CHAT_HISTORY_KIND,
  AGENT_STUDIO_CHAT_HISTORY_KIND,
  ASSISTANT_CHAT_HISTORY_KIND,
  type ChatHistoryListKind,
  type ChatHistorySessionSummary,
} from '@/services/chatHistoryApi'

import ConversationList from './ConversationList'
import formatConversationTitle from './formatConversationTitle'
import HistorySearchBar from './HistorySearchBar'
import {
  useBulkDeleteChatSessionsMutation,
  useChatHistoryListQuery,
  useDeleteChatSessionMutation,
  useRenameChatSessionMutation,
} from './useChatHistoryQuery'

const HISTORY_PAGE_LIST_LIMIT = 100
const HISTORY_KIND_OPTIONS: Array<{ label: string; value: ChatHistoryListKind }> = [
  { label: 'All', value: ALL_CHAT_HISTORY_KIND },
  { label: 'AI assistant chat', value: ASSISTANT_CHAT_HISTORY_KIND },
  { label: 'Agent Studio chat', value: AGENT_STUDIO_CHAT_HISTORY_KIND },
]

function areSetsEqual(left: Set<string>, right: Set<string>): boolean {
  if (left.size !== right.size) {
    return false
  }

  for (const value of left) {
    if (!right.has(value)) {
      return false
    }
  }

  return true
}

function pruneSessionIds(
  previousSessionIds: Set<string>,
  visibleSessionIds: Set<string>,
): Set<string> {
  const nextSessionIds = new Set<string>()

  previousSessionIds.forEach((sessionId) => {
    if (visibleSessionIds.has(sessionId)) {
      nextSessionIds.add(sessionId)
    }
  })

  return areSetsEqual(previousSessionIds, nextSessionIds)
    ? previousSessionIds
    : nextSessionIds
}

function normalizeHistoryKind(value: string | null): ChatHistoryListKind {
  if (
    value === ALL_CHAT_HISTORY_KIND
    || value === ASSISTANT_CHAT_HISTORY_KIND
    || value === AGENT_STUDIO_CHAT_HISTORY_KIND
  ) {
    return value
  }

  return ALL_CHAT_HISTORY_KIND
}

function getHistorySearchScopeLabel(chatKind: ChatHistoryListKind): string {
  if (chatKind === AGENT_STUDIO_CHAT_HISTORY_KIND) {
    return 'Agent Studio chats'
  }

  if (chatKind === ASSISTANT_CHAT_HISTORY_KIND) {
    return 'AI assistant chats'
  }

  return 'all chats'
}

function buildRestoreLocation(session: ChatHistorySessionSummary): string {
  const encodedSessionId = encodeURIComponent(session.session_id)

  if (session.chat_kind === AGENT_STUDIO_CHAT_HISTORY_KIND) {
    return `/agent-studio?session_id=${encodedSessionId}`
  }

  return `/?session=${encodedSessionId}`
}

export default function HistoryPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const selectedKind = normalizeHistoryKind(searchParams.get('kind'))
  const searchInput = normalizeChatHistoryValue(searchParams.get('q')) ?? ''
  const deferredSearchInput = useDeferredValue(searchInput)
  const normalizedSearchQuery = normalizeChatHistoryValue(deferredSearchInput)
  const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(new Set())
  const [expandedSessionIds, setExpandedSessionIds] = useState<Set<string>>(new Set())
  const [renameTarget, setRenameTarget] = useState<ChatHistorySessionSummary | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<ChatHistorySessionSummary | null>(null)
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false)

  useEffect(() => {
    const nextSearchParams = new URLSearchParams(searchParams)
    let changed = false

    if (searchParams.get('kind') !== selectedKind) {
      nextSearchParams.set('kind', selectedKind)
      changed = true
    }

    const rawQueryParam = searchParams.get('q')
    const normalizedQueryParam = normalizeChatHistoryValue(rawQueryParam)
    if ((normalizedQueryParam ?? null) !== rawQueryParam) {
      if (normalizedQueryParam) {
        nextSearchParams.set('q', normalizedQueryParam)
      } else {
        nextSearchParams.delete('q')
      }
      changed = true
    }

    if (changed) {
      setSearchParams(nextSearchParams, { replace: true })
    }
  }, [searchParams, selectedKind, setSearchParams])

  const listQuery = useChatHistoryListQuery({
    chatKind: selectedKind,
    limit: HISTORY_PAGE_LIST_LIMIT,
    query: normalizedSearchQuery,
  })
  const renameMutation = useRenameChatSessionMutation()
  const deleteMutation = useDeleteChatSessionMutation()
  const bulkDeleteMutation = useBulkDeleteChatSessionsMutation()

  const sessions = listQuery.data?.sessions ?? []
  const visibleSessionIds = useMemo(() => sessions.map((session) => session.session_id), [sessions])
  const visibleSessionIdSet = useMemo(() => new Set(visibleSessionIds), [visibleSessionIds])
  const allVisibleSelected = sessions.length > 0 && sessions.every((session) => selectedSessionIds.has(session.session_id))
  const normalizedRenameTitle = normalizeChatHistoryValue(renameTitle)

  const updateHistoryParams = ({
    chatKind = selectedKind,
    query = searchInput,
  }: {
    chatKind?: ChatHistoryListKind
    query?: string
  }) => {
    setSearchParams((currentSearchParams) => {
      const nextSearchParams = new URLSearchParams(currentSearchParams)
      nextSearchParams.set('kind', chatKind)

      const normalizedQuery = normalizeChatHistoryValue(query)
      if (normalizedQuery) {
        nextSearchParams.set('q', normalizedQuery)
      } else {
        nextSearchParams.delete('q')
      }

      return nextSearchParams
    }, { replace: true })
  }

  useEffect(() => {
    setSelectedSessionIds((previousSelectedSessionIds) => {
      return pruneSessionIds(previousSelectedSessionIds, visibleSessionIdSet)
    })
  }, [visibleSessionIdSet])

  useEffect(() => {
    setExpandedSessionIds((previousExpandedSessionIds) => {
      return pruneSessionIds(previousExpandedSessionIds, visibleSessionIdSet)
    })
  }, [visibleSessionIdSet])

  useEffect(() => {
    if (!renameTarget) {
      setRenameTitle('')
      return
    }

    setRenameTitle(renameTarget.title ?? '')
  }, [renameTarget])

  const clearRenameDialog = () => {
    renameMutation.reset?.()
    setRenameTarget(null)
    setRenameTitle('')
  }

  const clearDeleteDialog = () => {
    deleteMutation.reset?.()
    setDeleteTarget(null)
  }

  const clearBulkDeleteDialog = () => {
    bulkDeleteMutation.reset?.()
    setBulkDeleteDialogOpen(false)
  }

  const handleSelectSession = (sessionId: string, selected: boolean) => {
    setSelectedSessionIds((previousSelectedSessionIds) => {
      const nextSelectedSessionIds = new Set(previousSelectedSessionIds)
      if (selected) {
        nextSelectedSessionIds.add(sessionId)
      } else {
        nextSelectedSessionIds.delete(sessionId)
      }
      return nextSelectedSessionIds
    })
  }

  const handleToggleSelectAll = (selected: boolean) => {
    if (selected) {
      setSelectedSessionIds(new Set(visibleSessionIds))
      return
    }

    setSelectedSessionIds(new Set())
  }

  const handleToggleExpandSession = (sessionId: string) => {
    setExpandedSessionIds((previousExpandedSessionIds) => {
      const nextExpandedSessionIds = new Set(previousExpandedSessionIds)
      if (nextExpandedSessionIds.has(sessionId)) {
        nextExpandedSessionIds.delete(sessionId)
      } else {
        nextExpandedSessionIds.add(sessionId)
      }
      return nextExpandedSessionIds
    })
  }

  const handleRenameSubmit = async () => {
    if (!renameTarget || !normalizedRenameTitle) {
      return
    }

    await renameMutation.mutateAsync({
      sessionId: renameTarget.session_id,
      title: normalizedRenameTitle,
    })

    clearRenameDialog()
  }

  const handleDeleteSubmit = async () => {
    if (!deleteTarget) {
      return
    }

    await deleteMutation.mutateAsync({
      sessionId: deleteTarget.session_id,
    })

    setSelectedSessionIds((previousSelectedSessionIds) => {
      const nextSelectedSessionIds = new Set(previousSelectedSessionIds)
      nextSelectedSessionIds.delete(deleteTarget.session_id)
      return nextSelectedSessionIds
    })
    setExpandedSessionIds((previousExpandedSessionIds) => {
      const nextExpandedSessionIds = new Set(previousExpandedSessionIds)
      nextExpandedSessionIds.delete(deleteTarget.session_id)
      return nextExpandedSessionIds
    })

    clearDeleteDialog()
  }

  const handleBulkDeleteSubmit = async () => {
    const sessionIds = visibleSessionIds.filter((sessionId) => selectedSessionIds.has(sessionId))
    if (sessionIds.length === 0) {
      return
    }

    await bulkDeleteMutation.mutateAsync({ sessionIds })

    const deletedSessionIds = new Set(sessionIds)
    setSelectedSessionIds(new Set())
    setExpandedSessionIds((previousExpandedSessionIds) => {
      const nextExpandedSessionIds = new Set<string>()
      previousExpandedSessionIds.forEach((sessionId) => {
        if (!deletedSessionIds.has(sessionId)) {
          nextExpandedSessionIds.add(sessionId)
        }
      })
      return nextExpandedSessionIds
    })

    clearBulkDeleteDialog()
  }

  const handleRestoreSession = (session: ChatHistorySessionSummary) => {
    navigate(buildRestoreLocation(session))
  }

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        overflow: 'auto',
        px: { xs: 2, md: 3 },
        py: 3,
      }}
    >
      <Stack spacing={3}>
        <Stack spacing={1}>
          <Stack direction="row" spacing={1.25} alignItems="center">
            <HistoryIcon color="primary" />
            <Typography variant="h4">Chat History</Typography>
          </Stack>
          <Typography color="text.secondary" variant="body1">
            Browse stored conversations, search by title, and expand each transcript inline without leaving the page.
          </Typography>
        </Stack>

        <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3 }}>
          <Tabs
            aria-label="Chat kind filter"
            onChange={(_event, nextKind: ChatHistoryListKind) => updateHistoryParams({ chatKind: nextKind })}
            value={selectedKind}
            variant="scrollable"
            allowScrollButtonsMobile
            sx={{
              px: 1,
              '& .MuiTab-root': {
                minHeight: 56,
                textTransform: 'none',
              },
            }}
          >
            {HISTORY_KIND_OPTIONS.map((option) => (
              <Tab key={option.value} label={option.label} value={option.value} />
            ))}
          </Tabs>
        </Paper>

        <HistorySearchBar
          allVisibleSelected={allVisibleSelected}
          bulkDeleteDisabled={
            selectedSessionIds.size === 0 || bulkDeleteMutation.isPending || sessions.length === 0
          }
          hasVisibleSessions={sessions.length > 0}
          isFiltering={searchInput !== deferredSearchInput}
          onBulkDelete={() => setBulkDeleteDialogOpen(true)}
          onChange={(value) => updateHistoryParams({ query: value })}
          onToggleSelectAll={handleToggleSelectAll}
          searchScopeLabel={getHistorySearchScopeLabel(selectedKind)}
          selectedCount={selectedSessionIds.size}
          totalSessions={listQuery.data?.total_sessions ?? sessions.length}
          value={searchInput}
          visibleCount={sessions.length}
        />

        <Box sx={{ position: 'relative' }}>
          {listQuery.isFetching ? (
            <LinearProgress
              sx={{
                position: 'absolute',
                top: -1,
                left: 0,
                right: 0,
                zIndex: 1,
              }}
            />
          ) : null}

          {listQuery.error ? (
            <Alert
              action={
                <Button color="inherit" onClick={() => void listQuery.refetch()} size="small">
                  Retry
                </Button>
              }
              severity="error"
            >
              {listQuery.error.message}
            </Alert>
          ) : (
            <ConversationList
              chatKind={selectedKind}
              expandedSessionIds={expandedSessionIds}
              onDeleteSession={setDeleteTarget}
              onRenameSession={setRenameTarget}
              onRestoreSession={handleRestoreSession}
              onSelectSession={handleSelectSession}
              onToggleExpandSession={handleToggleExpandSession}
              searchQuery={normalizedSearchQuery ?? ''}
              selectedSessionIds={selectedSessionIds}
              sessions={sessions}
            />
          )}
        </Box>
      </Stack>

      <Dialog
        fullWidth
        maxWidth="sm"
        onClose={renameMutation.isPending ? undefined : clearRenameDialog}
        open={renameTarget !== null}
      >
        <DialogTitle>Rename conversation</DialogTitle>
        <DialogContent>
          {renameMutation.error ? (
            <Alert severity="error" sx={{ mb: 2 }}>
              {renameMutation.error.message}
            </Alert>
          ) : null}
          <TextField
            autoFocus
            fullWidth
            inputProps={{ maxLength: 255 }}
            label="Conversation title"
            margin="dense"
            onChange={(event) => setRenameTitle(event.target.value)}
            value={renameTitle}
          />
        </DialogContent>
        <DialogActions>
          <Button disabled={renameMutation.isPending} onClick={clearRenameDialog}>
            Cancel
          </Button>
          <Button
            disabled={!normalizedRenameTitle || renameMutation.isPending}
            onClick={() => void handleRenameSubmit()}
            variant="contained"
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        fullWidth
        maxWidth="sm"
        onClose={deleteMutation.isPending ? undefined : clearDeleteDialog}
        open={deleteTarget !== null}
      >
        <DialogTitle>Delete conversation?</DialogTitle>
        <DialogContent>
          {deleteMutation.error ? (
            <Alert severity="error" sx={{ mb: 2 }}>
              {deleteMutation.error.message}
            </Alert>
          ) : null}
          <Typography variant="body2">
            Delete <strong>{formatConversationTitle(deleteTarget)}</strong> from stored history? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button disabled={deleteMutation.isPending} onClick={clearDeleteDialog}>
            Cancel
          </Button>
          <Button
            color="error"
            disabled={deleteMutation.isPending}
            onClick={() => void handleDeleteSubmit()}
            startIcon={<DeleteOutlineIcon />}
            variant="contained"
          >
            Delete conversation
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        fullWidth
        maxWidth="sm"
        onClose={bulkDeleteMutation.isPending ? undefined : clearBulkDeleteDialog}
        open={bulkDeleteDialogOpen}
      >
        <DialogTitle>Delete selected conversations?</DialogTitle>
        <DialogContent>
          {bulkDeleteMutation.error ? (
            <Alert severity="error" sx={{ mb: 2 }}>
              {bulkDeleteMutation.error.message}
            </Alert>
          ) : null}
          <Typography variant="body2">
            Delete {selectedSessionIds.size} selected conversations from stored history? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button disabled={bulkDeleteMutation.isPending} onClick={clearBulkDeleteDialog}>
            Cancel
          </Button>
          <Button
            color="error"
            disabled={selectedSessionIds.size === 0 || bulkDeleteMutation.isPending}
            onClick={() => void handleBulkDeleteSubmit()}
            startIcon={<DeleteOutlineIcon />}
            variant="contained"
          >
            Delete selected conversations
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
