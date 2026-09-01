import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
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
  minWidth: 0,
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
  minWidth: 0,
  height: '100%',
  '& > *': {
    flex: 1,
    minHeight: 0,
    minWidth: 0,
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

export interface PersistentPdfWorkspaceLayoutController {
  focusGrid: () => void
  isPdfVisible: boolean
  showPdf: () => void
}

const PersistentPdfWorkspaceLayoutContext =
  createContext<PersistentPdfWorkspaceLayoutController | null>(null)

export function usePersistentPdfWorkspaceLayout(): PersistentPdfWorkspaceLayoutController {
  const controller = useContext(PersistentPdfWorkspaceLayoutContext)
  if (!controller) {
    throw new Error(
      'usePersistentPdfWorkspaceLayout must be used inside PersistentPdfWorkspaceLayout',
    )
  }
  return controller
}

export default function PersistentPdfWorkspaceLayout() {
  const { user } = useAuth()
  const theme = useTheme()
  const isCompactLayout = useMediaQuery(theme.breakpoints.down('md'))
  const [pdfVisible, setPdfVisible] = useState(true)
  const location = useLocation()
  const curationMatch = matchPath('/curation/:sessionId/:candidateId', location.pathname)
    ?? matchPath('/curation/:sessionId', location.pathname)
  const layoutKind = curationMatch ? 'curation' : 'home'
  const isCurationLayout = layoutKind === 'curation'
  const isPdfVisible = !isCurationLayout || pdfVisible
  const activeDocumentOwnerToken = curationMatch?.params.sessionId
    ? buildCurationPDFViewerOwner(curationMatch.params.sessionId)
    : HOME_PDF_VIEWER_OWNER
  const rootWorkbenchSx = isCurationLayout
    ? {
        backgroundColor: theme.palette.background.default,
        gap: 1,
        padding: { xs: 1, md: 1.25 },
        paddingTop: { xs: 1, md: 0.75 },
      }
    : undefined

  const focusGrid = useCallback(() => {
    if (isCurationLayout) {
      setPdfVisible(false)
    }
  }, [isCurationLayout])
  const showPdf = useCallback(() => setPdfVisible(true), [])
  const layoutController = useMemo<PersistentPdfWorkspaceLayoutController>(() => ({
    focusGrid,
    isPdfVisible,
    showPdf,
  }), [focusGrid, isPdfVisible, showPdf])

  useEffect(() => {
    if (!isCurationLayout) {
      setPdfVisible(true)
    }
  }, [isCurationLayout])

  if (isCompactLayout) {
    return (
      <PersistentPdfWorkspaceLayoutContext.Provider value={layoutController}>
        <Root
          data-layout-kind={layoutKind}
          data-pdf-visible={isPdfVisible ? 'true' : 'false'}
          data-theme-mode={theme.palette.mode}
          data-testid="persistent-pdf-workspace-layout"
          sx={rootWorkbenchSx}
        >
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
              aria-hidden={!isPdfVisible}
              data-testid="persistent-pdf-viewer-panel"
              sx={{
                flex: '0 0 42%',
                minHeight: 280,
                display: isPdfVisible ? 'flex' : 'none',
                flexDirection: 'column',
                overflow: 'hidden',
              }}
            >
              <PdfViewer
                activeDocumentOwnerToken={activeDocumentOwnerToken}
                storageUserId={user?.uid ?? null}
                variant={isCurationLayout ? 'curation' : 'default'}
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
      </PersistentPdfWorkspaceLayoutContext.Provider>
    )
  }

  return (
    <PersistentPdfWorkspaceLayoutContext.Provider value={layoutController}>
      <Root
        data-layout-kind={layoutKind}
        data-pdf-visible={isPdfVisible ? 'true' : 'false'}
        data-theme-mode={theme.palette.mode}
        data-testid="persistent-pdf-workspace-layout"
        sx={rootWorkbenchSx}
      >
        <PanelGroup
          autoSaveId={`persistent-pdf-workspace-layout-${layoutKind}`}
          direction="horizontal"
          style={{ width: '100%', height: '100%', display: 'flex', overflow: 'hidden' }}
        >
          <Panel
            defaultSize={isCurationLayout ? 36 : 34}
            maxSize={60}
            minSize={20}
            order={1}
            style={isPdfVisible ? undefined : { display: 'none' }}
          >
            <PanelSection
              aria-hidden={!isPdfVisible}
              data-testid="persistent-pdf-viewer-panel"
            >
              <PdfViewer
                activeDocumentOwnerToken={activeDocumentOwnerToken}
                storageUserId={user?.uid ?? null}
                variant={isCurationLayout ? 'curation' : 'default'}
              />
            </PanelSection>
          </Panel>

          <ResizeHandle
            aria-label="Resize PDF and route content panels"
            sx={{ display: isPdfVisible ? undefined : 'none' }}
          />

          <Panel
            defaultSize={66}
            minSize={24}
            maxSize={80}
            order={2}
            style={isPdfVisible ? undefined : { flexGrow: 1 }}
          >
            <PanelSection data-testid="persistent-pdf-route-content">
              <Outlet />
            </PanelSection>
          </Panel>
        </PanelGroup>
      </Root>
    </PersistentPdfWorkspaceLayoutContext.Provider>
  )
}
