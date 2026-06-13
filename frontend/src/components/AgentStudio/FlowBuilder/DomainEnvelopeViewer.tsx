/**
 * DomainEnvelopeViewer Component
 *
 * Resizable slide-over inspector for an agent's domain envelope metadata.
 * Keeps dense schema details out of the node configuration drawer.
 */

import {
  Box,
  IconButton,
  Paper,
  Slide,
  Tooltip,
  Typography,
} from '@mui/material'
import { styled, alpha } from '@mui/material/styles'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import CloseIcon from '@mui/icons-material/Close'
import SchemaIcon from '@mui/icons-material/Schema'

import DomainEnvelopeMetadataPanel from '../DomainEnvelopeMetadataPanel'
import type { DomainEnvelopeMetadata } from '@/services/agentStudioService'
import type { ValidationAttachmentSelection } from './types'

interface DomainEnvelopeViewerProps {
  agentName: string
  metadata: DomainEnvelopeMetadata
  validationAttachments: ValidationAttachmentSelection[]
  open: boolean
  onClose: () => void
}

const ViewerShell = styled(Box)(() => ({
  position: 'absolute',
  inset: 0,
  zIndex: 20,
  display: 'flex',
  overflow: 'hidden',
  pointerEvents: 'none',
}))

const ViewerPanel = styled(Paper)(({ theme }) => ({
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
  pointerEvents: 'auto',
  backgroundColor: theme.palette.background.paper,
  borderLeft: `1px solid ${theme.palette.divider}`,
  boxShadow: theme.shadows[10],
}))

const ViewerHeader = styled(Box)(({ theme }) => ({
  padding: theme.spacing(1.5, 2),
  borderBottom: `1px solid ${theme.palette.divider}`,
  display: 'flex',
  alignItems: 'center',
  gap: theme.spacing(1.25),
  backgroundColor: alpha(theme.palette.primary.main, 0.05),
}))

const ViewerContent = styled(Box)(({ theme }) => ({
  flex: 1,
  minHeight: 0,
  overflow: 'auto',
  padding: theme.spacing(2),
}))

const ResizeHandle = styled(PanelResizeHandle)(({ theme }) => ({
  width: 6,
  flex: '0 0 6px',
  pointerEvents: 'auto',
  cursor: 'col-resize',
  backgroundColor: alpha(theme.palette.divider, 0.6),
  transition: 'background-color 0.2s ease',
  '&:hover, &[data-resize-handle-active="true"]': {
    backgroundColor: theme.palette.primary.main,
  },
}))

function DomainEnvelopeViewer({
  agentName,
  metadata,
  validationAttachments,
  open,
  onClose,
}: DomainEnvelopeViewerProps) {
  return (
    <Slide direction="left" in={open} mountOnEnter unmountOnExit>
      <ViewerShell>
        <PanelGroup direction="horizontal">
          <Panel minSize={8} defaultSize={28} order={1}>
            <Box
              onClick={onClose}
              sx={{
                height: '100%',
                pointerEvents: 'auto',
                backgroundColor: (theme) => alpha(theme.palette.background.default, 0.22),
              }}
            />
          </Panel>
          <ResizeHandle />
          <Panel minSize={50} defaultSize={72} maxSize={92} order={2}>
            <ViewerPanel elevation={8}>
              <ViewerHeader>
                <SchemaIcon sx={{ fontSize: 20, color: 'primary.main' }} />
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="h6" sx={{ fontWeight: 650, fontSize: '1rem', lineHeight: 1.25 }}>
                    Domain envelope
                  </Typography>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: 'block', overflowWrap: 'anywhere' }}
                  >
                    {agentName}
                  </Typography>
                </Box>
                <Tooltip title="Close envelope inspector">
                  <IconButton onClick={onClose} size="small">
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </ViewerHeader>
              <ViewerContent>
                <DomainEnvelopeMetadataPanel
                  metadata={metadata}
                  validationAttachments={validationAttachments}
                  compact={false}
                  layout="flow-editor"
                  validationModeNote="Active validators run automatically when checked. Replace a default validator with a custom Data Validation agent by connecting it as a validation attachment on the extractor; that validator's steering prompt is saved on the attached validator node."
                />
              </ViewerContent>
            </ViewerPanel>
          </Panel>
        </PanelGroup>
      </ViewerShell>
    </Slide>
  )
}

export default DomainEnvelopeViewer
