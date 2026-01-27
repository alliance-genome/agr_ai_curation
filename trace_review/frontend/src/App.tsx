import { useState, useEffect } from 'react';
import {
  ThemeProvider,
  CssBaseline,
  Box,
  AppBar,
  Toolbar,
  Typography,
  TextField,
  Button,
  Alert,
  Drawer,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  CircularProgress,
  Chip,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import CloudIcon from '@mui/icons-material/Cloud';
import ComputerIcon from '@mui/icons-material/Computer';
import { theme } from './theme/theme';
import { api } from './services/api';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { SummaryView } from './views/SummaryView';
import { ConversationView } from './views/ConversationView';
import { ToolCallsView } from './views/ToolCallsView';
import { PDFCitationsView } from './views/PDFCitationsView';
import { TokenAnalysisView } from './views/TokenAnalysisView';
import { AgentContextView } from './views/AgentContextView';
import { TraceSummaryView } from './views/TraceSummaryView';
import { DocumentHierarchyView } from './views/DocumentHierarchyView';
import { AgentConfigsView } from './views/AgentConfigsView';

const DRAWER_WIDTH = 200;

type ViewName = 'summary' | 'conversation' | 'tool_calls' | 'pdf_citations' | 'token_analysis' | 'agent_context' | 'trace_summary' | 'document_hierarchy' | 'agent_configs';

const VIEWS: { name: ViewName; label: string }[] = [
  { name: 'summary', label: 'Summary' },
  { name: 'conversation', label: 'Conversation' },
  { name: 'tool_calls', label: 'Tool Calls' },
  { name: 'pdf_citations', label: 'PDF Citations' },
  { name: 'token_analysis', label: 'Tokens' },
  { name: 'agent_context', label: 'Agents' },
  { name: 'trace_summary', label: 'Full Summary' },
  { name: 'document_hierarchy', label: 'Doc Hierarchy' },
  { name: 'agent_configs', label: 'Agent Prompts' },
];

/**
 * ProtectedContent: Main app content that requires authentication
 * This component is only rendered when user is authenticated
 */
function ProtectedContent() {
  const { isAuthenticated, isLoading, login } = useAuth();
  const [traceId, setTraceId] = useState('');
  const [source, setSource] = useState<'remote' | 'local'>('remote');
  const [currentTraceId, setCurrentTraceId] = useState<string | null>(null);
  const [currentView, setCurrentView] = useState<ViewName>('summary');
  const [viewData, setViewData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cacheStatus, setCacheStatus] = useState<'hit' | 'miss' | null>(null);
  const [clearingCache, setClearingCache] = useState(false);

  // Redirect to login if not authenticated
  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      login();
    }
  }, [isLoading, isAuthenticated, login]);

  // Auto-load trace from URL query parameter (?trace_id=XXX&source=local)
  useEffect(() => {
    if (!isAuthenticated) return;

    const urlParams = new URLSearchParams(window.location.search);
    const traceIdParam = urlParams.get('trace_id');
    const sourceParam = urlParams.get('source');

    if (sourceParam === 'local' || sourceParam === 'remote') {
      setSource(sourceParam as 'remote' | 'local');
    }

    if (traceIdParam && !currentTraceId) {
      // Set trace ID and trigger analysis
      setTraceId(traceIdParam);

      // Trigger analysis after a brief delay to ensure state is set
      setTimeout(() => {
        const analyzeWithParam = async () => {
          setLoading(true);
          setError(null);
          setViewData(null);

          try {
            const analyzeResponse = await api.analyzeTrace(
              traceIdParam.trim(), 
              (sourceParam as 'remote' | 'local') || 'remote'
            );
            setCurrentTraceId(analyzeResponse.trace_id);
            setCacheStatus(analyzeResponse.cache_status);

            const viewResponse = await api.getTraceView(analyzeResponse.trace_id, 'summary');
            setViewData(viewResponse.data);
            setCurrentView('summary');
          } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to analyze trace');
            setCurrentTraceId(null);
            setCacheStatus(null);
          } finally {
            setLoading(false);
          }
        };

        analyzeWithParam();
      }, 100);
    }
  }, [isAuthenticated, currentTraceId]);

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

  // If not authenticated, show nothing (will redirect)
  if (!isAuthenticated) {
    return null;
  }

  const handleAnalyze = async () => {
    if (!traceId.trim()) {
      setError('Please enter a trace ID');
      return;
    }

    setLoading(true);
    setError(null);
    setViewData(null);

    try {
      // Analyze trace
      const analyzeResponse = await api.analyzeTrace(traceId.trim(), source);
      setCurrentTraceId(analyzeResponse.trace_id);
      setCacheStatus(analyzeResponse.cache_status);

      // Load initial view (summary)
      const viewResponse = await api.getTraceView(analyzeResponse.trace_id, 'summary');
      setViewData(viewResponse.data);
      setCurrentView('summary');
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to analyze trace');
      setCurrentTraceId(null);
      setCacheStatus(null);
    } finally {
      setLoading(false);
    }
  };

  const handleViewChange = async (viewName: ViewName) => {
    if (!currentTraceId) return;

    setLoading(true);
    setError(null);

    try {
      const response = await api.getTraceView(currentTraceId, viewName);
      setViewData(response.data);
      setCurrentView(viewName);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load view');
    } finally {
      setLoading(false);
    }
  };

  const handleClearCache = async () => {
    setClearingCache(true);
    setError(null);

    try {
      const response = await api.clearCache();
      // Show success message briefly
      setError(`✓ ${response.message}`);

      // Clear current trace data to force re-analysis
      setCurrentTraceId(null);
      setViewData(null);
      setCacheStatus(null);

      // Clear success message after 3 seconds
      setTimeout(() => setError(null), 3000);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to clear cache');
    } finally {
      setClearingCache(false);
    }
  };

  const handleSourceChange = (
    _event: React.MouseEvent<HTMLElement>,
    newSource: 'remote' | 'local' | null,
  ) => {
    if (newSource !== null) {
      setSource(newSource);
    }
  };

  const renderView = () => {
    if (!viewData) return null;

    switch (currentView) {
      case 'summary':
        return <SummaryView data={viewData} />;
      case 'conversation':
        return <ConversationView data={viewData} />;
      case 'tool_calls':
        return <ToolCallsView data={viewData} />;
      case 'pdf_citations':
        return <PDFCitationsView data={viewData} />;
      case 'token_analysis':
        return <TokenAnalysisView data={viewData} />;
      case 'agent_context':
        return <AgentContextView data={viewData} />;
      case 'trace_summary':
        return <TraceSummaryView data={viewData} />;
      case 'document_hierarchy':
        return <DocumentHierarchyView data={viewData} />;
      case 'agent_configs':
        return <AgentConfigsView data={viewData} />;
      default:
        return <Typography>View not found</Typography>;
    }
  };

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', minHeight: '100vh' }}>
        {/* App Bar */}
        <AppBar position="fixed" sx={{ zIndex: (theme) => theme.zIndex.drawer + 1 }}>
          <Toolbar>
            <Typography variant="h6" noWrap component="div" sx={{ flexGrow: 1 }}>
              🔍 Trace Review
            </Typography>
            {import.meta.env.VITE_DEV_MODE === 'true' && (
              <Chip label="Dev Mode" color="secondary" size="small" />
            )}
          </Toolbar>
        </AppBar>

        {/* Left Navigation Drawer */}
        <Drawer
          variant="permanent"
          sx={{
            width: DRAWER_WIDTH,
            flexShrink: 0,
            [`& .MuiDrawer-paper`]: {
              width: DRAWER_WIDTH,
              boxSizing: 'border-box',
              marginTop: '64px',
            },
          }}
        >
          <List>
            {VIEWS.map((view) => (
              <ListItem key={view.name} disablePadding>
                <ListItemButton
                  selected={currentView === view.name}
                  onClick={() => handleViewChange(view.name)}
                  disabled={!currentTraceId}
                >
                  <ListItemText
                    primary={view.label}
                    sx={{
                      '& .MuiTypography-root': {
                        color: currentView === view.name ? '#ffffff' : 'rgba(255, 255, 255, 0.9)',
                        fontWeight: currentView === view.name ? 600 : 400
                      }
                    }}
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </Drawer>

        {/* Main Content */}
        <Box component="main" sx={{ flexGrow: 1, p: 3, marginTop: '64px' }}>
          {/* Trace Input Section */}
          <Box sx={{ mb: 3 }}>
            <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
              <ToggleButtonGroup
                value={source}
                exclusive
                onChange={handleSourceChange}
                aria-label="trace source"
                color="primary"
                sx={{ height: 56 }}
              >
                <ToggleButton value="remote" aria-label="remote source">
                  <CloudIcon sx={{ mr: 1 }} />
                  Remote
                </ToggleButton>
                <ToggleButton value="local" aria-label="local source">
                  <ComputerIcon sx={{ mr: 1 }} />
                  Local
                </ToggleButton>
              </ToggleButtonGroup>
              
              <TextField
                fullWidth
                label="Trace ID"
                placeholder="Paste Langfuse trace ID here..."
                value={traceId}
                onChange={(e) => setTraceId(e.target.value)}
                onKeyPress={(e) => {
                  if (e.key === 'Enter') {
                    handleAnalyze();
                  }
                }}
                disabled={loading}
              />
              <Button
                variant="contained"
                onClick={handleAnalyze}
                disabled={loading}
                sx={{ minWidth: '120px' }}
              >
                {loading ? <CircularProgress size={24} /> : 'Analyze'}
              </Button>
              <Button
                variant="outlined"
                onClick={handleClearCache}
                disabled={clearingCache || loading}
                sx={{ minWidth: '140px' }}
              >
                {clearingCache ? <CircularProgress size={24} /> : '🗑️ Clear Cache'}
              </Button>
            </Box>

            {/* Status indicators */}
            {cacheStatus && (
              <Chip
                label={cacheStatus === 'hit' ? '✓ Loaded from cache' : `⟳ Fetched from Langfuse (${source})`}
                color={cacheStatus === 'hit' ? 'success' : 'info'}
                size="small"
              />
            )}

            {error && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {error}
              </Alert>
            )}
          </Box>

          {/* View Content */}
          <Box>
            {loading && !viewData && (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                <CircularProgress />
              </Box>
            )}

            {!loading && !viewData && !error && (
              <Alert severity="info">
                Enter a trace ID above to begin analysis
              </Alert>
            )}

            {renderView()}
          </Box>
        </Box>
      </Box>
    </ThemeProvider>
  );
}

/**
 * App: Root component with AuthProvider wrapper
 * Provides authentication context to all child components
 */
function App() {
  return (
    <AuthProvider>
      <ProtectedContent />
    </AuthProvider>
  );
}

export default App;
