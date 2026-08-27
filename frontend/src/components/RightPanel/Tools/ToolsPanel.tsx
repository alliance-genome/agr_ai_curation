/**
 * ToolsPanel Component
 *
 * Container component for the Tools tab content.
 * Includes the persistent chat default and Curation Flows sections.
 */

import React from 'react'
import { Box, Stack } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import ChatDefault from './ChatDefault'
import CurationFlows from './CurationFlows'
import type { SSEEvent } from '@/hooks/useChatStream'

/**
 * Props for ToolsPanel component
 */
export interface ToolsPanelProps {
  /** Current chat session ID */
  sessionId: string | null
  /** Shared SSE events from the chat stream */
  sseEvents: SSEEvent[]
  /** Callback to execute a flow */
  onExecuteFlow: (flowId: string, documentId?: string, userQuery?: string) => Promise<void>
  /** Callback to stop currently executing flow/chat stream */
  onStopFlow?: () => void | Promise<void>
  /** Whether a flow is currently executing */
  isExecuting?: boolean
  /** Current document loaded in PDF viewer */
  currentDocumentId?: string
}

/**
 * ToolsPanel component that combines all tools for the Tools tab.
 *
 * Layout:
 * - Chat default section (top) - for choosing future chat routing
 * - Curation Flows section (bottom) - for executing saved flows
 */
const ToolsPanel: React.FC<ToolsPanelProps> = ({
  sessionId,
  sseEvents,
  onExecuteFlow,
  onStopFlow,
  isExecuting = false,
  currentDocumentId,
}) => {
  const theme = useTheme()

  return (
    <Box
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        backgroundColor: 'transparent',
      }}
    >
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '1rem',
          // Custom scrollbar styling to match audit panel
          '&::-webkit-scrollbar': {
            width: '8px',
          },
          '&::-webkit-scrollbar-track': {
            background: alpha(theme.palette.text.secondary, 0.12),
            borderRadius: '4px',
          },
          '&::-webkit-scrollbar-thumb': {
            background: alpha(theme.palette.text.secondary, 0.24),
            borderRadius: '4px',
          },
          '&::-webkit-scrollbar-thumb:hover': {
            background: alpha(theme.palette.text.secondary, 0.36),
          },
        }}
      >
        <Stack spacing={2}>
          <ChatDefault />

          {/* Curation Flows Section */}
          <CurationFlows
            sessionId={sessionId}
            sseEvents={sseEvents}
            onExecuteFlow={onExecuteFlow}
            onStopFlow={onStopFlow}
            isExecuting={isExecuting}
            currentDocumentId={currentDocumentId}
          />
        </Stack>
      </Box>
    </Box>
  )
}

export default ToolsPanel
