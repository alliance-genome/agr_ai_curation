/**
 * AuditEventItem Component
 *
 * Displays a single audit event with prefix, label, severity styling,
 * and optional query details for TOOL_START events.
 */

import React from 'react'
import { alpha, useTheme } from '@mui/material/styles'
import type { AuditEvent } from '../types/AuditEvent'
import { formatAuditEvent, getEventSeverity } from '../utils/auditHelpers'

// CSS keyframes for animated dots (injected once)
const animatedDotsStyle = `
@keyframes auditDotPulse {
  0%, 20% { opacity: 0; }
  40% { opacity: 1; }
  100% { opacity: 1; }
}
.audit-animated-dots span {
  animation: auditDotPulse 1.4s infinite;
  opacity: 0;
}
.audit-animated-dots span:nth-child(1) { animation-delay: 0s; }
.audit-animated-dots span:nth-child(2) { animation-delay: 0.2s; }
.audit-animated-dots span:nth-child(3) { animation-delay: 0.4s; }
`

// Inject the style once when the module loads
if (typeof document !== 'undefined') {
  const styleId = 'audit-animated-dots-style'
  if (!document.getElementById(styleId)) {
    const style = document.createElement('style')
    style.id = styleId
    style.textContent = animatedDotsStyle
    document.head.appendChild(style)
  }
}

/**
 * Props for the AuditEventItem component
 */
export interface AuditEventItemProps {
  /** The audit event to display */
  event: AuditEvent
  /** Whether this is the most recent event (controls animation) */
  isLatest?: boolean
}

/**
 * Displays a single audit event with prefix, label, severity styling, and query details.
 *
 * This component is the fundamental building block of the audit panel, responsible for
 * rendering individual events with appropriate visual styling based on severity (info,
 * success, error). For TOOL_START events, it displays SQL queries or API parameters
 * inline to provide transparency into agent actions.
 *
 * The component uses monospace font for technical details and applies color-coded
 * backgrounds with borders based on event severity:
 * - Info (blue): General events like SUPERVISOR_START, TOOL_START
 * - Success (green): Completion events like AGENT_COMPLETE, SUPERVISOR_COMPLETE
 * - Error (red): Error events like SUPERVISOR_ERROR
 *
 * @component
 * @example
 * ```tsx
 * <AuditEventItem event={{
 *   id: '123',
 *   type: 'TOOL_START',
 *   timestamp: new Date(),
 *   sessionId: 'session123',
 *   details: {
 *     toolName: 'sql_query_tool',
 *     friendlyName: 'Searching database...',
 *     toolArgs: { query: "SELECT * FROM ontology_terms WHERE term_id = 'DOID:10652'" }
 *   }
 * }} />
 * ```
 */
const AuditEventItem: React.FC<AuditEventItemProps> = ({ event, isLatest = false }) => {
  const theme = useTheme()
  const severity = getEventSeverity(event.type, event.details)
  const formattedText = formatAuditEvent(event)

  const severityColors = {
    info: {
      main: theme.palette.info.main,
      backgroundOpacity: 0.14,
      borderOpacity: 0.24,
    },
    success: {
      main: theme.palette.success.main,
      backgroundOpacity: 0.16,
      borderOpacity: 0.3,
    },
    warning: {
      main: theme.palette.warning.main,
      backgroundOpacity: 0.16,
      borderOpacity: 0.3,
    },
    error: {
      main: theme.palette.error.main,
      backgroundOpacity: 0.16,
      borderOpacity: 0.3,
    },
    processing: {
      main: theme.palette.secondary.main,
      backgroundOpacity: 0.16,
      borderOpacity: 0.3,
    }
  } as const

  const isProcessing = severity === 'processing'

  const palette = severityColors[severity]

  const severityStyles: React.CSSProperties = {
    color: theme.palette.text.primary,
    padding: '10px 14px',
    marginBottom: '6px',
    borderRadius: '6px',
    backgroundColor: alpha(palette.main, palette.backgroundOpacity),
    border: `1px solid ${alpha(palette.main, palette.borderOpacity)}`,
    boxShadow: `0 1px 3px ${alpha(palette.main, 0.18)}`,
    fontSize: '0.875rem',
    fontFamily: 'monospace',
    lineHeight: '1.6',
    transition: 'all 0.2s ease-in-out',
    display: 'block'
  }

  const textStyles: React.CSSProperties = {
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word'
  }

  return (
    <div data-testid="audit-event-item" data-severity={severity} style={severityStyles}>
      <span style={textStyles}>
        {formattedText}
        {isProcessing && isLatest && (
          <span className="audit-animated-dots" style={{ marginLeft: '4px' }}>
            <span>.</span>
            <span>.</span>
            <span>.</span>
          </span>
        )}
      </span>
    </div>
  )
}

export default AuditEventItem
