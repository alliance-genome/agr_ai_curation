import type { ReactNode } from 'react'

import { Box, Stack } from '@mui/material'
import { alpha, styled } from '@mui/material/styles'

export interface WorkspaceShellProps {
  headerSlot?: ReactNode
  selectorSlot?: ReactNode
  fieldEditorSlot?: ReactNode
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
  borderRadius: theme.shape.borderRadius,
  border: `1px solid ${alpha(theme.palette.primary.light, 0.18)}`,
  background:
    `linear-gradient(180deg, ${alpha(theme.palette.common.white, 0.035)}, ${alpha(theme.palette.common.white, 0.01)}), #071524`,
  boxShadow: `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.05)}, 0 18px 42px ${alpha(theme.palette.common.black, 0.24)}`,
}))

const SlotFrame = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}))

const WorkPaneStack = styled(Stack)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  height: '100%',
  overflow: 'hidden',
  paddingTop: theme.spacing(1),
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
  selectorSlot,
  fieldEditorSlot,
}: WorkspaceShellProps) {
  return (
    <ShellRoot data-testid="workspace-shell">
      {headerSlot ? (
        <Box data-testid="workspace-shell-header">{headerSlot}</Box>
      ) : null}

      <WorkPaneStack spacing={0}>
        <WorkspacePane label="Review work pane" testId="workspace-shell-work-pane">
          {selectorSlot ? (
            <Box sx={{ flexShrink: 0 }} data-testid="workspace-shell-selector">
              {selectorSlot}
            </Box>
          ) : null}
          {fieldEditorSlot ? (
            <Box
              sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
              data-testid="workspace-shell-field-editor"
            >
              {fieldEditorSlot}
            </Box>
          ) : null}
        </WorkspacePane>
      </WorkPaneStack>
    </ShellRoot>
  )
}
