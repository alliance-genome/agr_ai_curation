import type { ReactNode } from 'react'

import { Box, Stack, useMediaQuery } from '@mui/material'
import { alpha, styled, useTheme } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'

export interface WorkspaceShellProps {
  headerSlot?: ReactNode
  entityTableSlot: ReactNode
  reviewTableLabel?: string
  fieldEditorSlot?: ReactNode
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
  overflow: 'hidden',
  paddingTop: theme.spacing(1),
}))

const ResizeHandle = styled(PanelResizeHandle)(({ theme }) => ({
  width: 4,
  flex: '0 0 4px',
  marginLeft: theme.spacing(0.5),
  marginRight: theme.spacing(0.5),
  backgroundColor: alpha(theme.palette.primary.light, 0.12),
  cursor: 'col-resize',
  transition: 'background-color 160ms ease',
  borderRadius: theme.shape.borderRadius,
  position: 'relative',
  '&:hover, &[data-resize-handle-active="true"]': {
    backgroundColor: theme.palette.primary.main,
  },
  '&::after': {
    content: '""',
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: 2,
    height: 32,
    borderRadius: 1,
    backgroundColor: alpha(theme.palette.common.white, 0.32),
    pointerEvents: 'none',
  },
}))

const MobilePanels = styled(Stack)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  overflow: 'auto',
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
  entityTableSlot,
  reviewTableLabel = 'Entity table panel',
  fieldEditorSlot,
}: WorkspaceShellProps) {
  const theme = useTheme()
  const isCompactLayout = useMediaQuery(theme.breakpoints.down('md'))
  const hasFieldEditor = Boolean(fieldEditorSlot)

  return (
    <ShellRoot data-testid="workspace-shell">
      {headerSlot ? (
        <Box data-testid="workspace-shell-header">{headerSlot}</Box>
      ) : null}

      {isCompactLayout ? (
        <MobilePanels spacing={1.5}>
          <WorkspacePane label={reviewTableLabel} testId="workspace-shell-entity-table-panel">
            {entityTableSlot}
          </WorkspacePane>
          {fieldEditorSlot ? (
            <WorkspacePane label="Field editor panel" testId="workspace-shell-field-editor-panel">
              {fieldEditorSlot}
            </WorkspacePane>
          ) : null}
        </MobilePanels>
      ) : hasFieldEditor ? (
        <DesktopPanels>
          <PanelGroup
            autoSaveId="workspace-shell-mid-right"
            direction="horizontal"
            style={{ width: '100%', height: '100%', display: 'flex', overflow: 'hidden' }}
          >
            <Panel defaultSize={56} minSize={32} maxSize={78} order={1}>
              <PanelSection>
                <WorkspacePane
                  label={reviewTableLabel}
                  testId="workspace-shell-entity-table-panel"
                >
                  {entityTableSlot}
                </WorkspacePane>
              </PanelSection>
            </Panel>

            <ResizeHandle aria-label="Resize object list and field editor panels" />

            <Panel defaultSize={44} minSize={22} maxSize={68} order={2}>
              <PanelSection>
                <WorkspacePane
                  label="Field editor panel"
                  testId="workspace-shell-field-editor-panel"
                >
                  {fieldEditorSlot}
                </WorkspacePane>
              </PanelSection>
            </Panel>
          </PanelGroup>
        </DesktopPanels>
      ) : (
        <DesktopPanels>
          <PanelSection>
            <WorkspacePane
              label={reviewTableLabel}
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
