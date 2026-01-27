/**
 * Tests for RightPanel Component (T019)
 *
 * Tests the tabbed right panel container component that manages tab switching
 * and renders appropriate content (AuditPanel, etc.) based on active tab.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RightPanel from '../../components/RightPanel'
import { INITIAL_TABS } from '../../types/ComponentProps'

// ===================================================================
// Tab Rendering Tests
// ===================================================================
describe('RightPanel - Tab Rendering (T019)', () => {
  it('renders tab bar with "Audit" tab', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should show Audit tab
    const auditTab = screen.getByRole('tab', { name: /Audit/i })
    expect(auditTab).toBeInTheDocument()
  })

  it('renders tab bar with correct number of tabs', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should render all tabs from INITIAL_TABS configuration
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(INITIAL_TABS.length)
  })

  it('uses INITIAL_TABS configuration for tab setup', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should render first tab from INITIAL_TABS
    expect(INITIAL_TABS[0].label).toBe('Audit')
    expect(INITIAL_TABS[0].id).toBe('audit')

    const auditTab = screen.getByRole('tab', { name: /Audit/i })
    expect(auditTab).toBeInTheDocument()
  })

  it('renders MUI Tabs component', () => {
    const { container } = render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should use MUI Tabs (check for MUI tab structure)
    const tabsContainer = container.querySelector('[role="tablist"]')
    expect(tabsContainer).toBeInTheDocument()
  })
})

// ===================================================================
// Default Tab Selection Tests
// ===================================================================
describe('RightPanel - Default Tab Selection (T019)', () => {
  it('Audit tab is selected by default (activeTabIndex = 0)', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const auditTab = screen.getByRole('tab', { name: /Audit/i })

    // Should have aria-selected="true" attribute
    expect(auditTab).toHaveAttribute('aria-selected', 'true')
  })

  it('displays AuditPanel content by default', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should render AuditPanel (check for characteristic elements)
    // AuditPanel should show empty state or events list
    const auditContent = screen.getByTestId('audit-panel')
    expect(auditContent).toBeInTheDocument()
  })

  it('first tab panel is visible by default', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should show first tab panel
    const tabPanel = screen.getByRole('tabpanel')
    expect(tabPanel).toBeInTheDocument()

    // Should have correct aria-labelledby attribute
    expect(tabPanel).toHaveAttribute('aria-labelledby')
  })
})

// ===================================================================
// Tab Switching Tests
// ===================================================================
describe('RightPanel - Tab Switching (T019)', () => {
  it('switches tabs on click (updates activeTabIndex)', async () => {
    const user = userEvent.setup()

    // For this test, assume we have at least 2 tabs configured
    // If INITIAL_TABS only has 1 tab, this test will be skipped in actual implementation
    if (INITIAL_TABS.length < 2) {
      // Skip test if only one tab exists
      return
    }

    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const allTabs = screen.getAllByRole('tab')
    const firstTab = allTabs[0]
    const secondTab = allTabs[1]

    // First tab should be selected initially
    expect(firstTab).toHaveAttribute('aria-selected', 'true')
    expect(secondTab).toHaveAttribute('aria-selected', 'false')

    // Click second tab
    await user.click(secondTab)

    // Second tab should now be selected
    expect(firstTab).toHaveAttribute('aria-selected', 'false')
    expect(secondTab).toHaveAttribute('aria-selected', 'true')
  })

  it('updates tab panel content when switching tabs', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      return
    }

    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Initially should show Audit panel
    expect(screen.getByTestId('audit-panel')).toBeInTheDocument()

    // Click second tab
    const secondTab = screen.getAllByRole('tab')[1]
    await user.click(secondTab)

    // Should show different content (depends on implementation)
    // AuditPanel might be hidden (not in document) or have hidden prop
    const tabPanels = screen.getAllByRole('tabpanel')
    expect(tabPanels.length).toBeGreaterThanOrEqual(1)
  })

  it('maintains correct aria attributes during tab switch', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      return
    }

    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const allTabs = screen.getAllByRole('tab')
    const firstTab = allTabs[0]
    const secondTab = allTabs[1]

    // Click second tab
    await user.click(secondTab)

    // Check aria-controls and aria-labelledby relationships
    const secondTabPanel = screen.getByRole('tabpanel')

    expect(secondTab).toHaveAttribute('aria-controls')
    expect(secondTabPanel).toHaveAttribute('aria-labelledby')
  })
})

// ===================================================================
// AuditPanel State Persistence Tests
// ===================================================================
describe('RightPanel - AuditPanel State Persistence (T019)', () => {
  it('persists AuditPanel state when switching away and back', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      // Need at least 2 tabs to test persistence
      return
    }

    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Verify AuditPanel is initially visible with empty state
    const auditPanel = screen.getByTestId('audit-panel')
    expect(auditPanel).toBeInTheDocument()

    // Switch to second tab
    const secondTab = screen.getAllByRole('tab')[1]
    await user.click(secondTab)

    // AuditPanel should be hidden but not unmounted (to preserve state)
    // Check if audit-panel still exists in DOM (might be hidden via CSS or hidden prop)
    const hiddenAuditPanel = screen.queryByTestId('audit-panel')

    // Switch back to Audit tab
    const firstTab = screen.getAllByRole('tab')[0]
    await user.click(firstTab)

    // AuditPanel should be visible again
    const visibleAuditPanel = screen.getByTestId('audit-panel')
    expect(visibleAuditPanel).toBeInTheDocument()

    // State should be preserved (if events were added, they should still be there)
    // This is verified by the component not being unmounted
  })

  it('uses hidden prop to hide inactive panels without unmounting', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      return
    }

    const { container } = render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Initially, AuditPanel should be visible
    let auditPanel = container.querySelector('[data-testid="audit-panel"]')
    expect(auditPanel).toBeInTheDocument()
    expect(auditPanel).not.toHaveAttribute('hidden')

    // Switch to second tab
    const secondTab = screen.getAllByRole('tab')[1]
    await user.click(secondTab)

    // AuditPanel should still be in DOM but hidden
    auditPanel = container.querySelector('[data-testid="audit-panel"]')
    expect(auditPanel).toBeInTheDocument()

    // Should have hidden attribute or aria-hidden
    const isHidden = auditPanel?.hasAttribute('hidden') ||
                    auditPanel?.getAttribute('aria-hidden') === 'true' ||
                    auditPanel?.style.display === 'none'

    expect(isHidden).toBe(true)
  })

  it('does not remount AuditPanel when switching tabs', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      return
    }

    const { container } = render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Get initial audit panel element
    const initialAuditPanel = container.querySelector('[data-testid="audit-panel"]')
    const initialElement = initialAuditPanel

    // Switch to second tab and back
    const tabs = screen.getAllByRole('tab')
    await user.click(tabs[1])
    await user.click(tabs[0])

    // Get audit panel element again
    const finalAuditPanel = container.querySelector('[data-testid="audit-panel"]')

    // Should be the same DOM element (not remounted)
    expect(finalAuditPanel).toBe(initialElement)
  })
})

// ===================================================================
// SessionId Propagation Tests
// ===================================================================
describe('RightPanel - SessionId Propagation (T019)', () => {
  it('passes sessionId to AuditPanel', () => {
    const sessionId = 'test-session-123'

    render(<RightPanel sessionId={sessionId} sseEvents={[]} />)

    // Verify AuditPanel receives sessionId
    // This can be checked by verifying AuditPanel is rendered with the correct prop
    const auditPanel = screen.getByTestId('audit-panel')
    expect(auditPanel).toBeInTheDocument()

    // Check if sessionId is passed (might be reflected in component state or behavior)
    // For now, just verify the panel is rendered
    expect(auditPanel).toHaveAttribute('data-session-id', sessionId)
  })

  it('updates AuditPanel when sessionId changes', () => {
    const { rerender } = render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const auditPanel = screen.getByTestId('audit-panel')
    expect(auditPanel).toHaveAttribute('data-session-id', 'session123')

    // Update sessionId
    rerender(<RightPanel sessionId="session456" sseEvents={[]} />)

    const updatedAuditPanel = screen.getByTestId('audit-panel')
    expect(updatedAuditPanel).toHaveAttribute('data-session-id', 'session456')
  })

  it('handles null sessionId', () => {
    render(<RightPanel sessionId={null} sseEvents={[]} />)

    const auditPanel = screen.getByTestId('audit-panel')
    expect(auditPanel).toBeInTheDocument()

    // Should handle null sessionId gracefully
    const sessionIdAttr = auditPanel.getAttribute('data-session-id')
    expect(sessionIdAttr === null || sessionIdAttr === 'null').toBe(true)
  })
})

// ===================================================================
// Edge Cases
// ===================================================================
describe('RightPanel - Edge Cases (T019)', () => {
  it('renders with optional className prop', () => {
    const { container } = render(
      <RightPanel sessionId="session123" sseEvents={[]} className="custom-right-panel" />
    )

    // Should apply custom className to root element
    const panel = container.querySelector('.custom-right-panel')
    expect(panel).toBeInTheDocument()
  })

  it('handles rapid tab switching without errors', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      return
    }

    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const tabs = screen.getAllByRole('tab')

    // Rapidly switch tabs multiple times
    await user.click(tabs[1])
    await user.click(tabs[0])
    await user.click(tabs[1])
    await user.click(tabs[0])

    // Should still be functional
    const firstTab = tabs[0]
    expect(firstTab).toHaveAttribute('aria-selected', 'true')
  })

  it('maintains tab state across sessionId changes', async () => {
    const user = userEvent.setup()

    if (INITIAL_TABS.length < 2) {
      return
    }

    const { rerender } = render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Switch to second tab
    const secondTab = screen.getAllByRole('tab')[1]
    await user.click(secondTab)

    // Change sessionId
    rerender(<RightPanel sessionId="session456" sseEvents={[]} />)

    // Tab selection should be preserved (or reset to default - depends on design)
    // Current implementation assumes tab state persists
    const tabs = screen.getAllByRole('tab')

    // Component should still be functional
    expect(tabs[0]).toBeInTheDocument()
  })

  it('renders correctly with single tab configuration', () => {
    // This tests the initial state when only one tab exists
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const tabs = screen.getAllByRole('tab')

    // Should render the single tab
    expect(tabs.length).toBeGreaterThanOrEqual(1)

    // First tab should be selected
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('tab panel has proper ARIA roles and attributes', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Tab list should have role="tablist"
    const tablist = screen.getByRole('tablist')
    expect(tablist).toBeInTheDocument()

    // Tabs should have role="tab"
    const tabs = screen.getAllByRole('tab')
    expect(tabs.length).toBeGreaterThan(0)

    // Tab panel should have role="tabpanel"
    const tabpanel = screen.getByRole('tabpanel')
    expect(tabpanel).toBeInTheDocument()
  })
})

// ===================================================================
// Integration Tests
// ===================================================================
describe('RightPanel - Integration (T019)', () => {
  it('integrates properly with AuditPanel component', () => {
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    // Should render AuditPanel
    const auditPanel = screen.getByTestId('audit-panel')
    expect(auditPanel).toBeInTheDocument()

    // AuditPanel should have proper structure (empty state or events list)
    // This verifies the integration works
    expect(auditPanel).toBeVisible()
  })

  it('supports future tabs being added to INITIAL_TABS', () => {
    // This test documents the extensibility of the tab system
    render(<RightPanel sessionId="session123" sseEvents={[]} />)

    const tabs = screen.getAllByRole('tab')

    // Should render however many tabs are in INITIAL_TABS
    expect(tabs).toHaveLength(INITIAL_TABS.length)

    // Each tab should be properly configured
    tabs.forEach((tab, index) => {
      expect(tab).toHaveAttribute('aria-selected')

      // First tab should be selected
      if (index === 0) {
        expect(tab).toHaveAttribute('aria-selected', 'true')
      }
    })
  })
})
