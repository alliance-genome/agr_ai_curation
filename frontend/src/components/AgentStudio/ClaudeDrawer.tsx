/**
 * ClaudeDrawer
 *
 * Narrow-width (below 1100px) container for AI Chat. A right-side MUI
 * Drawer with a scrim: it traps focus, closes on Escape and scrim click, and
 * keeps its children mounted while closed so the chat never remounts.
 */

import type { ReactNode } from 'react'
import { Drawer } from '@mui/material'

export const CLAUDE_DRAWER_WIDTH = 440

export interface ClaudeDrawerProps {
  id: string
  open: boolean
  /** Below 720px the drawer covers the full viewport width */
  fullWidth: boolean
  onClose: () => void
  children: ReactNode
}

function ClaudeDrawer({ id, open, fullWidth, onClose, children }: ClaudeDrawerProps) {
  return (
    <Drawer
      id={id}
      anchor="right"
      open={open}
      onClose={onClose}
      disableRestoreFocus
      ModalProps={{ keepMounted: true }}
      PaperProps={{
        role: 'dialog',
        'aria-modal': true,
        'aria-label': 'AI Chat',
        sx: {
          width: fullWidth ? '100%' : CLAUDE_DRAWER_WIDTH,
          maxWidth: '100%',
          borderRight: 'none',
          borderLeft: 1,
          borderColor: 'divider',
        },
      }}
    >
      {children}
    </Drawer>
  )
}

export default ClaudeDrawer
