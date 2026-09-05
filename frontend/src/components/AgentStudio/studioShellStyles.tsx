import { Box, Tabs, Tab } from '@mui/material'
import { styled } from '@mui/material/styles'
import { PanelResizeHandle } from 'react-resizable-panels'

export const Root = styled(Box)(({ theme }) => ({
  flex: 1,
  display: 'flex',
  height: '100%',
  overflow: 'hidden',
  padding: theme.spacing(1),
}))

/** Paper card with the shared 1px divider border and 8px radius from the shell mockup. */
export const PanelCard = styled(Box)(({ theme }) => ({
  display: 'flex',
  flexDirection: 'column',
  // The Panel element is a flex row; without these the card is sized by its
  // content and leaves the rest of the panel empty.
  flex: '1 1 0%',
  width: '100%',
  minWidth: 0,
  minHeight: 0,
  height: '100%',
  backgroundColor: theme.palette.background.paper,
  border: `1px solid ${theme.palette.divider}`,
  borderRadius: theme.shape.borderRadius * 2,
  overflow: 'hidden',
}))

export const ClaudePanelSection = styled(PanelCard, {
  shouldForwardProp: (prop) => prop !== 'collapsed',
})<{ collapsed: boolean }>(({ collapsed }) => ({
  visibility: collapsed ? 'hidden' : 'visible',
  '& > *': {
    flex: 1,
    minHeight: 0,
    height: '100%',
  },
}))

export const ResizeHandle = styled(PanelResizeHandle, {
  shouldForwardProp: (prop) => prop !== 'collapsed',
})<{ collapsed: boolean }>(({ theme, collapsed }) => ({
  width: 8,
  flex: '0 0 8px',
  display: collapsed ? 'none' : 'block',
  cursor: 'col-resize',
  position: 'relative',
  '&::after': {
    content: '""',
    position: 'absolute',
    top: 0,
    bottom: 0,
    left: 3,
    width: 2,
    borderRadius: 1,
    backgroundColor: theme.palette.divider,
    transition: 'background-color 0.2s ease',
  },
  '&:hover::after, &[data-resize-handle-active]::after': {
    backgroundColor: theme.palette.primary.main,
  },
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: -2,
  },
}))

export const TabBar = styled(Box)(({ theme }) => ({
  display: 'flex',
  alignItems: 'center',
  minHeight: 40,
  borderBottom: `1px solid ${theme.palette.divider}`,
  paddingRight: theme.spacing(1),
  flex: 'none',
}))

export const StyledTabs = styled(Tabs)(() => ({
  minHeight: 40,
  flex: 1,
  minWidth: 0,
  '& .MuiTabs-indicator': {
    height: 3,
  },
}))

export const VisuallyHidden = styled('span')({
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0, 0, 0, 0)',
  whiteSpace: 'nowrap',
  border: 0,
})

export const StyledTab = styled(Tab)(({ theme }) => ({
  minHeight: 40,
  textTransform: 'none',
  fontWeight: 500,
  fontSize: '0.85rem',
  '&.Mui-selected': {
    color: theme.palette.primary.main,
  },
}))

export const TabContent = styled(Box)(() => ({
  flex: 1,
  minHeight: 0,
  overflow: 'hidden',
}))

