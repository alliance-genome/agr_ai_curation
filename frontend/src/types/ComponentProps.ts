/**
 * Component Prop Interfaces
 *
 * TypeScript interfaces for component props and state.
 */

import type { AuditEvent } from './AuditEvent'

/**
 * AuditPanel Component Props
 */
export interface AuditPanelProps {
  /**
   * Current chat session ID
   * Used to scope audit events and trigger clear on session change
   */
  sessionId: string | null

  /**
   * Callback when user clicks clear button
   * Parent component should handle clearing events
   */
  onClear?: () => void

  /**
   * Optional className for styling
   */
  className?: string

  /**
    * Optional stop handler to abort current run
    */
  onStop?: () => void

  /**
    * Whether a run is currently streaming (disables stop button when false)
    */
  isStreaming?: boolean
}

/**
 * AuditPanel Component State
 */
export interface AuditPanelState {
  /**
   * Array of audit events for current session
   * Maintained in component state
   */
  events: AuditEvent[]

  /**
   * Whether panel is actively receiving events
   */
  isActive: boolean

  /**
   * Whether to show empty state message
   */
  showEmptyState: boolean
}

/**
 * RightPanel Component Props
 */
export interface RightPanelProps {
  /**
   * Current chat session ID
   * Passed down to child components (e.g., AuditPanel)
   */
  sessionId: string | null

  /**
   * Optional stop handler to abort current run
   */
  onStop?: () => void

  /**
   * Whether a run is currently streaming (for disabling stop)
   */
  isStreaming?: boolean

  /**
   * Optional className for styling
   */
  className?: string
}

/**
 * RightPanel Component State
 */
export interface RightPanelState {
  /**
   * Currently active tab index
   * 0 = Audit tab
   * Future: 1, 2, 3... for other tabs
   */
  activeTabIndex: number
}

/**
 * Tab Configuration
 */
export interface TabConfig {
  /**
   * Tab label displayed to user
   */
  label: string

  /**
   * Unique identifier for this tab
   */
  id: string

  /**
   * Optional icon component (MUI icon or string)
   */
  icon?: any

  /**
   * Whether tab is disabled
   */
  disabled?: boolean
}

/**
 * Initial tab configuration
 */
export const INITIAL_TABS: TabConfig[] = [
  {
    label: 'Audit',
    id: 'audit',
    disabled: false
  },
  {
    label: 'Tools',
    id: 'tools',
    disabled: false
  }
]
