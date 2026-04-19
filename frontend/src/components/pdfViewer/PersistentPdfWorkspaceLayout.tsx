import { Outlet, matchPath, useLocation } from 'react-router-dom'
import { Box, useMediaQuery } from '@mui/material'
import { alpha, styled, useTheme } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'

import { useAuth } from '@/contexts/AuthContext'
import {
  HOME_PDF_VIEWER_OWNER,
  buildCurationPDFViewerOwner,
} from './pdfEvents'
import PdfViewer from './PdfViewer'

const Root = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  minHeight: 0,
  height: '100%',
  overflow: 'hidden',
  padding: theme.spacing(2),
  paddingTop: theme.spacing(1.5),
}))

const PanelSection = styled(Box)(() => ({
  flex: 1,
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  height: '100%',
  '& > *': {
    flex: 1,
    minHeight: 0,
    height: '100%',
  },
}))

const ResizeHandle = styled(PanelResizeHandle)(({ theme }) => ({
  width: 4,
  flex: '0 0 4px',
  backgroundColor: theme.palette.divider,
  cursor: 'col-resize',
  transition: 'background-color 0.2s ease',
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
    backgroundColor: alpha(theme.palette.common.white, 0.45),
    pointerEvents: 'none',
  },
}))

export default function PersistentPdfWorkspaceLayout() {
  const { user } = useAuth()
  const theme = useTheme()
  const isCompactLayout = useMediaQuery(theme.breakpoints.down('md'))
  const location = useLocation()
  const curationMatch = matchPath('/curation/:sessionId/:candidateId', location.pathname)
    ?? matchPath('/curation/:sessionId', location.pathname)
  const layoutKind = curationMatch ? 'curation' : 'home'
  const activeDocumentOwnerToken = curationMatch?.params.sessionId
    ? buildCurationPDFViewerOwner(curationMatch.params.sessionId)
    : HOME_PDF_VIEWER_OWNER

  if (isCompactLayout) {
    return (
      <Root data-layout-kind={layoutKind} data-testid="persistent-pdf-workspace-layout">
        <Box
          sx={{
            width: '100%',
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            gap: 1.5,
          }}
        >
          <Box
            data-testid="persistent-pdf-viewer-panel"
            sx={{
              flex: '0 0 42%',
              minHeight: 280,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <PdfViewer
              activeDocumentOwnerToken={activeDocumentOwnerToken}
              storageUserId={user?.uid ?? null}
            />
          </Box>
          <Box
            data-testid="persistent-pdf-route-content"
            sx={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            <Outlet />
          </Box>
        </Box>
      </Root>
    )
  }

  return (
    <Root data-layout-kind={layoutKind} data-testid="persistent-pdf-workspace-layout">
      <PanelGroup
        autoSaveId={`persistent-pdf-workspace-layout-${layoutKind}`}
        direction="horizontal"
        style={{ width: '100%', height: '100%', display: 'flex', overflow: 'hidden' }}
      >
        <Panel defaultSize={34} minSize={20} maxSize={60} order={1}>
          <PanelSection data-testid="persistent-pdf-viewer-panel">
            <PdfViewer
              activeDocumentOwnerToken={activeDocumentOwnerToken}
              storageUserId={user?.uid ?? null}
            />
          </PanelSection>
        </Panel>

        <ResizeHandle aria-label="Resize PDF and route content panels" />

        <Panel defaultSize={66} minSize={24} maxSize={80} order={2}>
          <PanelSection data-testid="persistent-pdf-route-content">
            <Outlet />
          </PanelSection>
        </Panel>
      </PanelGroup>
    </Root>
  )
}
