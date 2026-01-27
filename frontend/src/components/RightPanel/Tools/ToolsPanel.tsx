/**
 * ToolsPanel Component
 *
 * Container component for the Tools tab content.
 * Includes Curation Flows section and PDF Highlight Tester.
 */

import React from 'react'
import { Box, Stack } from '@mui/material'
import CurationFlows from './CurationFlows'
import PdfHighlightTester from '../../pdfViewer/PdfHighlightTester'

/**
 * Props for ToolsPanel component
 */
export interface ToolsPanelProps {
  /** Current chat session ID */
  sessionId: string | null
  /** Callback to execute a flow */
  onExecuteFlow: (flowId: string, documentId?: string, userQuery?: string) => Promise<void>
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
  onExecuteFlow,
  isExecuting = false,
  currentDocumentId,
}) => {
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
            background: 'rgba(255, 255, 255, 0.05)',
            borderRadius: '4px',
          },
          '&::-webkit-scrollbar-thumb': {
            background: 'rgba(255, 255, 255, 0.15)',
            borderRadius: '4px',
          },
          '&::-webkit-scrollbar-thumb:hover': {
            background: 'rgba(255, 255, 255, 0.25)',
          },
        }}
      >
        <Stack spacing={2}>
          {/* Curation Flows Section */}
          <CurationFlows
            sessionId={sessionId}
            onExecuteFlow={onExecuteFlow}
            isExecuting={isExecuting}
            currentDocumentId={currentDocumentId}
          />

          {/* PDF Highlight Tester Section */}
          <Box
            sx={{
              border: '1px solid rgba(255, 255, 255, 0.08)',
              borderRadius: '8px',
              padding: '16px',
              backgroundColor: 'rgba(255, 255, 255, 0.02)',
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
