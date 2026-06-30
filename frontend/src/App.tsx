import React, { Suspense, lazy, useEffect, useState } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, Link, useLocation, useNavigate } from 'react-router-dom'
import { CssBaseline, Box, AppBar, Toolbar, Typography, CircularProgress, Button, Tooltip, Snackbar, Alert, GlobalStyles } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
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
import { clearAiCurationLocalCache } from './lib/aiCurationLocalCache'
import {
  BROWSER_STORAGE_PRESSURE_EVENT,
  safeGetItem,
  safeRemoveItem,
  safeSetItem,
  type BrowserStoragePressureEventDetail,
} from './lib/browserStorage'
import { POPUP_CHANGELOG_ENTRY } from './content/changelog'
import ChangelogDialog from './components/ChangelogDialog'
import { buildPdfTerminalNotification } from './features/documents/pdfTerminalNotifications'
import { useChatStream, type SSEEvent } from './hooks/useChatStream'
import { getStreamEventSessionId } from './lib/streamEventSession'
import './App.css'

export const queryClient = new QueryClient()
const DEFAULT_GLOBAL_SNACKBAR_AUTO_HIDE_MS = 4000
const DEFAULT_GLOBAL_SNACKBAR_ANCHOR = { vertical: 'bottom', horizontal: 'right' } as const

function isChatRoutePath(pathname: string): boolean {
  return (pathname.replace(/\/+$/, '') || '/') === '/'
}

function getLatestStreamSessionId(events: SSEEvent[]): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const sessionId = getStreamEventSessionId(events[index])
    if (sessionId) {
      return sessionId
    }
  }

  return null
}

function hasStoppedRunEvent(events: SSEEvent[]): boolean {
  return events.some((event) => event.type === 'STOP_CONFIRMED')
}

function hasErrorRunEvent(events: SSEEvent[]): boolean {
  return events.some((event) => event.type.includes('ERROR'))
}

const APP_THEME_GLOBAL_ALPHA = {
  dark: {
    textMuted: 0.6,
    subtleDivider: 0.08,
    headerShadow: 0.28,
    shadowSm: 0.28,
    focusRing: 0.24,
    iconButtonBg: 0.1,
    iconButtonBorder: 0.2,
    iconButtonColor: 0.72,
    iconButtonHoverBg: 0.2,
    inputBg: 0.22,
    inputBorder: 0.23,
    inputPlaceholder: 0.5,
    sendShadow: 0.3,
    sendShadowHover: 0.4,
    scrollbarTrack: 0.05,
    scrollbarThumb: 0.15,
    scrollbarThumbHover: 0.25,
    linkedFieldBg: 0.12,
    linkedFieldRing: 0.38,
  },
  light: {
    textMuted: 0.56,
    subtleDivider: 0.1,
    headerShadow: 0.16,
    shadowSm: 0.14,
    focusRing: 0.18,
    iconButtonBg: 0.08,
    iconButtonBorder: 0.18,
    iconButtonColor: 0.7,
    iconButtonHoverBg: 0.14,
    inputBg: 0.92,
    inputBorder: 0.22,
    inputPlaceholder: 0.48,
    sendShadow: 0.22,
    sendShadowHover: 0.28,
    scrollbarTrack: 0.06,
    scrollbarThumb: 0.18,
    scrollbarThumbHover: 0.28,
    linkedFieldBg: 0.1,
    linkedFieldRing: 0.28,
  },
} as const

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
const AddLiteraturePage = lazy(() => import('./pages/weaviate/AddLiteraturePage'))
const Dashboard = lazy(() => import('./pages/weaviate/Dashboard'))
const EmbeddingsSettings = lazy(() => import('./pages/weaviate/settings/EmbeddingsSettings'))
const DatabaseSettings = lazy(() => import('./pages/weaviate/settings/DatabaseSettings'))
const SchemaSettings = lazy(() => import('./pages/weaviate/settings/SchemaSettings'))
const ChunkingSettings = lazy(() => import('./pages/weaviate/settings/ChunkingSettings'))

function AppThemeGlobalStyles() {
  const theme = useTheme()
  const tone = APP_THEME_GLOBAL_ALPHA[theme.palette.mode]
  const textPrimary = theme.palette.text.primary
  const primaryMain = theme.palette.primary.main
  const warningMain = theme.palette.warning.main
  const userMessage = {
    dark: {
      background: theme.palette.grey[800],
      text: theme.palette.common.white,
    },
    light: {
      background: alpha(primaryMain, 0.12),
      text: textPrimary,
    },
  }[theme.palette.mode]
  const appVariables = {
    '--app-bg': theme.palette.background.default,
    '--app-text': textPrimary,
    '--app-text-secondary': theme.palette.text.secondary,
    '--app-text-muted': alpha(textPrimary, tone.textMuted),
    '--app-divider': theme.palette.divider,
    '--app-subtle-divider': alpha(textPrimary, tone.subtleDivider),
    '--app-primary': primaryMain,
    '--app-primary-hover': theme.palette.primary.dark,
    '--app-primary-contrast': theme.palette.primary.contrastText,
    '--app-warning-bg': warningMain,
    '--app-warning-border': theme.palette.warning.dark,
    '--app-warning-contrast': theme.palette.getContrastText(warningMain),
    '--app-action-disabled-bg': theme.palette.action.disabledBackground,
    '--app-action-disabled-text': theme.palette.action.disabled,
    '--app-header-shadow': `0 2px 4px ${alpha(theme.palette.common.black, tone.headerShadow)}`,
    '--app-shadow-sm': `0 1px 3px ${alpha(theme.palette.common.black, tone.shadowSm)}`,
    '--app-focus-ring': `0 0 0 3px ${alpha(primaryMain, tone.focusRing)}`,
    '--app-user-message-bg': userMessage.background,
    '--app-user-message-text': userMessage.text,
    '--app-assistant-message-bg': theme.palette.secondary.main,
    '--app-assistant-message-text': theme.palette.secondary.contrastText,
    '--app-icon-button-bg': alpha(textPrimary, tone.iconButtonBg),
    '--app-icon-button-border': alpha(textPrimary, tone.iconButtonBorder),
    '--app-icon-button-color': alpha(textPrimary, tone.iconButtonColor),
    '--app-icon-button-hover-bg': alpha(textPrimary, tone.iconButtonHoverBg),
    '--app-icon-button-hover-color': textPrimary,
    '--app-input-bg': alpha(theme.palette.background.paper, tone.inputBg),
    '--app-input-border': alpha(textPrimary, tone.inputBorder),
    '--app-input-placeholder': alpha(textPrimary, tone.inputPlaceholder),
    '--app-send-shadow': `0 2px 4px ${alpha(primaryMain, tone.sendShadow)}`,
    '--app-send-shadow-hover': `0 4px 8px ${alpha(primaryMain, tone.sendShadowHover)}`,
    '--app-scrollbar-track': alpha(textPrimary, tone.scrollbarTrack),
    '--app-scrollbar-thumb': alpha(textPrimary, tone.scrollbarThumb),
    '--app-scrollbar-thumb-hover': alpha(textPrimary, tone.scrollbarThumbHover),
    '--app-linked-field-bg': alpha(primaryMain, tone.linkedFieldBg),
    '--app-linked-field-ring': alpha(primaryMain, tone.linkedFieldRing),
  } as React.CSSProperties

  return (
    <GlobalStyles
      styles={{
        ':root': appVariables,
        html: {
          backgroundColor: theme.palette.background.default,
          color: theme.palette.text.primary,
        },
        body: {
          backgroundColor: theme.palette.background.default,
          color: theme.palette.text.primary,
        },
        '#root': {
          backgroundColor: theme.palette.background.default,
          color: theme.palette.text.primary,
        },
      }}
    />
  )
}

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
    const justLoggedOut = safeGetItem(() => window.sessionStorage, 'justLoggedOut', {
      owner: 'auth',
      key: 'justLoggedOut',
      workflowCritical: true,
    });
    if (justLoggedOut.ok && justLoggedOut.value === 'true') {
      pendingLogoutSuppressionRef.current = true;
      safeRemoveItem(() => window.sessionStorage, 'justLoggedOut', {
        owner: 'auth',
        key: 'justLoggedOut',
        workflowCritical: true,
      });
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
    safeSetItem(() => window.sessionStorage, 'intendedPath', location.pathname + location.search, {
      owner: 'auth',
      key: 'intendedPath',
      workflowCritical: true,
    });
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
  const {
    events: chatStreamEvents,
    isLoading: chatRunActive,
    error: chatRunError,
  } = useChatStream();
  const navigate = useNavigate();
  const location = useLocation();
  const normalizedPathname = location.pathname.replace(/\/+$/, '');
  const suppressChangelogDialog =
    normalizedPathname === '/weaviate/add-literature' ||
    normalizedPathname === '/weaviate/documents/import-mock';
  const lastAuthenticatedUserIdRef = React.useRef<string | null>(isAuthenticated ? user?.uid ?? null : null);
  const [logoutDialogOpen, setLogoutDialogOpen] = useState(false);
  const [changelogDialogOpen, setChangelogDialogOpen] = useState(false);
  const [globalSnackbar, setGlobalSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: 'success' | 'error' | 'warning' | 'info';
    autoHideDurationMs?: number | null;
    anchorOrigin?: {
      vertical: 'top' | 'bottom';
      horizontal: 'left' | 'center' | 'right';
    };
    showStorageRecoveryAction?: boolean;
    chatReturnPath?: string;
  }>({ open: false, message: '', severity: 'info' });
  const wasChatRunActiveRef = React.useRef(chatRunActive);
  const activeRunSessionIdRef = React.useRef<string | null>(getLatestStreamSessionId(chatStreamEvents));
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
    safeSetItem(() => window.localStorage, changelogStorageKey, POPUP_CHANGELOG_ENTRY.id, {
      owner: 'preferences',
      key: changelogStorageKey,
    });
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
    if (suppressChangelogDialog || !isAuthenticated || !user?.uid || !POPUP_CHANGELOG_ENTRY) {
      setChangelogDialogOpen(false);
      return;
    }

    const key = `changelog:last-seen:${user.uid}`;
    const lastSeenId = safeGetItem(() => window.localStorage, key, {
      owner: 'preferences',
      key,
    });
    if (!lastSeenId.ok || lastSeenId.value !== POPUP_CHANGELOG_ENTRY.id) {
      setChangelogDialogOpen(true);
    }
  }, [isAuthenticated, suppressChangelogDialog, user?.uid]);

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
        showStorageRecoveryAction: false,
        chatReturnPath: undefined,
      });
    };

    window.addEventListener(GLOBAL_TOAST_EVENT, onGlobalToast as EventListener);
    return () => {
      window.removeEventListener(GLOBAL_TOAST_EVENT, onGlobalToast as EventListener);
    };
  }, []);

  useEffect(() => {
    const onStoragePressure = (event: Event) => {
      const detail = (event as CustomEvent<BrowserStoragePressureEventDetail>).detail;
      if (!detail?.workflowCritical) {
        return;
      }

      setGlobalSnackbar({
        open: true,
        message: 'Browser storage is full or unavailable. Local AI Curation cache was not saved, but server-side documents and chat history are unchanged.',
        severity: 'warning',
        autoHideDurationMs: null,
        anchorOrigin: DEFAULT_GLOBAL_SNACKBAR_ANCHOR,
        showStorageRecoveryAction: true,
        chatReturnPath: undefined,
      });
    };

    window.addEventListener(BROWSER_STORAGE_PRESSURE_EVENT, onStoragePressure as EventListener);
    return () => {
      window.removeEventListener(BROWSER_STORAGE_PRESSURE_EVENT, onStoragePressure as EventListener);
    };
  }, []);

  const handleClearLocalCache = React.useCallback(() => {
    clearAiCurationLocalCache();
    setGlobalSnackbar({
      open: true,
      message: 'AI Curation local cache was cleared. Uploaded PDFs and server-side chat history were not deleted.',
      severity: 'success',
      showStorageRecoveryAction: false,
      chatReturnPath: undefined,
    });
  }, []);

  useEffect(() => {
    const latestSessionId = getLatestStreamSessionId(chatStreamEvents);
    if (chatRunActive && latestSessionId) {
      activeRunSessionIdRef.current = latestSessionId;
    }

    const wasChatRunActive = wasChatRunActiveRef.current;
    wasChatRunActiveRef.current = chatRunActive;

    if (!wasChatRunActive || chatRunActive || isChatRoutePath(location.pathname)) {
      return;
    }

    const stopped = hasStoppedRunEvent(chatStreamEvents);
    if (stopped) {
      activeRunSessionIdRef.current = null;
      return;
    }

    const errored = Boolean(chatRunError) || hasErrorRunEvent(chatStreamEvents);
    const returnSessionId = latestSessionId ?? activeRunSessionIdRef.current;
    const chatReturnPath = returnSessionId
      ? `/?session=${encodeURIComponent(returnSessionId)}`
      : '/';

    setGlobalSnackbar({
      open: true,
      message: errored ? 'Curation chat needs attention' : 'Curation chat finished',
      severity: errored ? 'error' : 'success',
      showStorageRecoveryAction: false,
      chatReturnPath,
    });
    activeRunSessionIdRef.current = null;
  }, [chatRunActive, chatRunError, chatStreamEvents, location.pathname]);

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
      const currentPathname = location.pathname.replace(/\/+$/, '');
      const onPdfJobsPage = currentPathname === '/weaviate/add-literature';
      const onBatchPage = location.pathname.startsWith('/batch');

      if (!onPdfJobsPage) {
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
        <Toolbar
          sx={{
            gap: { xs: 1, md: 0 },
            overflowX: { xs: 'auto', md: 'visible' },
            overflowY: 'hidden',
            whiteSpace: 'nowrap',
            '&::-webkit-scrollbar': {
              display: 'none',
            },
            scrollbarWidth: 'none',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexGrow: { xs: 0, md: 1 }, flexShrink: 0 }}>
            <Typography
              variant="h1"
              component={Link}
              to="/"
              noWrap
              sx={{
                textDecoration: 'none',
                color: 'inherit',
                cursor: 'pointer',
                maxWidth: { xs: 220, sm: 300 },
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
                  opacity: 1,
                  // AppBar is the fixed deep-blue bar in both modes, so use white
                  // (not text.primary, which is dark in light mode) for legibility.
                  color: (theme) => theme.palette.common.white,
                  fontSize: '0.7rem',
                  fontFamily: 'monospace',
                  backgroundColor: (theme) => alpha(theme.palette.common.white, 0.1),
                  px: 0.75,
                  py: 0.25,
                  borderRadius: 0.5,
                  cursor: 'default',
                  '&:hover': {
                    backgroundColor: (theme) => alpha(theme.palette.common.white, 0.16),
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
              bgcolor: 'background.default',
              color: 'text.primary',
            }}>
              <Typography variant="h2" color="inherit">
                PDF Viewer - Coming Soon
              </Typography>
            </Box>
          } />
          <Route path="/weaviate/*" element={<WeaviateLayout />}>
            <Route index element={<Navigate to="/weaviate/documents" replace />} />
            <Route path="documents" element={renderLazyRoute(<DocumentsPage />)} />
            <Route path="add-literature" element={renderLazyRoute(<AddLiteraturePage />)} />
            <Route path="documents/import-mock" element={renderLazyRoute(<AddLiteraturePage />)} />
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
        autoHideDuration={
          globalSnackbar.autoHideDurationMs === null
            ? null
            : globalSnackbar.autoHideDurationMs ?? DEFAULT_GLOBAL_SNACKBAR_AUTO_HIDE_MS
        }
        onClose={() => setGlobalSnackbar((prev) => ({ ...prev, open: false }))}
        anchorOrigin={globalSnackbar.anchorOrigin ?? DEFAULT_GLOBAL_SNACKBAR_ANCHOR}
      >
        <Alert
          severity={globalSnackbar.severity}
          onClose={() => setGlobalSnackbar((prev) => ({ ...prev, open: false }))}
          action={globalSnackbar.showStorageRecoveryAction ? (
            <Button
              color="inherit"
              size="small"
              onClick={handleClearLocalCache}
            >
              Clear local cache
            </Button>
          ) : globalSnackbar.chatReturnPath ? (
            <Button
              color="inherit"
              component={Link}
              size="small"
              to={globalSnackbar.chatReturnPath}
              onClick={() => setGlobalSnackbar((prev) => ({ ...prev, open: false }))}
            >
              Open chat
            </Button>
          ) : undefined}
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
      <AppThemeGlobalStyles />
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
