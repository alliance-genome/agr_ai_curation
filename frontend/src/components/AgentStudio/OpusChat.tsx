/**
 * OpusChat Component
 *
 * Provider-neutral Agent Studio AI Chat interface.
 * Includes tool support for suggestion submission.
 */

import { useState, useRef, useEffect, useCallback, useMemo, type Ref, type SetStateAction } from 'react'
import {
  Box,
  Typography,
  TextField,
  IconButton,
  Paper,
  CircularProgress,
  Chip,
  Tooltip,
  Button,
  Alert,
  Snackbar,
  Collapse,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import SendIcon from '@mui/icons-material/Send'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import LightbulbIcon from '@mui/icons-material/Lightbulb'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import BuildIcon from '@mui/icons-material/Build'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import CloseIcon from '@mui/icons-material/Close'
import {
  createAgentStudioSession,
  streamOpusChat,
} from '@/services/agentStudioService'
import { logger } from '@/services/logger'
import ModelessFeedbackSurface from '@/components/Feedback/ModelessFeedbackSurface'
import type {
  ChatMessage,
  ChatContext,
  PromptInfo,
  OpusChatEvent,
  ToolIdeaConversationEntry,
  WorkshopPromptUpdateProposal,
  FlowAuthoringProposal,
} from '@/types/promptExplorer'
import type { FlowProposalApplyResult } from './FlowBuilder/types'
import SuggestionDialog from './SuggestionDialog'
import { buildFlowVerificationPrompt } from './flowVerificationPrompt'

const ChatContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

const ChatHeader = styled(Box)(({ theme }) => ({
  height: 40,
  flex: 'none',
  padding: theme.spacing(0, 0.75, 0, 1.5),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
  minWidth: 0,
}))

const HeaderIconButton = styled(IconButton)(({ theme }) => ({
  width: 28,
  height: 28,
  borderRadius: 6,
  color: theme.palette.text.secondary,
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: 1,
  },
}))

const MessagesContainer = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  padding: theme.spacing(1.5),
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(1.5),
}))

const MessageBubble = styled(Paper, {
  shouldForwardProp: (prop) => prop !== 'isUser' && prop !== 'isSystem',
})<{ isUser?: boolean; isSystem?: boolean }>(({ theme, isUser, isSystem }) => ({
  padding: theme.spacing(1, 1.5),
  maxWidth: '88%',
  alignSelf: isUser ? 'flex-end' : isSystem ? 'center' : 'flex-start',
  backgroundColor: isUser
    ? theme.palette.primary.main
    : isSystem
    ? alpha(theme.palette.success.main, 0.1)
    : alpha(theme.palette.background.default, 0.6),
  color: isUser ? theme.palette.primary.contrastText : theme.palette.text.primary,
  borderRadius: theme.spacing(1.5),
  borderBottomRightRadius: isUser ? theme.spacing(0.5) : theme.spacing(1.5),
  borderBottomLeftRadius: isUser ? theme.spacing(1.5) : isSystem ? theme.spacing(1.5) : theme.spacing(0.5),
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  ...(isSystem && {
    border: `1px solid ${alpha(theme.palette.success.main, 0.3)}`,
  }),
}))

const InputContainer = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1),
  borderTop: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'flex-end',
  gap: theme.spacing(0.75),
}))

const ToolCallBox = styled(Box)(({ theme }) => ({
  backgroundColor: alpha(theme.palette.grey[900], 0.03),
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.shape.borderRadius,
  padding: theme.spacing(1.5),
  marginBottom: theme.spacing(1),
  fontSize: '0.75rem',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  maxHeight: 300,
  overflow: 'auto',
  color: theme.palette.text.secondary,
}))

// Tool call record for display
interface ToolCallRecord {
  tool_name: string
  tool_input: Record<string, unknown>
  call_id?: string | null
  result?: Record<string, unknown>
}

// Extended message type to include system messages and tool calls
interface DisplayMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
  toolCalls?: ToolCallRecord[]  // Tool calls made during this message
}

interface SharedOpusChatState {
  messages: DisplayMessage[]
  isStreaming: boolean
  streamStatus: string | null
  durableSessionId: string | null
  /** Process-local only; never reconstructed from durable chat history. */
  pendingFlowProposal: FlowAuthoringProposal | null
}

const OPUS_DRAFT_CONVERSATION_KEY = 'agent-studio:draft'
const sharedOpusChatStates = new Map<string, SharedOpusChatState>()
const sharedOpusChatAliases = new Map<string, string>()
const sharedOpusChatListeners = new Set<() => void>()

function resolveOpusConversationKey(
  context: ChatContext,
  durableSessionId?: string | null,
  sourceSessionId?: string,
): string {
  return durableSessionId || sourceSessionId || context.session_id || OPUS_DRAFT_CONVERSATION_KEY
}

function getSharedOpusChatState(
  key: string,
  initialConversation?: ToolIdeaConversationEntry[] | null,
  initialDurableSessionId?: string | null,
): SharedOpusChatState {
  const resolvedKey = sharedOpusChatAliases.get(key) ?? key
  const existing = sharedOpusChatStates.get(resolvedKey)
  if (existing) {
    return existing
  }

  const nextState: SharedOpusChatState = {
    messages: buildDisplayMessages(initialConversation),
    isStreaming: false,
    streamStatus: null,
    durableSessionId: initialDurableSessionId ?? null,
    pendingFlowProposal: null,
  }
  sharedOpusChatStates.set(resolvedKey, nextState)
  return nextState
}

function emitSharedOpusChatState(
  key: string,
  updater: (current: SharedOpusChatState) => SharedOpusChatState,
) {
  const resolvedKey = sharedOpusChatAliases.get(key) ?? key
  const current = getSharedOpusChatState(resolvedKey)
  sharedOpusChatStates.set(resolvedKey, updater(current))
  sharedOpusChatListeners.forEach((listener) => listener())
}

function migrateSharedOpusChatState(fromKey: string, toKey: string) {
  if (fromKey === toKey) {
    return
  }

  const fromState = sharedOpusChatStates.get(fromKey)
  if (!fromState) {
    return
  }

  const existingToState = sharedOpusChatStates.get(toKey)
  if (!existingToState || existingToState.messages.length === 0 || fromState.isStreaming) {
    sharedOpusChatStates.set(toKey, {
      ...fromState,
      durableSessionId: toKey,
    })
    sharedOpusChatListeners.forEach((listener) => listener())
  }
  sharedOpusChatAliases.set(fromKey, toKey)
}

export function resetSharedOpusChatStateForTests() {
  sharedOpusChatStates.clear()
  sharedOpusChatAliases.clear()
  sharedOpusChatListeners.clear()
}

const AGR_CURATION_METHOD_LABELS: Record<string, string> = {
  get_gene_by_exact_symbol: 'Gene Lookup (exact)',
  search_genes: 'Gene Search',
  get_gene_by_id: 'Gene by ID',
  get_allele_by_exact_symbol: 'Allele Lookup (exact)',
  search_alleles: 'Allele Search',
  get_allele_by_id: 'Allele by ID',
  get_ontology_term: 'Ontology Term Lookup',
  search_ontology_terms: 'Ontology Term Search',
  get_vocabulary_term: 'Vocabulary Term Lookup',
  search_vocabulary_terms: 'Vocabulary Term Search',
  get_data_provider: 'Data Provider Lookup',
  search_data_providers: 'Data Provider Search',
  get_reference_by_curie: 'Reference Lookup',
  search_references: 'Reference Search',
  get_agm_by_id: 'AGM Lookup',
  search_agms: 'AGM Search',
  get_species: 'Species List',
  get_data_providers: 'Data Providers',
  search_anatomy_terms: 'Anatomy Terms Search',
  search_life_stage_terms: 'Life Stage Terms Search',
  search_go_terms: 'GO Terms Search',
}

const AGR_CURATION_FIELD_LABELS: Record<string, string> = {
  allele_id: 'Allele ID',
  allele_symbol: 'Allele Symbol',
  abbreviation: 'Abbreviation',
  curie: 'CURIE',
  data_provider: 'Data Provider',
  gene_id: 'Gene ID',
  gene_symbol: 'Gene Symbol',
  group_id: 'Group',
  label: 'Label',
  limit: 'Limit',
  method: 'Method',
  name: 'Name',
  ontology_term_type: 'Ontology Term Type',
  provider: 'Provider',
  reference_curie: 'Reference CURIE',
  search_term: 'Search Term',
  subject_identifier: 'Subject Identifier',
  subject_label: 'Subject Label',
  subject_type: 'Subject Type',
  synonym: 'Synonym',
  taxon_id: 'Taxon',
  term: 'Term',
  term_curie: 'Term CURIE',
  vocabulary: 'Vocabulary',
  vocabulary_name: 'Vocabulary',
}

function titleCaseFieldName(fieldName: string): string {
  return fieldName
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/_/g, ' ')
    .replace(/\bid\b/gi, 'ID')
    .replace(/\bcurie\b/gi, 'CURIE')
    .replace(/\bagm\b/gi, 'AGM')
    .replace(/\bgo\b/gi, 'GO')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function displayValue(value: unknown): string | null {
  if (value === undefined || value === null || value === '') {
    return null
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => displayValue(item))
      .filter((item): item is string => Boolean(item))
      .join(', ')
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

function formatAgrCurationInput(input: Record<string, unknown>): string {
  const method = typeof input.method === 'string' ? input.method : undefined
  const methodLabel = method
    ? AGR_CURATION_METHOD_LABELS[method] || titleCaseFieldName(method)
    : 'Query'
  const lines = [`AGR Curation: ${methodLabel}`]
  const displayedFields = new Set<string>()

  if (method) {
    lines.push(`Method: ${method}`)
    displayedFields.add('method')
  }

  Object.entries(input).forEach(([fieldName, rawValue]) => {
    if (displayedFields.has(fieldName)) {
      return
    }
    const value = displayValue(rawValue)
    if (!value) {
      return
    }
    const label = AGR_CURATION_FIELD_LABELS[fieldName] || titleCaseFieldName(fieldName)
    lines.push(`${label}: ${value}`)
    displayedFields.add(fieldName)
  })

  return lines.join('\n')
}

function formatToolInput(toolName: string, input: Record<string, unknown>): string {
  // Handle SQL query tools
  if (input.query && typeof input.query === 'string') {
    return input.query
  }
  if (toolName === 'agr_curation_query') {
    return formatAgrCurationInput(input)
  }
  if (toolName === 'get_prompt') {
    const parts: string[] = []
    if (input.agent_id) parts.push(`Agent: ${input.agent_id}`)
    if (input.group_id) parts.push(`Group: ${input.group_id}`)
    return parts.length > 0 ? parts.join(', ') : JSON.stringify(input, null, 2)
  }
  if (toolName.includes('api_call')) {
    const parts: string[] = []
    Object.entries(input).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        parts.push(`${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
      }
    })
    return parts.join('\n')
  }
  return JSON.stringify(input, null, 2)
}

function numericResultCount(result: Record<string, unknown>): number | null {
  if (typeof result.count === 'number') {
    return result.count
  }
  if (typeof result.result_count === 'number') {
    return result.result_count
  }
  if (Array.isArray(result.rows)) {
    return result.rows.length
  }
  if (Array.isArray(result.results)) {
    return result.results.length
  }
  return null
}

function formatToolResult(result: Record<string, unknown> | undefined): string | null {
  if (!result) return null

  if (result.status === 'ok' && Array.isArray(result.rows)) {
    const count = numericResultCount(result) ?? result.rows.length
    if (result.rows.length === 0) {
      return '✓ No results'
    }
    return `✓ ${count} row${count !== 1 ? 's' : ''} returned`
  }
  if (result.status === 'error' || result.success === false) {
    return `✗ Error: ${result.message || result.error || 'Unknown error'}`
  }
  if (result.success === true) {
    const count = numericResultCount(result)
    if (count !== null) {
      return count === 0
        ? '✓ No results'
        : `✓ ${count} result${count !== 1 ? 's' : ''} returned`
    }
    return '✓ Success'
  }

  const str = JSON.stringify(result, null, 2)
  return str.length > 200 ? `${str.slice(0, 200)}...` : str
}

function buildDisplayMessages(
  conversation: ToolIdeaConversationEntry[] | null | undefined,
): DisplayMessage[] {
  return (conversation ?? []).flatMap((message) => {
    if (!message.content.trim()) {
      return []
    }

    return [{
      role: message.role,
      content: message.content,
      timestamp: message.timestamp ?? undefined,
    }]
  })
}

function formatShortSessionId(sessionId: string): string {
  return sessionId.length > 8 ? `${sessionId.slice(0, 8)}...` : sessionId
}

interface OpusChatProps {
  context: ChatContext
  /** Capture exact editor state at send time before asynchronous work begins. */
  captureContext?: () => Promise<ChatContext>
  initialConversation?: ToolIdeaConversationEntry[] | null
  durableSessionId?: string | null
  sourceSessionId?: string
  selectedAgent?: PromptInfo
  /** Message to auto-send (e.g., from the Verify with AI Chat button) */
  verifyMessage?: string | null
  /** Callback after verify message is sent */
  onVerifyMessageSent?: () => void
  /** Message to auto-send (e.g., from the Discuss with AI Chat button) */
  discussMessage?: string | null
  /** Callback after discuss message is sent */
  onDiscussMessageSent?: () => void
  /** Notify parent when a new durable Agent Studio session is minted */
  onDurableSessionIdChange?: (sessionId: string) => void
  /** Callback with current chat transcript for workshop tool ideation */
  onConversationSnapshotChange?: (messages: ToolIdeaConversationEntry[]) => void
  /** Apply an approved prompt replacement into the Agent Workshop editor */
  onApplyWorkshopPromptUpdate?: (proposal: WorkshopPromptUpdateProposal) => void
  /** Apply a reviewed transient flow proposal to the in-memory editor draft. */
  onApplyFlowProposal?: (proposal: FlowAuthoringProposal) => Promise<FlowProposalApplyResult>
  /** Shell placement: side panel (hide control) or narrow-width drawer (close control) */
  variant?: 'panel' | 'drawer'
  /** DOM id of the shell container the hide/close control toggles (aria-controls) */
  panelId?: string
  /** Hide or close the chat shell; renders the header control only when provided */
  onHide?: () => void
  /** Ref to the chat input so the shell can move focus into the chat */
  inputRef?: Ref<HTMLTextAreaElement>
  /** Notify the shell when an AI Chat turn starts or finishes streaming */
  onStreamingChange?: (isStreaming: boolean) => void
}

interface PromptLineDiff {
  line: string
  kind: 'unchanged' | 'added' | 'removed'
}

function normalizePromptForComparison(value: string | undefined | null): string {
  return (value || '').replace(/\r\n/g, '\n').trim()
}

function buildPromptLineDiff(currentPrompt: string, proposedPrompt: string): PromptLineDiff[] {
  const currentLines = currentPrompt.replace(/\r\n/g, '\n').split('\n')
  const proposedLines = proposedPrompt.replace(/\r\n/g, '\n').split('\n')
  const currentCount = currentLines.length
  const proposedCount = proposedLines.length

  const lcs: number[][] = Array.from({ length: currentCount + 1 }, () =>
    Array.from({ length: proposedCount + 1 }, () => 0)
  )

  for (let i = 1; i <= currentCount; i += 1) {
    for (let j = 1; j <= proposedCount; j += 1) {
      if (currentLines[i - 1] === proposedLines[j - 1]) {
        lcs[i][j] = lcs[i - 1][j - 1] + 1
      } else {
        lcs[i][j] = Math.max(lcs[i - 1][j], lcs[i][j - 1])
      }
    }
  }

  const reversedDiff: PromptLineDiff[] = []
  let i = currentCount
  let j = proposedCount

  while (i > 0 && j > 0) {
    if (currentLines[i - 1] === proposedLines[j - 1]) {
      reversedDiff.push({ line: currentLines[i - 1], kind: 'unchanged' })
      i -= 1
      j -= 1
      continue
    }

    if (lcs[i][j - 1] >= lcs[i - 1][j]) {
      reversedDiff.push({ line: proposedLines[j - 1], kind: 'added' })
      j -= 1
    } else {
      reversedDiff.push({ line: currentLines[i - 1], kind: 'removed' })
      i -= 1
    }
  }

  while (i > 0) {
    reversedDiff.push({ line: currentLines[i - 1], kind: 'removed' })
    i -= 1
  }
  while (j > 0) {
    reversedDiff.push({ line: proposedLines[j - 1], kind: 'added' })
    j -= 1
  }

  return reversedDiff.reverse()
}

function buildAutoReviewRequest(proposal: WorkshopPromptUpdateProposal): string {
  const summaryText = proposal.summary?.trim()
    ? proposal.summary.trim()
    : 'No summary provided.'
  const targetPrompt = proposal.target_prompt === 'group' ? 'group prompt draft' : 'main workshop prompt draft'
  const groupLabel = proposal.target_prompt === 'group' && proposal.target_group_id
    ? ` (${proposal.target_group_id})`
    : ''
  return `Please run a post-apply review of my Agent Workshop draft.\n\nTarget reviewed: ${targetPrompt}${groupLabel}\n\nChecklist:\n1. Confirm the intended update is present in the current target prompt draft.\n2. Flag any regressions, contradictions, or ambiguities introduced by the edit.\n3. Suggest one follow-up tweak only if it clearly improves behavior.\n\nApplied update summary: ${summaryText}`
}

function formatFlowDiffValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function OpusChat({
  context,
  captureContext,
  initialConversation,
  durableSessionId: durableSessionIdProp,
  sourceSessionId,
  selectedAgent,
  verifyMessage,
  onVerifyMessageSent,
  discussMessage,
  onDiscussMessageSent,
  onDurableSessionIdChange,
  onConversationSnapshotChange,
  onApplyWorkshopPromptUpdate,
  onApplyFlowProposal,
  variant = 'panel',
  panelId,
  onHide,
  inputRef,
  onStreamingChange,
}: OpusChatProps) {
  const conversationKey = useMemo(
    () => resolveOpusConversationKey(context, durableSessionIdProp, sourceSessionId),
    [context, durableSessionIdProp, sourceSessionId]
  )
  const [sharedSnapshot, setSharedSnapshot] = useState<SharedOpusChatState>(() =>
    getSharedOpusChatState(conversationKey, initialConversation, durableSessionIdProp)
  )
  const messages = sharedSnapshot.messages
  const isStreaming = sharedSnapshot.isStreaming
  const streamStatus = sharedSnapshot.streamStatus
  const durableSessionId = sharedSnapshot.durableSessionId
  const pendingFlowProposal = sharedSnapshot.pendingFlowProposal
  const [input, setInput] = useState('')
  const [toolCallsExpanded, setToolCallsExpanded] = useState<{ [key: number]: boolean }>({})  // Track expanded state per message
  const [suggestionDialogOpen, setSuggestionDialogOpen] = useState(false)
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false)
  const [feedbackMenuAnchor, setFeedbackMenuAnchor] = useState<HTMLElement | null>(null)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [isSubmittingDirect, setIsSubmittingDirect] = useState(false)
  const [submissionSent, setSubmissionSent] = useState(false)
  const [promptUpdateDialogOpen, setPromptUpdateDialogOpen] = useState(false)
  const [pendingPromptUpdate, setPendingPromptUpdate] = useState<WorkshopPromptUpdateProposal | null>(null)
  const [awaitingAppliedPromptUpdate, setAwaitingAppliedPromptUpdate] = useState<WorkshopPromptUpdateProposal | null>(null)
  const [flowProposalApplying, setFlowProposalApplying] = useState(false)
  const [queuedAutoReviewMessage, setQueuedAutoReviewMessage] = useState<string | null>(null)
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false,
    message: '',
    severity: 'success',
  })
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const appliedSourceConversationRef = useRef<string | null>(
    sourceSessionId && initialConversation?.length ? sourceSessionId : null
  )
  const preserveCurrentConversationSessionRef = useRef<string | null>(null)
  const sessionCreatePromiseRef = useRef<Promise<string> | null>(null)
  const pendingToolCallIdsRef = useRef(new Set<string>())

  useEffect(() => {
    setSharedSnapshot(getSharedOpusChatState(conversationKey, initialConversation, durableSessionIdProp))
    const listener = () => {
      setSharedSnapshot(getSharedOpusChatState(conversationKey, initialConversation, durableSessionIdProp))
    }
    sharedOpusChatListeners.add(listener)
    return () => {
      sharedOpusChatListeners.delete(listener)
    }
  }, [conversationKey, durableSessionIdProp, initialConversation])

  const setMessages = useCallback((nextMessages: SetStateAction<DisplayMessage[]>) => {
    emitSharedOpusChatState(conversationKey, (current) => ({
      ...current,
      messages: typeof nextMessages === 'function'
        ? nextMessages(current.messages)
        : nextMessages,
    }))
  }, [conversationKey])

  const setIsStreaming = useCallback((nextIsStreaming: SetStateAction<boolean>) => {
    emitSharedOpusChatState(conversationKey, (current) => ({
      ...current,
      isStreaming: typeof nextIsStreaming === 'function'
        ? nextIsStreaming(current.isStreaming)
        : nextIsStreaming,
    }))
  }, [conversationKey])

  const setPendingFlowProposal = useCallback((proposal: FlowAuthoringProposal | null) => {
    emitSharedOpusChatState(conversationKey, (current) => ({
      ...current,
      pendingFlowProposal: proposal,
    }))
  }, [conversationKey])

  const setStreamStatus = useCallback((nextStatus: SetStateAction<string | null>) => {
    emitSharedOpusChatState(conversationKey, (current) => ({
      ...current,
      streamStatus: typeof nextStatus === 'function'
        ? nextStatus(current.streamStatus)
        : nextStatus,
    }))
  }, [conversationKey])

  const setDurableSessionId = useCallback((nextSessionId: SetStateAction<string | null>) => {
    emitSharedOpusChatState(conversationKey, (current) => ({
      ...current,
      durableSessionId: typeof nextSessionId === 'function'
        ? nextSessionId(current.durableSessionId)
        : nextSessionId,
    }))
  }, [conversationKey])

  const syncDurableSessionId = useCallback((
    nextSessionId: string | null,
    options: { notifyParent?: boolean } = {},
  ) => {
    if (nextSessionId) {
      migrateSharedOpusChatState(conversationKey, nextSessionId)
    }
    setDurableSessionId(nextSessionId)

    if (nextSessionId && options.notifyParent) {
      onDurableSessionIdChange?.(nextSessionId)
    }
  }, [conversationKey, onDurableSessionIdChange, setDurableSessionId])

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    // Only mirror actual durable-session prop changes. Parent URL updates can
    // recreate callbacks before the prop catches up, and we must not clear a
    // freshly minted session during that handoff window.
    setDurableSessionId(durableSessionIdProp ?? null)
  }, [durableSessionIdProp])

  useEffect(() => {
    if (!sourceSessionId || !initialConversation?.length) {
      return
    }

    if (appliedSourceConversationRef.current === sourceSessionId) {
      return
    }

    const shouldPreserveCurrentConversation =
      preserveCurrentConversationSessionRef.current === sourceSessionId

    setMessages((currentMessages) => {
      appliedSourceConversationRef.current = sourceSessionId
      preserveCurrentConversationSessionRef.current = null

      if (shouldPreserveCurrentConversation && currentMessages.length > 0) {
        return currentMessages
      }

      return buildDisplayMessages(initialConversation)
    })
  }, [initialConversation, sourceSessionId])

  useEffect(() => {
    onStreamingChange?.(isStreaming)
  }, [isStreaming, onStreamingChange])

  // Publish normalized conversation snapshot for features that need transcript context.
  useEffect(() => {
    if (!onConversationSnapshotChange) return
    const snapshot: ToolIdeaConversationEntry[] = messages
      .map((message) => ({
        role: message.role,
        content: message.content,
        timestamp: message.timestamp || undefined,
      }))
      .filter((message) => Boolean(message.content && message.content.trim()))
    onConversationSnapshotChange(snapshot)
  }, [messages, onConversationSnapshotChange])

  const ensureDurableSessionId = useCallback(async (): Promise<string> => {
    if (durableSessionId) {
      return durableSessionId
    }

    if (!sessionCreatePromiseRef.current) {
      sessionCreatePromiseRef.current = createAgentStudioSession()
        .then((session) => {
          preserveCurrentConversationSessionRef.current = session.session_id
          syncDurableSessionId(session.session_id, { notifyParent: true })
          return session.session_id
        })
        .finally(() => {
          sessionCreatePromiseRef.current = null
        })
    }

    return sessionCreatePromiseRef.current
  }, [durableSessionId, syncDurableSessionId])

  // Reference for auto-sending verify message
  const handleSendRef = useRef<(messageText: string) => Promise<void>>()
  // Track which verify message was already sent to prevent duplicates
  const verifyMessageSentRef = useRef<string | null>(null)
  // Track which discuss message was already sent to prevent duplicates
  const discussMessageSentRef = useRef<string | null>(null)
  const currentMainWorkshopDraft = context?.agent_workshop?.prompt_draft || ''
  const currentGroupWorkshopDraft = context?.agent_workshop?.selected_group_prompt_draft || ''
  const currentPromptForPendingUpdate =
    pendingPromptUpdate?.target_prompt === 'group' ? currentGroupWorkshopDraft : currentMainWorkshopDraft
  const promptLineDiff = useMemo(
    () => buildPromptLineDiff(currentPromptForPendingUpdate, pendingPromptUpdate?.prompt || ''),
    [currentPromptForPendingUpdate, pendingPromptUpdate?.prompt]
  )
  const addedLineCount = useMemo(
    () => promptLineDiff.filter((entry) => entry.kind === 'added').length,
    [promptLineDiff]
  )
  const removedLineCount = useMemo(
    () => promptLineDiff.filter((entry) => entry.kind === 'removed').length,
    [promptLineDiff]
  )

  // Attach serial tool events to the current assistant message. Live events
  // correlate by call_id; durable replay events intentionally fall back to the
  // existing serial order because historical replay does not expose call IDs.
  const handleToolEvent = useCallback((event: OpusChatEvent): boolean => {
    if (event.type === 'TOOL_USE' && event.tool_name && event.tool_input) {
      if (event.call_id) {
        pendingToolCallIdsRef.current.add(event.call_id)
      }
      // Add tool call to the current assistant message
      setMessages((prev) => {
        const updated = [...prev]
        const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
        if (lastAssistantIdx !== -1) {
          const currentToolCalls = updated[lastAssistantIdx].toolCalls || []
          updated[lastAssistantIdx] = {
            ...updated[lastAssistantIdx],
            toolCalls: [
              ...currentToolCalls,
              {
                tool_name: event.tool_name as string,
                tool_input: event.tool_input as Record<string, unknown>,
                call_id: event.call_id,
              },
            ],
          }
        }
        return updated
      })

      // For suggestion tool, also add a system message
      if (event.tool_name === 'submit_prompt_suggestion') {
        const summary = (event.tool_input?.summary as string) || 'a suggestion'
        setMessages((prev) => [
          ...prev,
          {
            role: 'system',
            content: `Submitting suggestion: "${summary}"...`,
            timestamp: new Date().toISOString(),
          },
        ])
      }
    } else if (event.type === 'TOOL_RESULT' && event.result) {
      if (event.call_id && !pendingToolCallIdsRef.current.delete(event.call_id)) {
        logger.error(
          'Agent Studio tool event correlation failure',
          new Error('Agent Studio tool event correlation failure'),
          {
            component: 'OpusChat',
            action: 'correlate_tool_result',
            metadata: { eventType: event.type, hasCallId: true },
          },
        )
        setMessages((prev) => {
          const updated = [...prev]
          const lastAssistantIdx = updated.findLastIndex((message) => message.role === 'assistant')
          if (lastAssistantIdx !== -1) {
            updated[lastAssistantIdx] = {
              ...updated[lastAssistantIdx],
              content: 'AI Chat received an unexpected tool result. Please retry.',
            }
          }
          return updated
        })
        return false
      }

      const toolResult = event.result as Record<string, unknown>
      const displayToolResult = event.tool_name === 'propose_flow_draft_update'
        ? {
            contract_version: toolResult.contract_version,
            success: toolResult.success,
            valid: toolResult.valid,
            pending_user_approval: toolResult.pending_user_approval,
            approval_status: toolResult.approval_status,
            change_summary: toolResult.change_summary,
            finding_count: Array.isArray(toolResult.findings) ? toolResult.findings.length : 0,
            diff_count: Array.isArray(toolResult.diff) ? toolResult.diff.length : 0,
            message: toolResult.message,
            error: toolResult.error,
          }
        : toolResult
      // Update the last tool call with its result
      setMessages((prev) => {
        const updated = [...prev]
        const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
        if (lastAssistantIdx !== -1 && updated[lastAssistantIdx].toolCalls?.length) {
          const toolCalls = [...(updated[lastAssistantIdx].toolCalls || [])]
          const lastToolIdx = event.call_id
            ? toolCalls.findLastIndex((toolCall) => toolCall.call_id === event.call_id)
            : toolCalls.length - 1
          if (lastToolIdx >= 0) {
            toolCalls[lastToolIdx] = {
              ...toolCalls[lastToolIdx],
              result: displayToolResult,
            }
            updated[lastAssistantIdx] = {
              ...updated[lastAssistantIdx],
              toolCalls,
            }
          }
        }
        return updated
      })

      // Update system message for suggestion tool results
      if (event.tool_name === 'submit_prompt_suggestion') {
        setMessages((prev) => {
          const updated = [...prev]
          const lastSystemIdx = updated.findLastIndex((m) => m.role === 'system')
          const suggestionId =
            typeof toolResult.suggestion_id === 'string' ? toolResult.suggestion_id : 'unknown'
          const suggestionError =
            typeof toolResult.error === 'string' ? toolResult.error : 'Unknown error'
          if (lastSystemIdx !== -1 && toolResult?.success) {
            updated[lastSystemIdx] = {
              role: 'system',
              content: `✓ Suggestion submitted successfully (ID: ${suggestionId})`,
              timestamp: updated[lastSystemIdx].timestamp,
            }
          } else if (lastSystemIdx !== -1) {
            updated[lastSystemIdx] = {
              role: 'system',
              content: `✗ Failed to submit suggestion: ${suggestionError}`,
              timestamp: updated[lastSystemIdx].timestamp,
            }
          }
          return updated
        })
      }

      if (event.tool_name === 'update_workshop_prompt_draft') {
        const success = toolResult.success === true
        const proposedPrompt =
          typeof toolResult.proposed_prompt === 'string'
            ? toolResult.proposed_prompt
            : ''
        const changeSummary =
          typeof toolResult.change_summary === 'string'
            ? toolResult.change_summary
            : undefined
        const applyMode =
          toolResult.apply_mode === 'replace' || toolResult.apply_mode === 'targeted_edit'
            ? toolResult.apply_mode
            : undefined
        const targetPrompt =
          toolResult.target_prompt === 'group'
            ? 'group'
            : 'main'
        const targetGroupId =
          typeof toolResult.target_group_id === 'string' && toolResult.target_group_id.trim()
            ? toolResult.target_group_id.trim().toUpperCase()
            : undefined

        if (success && proposedPrompt) {
          setPendingPromptUpdate({
            prompt: proposedPrompt,
            summary: changeSummary,
            apply_mode: applyMode || 'replace',
            target_prompt: targetPrompt,
            target_group_id: targetPrompt === 'group' ? targetGroupId : undefined,
          })
          setPromptUpdateDialogOpen(true)
          const targetLabel = targetPrompt === 'group'
            ? `group prompt${targetGroupId ? ` (${targetGroupId})` : ''}`
            : 'main prompt'
          setMessages((prev) => [
            ...prev,
            {
              role: 'system',
              content: `AI Chat prepared a ${targetLabel} update proposal. Review and approve it to apply to your workshop draft.`,
              timestamp: new Date().toISOString(),
            },
          ])
        } else {
          const errorText =
            typeof toolResult.error === 'string'
              ? toolResult.error
              : 'Unable to prepare workshop prompt update.'
          setMessages((prev) => [
            ...prev,
            {
              role: 'system',
              content: `✗ Prompt update proposal failed: ${errorText}`,
              timestamp: new Date().toISOString(),
            },
          ])
        }
      }
      if (event.tool_name === 'propose_flow_draft_update') {
        const candidate = toolResult.candidate
        const proposalReady =
          toolResult.success === true
          && toolResult.valid === true
          && toolResult.pending_user_approval === true
          && toolResult.contract_version === 'flow_authoring_proposal.v1'
          && typeof toolResult.base_draft_fingerprint === 'string'
          && typeof toolResult.candidate_draft_fingerprint === 'string'
          && typeof toolResult.change_summary === 'string'
          && Array.isArray(toolResult.diff)
          && Array.isArray(toolResult.findings)
          && candidate !== null
          && typeof candidate === 'object'
          && typeof (candidate as Record<string, unknown>).name === 'string'
          && typeof (candidate as Record<string, unknown>).description === 'string'
          && typeof (candidate as Record<string, unknown>).flow_definition === 'object'
        if (proposalReady) {
          setPendingFlowProposal(toolResult as unknown as FlowAuthoringProposal)
          setMessages((prev) => [
            ...prev,
            {
              role: 'system',
              content: 'AI Chat prepared a validated Flow Builder proposal. Review the exact changes, then Apply or Cancel. Save remains manual.',
              timestamp: new Date().toISOString(),
            },
          ])
        } else {
          const errorText = typeof toolResult.error === 'string'
            ? toolResult.error
            : 'The flow proposal needs repair before it can be reviewed.'
          setMessages((prev) => [
            ...prev,
            {
              role: 'system',
              content: `✗ Flow proposal not ready: ${errorText}`,
              timestamp: new Date().toISOString(),
            },
          ])
        }
      }
    }
    return true
  }, [setMessages, setPendingFlowProposal])

  // Handle sending a message (optionally with a specific message text for auto-send)
  const handleSend = useCallback(async (messageOverride?: string) => {
    const messageText = messageOverride || input.trim()
    if (!messageText || isStreaming) return

    // Calling the provider starts with a synchronous copy of editor state; its
    // promise only awaits deterministic fingerprint hashing afterward.
    const contextPromise = captureContext
      ? captureContext()
      : Promise.resolve(context)

    pendingToolCallIdsRef.current.clear()

    const userMessage: DisplayMessage = {
      role: 'user',
      content: messageText,
      timestamp: new Date().toISOString(),
    }
    const newMessages = [...messages, userMessage]
    setMessages(newMessages)
    if (!messageOverride) setInput('')  // Only clear input if not using override
    setIsStreaming(true)
    setStreamStatus('Preparing AI Chat context…')

    // Add empty assistant message to stream into
    setMessages((prev) => [
      ...prev,
      {
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        toolCalls: [],
      },
    ])

    // Convert to ChatMessage format (only user/assistant for API)
    const apiMessages: ChatMessage[] = newMessages
      .filter((m) => m.role !== 'system')
      .map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content }))

    try {
      const [activeSessionId, sendContext] = await Promise.all([
        ensureDurableSessionId(),
        contextPromise,
      ])

      for await (const event of streamOpusChat(apiMessages, sendContext, activeSessionId)) {
        if (event.type === 'TEXT_DELTA' && event.delta) {
          setStreamStatus(null)
          setMessages((prev) => {
            const updated = [...prev]
            const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
            if (lastAssistantIdx !== -1) {
              updated[lastAssistantIdx] = {
                ...updated[lastAssistantIdx],
                content: updated[lastAssistantIdx].content + event.delta,
              }
            }
            return updated
          })
        } else if (event.type === 'TOOL_SEARCH') {
          setStreamStatus('Finding relevant capabilities…')
        } else if (event.type === 'TOOL_SEARCH_RESULT') {
          setStreamStatus(
            event.loaded_tool_count === 0
              ? 'No additional capabilities were needed.'
              : 'Relevant capabilities are ready.',
          )
        } else if (event.type === 'PROVIDER_CONTEXT_PREFLIGHT') {
          setStreamStatus('Preparing AI Chat context…')
        } else if (event.type === 'TOOL_USE' || event.type === 'TOOL_RESULT') {
          setStreamStatus('Using an authorized capability…')
          if (!handleToolEvent(event)) break
        } else if (
          event.type === 'CONTEXT_OVERFLOW'
          || event.type === 'REFUSAL'
          || event.type === 'INCOMPLETE'
          || event.type === 'ERROR'
        ) {
          const terminalPrefix = {
            CONTEXT_OVERFLOW: 'Conversation too long',
            REFUSAL: 'Request declined',
            INCOMPLETE: 'Response incomplete',
            ERROR: 'Error',
          }[event.type]
          setMessages((prev) => {
            const updated = [...prev]
            const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
            if (lastAssistantIdx !== -1) {
              updated[lastAssistantIdx] = {
                ...updated[lastAssistantIdx],
                content: `${terminalPrefix}: ${event.message}`,
              }
            }
            return updated
          })
          break
        } else if (event.type === 'DONE') {
          break
        }
      }
    } catch (error) {
      const isProtocolError = error instanceof Error && error.name === 'AgentStudioStreamProtocolError'
      if (!isProtocolError) {
        logger.error(
          'Agent Studio AI Chat stream failed',
          new Error('Agent Studio AI Chat stream failed'),
          {
            component: 'OpusChat',
            action: 'stream_chat',
            metadata: {
              activeTab: context?.active_tab ?? 'agents',
              hasDurableSession: Boolean(durableSessionId),
            },
          },
        )
      }
      setMessages((prev) => {
        const updated = [...prev]
        const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
        if (
          lastAssistantIdx !== -1
          && (isProtocolError || !updated[lastAssistantIdx].content)
        ) {
          updated[lastAssistantIdx] = {
            ...updated[lastAssistantIdx],
            content: isProtocolError && error instanceof Error
              ? error.message
              : 'Sorry, an error occurred. Please try again.',
          }
        }
        return updated
      })
    } finally {
      pendingToolCallIdsRef.current.clear()
      setStreamStatus(null)
      setIsStreaming(false)
    }
  }, [
    captureContext,
    input,
    messages,
    context,
    isStreaming,
    durableSessionId,
    ensureDurableSessionId,
    handleToolEvent,
    setIsStreaming,
    setMessages,
    setStreamStatus,
  ])

  // Update ref for auto-send
  handleSendRef.current = handleSend

  // Auto-send verify message when provided (from FlowBuilder's Verify button)
  // Uses ref to prevent duplicate sends when isStreaming briefly toggles
  useEffect(() => {
    if (
      verifyMessage &&
      verifyMessage !== verifyMessageSentRef.current &&
      !isStreaming &&
      handleSendRef.current
    ) {
      verifyMessageSentRef.current = verifyMessage
      handleSendRef.current(verifyMessage)
      onVerifyMessageSent?.()
    }
  }, [verifyMessage, isStreaming, onVerifyMessageSent])

  // Auto-send a discussion message from an Agent Details AI Chat action.
  // Uses ref to prevent duplicate sends when isStreaming briefly toggles
  useEffect(() => {
    if (
      discussMessage &&
      discussMessage !== discussMessageSentRef.current &&
      !isStreaming &&
      handleSendRef.current
    ) {
      discussMessageSentRef.current = discussMessage
      handleSendRef.current(discussMessage)
      onDiscussMessageSent?.()
    }
  }, [discussMessage, isStreaming, onDiscussMessageSent])

  // Handle key press
  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  // Handle suggestion dialog success
  const handleSuggestionSuccess = (suggestionId: string) => {
    setSuggestionDialogOpen(false)
    setSnackbar({
      open: true,
      message: `Suggestion submitted successfully (ID: ${suggestionId})`,
      severity: 'success',
    })
  }

  // Handle suggestion dialog error
  const handleSuggestionError = (error: string) => {
    setSnackbar({
      open: true,
      message: `Failed to submit suggestion: ${error}`,
      severity: 'error',
    })
  }

  const handleCloseDirectSubmission = useCallback(() => {
    if (!isSubmittingDirect && !submissionSent) {
      setConfirmDialogOpen(false)
      setFeedbackComment('')
    }
  }, [isSubmittingDirect, submissionSent])

  // Handle direct AI-assisted submission (bypasses chat UI)
  const handleDirectSubmission = useCallback(async (additionalComment?: string) => {
    setIsSubmittingDirect(true)

    try {
      // Filter and format messages for backend (exclude system messages, include only role+content)
      const conversationMessages = messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({
          role: m.role,
          content: m.content,
        }))

      // Build context based on active tab
      const feedbackContext: Record<string, unknown> = {
        trace_id: context?.trace_id || null,
        active_tab: context?.active_tab || 'agents',
      }

      // Add agents-specific context
      if (context?.active_tab !== 'flows') {
        feedbackContext.selected_agent_id = context?.selected_agent_id || selectedAgent?.agent_id || null
        feedbackContext.selected_group_id = context?.selected_group_id || null
        if (context?.active_tab === 'agent_workshop' && context?.agent_workshop) {
          feedbackContext.agent_workshop = context.agent_workshop
        }
      }

      // Add flows-specific context
      if (context?.active_tab === 'flows') {
        feedbackContext.flow_name = context?.flow_name || null
        feedbackContext.flow_definition = context?.flow_definition || null
      }

      // Add optional comment if provided
      if (additionalComment?.trim()) {
        feedbackContext.additional_comment = additionalComment.trim()
      }

      const response = await fetch('/api/agent-studio/submit-suggestion-direct', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          context: feedbackContext,
          messages: conversationMessages,
        }),
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const result = await response.json()

      if (result.success) {
        setFeedbackComment('')  // Clear comment on success
        setIsSubmittingDirect(false)
        setSubmissionSent(true)
        // Auto-close dialog after 1.5 seconds
        setTimeout(() => {
          setConfirmDialogOpen(false)
          setSubmissionSent(false)
        }, 1500)
      } else {
        throw new Error(result.error || 'Unknown error')
      }
    } catch (error) {
      setIsSubmittingDirect(false)
      setConfirmDialogOpen(false)
      setSnackbar({
        open: true,
        message: `Failed to submit suggestion: ${error instanceof Error ? error.message : 'Unknown error'}`,
        severity: 'error',
      })
    }
  }, [context, selectedAgent, messages])

  const handleApprovePromptUpdate = useCallback(() => {
    if (!pendingPromptUpdate) return
    if (!onApplyWorkshopPromptUpdate) {
      setSnackbar({
        open: true,
        message: 'Prompt update cannot be applied from this view.',
        severity: 'error',
      })
      setPromptUpdateDialogOpen(false)
      setPendingPromptUpdate(null)
      return
    }

    const approvedProposal = pendingPromptUpdate
    onApplyWorkshopPromptUpdate(approvedProposal)
    setAwaitingAppliedPromptUpdate(approvedProposal)
    setPromptUpdateDialogOpen(false)
    setPendingPromptUpdate(null)
    setMessages((prev) => [
      ...prev,
      {
        role: 'system',
        content: '✓ Prompt update sent to your Agent Workshop draft. I will verify it and run an automatic quality review once the draft updates.',
        timestamp: new Date().toISOString(),
      },
    ])
  }, [onApplyWorkshopPromptUpdate, pendingPromptUpdate])

  const handleCancelPromptUpdate = useCallback(() => {
    setPromptUpdateDialogOpen(false)
    setPendingPromptUpdate(null)
  }, [])

  const handleApplyFlowProposal = useCallback(async () => {
    if (!pendingFlowProposal || !onApplyFlowProposal) {
      setSnackbar({
        open: true,
        message: 'This flow proposal cannot be applied from the current view.',
        severity: 'error',
      })
      return
    }
    setFlowProposalApplying(true)
    try {
      const result = await onApplyFlowProposal(pendingFlowProposal)
      setSnackbar({ open: true, message: result.message, severity: result.applied ? 'success' : 'error' })
      if (result.applied) {
        setPendingFlowProposal(null)
        setMessages((prev) => [
          ...prev,
          {
            role: 'system',
            content: '✓ Flow proposal applied to the in-memory draft. It has not been saved.',
            timestamp: new Date().toISOString(),
          },
        ])
      }
    } finally {
      setFlowProposalApplying(false)
    }
  }, [onApplyFlowProposal, pendingFlowProposal, setMessages, setPendingFlowProposal])

  const handleCancelFlowProposal = useCallback(() => {
    logger.info('Canceled transient flow proposal', {
      component: 'OpusChat',
      action: 'review_flow_authoring_proposal',
      metadata: { outcome: 'canceled' },
    })
    setPendingFlowProposal(null)
    setMessages((prev) => [
      ...prev,
      {
        role: 'system',
        content: 'Flow proposal canceled; the editor draft was not changed.',
        timestamp: new Date().toISOString(),
      },
    ])
  }, [setMessages, setPendingFlowProposal])

  useEffect(() => {
    if (!awaitingAppliedPromptUpdate) return
    if (context?.active_tab !== 'agent_workshop') return
    const targetPrompt = awaitingAppliedPromptUpdate.target_prompt === 'group' ? 'group' : 'main'
    const expectedGroupId = awaitingAppliedPromptUpdate.target_group_id?.trim().toUpperCase()
    const currentGroupId = context?.agent_workshop?.selected_group_id?.trim().toUpperCase()
    if (targetPrompt === 'group' && expectedGroupId && currentGroupId !== expectedGroupId) return

    const sourcePrompt = targetPrompt === 'group'
      ? context?.agent_workshop?.selected_group_prompt_draft
      : context?.agent_workshop?.prompt_draft
    if (!sourcePrompt) return

    const normalizedCurrent = normalizePromptForComparison(sourcePrompt)
    const normalizedExpected = normalizePromptForComparison(awaitingAppliedPromptUpdate.prompt)
    if (!normalizedCurrent || normalizedCurrent !== normalizedExpected) return

    const autoReviewRequest = buildAutoReviewRequest(awaitingAppliedPromptUpdate)
    setAwaitingAppliedPromptUpdate(null)
    const targetLabel = targetPrompt === 'group'
      ? `group prompt${expectedGroupId ? ` (${expectedGroupId})` : ''}`
      : 'main prompt'
    setMessages((prev) => [
      ...prev,
      {
        role: 'system',
        content: `✓ Prompt update confirmed in the ${targetLabel}. Starting an automatic post-apply review now.`,
        timestamp: new Date().toISOString(),
      },
    ])

    if (isStreaming || !handleSendRef.current) {
      setQueuedAutoReviewMessage(autoReviewRequest)
      return
    }
    handleSendRef.current(autoReviewRequest)
  }, [
    awaitingAppliedPromptUpdate,
    context?.active_tab,
    context?.agent_workshop?.prompt_draft,
    context?.agent_workshop?.selected_group_prompt_draft,
    context?.agent_workshop?.selected_group_id,
    isStreaming,
  ])

  useEffect(() => {
    if (!queuedAutoReviewMessage) return
    if (isStreaming || !handleSendRef.current) return

    const nextMessage = queuedAutoReviewMessage
    setQueuedAutoReviewMessage(null)
    handleSendRef.current(nextMessage)
  }, [queuedAutoReviewMessage, isStreaming])

  // Quick action buttons - agent-related suggestions (shown when on agents tab)
  const promptQuickActions = [
    { label: 'Discuss the prompts', prompt: 'Can you explain how the prompts work and how they\'re structured?' },
    { label: 'Ask general questions', prompt: 'I have some general questions about the program and the prompts.' },
    { label: 'Suggest improvements', prompt: 'What improvements would you suggest for this prompt?' },
  ]

  // Flow-specific suggestions (shown when on flows tab)
  const flowQuickActions = [
    { label: 'Verify my flow', prompt: buildFlowVerificationPrompt() },
    { label: 'Help build a flow', prompt: 'I want to build a new curation flow. Please help me design it starting with Initial Instructions. What should I define in my initial instructions, and what agents should follow?' },
    { label: 'Optimize my flow', prompt: 'Can you suggest optimizations for my current flow? I want to make sure it\'s efficient and well-designed.' },
  ]

  // Agent Workshop suggestions (shown when on agent_workshop tab)
  const workshopQuickActions = [
    { label: 'Critique this draft', prompt: 'Please refresh and critique my current Agent Workshop draft, inspect attached tool schemas if tool behavior matters, and suggest concrete edits.' },
    { label: 'Plan flow tests', prompt: 'Given my draft, what 3 flow-based validation tests should I run next, including one compare-with-template case?' },
    { label: 'Improve structure', prompt: 'Can you help me restructure this draft prompt so instructions and output expectations are clearer?' },
  ]

  // Trace-specific suggestions - only shown if trace_id exists
  const traceQuickActions = [
    { label: 'Discuss the trace', prompt: 'Can you help me understand what happened in this trace?' },
    { label: 'Issues I encountered', prompt: 'I had some issues with this trace. Can you help me figure out what went wrong?' },
    { label: 'Find out why it\'s not working', prompt: 'Things aren\'t working the way I expected. Can you help diagnose the issue?' },
  ]

  // Determine which quick actions to show based on active tab
  const activeTab = context?.active_tab || 'agents'
  const baseQuickActions =
    activeTab === 'flows'
      ? flowQuickActions
      : activeTab === 'agent_workshop'
      ? workshopQuickActions
      : promptQuickActions

  const selectedChipLabel =
    activeTab === 'agent_workshop'
      ? context?.agent_workshop?.custom_agent_name || context?.agent_workshop?.template_name || undefined
      : selectedAgent?.agent_name
  const durableSeedLabel = sourceSessionId
    ? `Loaded from durable chat ${formatShortSessionId(sourceSessionId)}`
    : null

  const handleQuickAction = (prompt: string) => {
    setInput(prompt)
  }

  const feedbackDisabled = messages.length === 0 || isStreaming || isSubmittingDirect
  const feedbackMenuOpen = Boolean(feedbackMenuAnchor)
  const closeFeedbackMenu = () => setFeedbackMenuAnchor(null)
  const hideLabel = variant === 'drawer' ? 'Close AI Chat' : 'Hide AI Chat'

  return (
    <ChatContainer>
      <ChatHeader>
        <AutoAwesomeIcon sx={{ color: 'primary.main', fontSize: 18 }} />
        <Typography
          component="h2"
          variant="subtitle2"
          sx={{ fontWeight: 600, fontSize: '0.85rem', whiteSpace: 'nowrap', lineHeight: 1 }}
        >
          AI Chat
        </Typography>
        {durableSeedLabel ? (
          <Chip
            color="info"
            size="small"
            variant="outlined"
            label={durableSeedLabel}
            sx={{ height: 20, fontSize: '0.7rem', maxWidth: 220 }}
          />
        ) : selectedChipLabel ? (
          <Chip
            size="small"
            label={selectedChipLabel}
            sx={{
              height: 20,
              fontSize: '0.7rem',
              maxWidth: 150,
              bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12),
              color: (theme) => (theme.palette.mode === 'light' ? theme.palette.primary.dark : theme.palette.primary.light),
            }}
          />
        ) : null}
        <Box sx={{ ml: 'auto', display: 'flex', gap: 0.25, alignItems: 'center', flexShrink: 0 }}>
          <Tooltip title="Send feedback to the developers">
            <HeaderIconButton
              size="small"
              aria-label="Send feedback"
              aria-haspopup="menu"
              aria-expanded={feedbackMenuOpen}
              aria-controls={feedbackMenuOpen ? 'opus-chat-feedback-menu' : undefined}
              onClick={(event) => setFeedbackMenuAnchor(event.currentTarget)}
            >
              {isSubmittingDirect ? <CircularProgress size={14} /> : <LightbulbIcon sx={{ fontSize: 18 }} />}
            </HeaderIconButton>
          </Tooltip>
          <Menu
            id="opus-chat-feedback-menu"
            anchorEl={feedbackMenuAnchor}
            open={feedbackMenuOpen}
            onClose={closeFeedbackMenu}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
            transformOrigin={{ vertical: 'top', horizontal: 'right' }}
          >
            <MenuItem
              disabled={feedbackDisabled}
              onClick={() => {
                closeFeedbackMenu()
                setConfirmDialogOpen(true)
              }}
            >
              <ListItemIcon>
                <AutoAwesomeIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="AI-assisted" />
            </MenuItem>
            <MenuItem
              disabled={feedbackDisabled}
              onClick={() => {
                closeFeedbackMenu()
                setSuggestionDialogOpen(true)
              }}
            >
              <ListItemIcon>
                <LightbulbIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="Manual" />
            </MenuItem>
          </Menu>
          {onHide && (
            <Tooltip title={`${hideLabel} (Ctrl+.)`}>
              <HeaderIconButton
                size="small"
                aria-label={hideLabel}
                aria-expanded="true"
                aria-controls={panelId}
                onClick={onHide}
              >
                {variant === 'drawer'
                  ? <CloseIcon sx={{ fontSize: 18 }} />
                  : <ChevronRightIcon sx={{ fontSize: 20 }} />}
              </HeaderIconButton>
            </Tooltip>
          )}
        </Box>
      </ChatHeader>

      <MessagesContainer>
        {messages.length === 0 ? (
          <Box
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              gap: 2,
              color: 'text.secondary',
            }}
          >
            <AutoAwesomeIcon sx={{ fontSize: 48, opacity: 0.5 }} />
            <Typography variant="body1" textAlign="center">
              {activeTab === 'flows' ? (
                <>
                  Ask AI Chat about curation flows, flow design,
                  <br />
                  or verify your current flow.
                </>
              ) : activeTab === 'agent_workshop' ? (
                <>
                  Ask AI Chat to improve your workshop prompt draft,
                  <br />
                  plan flow tests, and compare against the template-source prompt.
                </>
              ) : (
                <>
                  Ask AI Chat about prompts, prompt engineering,
                  <br />
                  or discuss improvements.
                </>
              )}
            </Typography>

            {/* Base suggestions - always shown */}
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2, width: '100%', maxWidth: 600 }}>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'center' }}>
                {baseQuickActions.map((action) => (
                  <Chip
                    key={action.label}
                    label={action.label}
                    onClick={() => handleQuickAction(action.prompt)}
                    clickable
                    variant="outlined"
                    size="small"
                  />
                ))}
              </Box>

              {/* Trace-specific suggestions - only if trace_id exists */}
              {context?.trace_id && (
                <>
                  <Divider sx={{ my: 1 }}>
                    <Chip label="Trace Analysis" size="small" />
                  </Divider>
                  <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'center' }}>
                    {traceQuickActions.map((action) => (
                      <Chip
                        key={action.label}
                        label={action.label}
                        onClick={() => handleQuickAction(action.prompt)}
                        clickable
                        variant="outlined"
                        size="small"
                        color="primary"
                      />
                    ))}
                  </Box>
                </>
              )}
            </Box>
          </Box>
        ) : (
          <>
            {messages.map((msg, idx) => (
              <Box key={idx}>
                {/* Show tool calls for assistant messages */}
                {msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.length > 0 && (
                  <Box sx={{ mb: 1, maxWidth: '85%' }}>
                    <Box
                      sx={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 0.5,
                        cursor: 'pointer',
                        color: 'warning.main',
                        mb: 0.5,
                      }}
                      onClick={() =>
                        setToolCallsExpanded((prev) => ({ ...prev, [idx]: !prev[idx] }))
                      }
                    >
                      <BuildIcon sx={{ fontSize: 16 }} />
                      <Typography variant="caption" sx={{ fontWeight: 500 }}>
                        Tool Calls ({msg.toolCalls.length})
                      </Typography>
                      {toolCallsExpanded[idx] ? (
                        <ExpandLessIcon sx={{ fontSize: 16, ml: 'auto' }} />
                      ) : (
                        <ExpandMoreIcon sx={{ fontSize: 16, ml: 'auto' }} />
                      )}
                    </Box>
                    <Collapse in={toolCallsExpanded[idx]}>
                      <ToolCallBox>
                        {msg.toolCalls.map((tc, tcIdx) => {
                          const resultText = formatToolResult(tc.result)
                          const isError = Boolean(
                            tc.result
                            && (
                              (tc.result as Record<string, unknown>).status === 'error'
                              || (tc.result as Record<string, unknown>).success === false
                            )
                          )

                          return (
                            <Box
                              key={tcIdx}
                              sx={{
                                mb: tcIdx < msg.toolCalls!.length - 1 ? 1.5 : 0,
                                pb: tcIdx < msg.toolCalls!.length - 1 ? 1.5 : 0,
                                borderBottom: tcIdx < msg.toolCalls!.length - 1 ? '1px solid' : 'none',
                                borderColor: 'divider',
                              }}
                            >
                              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                                <Chip
                                  label={tc.tool_name}
                                  size="small"
                                  sx={{
                                    height: 20,
                                    fontSize: '0.7rem',
                                    fontWeight: 600,
                                    bgcolor: 'warning.main',
                                    color: 'warning.contrastText',
                                  }}
                                />
                                {resultText && (
                                  <Typography
                                    variant="caption"
                                    sx={{
                                      color: isError ? 'error.main' : 'success.main',
                                      fontWeight: 500,
                                    }}
                                  >
                                    {resultText}
                                  </Typography>
                                )}
                              </Box>
                              <Box
                                sx={{
                                  bgcolor: 'grey.900',
                                  borderRadius: 1,
                                  p: 1,
                                  fontFamily: 'monospace',
                                  fontSize: '0.7rem',
                                  whiteSpace: 'pre-wrap',
                                  wordBreak: 'break-word',
                                  color: 'grey.300',
                                }}
                              >
                                {formatToolInput(tc.tool_name, tc.tool_input)}
                              </Box>
                            </Box>
                          )
                        })}
                      </ToolCallBox>
                    </Collapse>
                  </Box>
                )}
                <MessageBubble
                  isUser={msg.role === 'user'}
                  isSystem={msg.role === 'system'}
                  elevation={0}
                >
                  {msg.role === 'system' && (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
                      <CheckCircleIcon sx={{ fontSize: 16, color: 'success.main' }} />
                      <Typography variant="caption" sx={{ fontWeight: 500, color: 'success.main' }}>
                        System
                      </Typography>
                    </Box>
                  )}
                  <Typography variant="body2">{msg.content}</Typography>
                </MessageBubble>
              </Box>
            ))}
            {isStreaming && (
              <Box
                role="status"
                aria-live="polite"
                sx={{ display: 'flex', alignItems: 'center', gap: 1, color: 'text.secondary' }}
              >
                <CircularProgress size={16} />
                <Typography variant="body2">
                  {streamStatus || 'AI Chat is responding…'}
                </Typography>
              </Box>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </MessagesContainer>

      <InputContainer>
        <TextField
          fullWidth
          multiline
          maxRows={4}
          placeholder={
              activeTab === 'flows'
              ? 'Ask about flows...'
              : activeTab === 'agent_workshop'
              ? 'Ask about your workshop draft...'
              : 'Ask about prompts...'
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyPress}
          disabled={isStreaming}
          size="small"
          inputRef={inputRef}
          sx={{
            '& .MuiOutlinedInput-root': {
              borderRadius: 2,
            },
          }}
        />
        <Tooltip title="Send message">
          <span>
            <IconButton
              color="primary"
              onClick={() => handleSend()}
              disabled={!input.trim() || isStreaming}
              sx={{
                backgroundColor: 'primary.main',
                color: 'primary.contrastText',
                '&:hover': {
                  backgroundColor: 'primary.dark',
                },
                '&.Mui-disabled': {
                  backgroundColor: 'action.disabledBackground',
                },
              }}
            >
              {isStreaming ? <CircularProgress size={20} color="inherit" /> : <SendIcon />}
            </IconButton>
          </span>
        </Tooltip>
      </InputContainer>

      {/* Confirmation surface for AI-Assisted Submission */}
      <ModelessFeedbackSurface
        open={confirmDialogOpen}
        onClose={handleCloseDirectSubmission}
        title="Submit Feedback to Developers?"
        titleIcon={<AutoAwesomeIcon color="primary" />}
        width="sm"
        moveControlLabel="Move feedback popup"
        closeControlLabel="Close feedback popup"
        actions={!submissionSent && (
          <>
            <Button
              onClick={() => {
                setConfirmDialogOpen(false)
                setFeedbackComment('')
              }}
              color="inherit"
              disabled={isSubmittingDirect}
            >
              Cancel
            </Button>
            <Button
              onClick={() => handleDirectSubmission(feedbackComment)}
              variant="contained"
              color="primary"
              disabled={isSubmittingDirect}
              startIcon={isSubmittingDirect ? <CircularProgress size={16} /> : <AutoAwesomeIcon />}
            >
              {isSubmittingDirect ? 'Submitting...' : 'Submit'}
            </Button>
          </>
        )}
      >
        {submissionSent ? (
          <Box sx={{ textAlign: 'center', py: 4 }}>
            <CheckCircleIcon sx={{ fontSize: 64, color: 'success.main', mb: 2 }} />
            <Typography sx={{ fontSize: '1.25rem', fontWeight: 500 }}>
              Submission sent!
            </Typography>
          </Box>
        ) : (
          <>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              AI Chat will analyze your conversation and submit a feedback report to the development team.
            </Typography>
            <TextField
              autoFocus
              fullWidth
              multiline
              rows={3}
              placeholder="Add any additional comments for the developers (optional)"
              value={feedbackComment}
              onChange={(e) => setFeedbackComment(e.target.value)}
              variant="outlined"
              size="small"
              disabled={isSubmittingDirect}
            />
          </>
        )}
      </ModelessFeedbackSurface>

      {/* Process-local review surface for Flow Builder proposals. */}
      <Dialog
        open={Boolean(pendingFlowProposal)}
        onClose={flowProposalApplying ? undefined : handleCancelFlowProposal}
        maxWidth="md"
        fullWidth
        aria-labelledby="flow-proposal-review-title"
      >
        <DialogTitle id="flow-proposal-review-title">Review Flow Builder Proposal</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1.5 }}>
            Review the exact draft changes below. Apply changes only updates the in-memory
            Flow Builder draft; you must still use Save to persist it.
          </DialogContentText>
          {pendingFlowProposal?.change_summary && (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              {pendingFlowProposal.change_summary}
            </Alert>
          )}
          <Alert severity="success" sx={{ mb: 1.5 }} role="status" aria-live="polite">
            Canonical validation passed with {pendingFlowProposal?.findings.length ?? 0} non-blocking
            finding{pendingFlowProposal?.findings.length === 1 ? '' : 's'}.
          </Alert>
          {pendingFlowProposal?.findings.map((finding, index) => (
            <Alert
              key={`${finding.code}-${finding.path}-${index}`}
              severity={finding.severity === 'warning' ? 'warning' : finding.severity === 'error' ? 'error' : 'info'}
              sx={{ mb: 1 }}
            >
              {finding.message} ({finding.path})
            </Alert>
          ))}
          <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
            Exact changes ({pendingFlowProposal?.diff.length ?? 0})
          </Typography>
          <Box
            component="ol"
            aria-label="Exact flow proposal changes"
            sx={{
              m: 0,
              pl: 3.5,
              py: 1,
              pr: 1,
              maxHeight: 360,
              overflow: 'auto',
              border: (theme) => `1px solid ${theme.palette.divider}`,
              borderRadius: 1,
              bgcolor: 'background.default',
            }}
          >
            {pendingFlowProposal?.diff.map((entry, index) => (
              <Box component="li" key={`${entry.path}-${index}`} sx={{ mb: 0.75 }}>
                <Chip
                  size="small"
                  label={entry.kind.toUpperCase()}
                  color={entry.kind === 'added' ? 'success' : entry.kind === 'removed' ? 'error' : 'warning'}
                  variant="outlined"
                  sx={{ mr: 1, minWidth: 76 }}
                />
                <Typography component="code" variant="caption" sx={{ wordBreak: 'break-word' }}>
                  {entry.path}
                </Typography>
                {Object.prototype.hasOwnProperty.call(entry, 'before') && (
                  <Box sx={{ mt: 0.5 }}>
                    <Typography component="span" variant="caption" fontWeight={600}>
                      Before
                    </Typography>
                    <Typography
                      component="pre"
                      variant="caption"
                      sx={{ m: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                    >
                      {formatFlowDiffValue(entry.before)}
                    </Typography>
                  </Box>
                )}
                {Object.prototype.hasOwnProperty.call(entry, 'after') && (
                  <Box sx={{ mt: 0.5 }}>
                    <Typography component="span" variant="caption" fontWeight={600}>
                      After
                    </Typography>
                    <Typography
                      component="pre"
                      variant="caption"
                      sx={{ m: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
                    >
                      {formatFlowDiffValue(entry.after)}
                    </Typography>
                  </Box>
                )}
              </Box>
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelFlowProposal} color="inherit" disabled={flowProposalApplying}>
            Cancel
          </Button>
          <Button
            onClick={() => void handleApplyFlowProposal()}
            variant="contained"
            disabled={flowProposalApplying || !onApplyFlowProposal}
            startIcon={flowProposalApplying ? <CircularProgress size={16} color="inherit" /> : undefined}
          >
            {flowProposalApplying ? 'Applying…' : 'Apply changes'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Approval Dialog for Workshop Prompt Updates */}
      <Dialog
        open={promptUpdateDialogOpen}
        onClose={handleCancelPromptUpdate}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Apply AI Chat Prompt Update?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1.5 }}>
            AI Chat generated a {pendingPromptUpdate?.apply_mode === 'targeted_edit' ? 'targeted prompt update' : 'full replacement prompt'} for your {pendingPromptUpdate?.target_prompt === 'group' ? `group prompt${pendingPromptUpdate?.target_group_id ? ` (${pendingPromptUpdate.target_group_id})` : ''}` : 'main prompt'} draft. Review below, then choose whether to apply it.
          </DialogContentText>
          {pendingPromptUpdate?.summary && (
            <Alert severity="info" sx={{ mb: 1.5 }}>
              {pendingPromptUpdate.summary}
            </Alert>
          )}
          <Alert severity="success" sx={{ mb: 1.5 }}>
            Proposed additions are highlighted in green ({addedLineCount} line{addedLineCount === 1 ? '' : 's'}).
          </Alert>
          {removedLineCount > 0 && (
            <Alert severity="warning" sx={{ mb: 1.5 }}>
              Proposed removals are highlighted in red with strikethrough ({removedLineCount} line{removedLineCount === 1 ? '' : 's'}).
            </Alert>
          )}
          <Box
            sx={{
              border: (theme) => `1px solid ${theme.palette.divider}`,
              borderRadius: 1,
              maxHeight: 420,
              overflow: 'auto',
              bgcolor: 'background.default',
              px: 1,
              py: 1,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              fontSize: '0.8rem',
            }}
          >
            {promptLineDiff.map((entry, idx) => (
              <Box
                key={`proposal-line-${idx}`}
                component="div"
                sx={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  bgcolor:
                    entry.kind === 'added'
                      ? (theme) => alpha(theme.palette.success.main, 0.16)
                      : entry.kind === 'removed'
                      ? (theme) => alpha(theme.palette.error.main, 0.16)
                      : 'transparent',
                  color: entry.kind === 'removed' ? 'error.main' : 'inherit',
                  textDecoration: entry.kind === 'removed' ? 'line-through' : 'none',
                  px: 0.5,
                  borderRadius: 0.5,
                }}
              >
                {entry.line || ' '}
              </Box>
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelPromptUpdate} color="inherit">
            Cancel
          </Button>
          <Button onClick={handleApprovePromptUpdate} variant="contained">
            Apply to Draft
          </Button>
        </DialogActions>
      </Dialog>

      {/* Suggestion Dialog */}
      <SuggestionDialog
        open={suggestionDialogOpen}
        onClose={() => setSuggestionDialogOpen(false)}
        onSuccess={handleSuggestionSuccess}
        onError={handleSuggestionError}
        context={context}
        selectedAgent={selectedAgent}
      />

      {/* Snackbar for notifications */}
      <Snackbar
        open={snackbar.open}
        autoHideDuration={6000}
        onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={() => setSnackbar((prev) => ({ ...prev, open: false }))}
          severity={snackbar.severity}
          sx={{ width: '100%' }}
        >
          {snackbar.message}
        </Alert>
      </Snackbar>
    </ChatContainer>
  )
}

export default OpusChat
