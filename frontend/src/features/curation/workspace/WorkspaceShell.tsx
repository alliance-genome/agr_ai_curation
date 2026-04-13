import type { ReactNode } from 'react'

import { Box, Stack, useMediaQuery } from '@mui/material'
import { alpha, styled, useTheme } from '@mui/material/styles'

export interface WorkspaceShellProps {
  headerSlot?: ReactNode
  entityTableSlot: ReactNode
}

const ShellRoot = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
}))

const PanelSection = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  height: '100%',
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
  borderRadius: theme.shape.borderRadius * 1.25,
  border: `1px solid ${alpha(theme.palette.divider, 0.85)}`,
  backgroundColor: alpha(theme.palette.background.paper, 0.86),
  boxShadow: `inset 0 1px 0 ${alpha(theme.palette.common.white, 0.03)}`,
}))

const SlotFrame = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  '& > *': {
    flex: 1,
    minHeight: 0,
  },
}))

const DesktopPanels = styled(Box)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  paddingTop: theme.spacing(1.5),
  '& > [data-panel-group]': {
    flex: 1,
    minHeight: 0,
  },
}))

const MobilePanels = styled(Stack)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  overflow: 'auto',
  paddingTop: theme.spacing(1.5),
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
  entityTableSlot,
}: WorkspaceShellProps) {
  const theme = useTheme()
  const isCompactLayout = useMediaQuery(theme.breakpoints.down('md'))

  return (
    <ShellRoot data-testid="workspace-shell">
      {headerSlot ? (
        <Box data-testid="workspace-shell-header">{headerSlot}</Box>
      ) : null}

      {isCompactLayout ? (
        <MobilePanels spacing={1.5}>
          <WorkspacePane label="Entity table panel" testId="workspace-shell-entity-table-panel">
            {entityTableSlot}
          </WorkspacePane>
        </MobilePanels>
      ) : (
        <DesktopPanels>
          <PanelSection>
            <WorkspacePane
              label="Entity table panel"
              testId="workspace-shell-entity-table-panel"
            >
              {entityTableSlot}
            </WorkspacePane>
          </PanelSection>
        </DesktopPanels>
      )}
    </ShellRoot>
  )
}
