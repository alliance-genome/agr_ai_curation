import React, { Suspense, lazy, useEffect, useState } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, Link, useLocation, useNavigate } from 'react-router-dom'
import { CssBaseline, Box, AppBar, Toolbar, Typography, CircularProgress, Button, Tooltip, Snackbar, Alert } from '@mui/material'
import {
  Logout as LogoutIcon,
  AutoAwesome as AgentStudioIcon,
  FactCheck as CurationIcon,
  Home as HomeIcon,
  HelpOutline as HelpIcon,
  History as HistoryIcon,
  Update as ChangelogIcon,
} from '@mui/icons-material'
import { getVersionDisplay, getFullVersionInfo } from './config/version'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { AgentMetadataProvider } from './contexts/AgentMetadataContext'
import { ThemeModeProvider } from './contexts/ThemeModeContext'
import LogoutDialog from './components/LogoutDialog'
import ThemeModeToggle from './components/ThemeModeToggle'
import WeaviateNavIcon from './components/weaviate/WeaviateNavIcon'
import WeaviateLayout from './components/weaviate/WeaviateLayout'
import BatchNavIcon from './components/BatchNavIcon'
import ForceScrollFix from './components/ForceScrollFix'
import MaintenanceBanner from './components/MaintenanceBanner'
import ConnectionsHealthBanner from './components/ConnectionsHealthBanner'
import { GLOBAL_TOAST_EVENT, GlobalToastEventDetail } from './lib/globalNotifications'
import { POPUP_CHANGELOG_ENTRY } from './content/changelog'
import ChangelogDialog from './components/ChangelogDialog'
import { buildPdfTerminalNotification } from './features/documents/pdfTerminalNotifications'
import './App.css'

export const queryClient = new QueryClient()
const DEFAULT_GLOBAL_SNACKBAR_AUTO_HIDE_MS = 4000
const DEFAULT_GLOBAL_SNACKBAR_ANCHOR = { vertical: 'bottom', horizontal: 'right' } as const

const HomePage = lazy(() => import('./pages/HomePage'))
const ViewerSettings = lazy(() => import('./pages/ViewerSettings'))
const AgentStudioPage = lazy(() => import('./pages/AgentStudioPage'))
const BatchPage = lazy(() => import('./pages/BatchPage'))
const ChangelogPage = lazy(() => import('./pages/ChangelogPage'))
const CurationInventoryPage = lazy(() => import('./pages/CurationInventoryPage'))
const CurationWorkspacePage = lazy(() => import('./pages/CurationWorkspacePage'))
const HistoryPage = lazy(() => import('./features/history/HistoryPage'))
const PersistentPdfWorkspaceLayout = lazy(() => import('./components/pdfViewer/PersistentPdfWorkspaceLayout'))
const Settings = lazy(() => import('./pages/weaviate/Settings'))
const DocumentDetail = lazy(() => import('./pages/weaviate/DocumentDetail'))
const DocumentsPage = lazy(() => import('./pages/weaviate/DocumentsPage'))
const Dashboard = lazy(() => import('./pages/weaviate/Dashboard'))
const EmbeddingsSettings = lazy(() => import('./pages/weaviate/settings/EmbeddingsSettings'))
const DatabaseSettings = lazy(() => import('./pages/weaviate/settings/DatabaseSettings'))
const SchemaSettings = lazy(() => import('./pages/weaviate/settings/SchemaSettings'))
const ChunkingSettings = lazy(() => import('./pages/weaviate/settings/ChunkingSettings'))

function RouteLoadingFallback() {
  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: 240,
        width: '100%',
      }}
    >
      <CircularProgress />
    </Box>
  );
}

function renderLazyRoute(element: React.ReactNode) {
  return (
    <Suspense fallback={<RouteLoadingFallback />}>
      {element}
    </Suspense>
  );
}

/**
 * ProtectedRoutes: Wrapper component that checks authentication before rendering routes
 * If not authenticated, redirects to login
 */
export function ProtectedRoutes({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, login } = useAuth();
  const location = useLocation();
  const pendingLogoutSuppressionRef = React.useRef(false);

  useEffect(() => {
    // Latch logout suppression in a ref so it survives the auth-state render sequence.
    if (sessionStorage.getItem('justLoggedOut') === 'true') {
      pendingLogoutSuppressionRef.current = true;
      sessionStorage.removeItem('justLoggedOut');
    }

    if (isAuthenticated) {
      return;
    }

    if (isLoading) {
      return;
    }

    // Consume the logout suppression exactly once after loading settles.
    if (pendingLogoutSuppressionRef.current) {
      pendingLogoutSuppressionRef.current = false;
      return;
    }

    // Save intended destination for redirect after login (future enhancement)
    sessionStorage.setItem('intendedPath', location.pathname + location.search);
    login();
  }, [isLoading, isAuthenticated, login, location.pathname, location.search]);

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

export function AppContent() {
  const { user, logout, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const lastAuthenticatedUserIdRef = React.useRef<string | null>(isAuthenticated ? user?.uid ?? null : null);
  const [logoutDialogOpen, setLogoutDialogOpen] = useState(false);
  const [changelogDialogOpen, setChangelogDialogOpen] = useState(false);
  const [globalSnackbar, setGlobalSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'warning' | 'info';
    autoHideDurationMs?: number;
    anchorOrigin?: {
      vertical: 'top' | 'bottom';
      horizontal: 'left' | 'center' | 'right';
    };
  }>({ open: false, message: '', severity: 'info' });
  const seededPdfJobsRef = React.useRef(false);
  const seededBatchesRef = React.useRef(false);
  const seenPdfTerminalRef = React.useRef<Set<string>>(new Set());
  const seenBatchTerminalRef = React.useRef<Set<string>>(new Set());
  const changelogStorageKey = user?.uid ? `changelog:last-seen:${user.uid}` : null;

  /**
   * Handle logout confirmation
   * Called when user confirms logout in the dialog
   */
  const handleLogoutConfirm = async () => {
    setLogoutDialogOpen(false);
    await logout();
  };

  const markLatestChangelogSeen = React.useCallback(() => {
    if (!changelogStorageKey || !POPUP_CHANGELOG_ENTRY) {
      return;
    }
    localStorage.setItem(changelogStorageKey, POPUP_CHANGELOG_ENTRY.id);
  }, [changelogStorageKey]);

  const handleChangelogDialogClose = React.useCallback(() => {
    markLatestChangelogSeen();
    setChangelogDialogOpen(false);
  }, [markLatestChangelogSeen]);

  const handleChangelogViewAll = React.useCallback(() => {
    markLatestChangelogSeen();
    setChangelogDialogOpen(false);
    navigate('/changelog');
  }, [markLatestChangelogSeen, navigate]);

  useEffect(() => {
    if (!isAuthenticated || !user?.uid || !POPUP_CHANGELOG_ENTRY) {
      setChangelogDialogOpen(false);
      return;
    }

    const key = `changelog:last-seen:${user.uid}`;
    const lastSeenId = localStorage.getItem(key);
    if (lastSeenId !== POPUP_CHANGELOG_ENTRY.id) {
      setChangelogDialogOpen(true);
    }
  }, [isAuthenticated, user?.uid]);

  useEffect(() => {
    const onGlobalToast = (event: Event) => {
      const customEvent = event as CustomEvent<GlobalToastEventDetail>;
      const detail = customEvent.detail;
      if (!detail?.message) {
        return;
      }
      setGlobalSnackbar({
        open: true,
        message: detail.message,
        severity: detail.severity ?? 'info',
        autoHideDurationMs: detail.autoHideDurationMs,
        anchorOrigin: detail.anchorOrigin,
      });
    };

    window.addEventListener(GLOBAL_TOAST_EVENT, onGlobalToast as EventListener);
    return () => {
      window.removeEventListener(GLOBAL_TOAST_EVENT, onGlobalToast as EventListener);
    };
  }, []);

  useEffect(() => {
    const currentUserId = isAuthenticated ? user?.uid ?? null : null

    if (lastAuthenticatedUserIdRef.current !== currentUserId) {
      queryClient.clear()
    }

    lastAuthenticatedUserIdRef.current = currentUserId
  }, [isAuthenticated, user?.uid])

  useEffect(() => {
    if (!isAuthenticated) {
      seededPdfJobsRef.current = false;
      seededBatchesRef.current = false;
      seenPdfTerminalRef.current.clear();
      seenBatchTerminalRef.current.clear();
      return;
    }

    const pollNotifications = async () => {
      const onDocumentsPage = location.pathname.startsWith('/weaviate/documents');
      const onBatchPage = location.pathname.startsWith('/batch');

      if (!onDocumentsPage) {
        try {
          const response = await fetch('/api/weaviate/pdf-jobs?window_days=7&limit=50&offset=0', {
            credentials: 'include',
          });
          if (response.ok) {
            const payload = (await response.json()) as {
              jobs?: Array<{ job_id: string; status: string; filename?: string; document_id: string; cancel_requested?: boolean }>;
            };
            const jobs = payload.jobs ?? [];

            for (const job of jobs) {
              const notification = buildPdfTerminalNotification(job);
              if (!notification) {
                continue;
              }
              const terminalKey = notification.key;
              const alreadySeen = seenPdfTerminalRef.current.has(terminalKey);
              if (!seededPdfJobsRef.current) {
                seenPdfTerminalRef.current.add(terminalKey);
                continue;
              }
              if (alreadySeen) {
                continue;
              }

              seenPdfTerminalRef.current.add(terminalKey);
              setGlobalSnackbar({ open: true, message: notification.message, severity: notification.severity });
            }

            seededPdfJobsRef.current = true;
          }
        } catch (error) {
          console.error('Global PDF job notification poll failed:', error);
        }
      }

      if (!onBatchPage) {
        try {
          const response = await fetch('/api/batches', { credentials: 'include' });
          if (response.ok) {
            const payload = (await response.json()) as {
              batches?: Array<{
                id: string;
                status: string;
                flow_name?: string | null;
                completed_documents?: number;
                total_documents?: number;
                failed_documents?: number;
              }>;
            };
            const batches = payload.batches ?? [];

            for (const batch of batches) {
              const status = String(batch.status).toLowerCase();
              if (!['completed', 'cancelled'].includes(status)) {
                continue;
              }
              const terminalKey = `${batch.id}:${status}`;
              const alreadySeen = seenBatchTerminalRef.current.has(terminalKey);
              if (!seededBatchesRef.current) {
                seenBatchTerminalRef.current.add(terminalKey);
                continue;
              }
              if (alreadySeen) {
                continue;
              }

              seenBatchTerminalRef.current.add(terminalKey);
              const flowLabel = batch.flow_name ? ` (${batch.flow_name})` : '';
              if (status === 'cancelled') {
                setGlobalSnackbar({
                  open: true,
                  message: `Batch processing cancelled${flowLabel}`,
                  severity: 'info',
                });
              } else {
                setGlobalSnackbar({
                  open: true,
                  message: `Batch completed${flowLabel}: ${batch.completed_documents ?? 0}/${batch.total_documents ?? 0} succeeded, ${batch.failed_documents ?? 0} failed`,
                  severity: 'success',
                });
              }
            }

            seededBatchesRef.current = true;
          }
        } catch (error) {
          console.error('Global batch notification poll failed:', error);
        }
      }
    };

    void pollNotifications();
    const intervalId = window.setInterval(() => {
      void pollNotifications();
    }, 10000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [isAuthenticated, location.pathname]);

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        overflow: 'hidden',
        bgcolor: 'background.default',
        color: 'text.primary',
      }}
    >
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
            component={Link}
            to="/curation"
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
            <CurationIcon fontSize="small" />
            <Typography variant="body2">Curation</Typography>
          </Box>
          <Box
            component={Link}
            to="/history"
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
            <HistoryIcon fontSize="small" />
            <Typography variant="body2">Chat History</Typography>
          </Box>
          <Box
            component={Link}
            to="/changelog"
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
            <ChangelogIcon fontSize="small" />
            <Typography variant="body2">Changelog</Typography>
          </Box>
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
          <ThemeModeToggle />

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
      <ChangelogDialog
        open={changelogDialogOpen}
        entry={POPUP_CHANGELOG_ENTRY}
        onClose={handleChangelogDialogClose}
        onViewAll={handleChangelogViewAll}
      />

      <Box
        component="main"
        sx={{
          flex: 1,
          display: 'flex',
          minHeight: 0,
          overflow: 'hidden',
          bgcolor: 'background.default',
          color: 'text.primary',
        }}
      >
        <Routes>
          <Route path="/history" element={renderLazyRoute(<HistoryPage />)} />
          <Route path="/changelog" element={renderLazyRoute(<ChangelogPage />)} />
          <Route path="/viewer-settings" element={renderLazyRoute(<ViewerSettings />)} />
          <Route path="/agent-studio" element={renderLazyRoute(<AgentStudioPage />)} />
          <Route path="/batch" element={renderLazyRoute(<BatchPage />)} />
          <Route path="/curation" element={renderLazyRoute(<CurationInventoryPage />)} />
          <Route element={renderLazyRoute(<PersistentPdfWorkspaceLayout />)}>
            <Route index element={renderLazyRoute(<HomePage />)} />
            <Route
              path="curation/:sessionId"
              element={renderLazyRoute(<CurationWorkspacePage />)}
            />
            <Route
              path="curation/:sessionId/:candidateId"
              element={renderLazyRoute(<CurationWorkspacePage />)}
            />
          </Route>
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
            <Route path="documents" element={renderLazyRoute(<DocumentsPage />)} />
            <Route path="documents/:id" element={renderLazyRoute(<DocumentDetail />)} />
            <Route path="dashboard" element={renderLazyRoute(<Dashboard />)} />
            <Route path="settings" element={renderLazyRoute(<Settings />)} />
            <Route path="settings/embeddings" element={renderLazyRoute(<EmbeddingsSettings />)} />
            <Route path="settings/database" element={renderLazyRoute(<DatabaseSettings />)} />
            <Route path="settings/schema" element={renderLazyRoute(<SchemaSettings />)} />
            <Route path="settings/chunking" element={renderLazyRoute(<ChunkingSettings />)} />
          </Route>
        </Routes>
      </Box>

      <Snackbar
        open={globalSnackbar.open}
        autoHideDuration={globalSnackbar.autoHideDurationMs ?? DEFAULT_GLOBAL_SNACKBAR_AUTO_HIDE_MS}
        onClose={() => setGlobalSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={globalSnackbar.anchorOrigin ?? DEFAULT_GLOBAL_SNACKBAR_ANCHOR}
      >
        <Alert
          severity={globalSnackbar.severity}
          onClose={() => setGlobalSnackbar((prev) => ({ ...prev, open: false }))}
          sx={{ width: '100%' }}
        >
          {globalSnackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}

function App() {
  return (
    <ThemeModeProvider>
      <CssBaseline enableColorScheme />
      <ForceScrollFix />
      <MaintenanceBanner />
      <QueryClientProvider client={queryClient}>
        <ConnectionsHealthBanner />
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
    </ThemeModeProvider>
  )
}

export default App
