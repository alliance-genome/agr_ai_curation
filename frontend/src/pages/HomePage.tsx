import { AppBar, Toolbar, Typography, Button, Box, IconButton } from '@mui/material';
import { Home as HomeIcon, AdminPanelSettings as AdminIcon, Brightness4, Brightness7 } from '@mui/icons-material';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { useNavigate } from 'react-router-dom';
import { useTheme } from '@mui/material/styles';
import PdfViewer from '../components/PdfViewer';
import ChatInterface from '../components/ChatInterface';
import CurationPanel from '../components/CurationPanel';

interface HomePageProps {
  toggleColorMode: () => void;
}

function HomePage({ toggleColorMode }: HomePageProps) {
  const navigate = useNavigate();
  const theme = useTheme();

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <AppBar position="static" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
        <Toolbar>
          <Typography 
            variant="h6" 
            component="div" 
            sx={{ flexGrow: 1, cursor: 'pointer' }}
            onClick={() => navigate('/')}
          >
            Alliance AI-Assisted Curation Interface
          </Typography>
          
          <IconButton onClick={toggleColorMode} color="inherit" sx={{ mr: 1 }}>
            {theme.palette.mode === 'dark' ? <Brightness7 /> : <Brightness4 />}
          </IconButton>
          
          <Button
            color="inherit"
            startIcon={<HomeIcon />}
            onClick={() => navigate('/')}
            sx={{ mr: 1 }}
          >
            Home
          </Button>
          
          <Button
            color="inherit"
            variant="outlined"
            startIcon={<AdminIcon />}
            onClick={() => navigate('/admin')}
          >
            Admin
          </Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ flexGrow: 1, display: 'flex', overflow: 'hidden' }}>
        <PanelGroup direction="horizontal" style={{ height: '100%' }}>
          <Panel defaultSize={33} minSize={20} maxSize={50}>
            <PdfViewer />
          </Panel>
          
          <PanelResizeHandle 
            style={{ 
              width: '4px', 
              backgroundColor: theme.palette.divider,
              cursor: 'col-resize',
              transition: 'background-color 0.2s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = theme.palette.primary.main;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = theme.palette.divider;
            }}
          />
          
          <Panel defaultSize={34} minSize={20} maxSize={60}>
            <ChatInterface />
          </Panel>
          
          <PanelResizeHandle 
            style={{ 
              width: '4px', 
              backgroundColor: theme.palette.divider,
              cursor: 'col-resize',
              transition: 'background-color 0.2s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = theme.palette.primary.main;
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = theme.palette.divider;
            }}
          />
          
          <Panel defaultSize={33} minSize={20} maxSize={50}>
            <CurationPanel />
          </Panel>
        </PanelGroup>
      </Box>
    </Box>
  );
}

export default HomePage;