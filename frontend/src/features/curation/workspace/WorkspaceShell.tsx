import type { ReactNode } from 'react'

import { Box, Stack, useMediaQuery } from '@mui/material'
import { alpha, styled, useTheme } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'

export interface WorkspaceShellProps {
  headerSlot?: ReactNode
  pdfSlot: ReactNode
  entityTableSlot: ReactNode
  outerAutoSaveId?: string
}

const DEFAULT_OUTER_AUTO_SAVE_ID = 'curation-workspace-shell-panels'

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

const ToolbarSurface = styled(PanelSurface)(({ theme }) => ({
  flex: '0 0 auto',
  minHeight: theme.spacing(7),
}))

const StyledResizeHandle = styled(PanelResizeHandle, {
  shouldForwardProp: (prop) => prop !== 'groupDirection',
})<{
  groupDirection: 'horizontal' | 'vertical'
}>(({ theme, groupDirection }) => ({
  position: 'relative',
  flex: '0 0 auto',
  borderRadius: theme.shape.borderRadius,
  backgroundColor: theme.palette.divider,
  transition: 'background-color 0.2s ease',
  ...(groupDirection === 'horizontal'
    ? {
        width: 4,
        marginInline: theme.spacing(0.75),
        cursor: 'col-resize',
      }
    : {
        height: 4,
        marginBlock: theme.spacing(0.75),
        cursor: 'row-resize',
      }),
  '&:hover, &[data-resize-handle-active="true"]': {
    backgroundColor: theme.palette.primary.main,
  },
  '&::after': {
    content: '""',
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    borderRadius: 999,
    pointerEvents: 'none',
    backgroundColor: alpha(theme.palette.common.white, 0.45),
    ...(groupDirection === 'horizontal'
      ? {
          width: 2,
          height: 34,
        }
      : {
          width: 34,
          height: 2,
        }),
  },
}))

function WorkspaceResizeHandle({
  groupDirection,
  label,
  testId,
}: {
  groupDirection: 'horizontal' | 'vertical'
  label: string
  testId: string
}) {
  return (
    <StyledResizeHandle
      aria-label={label}
      data-testid={testId}
      groupDirection={groupDirection}
    />
  )
}

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
  pdfSlot,
  entityTableSlot,
  outerAutoSaveId = DEFAULT_OUTER_AUTO_SAVE_ID,
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
          <WorkspacePane label="PDF panel" testId="workspace-shell-pdf-panel">
            {pdfSlot}
          </WorkspacePane>
          <WorkspacePane label="Entity table panel" testId="workspace-shell-entity-table-panel">
            {entityTableSlot}
          </WorkspacePane>
        </MobilePanels>
      ) : (
        <DesktopPanels>
          <PanelGroup autoSaveId={outerAutoSaveId} direction="horizontal">
            <Panel defaultSize={45} minSize={28} order={1}>
              <PanelSection>
                <WorkspacePane label="PDF panel" testId="workspace-shell-pdf-panel">
                  {pdfSlot}
                </WorkspacePane>
              </PanelSection>
            </Panel>

            <WorkspaceResizeHandle
              groupDirection="horizontal"
              label="Resize PDF and entity table panels"
              testId="workspace-shell-handle-pdf-table"
            />

            <Panel defaultSize={55} minSize={30} order={2}>
              <PanelSection>
                <WorkspacePane
                  label="Entity table panel"
                  testId="workspace-shell-entity-table-panel"
                >
                  {entityTableSlot}
                </WorkspacePane>
              </PanelSection>
            </Panel>
          </PanelGroup>
        </DesktopPanels>
      )}
    </ShellRoot>
  )
}
