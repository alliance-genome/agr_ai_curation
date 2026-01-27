import React, { useEffect, useState } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, Link, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { CssBaseline, Box, AppBar, Toolbar, Typography, CircularProgress, Button, Tooltip } from '@mui/material'
import { Logout as LogoutIcon, AutoAwesome as AgentStudioIcon, Home as HomeIcon, Settings as SettingsIcon, HelpOutline as HelpIcon } from '@mui/icons-material'
import { getVersionDisplay, getFullVersionInfo } from './config/version'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { AgentMetadataProvider } from './contexts/AgentMetadataContext'
import LogoutDialog from './components/LogoutDialog'
import WeaviateNavIcon from './components/weaviate/WeaviateNavIcon'
import BatchNavIcon from './components/BatchNavIcon'
import WeaviateLayout from './components/weaviate/WeaviateLayout'
import Settings from './pages/weaviate/Settings'
import DocumentDetail from './pages/weaviate/DocumentDetail'
import DocumentsPage from './pages/weaviate/DocumentsPage'
import Dashboard from './pages/weaviate/Dashboard'
import EmbeddingsSettings from './pages/weaviate/settings/EmbeddingsSettings'
import DatabaseSettings from './pages/weaviate/settings/DatabaseSettings'
import SchemaSettings from './pages/weaviate/settings/SchemaSettings'
import ChunkingSettings from './pages/weaviate/settings/ChunkingSettings'
import HomePage from './pages/HomePage'
import ViewerSettings from './pages/ViewerSettings'
import AgentStudioPage from './pages/AgentStudioPage'
import BatchPage from './pages/BatchPage'
import ForceScrollFix from './components/ForceScrollFix'
import MaintenanceBanner from './components/MaintenanceBanner'
import theme from './theme'
import './App.css'

const queryClient = new QueryClient()

/**
 * ProtectedRoutes: Wrapper component that checks authentication before rendering routes
 * If not authenticated, redirects to Okta login
 */
function ProtectedRoutes({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, login } = useAuth();
  const location = useLocation();

  useEffect(() => {
    // Check if user just logged out (flag set in AuthContext before Cognito redirect)
    const justLoggedOut = sessionStorage.getItem('justLoggedOut') === 'true';

    if (justLoggedOut) {
      // Clear the flag now that we've checked it
      sessionStorage.removeItem('justLoggedOut');
    }

    // If not loading and not authenticated, redirect to login
    // UNLESS the user just logged out (in which case show logged out state)
    if (!isLoading && !isAuthenticated && !justLoggedOut) {
      // Save intended destination for redirect after login (future enhancement)
      sessionStorage.setItem('intendedPath', location.pathname + location.search);
      login();
    }
  }, [isLoading, isAuthenticated, login, location]);

  // Show loading spinner while checking auth
  if (isLoading) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          height: '100vh',
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  // If authenticated, render children
  if (isAuthenticated) {
    return <>{children}</>;
  }

  // If not authenticated and not loading, render nothing (redirect will happen)
  return null;
}

function AppContent() {
  const { user, logout } = useAuth();
  const [logoutDialogOpen, setLogoutDialogOpen] = useState(false);

  /**
   * Handle logout confirmation
   * Called when user confirms logout in the dialog
   */
  const handleLogoutConfirm = async () => {
    setLogoutDialogOpen(false);
    await logout();
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
        <Toolbar>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexGrow: 1 }}>
            <Typography
              variant="h1"
              component={Link}
              to="/"
              sx={{
                textDecoration: 'none',
                color: 'inherit',
                cursor: 'pointer',
                '&:hover': {
                  opacity: 0.9
                }
              }}
            >
              Alliance AI-Assisted Curation Interface
            </Typography>
            <Tooltip title={getFullVersionInfo()} arrow>
              <Typography
                variant="caption"
                sx={{
                  opacity: 0.7,
                  fontSize: '0.7rem',
                  fontFamily: 'monospace',
                  backgroundColor: 'rgba(255, 255, 255, 0.1)',
                  px: 0.75,
                  py: 0.25,
                  borderRadius: 0.5,
                  cursor: 'default',
                  '&:hover': {
                    opacity: 1,
                    backgroundColor: 'rgba(255, 255, 255, 0.15)',
                  }
                }}
              >
                {getVersionDisplay()}
              </Typography>
            </Tooltip>
          </Box>
          <Box
            component={Link}
            to="/"
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              textDecoration: 'none',
              color: 'inherit',
              marginRight: 2,
              '&:hover': {
                opacity: 0.8
              }
            }}
          >
            <HomeIcon fontSize="small" />
            <Typography variant="body2">Home</Typography>
          </Box>
          <Box
            component={Link}
            to="/agent-studio"
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              textDecoration: 'none',
              color: 'inherit',
              marginRight: 2,
              '&:hover': {
                opacity: 0.8
              }
            }}
          >
            <AgentStudioIcon fontSize="small" />
            <Typography variant="body2">Agent Studio</Typography>
          </Box>
          <WeaviateNavIcon />
          <BatchNavIcon />
          <Box
            component="a"
            href="https://github.com/alliance-genome/agr_ai_curation/blob/main/docs/curator/README.md"
            target="_blank"
            rel="noopener noreferrer"
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.5,
              textDecoration: 'none',
              color: 'inherit',
              marginRight: 2,
              '&:hover': {
                opacity: 0.8
              }
            }}
          >
            <HelpIcon fontSize="small" />
            <Typography variant="body2">Help</Typography>
          </Box>

          {/* T045: User Identity Display */}
          {user && (
            <Typography
              variant="body2"
              sx={{
                marginLeft: 2,
                marginRight: 2,
                opacity: 0.9,
              }}
            >
              {user.name || user.email || user.uid}
            </Typography>
          )}

          {/* T043: Logout Button */}
          <Button
            color="inherit"
            onClick={() => setLogoutDialogOpen(true)}
            startIcon={<LogoutIcon />}
            sx={{
              marginLeft: 1,
            }}
          >
            Logout
          </Button>
        </Toolbar>
      </AppBar>
      <Toolbar /> {/* This creates spacing for the fixed AppBar */}

      {/* T044: Logout Confirmation Dialog */}
      <LogoutDialog
        open={logoutDialogOpen}
        onClose={() => setLogoutDialogOpen(false)}
        onConfirm={handleLogoutConfirm}
      />

      <Box component="main" sx={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden' }}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/viewer-settings" element={<ViewerSettings />} />
          <Route path="/agent-studio" element={<AgentStudioPage />} />
          <Route path="/batch" element={<BatchPage />} />
          <Route path="/pdf-viewer" element={
            <Box sx={{
              width: '100%',
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              padding: 2,
              backgroundColor: '#121212'
            }}>
              <Typography variant="h2" sx={{ color: '#fff' }}>
                PDF Viewer - Coming Soon
              </Typography>
            </Box>
          } />
          <Route path="/weaviate/*" element={<WeaviateLayout />}>
            <Route index element={<Navigate to="/weaviate/documents" replace />} />
            <Route path="documents" element={<DocumentsPage />} />
            <Route path="documents/:id" element={<DocumentDetail />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="settings" element={<Settings />} />
            <Route path="settings/embeddings" element={<EmbeddingsSettings />} />
            <Route path="settings/database" element={<DatabaseSettings />} />
            <Route path="settings/schema" element={<SchemaSettings />} />
            <Route path="settings/chunking" element={<ChunkingSettings />} />
          </Route>
        </Routes>
      </Box>
    </Box>
  );
}

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <ForceScrollFix />
      <MaintenanceBanner />
      <QueryClientProvider client={queryClient}>
        <Router>
          <AuthProvider>
            <ProtectedRoutes>
              <AgentMetadataProvider>
                <AppContent />
              </AgentMetadataProvider>
            </ProtectedRoutes>
          </AuthProvider>
        </Router>
      </QueryClientProvider>
    </ThemeProvider>
  )
}

export default App
