/**
 * OpusChat Component
 *
 * Chat interface for conversing with Claude Opus 4.5 about prompts.
 * Includes tool support for suggestion submission.
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
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
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import SendIcon from '@mui/icons-material/Send'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import LightbulbIcon from '@mui/icons-material/Lightbulb'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import BuildIcon from '@mui/icons-material/Build'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import { streamOpusChat } from '@/services/agentStudioService'
import type {
  ChatMessage,
  ChatContext,
  PromptInfo,
  OpusChatEvent,
  ToolIdeaConversationEntry,
  WorkshopPromptUpdateProposal,
} from '@/types/promptExplorer'
import SuggestionDialog from './SuggestionDialog'

const ChatContainer = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.shape.borderRadius,
  overflow: 'hidden',
}))

const ChatHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1),
}))

const MessagesContainer = styled(Box)(({ theme }) => ({
  flex: 1,
  overflow: 'auto',
  padding: theme.spacing(2),
  display: 'flex',
  flexDirection: 'column',
  gap: theme.spacing(2),
}))

const MessageBubble = styled(Paper, {
  shouldForwardProp: (prop) => prop !== 'isUser' && prop !== 'isSystem',
})<{ isUser?: boolean; isSystem?: boolean }>(({ theme, isUser, isSystem }) => ({
  padding: theme.spacing(1.5, 2),
  maxWidth: '85%',
  alignSelf: isUser ? 'flex-end' : isSystem ? 'center' : 'flex-start',
  backgroundColor: isUser
    ? theme.palette.primary.main
    : isSystem
    ? alpha(theme.palette.success.main, 0.1)
    : alpha(theme.palette.background.default, 0.6),
  color: isUser ? theme.palette.primary.contrastText : theme.palette.text.primary,
  borderRadius: theme.spacing(2),
  borderBottomRightRadius: isUser ? theme.spacing(0.5) : theme.spacing(2),
  borderBottomLeftRadius: isUser ? theme.spacing(2) : isSystem ? theme.spacing(2) : theme.spacing(0.5),
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  ...(isSystem && {
    border: `1px solid ${alpha(theme.palette.success.main, 0.3)}`,
  }),
}))

const InputContainer = styled(Box)(({ theme }) => ({
  padding: theme.spacing(2),
  borderTop: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  gap: theme.spacing(1),
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
  result?: Record<string, unknown>
}

// Extended message type to include system messages and tool calls
interface DisplayMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
  toolCalls?: ToolCallRecord[]  // Tool calls made during this message
}

interface OpusChatProps {
  context: ChatContext
  selectedAgent?: PromptInfo
  /** Message to auto-send (e.g., from Verify with Claude button) */
  verifyMessage?: string | null
  /** Callback after verify message is sent */
  onVerifyMessageSent?: () => void
  /** Message to auto-send (e.g., from Discuss with Claude button) */
  discussMessage?: string | null
  /** Callback after discuss message is sent */
  onDiscussMessageSent?: () => void
  /** Callback with current chat transcript for workshop tool ideation */
  onConversationSnapshotChange?: (messages: ToolIdeaConversationEntry[]) => void
  /** Apply an approved prompt replacement into the Agent Workshop editor */
  onApplyWorkshopPromptUpdate?: (proposal: WorkshopPromptUpdateProposal) => void
}

interface ProposedLineDiff {
  line: string
  added: boolean
}

function normalizePromptForComparison(value: string | undefined | null): string {
  return (value || '').replace(/\r\n/g, '\n').trim()
}

function buildAddedLineDiff(currentPrompt: string, proposedPrompt: string): ProposedLineDiff[] {
  const currentLines = currentPrompt.replace(/\r\n/g, '\n').split('\n')
  const proposedLines = proposedPrompt.replace(/\r\n/g, '\n').split('\n')

  const currentLineCounts = new Map<string, number>()
  for (const line of currentLines) {
    currentLineCounts.set(line, (currentLineCounts.get(line) || 0) + 1)
  }

  return proposedLines.map((line) => {
    const existingCount = currentLineCounts.get(line) || 0
    if (existingCount > 0) {
      currentLineCounts.set(line, existingCount - 1)
      return { line, added: false }
    }
    return { line, added: true }
  })
}

function buildRemovedLineDiff(currentPrompt: string, proposedPrompt: string): ProposedLineDiff[] {
  const currentLines = currentPrompt.replace(/\r\n/g, '\n').split('\n')
  const proposedLines = proposedPrompt.replace(/\r\n/g, '\n').split('\n')

  const proposedLineCounts = new Map<string, number>()
  for (const line of proposedLines) {
    proposedLineCounts.set(line, (proposedLineCounts.get(line) || 0) + 1)
  }

  return currentLines.flatMap((line) => {
    const existingCount = proposedLineCounts.get(line) || 0
    if (existingCount > 0) {
      proposedLineCounts.set(line, existingCount - 1)
      return []
    }
    return [{ line, added: false }]
  })
}

function buildAutoReviewRequest(proposal: WorkshopPromptUpdateProposal): string {
  const summaryText = proposal.summary?.trim()
    ? proposal.summary.trim()
    : 'No summary provided.'
  const targetPrompt = proposal.target_prompt === 'mod' ? 'MOD prompt draft' : 'main workshop prompt draft'
  const modLabel = proposal.target_prompt === 'mod' && proposal.target_mod_id
    ? ` (${proposal.target_mod_id})`
    : ''
  return `Please run a post-apply review of my Agent Workshop draft.\n\nTarget reviewed: ${targetPrompt}${modLabel}\n\nChecklist:\n1. Confirm the intended update is present in the current target prompt draft.\n2. Flag any regressions, contradictions, or ambiguities introduced by the edit.\n3. Suggest one follow-up tweak only if it clearly improves behavior.\n\nApplied update summary: ${summaryText}`
}

function OpusChat({
  context,
  selectedAgent,
  verifyMessage,
  onVerifyMessageSent,
  discussMessage,
  onDiscussMessageSent,
  onConversationSnapshotChange,
  onApplyWorkshopPromptUpdate,
}: OpusChatProps) {
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [toolCallsExpanded, setToolCallsExpanded] = useState<{ [key: number]: boolean }>({})  // Track expanded state per message
  const [suggestionDialogOpen, setSuggestionDialogOpen] = useState(false)
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false)
  const [feedbackComment, setFeedbackComment] = useState('')
  const [isSubmittingDirect, setIsSubmittingDirect] = useState(false)
  const [submissionSent, setSubmissionSent] = useState(false)
  const [promptUpdateDialogOpen, setPromptUpdateDialogOpen] = useState(false)
  const [pendingPromptUpdate, setPendingPromptUpdate] = useState<WorkshopPromptUpdateProposal | null>(null)
  const [awaitingAppliedPromptUpdate, setAwaitingAppliedPromptUpdate] = useState<WorkshopPromptUpdateProposal | null>(null)
  const [queuedAutoReviewMessage, setQueuedAutoReviewMessage] = useState<string | null>(null)
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string; severity: 'success' | 'error' }>({
    open: false,
    message: '',
    severity: 'success',
  })
  const messagesEndRef = useRef<HTMLDivElement>(null)

  // Scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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

  // Reference for auto-sending verify message
  const handleSendRef = useRef<(messageText: string) => Promise<void>>()
  // Track which verify message was already sent to prevent duplicates
  const verifyMessageSentRef = useRef<string | null>(null)
  // Track which discuss message was already sent to prevent duplicates
  const discussMessageSentRef = useRef<string | null>(null)
  const currentMainWorkshopDraft = context?.agent_workshop?.prompt_draft || ''
  const currentModWorkshopDraft = context?.agent_workshop?.selected_mod_prompt_draft || ''
  const currentPromptForPendingUpdate =
    pendingPromptUpdate?.target_prompt === 'mod' ? currentModWorkshopDraft : currentMainWorkshopDraft
  const proposedLineDiff = useMemo(
    () => buildAddedLineDiff(currentPromptForPendingUpdate, pendingPromptUpdate?.prompt || ''),
    [currentPromptForPendingUpdate, pendingPromptUpdate?.prompt]
  )
  const removedLineDiff = useMemo(
    () => buildRemovedLineDiff(currentPromptForPendingUpdate, pendingPromptUpdate?.prompt || ''),
    [currentPromptForPendingUpdate, pendingPromptUpdate?.prompt]
  )
  const addedLineCount = useMemo(
    () => proposedLineDiff.filter((entry) => entry.added).length,
    [proposedLineDiff]
  )
  const removedLineCount = useMemo(
    () => removedLineDiff.length,
    [removedLineDiff]
  )

  // Handle tool events from Opus - add tool calls to the current assistant message
  const handleToolEvent = useCallback((event: OpusChatEvent) => {
    if (event.type === 'TOOL_USE' && event.tool_name && event.tool_input) {
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
      const toolResult = event.result as Record<string, unknown>
      // Update the last tool call with its result
      setMessages((prev) => {
        const updated = [...prev]
        const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
        if (lastAssistantIdx !== -1 && updated[lastAssistantIdx].toolCalls?.length) {
          const toolCalls = [...(updated[lastAssistantIdx].toolCalls || [])]
          const lastToolIdx = toolCalls.length - 1
          if (lastToolIdx >= 0) {
            toolCalls[lastToolIdx] = {
              ...toolCalls[lastToolIdx],
              result: toolResult,
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
          toolResult.target_prompt === 'mod'
            ? 'mod'
            : 'main'
        const targetModId =
          typeof toolResult.target_mod_id === 'string' && toolResult.target_mod_id.trim()
            ? toolResult.target_mod_id.trim().toUpperCase()
            : undefined

        if (success && proposedPrompt) {
          setPendingPromptUpdate({
            prompt: proposedPrompt,
            summary: changeSummary,
            apply_mode: applyMode || 'replace',
            target_prompt: targetPrompt,
            target_mod_id: targetPrompt === 'mod' ? targetModId : undefined,
          })
          setPromptUpdateDialogOpen(true)
          const targetLabel = targetPrompt === 'mod'
            ? `MOD prompt${targetModId ? ` (${targetModId})` : ''}`
            : 'main prompt'
          setMessages((prev) => [
            ...prev,
            {
              role: 'system',
              content: `Claude prepared a ${targetLabel} update proposal. Review and approve it to apply to your workshop draft.`,
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
    }
  }, [])

  // Handle sending a message (optionally with a specific message text for auto-send)
  const handleSend = useCallback(async (messageOverride?: string) => {
    const messageText = messageOverride || input.trim()
    if (!messageText || isStreaming) return

    const userMessage: DisplayMessage = {
      role: 'user',
      content: messageText,
      timestamp: new Date().toISOString(),
    }
    const newMessages = [...messages, userMessage]
    setMessages(newMessages)
    if (!messageOverride) setInput('')  // Only clear input if not using override
    setIsStreaming(true)

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
      for await (const event of streamOpusChat(apiMessages, context)) {
        if (event.type === 'TEXT_DELTA' && event.delta) {
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
        } else if (event.type === 'TOOL_USE' || event.type === 'TOOL_RESULT') {
          handleToolEvent(event)
        } else if (event.type === 'ERROR') {
          setMessages((prev) => {
            const updated = [...prev]
            const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
            if (lastAssistantIdx !== -1) {
              updated[lastAssistantIdx] = {
                ...updated[lastAssistantIdx],
                content: `Error: ${event.message || 'Unknown error'}`,
              }
            }
            return updated
          })
          break
        } else if (event.type === 'DONE') {
          break
        }
      }
    } catch {
      setMessages((prev) => {
        const updated = [...prev]
        const lastAssistantIdx = updated.findLastIndex((m) => m.role === 'assistant')
        if (lastAssistantIdx !== -1 && !updated[lastAssistantIdx].content) {
          updated[lastAssistantIdx] = {
            ...updated[lastAssistantIdx],
            content: 'Sorry, an error occurred. Please try again.',
          }
        }
        return updated
      })
    } finally {
      setIsStreaming(false)
    }
  }, [input, messages, context, isStreaming, handleToolEvent])

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

  // Auto-send discuss message when provided (from AgentDetailsPanel's Discuss with Claude button)
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
        feedbackContext.selected_mod_id = context?.selected_mod_id || null
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

  useEffect(() => {
    if (!awaitingAppliedPromptUpdate) return
    if (context?.active_tab !== 'agent_workshop') return
    const targetPrompt = awaitingAppliedPromptUpdate.target_prompt === 'mod' ? 'mod' : 'main'
    const expectedModId = awaitingAppliedPromptUpdate.target_mod_id?.trim().toUpperCase()
    const currentModId = context?.agent_workshop?.selected_mod_id?.trim().toUpperCase()
    if (targetPrompt === 'mod' && expectedModId && currentModId !== expectedModId) return

    const sourcePrompt = targetPrompt === 'mod'
      ? context?.agent_workshop?.selected_mod_prompt_draft
      : context?.agent_workshop?.prompt_draft
    if (!sourcePrompt) return

    const normalizedCurrent = normalizePromptForComparison(sourcePrompt)
    const normalizedExpected = normalizePromptForComparison(awaitingAppliedPromptUpdate.prompt)
    if (!normalizedCurrent || normalizedCurrent !== normalizedExpected) return

    const autoReviewRequest = buildAutoReviewRequest(awaitingAppliedPromptUpdate)
    setAwaitingAppliedPromptUpdate(null)
    const targetLabel = targetPrompt === 'mod'
      ? `MOD prompt${expectedModId ? ` (${expectedModId})` : ''}`
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
    context?.agent_workshop?.selected_mod_prompt_draft,
    context?.agent_workshop?.selected_mod_id,
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
    { label: 'Verify my flow', prompt: `Verify my current curation flow.

REQUIRED: Call these tools first:
1. get_current_flow() - returns flow definition with validation_warnings and has_critical_issues flag
2. get_available_agents() - get agent categories and output_agents list

IMPORTANT: Check get_current_flow() response for:
- has_critical_issues: true/false - if TRUE, verification MUST FAIL
- validation_warnings: array of issues with type (CRITICAL/WARNING) and message
- task_instructions_is_empty: true in any step means CRITICAL error

CRITICAL ERRORS (must fail verification):
- has_critical_issues is TRUE in get_current_flow() response
- Any step has task_instructions_is_empty: true
- task_input node has EMPTY task_instructions (this is required content)
- Disconnected nodes (won't execute)
- Cycles (infinite loops)
- Input sources referencing non-existent outputs
- PARALLEL/BRANCHING FLOWS: Any node with multiple outgoing edges is NOT YET SUPPORTED (parallel flows will be supported in a future update - for now, each node can only connect to ONE next node)

HIGH PRIORITY ISSUES:
- Flow doesn't end with an output-category agent
- Duplicate output_key values

SUGGESTIONS (only if evidence-based):
- ONLY suggest alternative agents if the curator's task_instructions or custom_instructions explicitly mention something that a different agent handles better
- Do NOT make speculative suggestions without evidence from the instructions

OUTPUT:
### FLOW VERIFICATION: [PASS/FAIL]
**Critical:** [list or "None"]
**High:** [list or "None"]
**Suggestions:** [evidence-based only, or "None"]` },
    { label: 'Help build a flow', prompt: 'I want to build a new curation flow. Please help me design it starting with Initial Instructions. What should I define in my initial instructions, and what agents should follow?' },
    { label: 'Optimize my flow', prompt: 'Can you suggest optimizations for my current flow? I want to make sure it\'s efficient and well-designed.' },
  ]

  // Agent Workshop suggestions (shown when on agent_workshop tab)
  const workshopQuickActions = [
    { label: 'Critique this draft', prompt: 'Please critique my current Agent Workshop draft and suggest concrete edits.' },
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

  const handleQuickAction = (prompt: string) => {
    setInput(prompt)
  }

  return (
    <ChatContainer>
      <ChatHeader>
        <AutoAwesomeIcon sx={{ color: 'primary.main', fontSize: 20 }} />
        <Typography variant="subtitle1" sx={{ fontWeight: 500, whiteSpace: 'nowrap' }}>
          Chat with Claude
        </Typography>
        {selectedChipLabel && (
          <Chip
            size="small"
            label={selectedChipLabel}
            sx={{ ml: 0.5, maxWidth: 150 }}
          />
        )}
        <Box sx={{ ml: 'auto', display: 'flex', gap: 0.5, alignItems: 'center', flexShrink: 0 }}>
          <Typography variant="caption" sx={{ color: 'text.secondary', mr: 0.5, whiteSpace: 'nowrap' }}>
            Contact Devs:
          </Typography>
          <Tooltip title="Have Claude analyze your conversation and submit feedback to developers">
            <Button
              variant="outlined"
              size="small"
              startIcon={<AutoAwesomeIcon sx={{ fontSize: 16 }} />}
              onClick={() => setConfirmDialogOpen(true)}
              disabled={messages.length === 0 || isStreaming || isSubmittingDirect}
              sx={{ fontSize: '0.75rem', py: 0.5, px: 1, minWidth: 'auto' }}
            >
              {isSubmittingDirect ? '...' : 'AI-Assisted'}
            </Button>
          </Tooltip>
          <Tooltip title="Open a form to write and submit your feedback directly">
            <Button
              variant="outlined"
              size="small"
              startIcon={<LightbulbIcon sx={{ fontSize: 16 }} />}
              onClick={() => setSuggestionDialogOpen(true)}
              disabled={messages.length === 0 || isStreaming || isSubmittingDirect}
              sx={{ fontSize: '0.75rem', py: 0.5, px: 1, minWidth: 'auto' }}
            >
              Manual
            </Button>
          </Tooltip>
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
                  Ask Claude about curation flows, flow design,
                  <br />
                  or verify your current flow.
                </>
              ) : activeTab === 'agent_workshop' ? (
                <>
                  Ask Claude to improve your workshop prompt draft,
                  <br />
                  plan flow tests, and compare against the template-source prompt.
                </>
              ) : (
                <>
                  Ask Claude about prompts, prompt engineering,
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
                          // Format input nicely - special handling for tools with queries
                          const formatInput = () => {
                            const input = tc.tool_input
                            // Handle SQL query tools
                            if (input.query && typeof input.query === 'string') {
                              return input.query
                            }
                            // Handle API tools with specific parameters - show key fields
                            if (tc.tool_name === 'agr_curation_query') {
                              const parts: string[] = []
                              if (input.entity_type) parts.push(`Entity: ${input.entity_type}`)
                              if (input.search_term) parts.push(`Search: "${input.search_term}"`)
                              if (input.mod_id) parts.push(`MOD: ${input.mod_id}`)
                              if (input.limit) parts.push(`Limit: ${input.limit}`)
                              return parts.length > 0 ? parts.join('\n') : JSON.stringify(input, null, 2)
                            }
                            if (tc.tool_name === 'get_prompt') {
                              const parts: string[] = []
                              if (input.agent_id) parts.push(`Agent: ${input.agent_id}`)
                              if (input.mod_id) parts.push(`MOD: ${input.mod_id}`)
                              return parts.length > 0 ? parts.join(', ') : JSON.stringify(input, null, 2)
                            }
                            if (tc.tool_name.includes('api_call')) {
                              // Format API calls nicely
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

                          // Format result summary
                          const formatResult = () => {
                            if (!tc.result) return null
                            const result = tc.result as Record<string, unknown>
                            if (result.status === 'ok' && Array.isArray(result.rows)) {
                              const count = result.count || result.rows.length
                              if (result.rows.length === 0) {
                                return '✓ No results'
                              }
                              return `✓ ${count} row${count !== 1 ? 's' : ''} returned`
                            }
                            if (result.status === 'error') {
                              return `✗ Error: ${result.message || 'Unknown error'}`
                            }
                            // Truncate other results
                            const str = JSON.stringify(result, null, 2)
                            return str.length > 200 ? str.slice(0, 200) + '...' : str
                          }

                          const resultText = formatResult()
                          const isError = tc.result && (tc.result as Record<string, unknown>).status === 'error'

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
                                {formatInput()}
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
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: 'text.secondary' }}>
                <CircularProgress size={16} />
                <Typography variant="body2">
Claude is responding...
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

      {/* Confirmation Dialog for AI-Assisted Submission */}
      <Dialog
        open={confirmDialogOpen}
        onClose={() => {
          if (!isSubmittingDirect && !submissionSent) {
            setConfirmDialogOpen(false)
            setFeedbackComment('')
          }
        }}
        maxWidth="sm"
        fullWidth
      >
        {submissionSent ? (
          // Success state
          <>
            <DialogContent sx={{ textAlign: 'center', py: 4 }}>
              <CheckCircleIcon sx={{ fontSize: 64, color: 'success.main', mb: 2 }} />
              <DialogContentText sx={{ fontSize: '1.25rem', fontWeight: 500 }}>
                Submission sent!
              </DialogContentText>
            </DialogContent>
          </>
        ) : (
          // Normal state
          <>
            <DialogTitle>Submit Feedback to Developers?</DialogTitle>
            <DialogContent>
              <DialogContentText sx={{ mb: 2 }}>
                Claude will analyze your conversation and submit a feedback report to the development team.
              </DialogContentText>
              <TextField
                fullWidth
                multiline
                rows={3}
                placeholder="Add any additional comments for the developers (optional)"
                value={feedbackComment}
                onChange={(e) => setFeedbackComment(e.target.value)}
                variant="outlined"
                size="small"
                sx={{ mt: 1 }}
                disabled={isSubmittingDirect}
              />
            </DialogContent>
            <DialogActions>
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
            </DialogActions>
          </>
        )}
      </Dialog>

      {/* Approval Dialog for Workshop Prompt Updates */}
      <Dialog
        open={promptUpdateDialogOpen}
        onClose={handleCancelPromptUpdate}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>Apply Claude Prompt Update?</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1.5 }}>
            Claude generated a {pendingPromptUpdate?.apply_mode === 'targeted_edit' ? 'targeted prompt update' : 'full replacement prompt'} for your {pendingPromptUpdate?.target_prompt === 'mod' ? `MOD prompt${pendingPromptUpdate?.target_mod_id ? ` (${pendingPromptUpdate.target_mod_id})` : ''}` : 'main prompt'} draft. Review below, then choose whether to apply it.
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
            {proposedLineDiff.map((entry, idx) => (
              <Box
                key={`proposal-line-${idx}`}
                component="div"
                sx={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  bgcolor: entry.added ? (theme) => alpha(theme.palette.success.main, 0.16) : 'transparent',
                  px: 0.5,
                  borderRadius: 0.5,
                }}
              >
                {entry.line || ' '}
              </Box>
            ))}
          </Box>
          {removedLineCount > 0 && (
            <Box sx={{ mt: 1.5 }}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
                Removed lines
              </Typography>
              <Box
                sx={{
                  border: (theme) => `1px solid ${theme.palette.divider}`,
                  borderRadius: 1,
                  maxHeight: 220,
                  overflow: 'auto',
                  bgcolor: 'background.default',
                  px: 1,
                  py: 1,
                  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                  fontSize: '0.8rem',
                }}
              >
                {removedLineDiff.map((entry, idx) => (
                  <Box
                    key={`removed-line-${idx}`}
                    component="div"
                    sx={{
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                      bgcolor: (theme) => alpha(theme.palette.error.main, 0.16),
                      color: 'error.main',
                      textDecoration: 'line-through',
                      px: 0.5,
                      borderRadius: 0.5,
                    }}
                  >
                    {entry.line || ' '}
                  </Box>
                ))}
              </Box>
            </Box>
          )}
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
