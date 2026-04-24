/**
 * ToolsPanel Component
 *
 * Container component for the Tools tab content.
 * Includes Curation Flows section and PDF Highlight Tester.
 */

import React from 'react'
import { Box, Stack } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import CurationFlows from './CurationFlows'
import PdfHighlightTester from '../../pdfViewer/PdfHighlightTester'
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
 * - Curation Flows section (top) - for executing saved flows
 * - PDF Highlight Tester section (bottom) - for manual highlighting
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
          {/* Curation Flows Section */}
          <CurationFlows
            sessionId={sessionId}
            sseEvents={sseEvents}
            onExecuteFlow={onExecuteFlow}
            onStopFlow={onStopFlow}
            isExecuting={isExecuting}
            currentDocumentId={currentDocumentId}
          />

          {/* PDF Highlight Tester Section */}
          <Box
            sx={{
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: '8px',
              padding: '16px',
              backgroundColor: alpha(theme.palette.background.paper, 0.52),
            }}
          >
            <PdfHighlightTester />
          </Box>
        </Stack>
      </Box>
    </Box>
  )
}

export default ToolsPanel
