import React, { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Box } from '@mui/material'
import Sidebar from './Sidebar'

const WeaviateLayout: React.FC = () => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true)
  const drawerWidth = isSidebarOpen ? 240 : 64

  return (
    <Box sx={{ display: 'flex', width: '100%', height: '100%' }}>
      <Sidebar
        open={isSidebarOpen}
        onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
      />
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 3,
          width: '100%',
          marginLeft: `${drawerWidth}px`,
          transition: theme => theme.transitions.create(['margin', 'width'], {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.leavingScreen,
          }),
          maxWidth: '1400px',
          mx: 'auto',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        <Box sx={{ width: '100%', maxWidth: '1200px' }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  )
}

export default WeaviateLayout