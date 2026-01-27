/**
 * RightPanel Component
 *
 * Container component for tabbed right panel interface.
 * Manages tab switching and renders appropriate content for each tab.
 * Uses MUI Tabs component with state persistence during tab navigation.
 */

import React, { useState } from 'react'
import { Box, Tabs, Tab } from '@mui/material'
import type { RightPanelProps } from '../types/ComponentProps'
import { INITIAL_TABS } from '../types/ComponentProps'
import AuditPanel from './AuditPanel'
import { ToolsPanel } from './RightPanel/Tools'
import type { SSEEvent } from '../hooks/useChatStream'

/**
 * Main right panel container component with tabbed interface.
 *
 * The RightPanel provides a flexible tab-based navigation system for the right side
 * of the application. It manages tab state and renders appropriate content based on
 * the active tab. Currently supports:
 * - **Audit tab** (Tab 0, default): Displays real-time AI agent activity via AuditPanel
 * - **Tools tab** (Tab 1): Contains the PdfHighlightTester component
 *
 * The component uses MUI Tabs for accessibility (ARIA attributes, keyboard navigation)
 * and implements the hidden prop pattern to preserve component state when switching
 * tabs. This means AuditPanel events are retained in memory when navigating to other
 * tabs and reappear when returning to the Audit tab.
 *
 * Tab configuration is controlled by the INITIAL_TABS constant, making it easy to
 * add new tabs in the future. Each tab can be enabled/disabled via the `disabled`
 * property in the configuration.
 *
 * @component
 * @example
 * ```tsx
 * import { useState } from 'react'
 * import { useChatStream } from '../hooks/useChatStream'
 *
 * function HomePage() {
 *   // Session ID managed separately
 *   const [sessionId, setSessionId] = useState<string | null>(null)
 *
 *   // Shared SSE stream (events renamed to sseEvents when passing to RightPanel)
 *   const { events, isLoading, sendMessage } = useChatStream()
 *
 *   return (
 *     <RightPanel
 *       sessionId={sessionId}
 *       sseEvents={events}
 *       className="right-panel-container"
 *     />
 *   )
 * }
 * ```
 */
/**
 * Extended props for RightPanel including flow execution
 */
interface ExtendedRightPanelProps extends RightPanelProps {
  sseEvents: SSEEvent[]
  /** Callback to execute a curation flow */
  onExecuteFlow?: (flowId: string, documentId?: string, userQuery?: string) => Promise<void>
  /** Current document ID loaded in PDF viewer */
  currentDocumentId?: string
  /** Controlled active tab index (for persistence) */
  activeTabIndex?: number
  /** Callback when tab changes (for persistence) */
  onTabChange?: (tabIndex: number) => void
}

const RightPanel: React.FC<ExtendedRightPanelProps> = ({
  sessionId,
  sseEvents,
  className,
  onStop,
  isStreaming,
  onExecuteFlow,
  currentDocumentId,
  activeTabIndex: controlledTabIndex,
  onTabChange,
}) => {
  // Internal state for uncontrolled mode
  const [internalTabIndex, setInternalTabIndex] = useState<number>(0)

  // Use controlled value if provided, otherwise use internal state
  const activeTabIndex = controlledTabIndex ?? internalTabIndex

  const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
    // Update internal state (for uncontrolled mode)
    setInternalTabIndex(newValue)
    // Notify parent (for controlled mode / persistence)
    onTabChange?.(newValue)
  }

  return (
    <Box
      className={className}
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'transparent'
      }}
    >
      {/* Tab bar */}
      <Tabs
        value={activeTabIndex}
        onChange={handleTabChange}
        aria-label="Right panel tabs"
        sx={{
          borderBottom: '1px solid rgba(255, 255, 255, 0.12)',
          minHeight: '48px',
          backgroundColor: 'transparent',
          '& .MuiTabs-indicator': {
            backgroundColor: '#2196f3', // Active tab indicator color (primary blue)
            height: '3px'
          },
          '& .MuiTabs-flexContainer': {
            height: '48px'
          }
        }}
      >
        {INITIAL_TABS.map((tab, index) => (
          <Tab
            key={tab.id}
            label={tab.label}
            id={`right-panel-tab-${index}`}
            aria-controls={`right-panel-tabpanel-${index}`}
            disabled={tab.disabled}
            sx={{
              textTransform: 'none',
              minHeight: '48px',
              fontSize: '0.875rem',
              fontWeight: 500,
              color: 'rgba(255, 255, 255, 0.7)',
              transition: 'all 0.2s',
              '&.Mui-selected': {
                color: '#2196f3', // Active tab text color
                fontWeight: 600
              },
              '&:hover': {
                color: 'rgba(255, 255, 255, 0.9)',
                backgroundColor: 'rgba(255, 255, 255, 0.05)'
              },
              '&.Mui-disabled': {
                color: 'rgba(255, 255, 255, 0.3)'
              }
            }}
          />
        ))}
      </Tabs>

      {/* Tab panels */}
      {INITIAL_TABS.map((tab, index) => (
        <Box
          key={tab.id}
          role="tabpanel"
          hidden={activeTabIndex !== index}
          id={`right-panel-tabpanel-${index}`}
          aria-labelledby={`right-panel-tab-${index}`}
          sx={{
            flex: 1,
            minHeight: 0,
            overflow: 'hidden',
            display: activeTabIndex === index ? 'block' : 'none',
            backgroundColor: 'transparent',
            height: '100%'
          }}
        >
          {/* Render AuditPanel for Audit tab */}
          {tab.id === 'audit' && (
            <AuditPanel sessionId={sessionId} sseEvents={sseEvents} onStop={onStop} isStreaming={isStreaming} />
          )}

          {/* Render ToolsPanel for Tools tab */}
          {tab.id === 'tools' && (
            <ToolsPanel
              sessionId={sessionId}
              onExecuteFlow={onExecuteFlow || (async () => {})}
              isExecuting={isStreaming}
              currentDocumentId={currentDocumentId}
            />
          )}
        </Box>
      ))}
    </Box>
  )
}

export default RightPanel
