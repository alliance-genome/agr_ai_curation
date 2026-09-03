import type { ReactNode } from 'react'

import { Box, Stack } from '@mui/material'
import { alpha, styled } from '@mui/material/styles'

export interface WorkspaceShellProps {
  headerSlot?: ReactNode
  workPaneSlot?: ReactNode
}

const ShellRoot = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}))

const PanelSurface = styled(Box)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  borderRadius: `0 0 ${theme.shape.borderRadius}px ${theme.shape.borderRadius}px`,
  border: `1px solid ${theme.palette.divider}`,
  borderTop: 0,
  backgroundColor: theme.palette.background.paper,
  boxShadow: `0 8px 24px ${alpha(theme.palette.common.black, theme.palette.mode === 'dark' ? 0.24 : 0.08)}`,
}))

const SlotFrame = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}))

const WorkPaneStack = styled(Stack)(() => ({
  flex: 1,
  minHeight: 0,
  height: '100%',
  overflow: 'hidden',
  paddingTop: 0,
}))

function WorkspacePane({
  children,
  label,
  testId,
}: {
  children?: ReactNode
  label: string
  testId: string
}) {
  return (
    <PanelSurface aria-label={label} data-testid={testId} role="region">
      <SlotFrame>{children}</SlotFrame>
    </PanelSurface>
  )
}

export default function WorkspaceShell({
  headerSlot,
  workPaneSlot,
}: WorkspaceShellProps) {
  return (
    <ShellRoot data-testid="workspace-shell">
      {headerSlot ? (
        <Box data-testid="workspace-shell-header">{headerSlot}</Box>
      ) : null}

      <WorkPaneStack spacing={0}>
        <WorkspacePane label="Review work pane" testId="workspace-shell-work-pane">
          {workPaneSlot ? (
            <Box
              sx={{
                display: 'flex',
                flex: 1,
                flexDirection: 'column',
                minHeight: 0,
                minWidth: 0,
                overflow: 'hidden',
              }}
              data-testid="workspace-shell-work-pane-content"
            >
              {workPaneSlot}
            </Box>
          ) : null}
        </WorkspacePane>
      </WorkPaneStack>
    </ShellRoot>
  )
}
