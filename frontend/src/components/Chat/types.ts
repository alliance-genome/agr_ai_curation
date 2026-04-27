import type { CSSProperties } from 'react'

import type { CurationWorkspaceLaunchTarget } from '@/features/curation/navigation/openCurationWorkspace'
import type { EvidenceRecord } from '@/features/curation/types'
import type { SendChatMessageOptions, SSEEvent } from '@/hooks/useChatStream'
import type { RestorableChatMessage } from '@/services/chatHistoryApi'
import type { FlowStepEvidenceDetails } from '@/types/AuditEvent'

import type { FileInfo } from './FileDownloadCard'

export type MessageRole = 'user' | 'assistant' | 'flow'
export type TerminalTurnState =
  | 'turn_completed'
  | 'turn_interrupted'
  | 'turn_failed'
  | 'turn_save_failed'
  | 'session_gone'
export type RescueState = 'pending' | 'failed' | null
export type ChatNoticeTone = 'success' | 'error' | 'info' | 'warning'

export type ChatCssVariables = CSSProperties & {
  '--chat-text-primary': string
  '--chat-text-secondary': string
  '--chat-divider': string
  '--chat-subtle-divider': string
  '--chat-user-bg': string
  '--chat-user-color': string
  '--chat-assistant-bg': string
  '--chat-assistant-color': string
  '--chat-message-shadow': string
  '--chat-action-bg': string
  '--chat-action-border': string
  '--chat-action-color': string
  '--chat-action-hover-bg': string
  '--chat-action-hover-color': string
  '--chat-input-border': string
  '--chat-input-focus-border': string
  '--chat-input-placeholder': string
  '--chat-send-bg': string
  '--chat-send-hover-bg': string
  '--chat-send-color': string
  '--chat-send-shadow': string
  '--chat-send-hover-shadow': string
  '--chat-send-disabled-bg': string
  '--chat-warning-bg': string
  '--chat-warning-color': string
  '--chat-warning-border': string
  '--chat-empty-color': string
  '--chat-scrollbar-track': string
  '--chat-scrollbar-thumb': string
  '--chat-scrollbar-thumb-hover': string
}

export interface Message {
  role: MessageRole
  content: string
  timestamp: Date
  id?: string
  traceIds?: string[]
  turnId?: string
  terminalState?: TerminalTurnState | null
  terminalMessage?: string | null
  rescueState?: RescueState
  type?: 'text' | 'file_download'
  fileData?: FileInfo
  flowStepEvidence?: FlowStepEvidenceDetails
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
  evidenceRecords?: EvidenceRecord[]
  evidenceCurationSupported?: boolean | null
  evidenceCurationAdapterKey?: string | null
}

export interface SerializedMessage extends RestorableChatMessage {
  terminalState?: TerminalTurnState | null
  terminalMessage?: string | null
  rescueState?: RescueState
  reviewAndCurateTarget?: CurationWorkspaceLaunchTarget | null
}

export interface ActiveDocument {
  id: string
  filename?: string | null
  chunk_count?: number | null
  vector_count?: number | null
}

export interface ConversationStatus {
  is_active: boolean
  conversation_id?: string | null
  memory_stats?: {
    memory_sizes?: {
      short_term?: { file_count: number; size_mb: number }
      long_term?: { file_count: number; size_mb: number }
      entity?: { file_count: number; size_mb: number }
    }
  }
}

export interface ChatProps {
  /**
   * Session ID passed from parent (HomePage).
   * Used to scope messages and sync with audit panel.
   */
  sessionId: string | null

  /**
   * Callback to notify parent when session ID changes (e.g., after reset).
   * Parent (HomePage) must update its session state when this is called.
   */
  onSessionChange?: (newSessionId: string) => void

  /**
   * Shared SSE events from useChatStream hook (lifted to HomePage).
   */
  events: SSEEvent[]

  /**
   * Loading state from useChatStream hook.
   */
  isLoading: boolean

  /**
   * Send message function from useChatStream hook.
   */
  sendMessage: (
    message: string,
    sessionId: string,
    options?: SendChatMessageOptions,
  ) => Promise<void>
}

export interface StoredChatData {
  session_id: string | null
  messages: SerializedMessage[]
}

export interface PrepStatus {
  kind: 'success' | 'error' | 'info'
  message: string
}
