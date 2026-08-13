import React, { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Box, IconButton, Tooltip, useMediaQuery } from '@mui/material'
import { Menu as MenuIcon } from '@mui/icons-material'
import { useTheme } from '@mui/material/styles'
import Sidebar from './Sidebar'

const WeaviateLayout: React.FC = () => {
  const theme = useTheme()
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'))
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)

  useEffect(() => {
    setIsSidebarOpen(!isMobile)
  }, [isMobile])

  return (
    <Box sx={{ display: 'flex', width: '100%', height: '100%', minHeight: 0 }}>
      <Sidebar
        open={!isMobile || isSidebarOpen}
        onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
      />
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          width: '100%',
          height: '100%',
          minWidth: 0,
          minHeight: 0,
          marginLeft: 0,
          p: { xs: 2, sm: 3 },
          transition: theme => theme.transitions.create(['margin', 'width'], {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.leavingScreen,
          }),
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'stretch',
          overflow: 'hidden',
        }}
      >
        {isMobile && !isSidebarOpen && (
          <Tooltip title="Open Documents navigation">
            <IconButton
              aria-label="Open Documents navigation"
              onClick={() => setIsSidebarOpen(true)}
              size="small"
              sx={{
                alignSelf: 'flex-start',
                mb: 1,
                bgcolor: 'background.paper',
                border: 1,
                borderColor: 'divider',
                '&:hover': {
                  bgcolor: 'action.hover',
                },
              }}
            >
              <MenuIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
        <Box
          data-testid="weaviate-outlet-frame"
          sx={{
            width: '100%',
            maxWidth: 'none',
            flex: '1 1 auto',
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <Outlet />
        </Box>
      </Box>
    </Box>
  )
}

export default WeaviateLayout
