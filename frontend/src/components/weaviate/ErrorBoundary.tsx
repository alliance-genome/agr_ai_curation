import { Component, ErrorInfo, ReactNode } from 'react';
import {
  Box,
  Typography,
  Button,
  Paper,
  Alert,
  AlertTitle,
  Stack,
  Collapse,
  IconButton,
} from '@mui/material';
import {
  Refresh,
  ExpandMore,
  ExpandLess,
  BugReport,
  Home,
  ContentCopy,
} from '@mui/icons-material';
import { logger } from '../../services/logger';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  showDetails: boolean;
  errorCount: number;
}

class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      showDetails: false,
      errorCount: 0,
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return {
      hasError: true,
      error,
    };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Log error with structured logging
    logger.logComponentError('ErrorBoundary', error, errorInfo);

    this.setState((prevState) => ({
      errorInfo,
      errorCount: prevState.errorCount + 1,
    }));

    // Call the optional error handler
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }

    // Report to error tracking service (e.g., Sentry)
    this.reportError(error, errorInfo);
  }

  reportError = (error: Error, errorInfo: ErrorInfo): void => {
    // Log error with correlation ID
    logger.error('Error boundary triggered', error, {
      component: 'ErrorBoundary',
      action: 'reportError',
      metadata: {
        errorCount: this.state.errorCount,
        componentStack: errorInfo.componentStack,
      },
    });

    // The logger service handles sending to backend
  };

  handleReset = (): void => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      showDetails: false,
    });
  };

  handleReload = (): void => {
    window.location.reload();
  };

  handleGoHome = (): void => {
    window.location.href = '/';
  };

  toggleDetails = (): void => {
    this.setState((prevState) => ({
      showDetails: !prevState.showDetails,
    }));
  };

  copyErrorToClipboard = (): void => {
    const { error, errorInfo } = this.state;
    if (error && errorInfo) {
      const errorText = `
Error: ${error.message}
Stack: ${error.stack}
Component Stack: ${errorInfo.componentStack}
      `.trim();

      navigator.clipboard.writeText(errorText);
    }
  };

  render(): ReactNode {
    const { hasError, error, errorInfo, showDetails, errorCount } = this.state;
    const { children, fallback } = this.props;

    if (hasError && error) {
      // Custom fallback UI if provided
      if (fallback) {
        return <>{fallback}</>;
      }

      // Default error UI
      return (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '100vh',
            p: 3,
            bgcolor: 'background.default',
          }}
        >
          <Paper
            elevation={3}
            sx={{
              maxWidth: 600,
              width: '100%',
              p: 4,
              borderRadius: 2,
            }}
          >
            <Stack spacing={3}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <BugReport color="error" sx={{ fontSize: 40 }} />
                <Box>
                  <Typography variant="h4" gutterBottom>
                    Oops! Something went wrong
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    We apologize for the inconvenience. The application encountered an unexpected error.
                  </Typography>
                </Box>
              </Box>

              <Alert severity="error">
                <AlertTitle>Error Details</AlertTitle>
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  {error.message}
                </Typography>
              </Alert>

              {errorCount > 2 && (
                <Alert severity="warning">
                  <AlertTitle>Multiple Errors Detected</AlertTitle>
                  <Typography variant="body2">
                    This error has occurred {errorCount} times. If the problem persists,
                    please try reloading the page or contact support.
                  </Typography>
                </Alert>
              )}

              <Stack direction="row" spacing={2} justifyContent="center">
                <Button
                  variant="contained"
                  startIcon={<Refresh />}
                  onClick={this.handleReset}
                  color="primary"
                >
                  Try Again
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<Home />}
                  onClick={this.handleGoHome}
                >
                  Go Home
                </Button>
                <Button
                  variant="outlined"
                  onClick={this.handleReload}
                  color="secondary"
                >
                  Reload Page
                </Button>
              </Stack>

              <Box>
                <Button
                  fullWidth
                  onClick={this.toggleDetails}
                  endIcon={showDetails ? <ExpandLess /> : <ExpandMore />}
                  sx={{ justifyContent: 'space-between' }}
                >
                  {showDetails ? 'Hide' : 'Show'} Technical Details
                </Button>

                <Collapse in={showDetails}>
                  <Box sx={{ mt: 2 }}>
                    <Paper
                      variant="outlined"
                      sx={{
                        p: 2,
                        bgcolor: 'grey.50',
                        position: 'relative',
                      }}
                    >
                      <IconButton
                        size="small"
                        onClick={this.copyErrorToClipboard}
                        sx={{
                          position: 'absolute',
                          top: 8,
                          right: 8,
                        }}
                        title="Copy error to clipboard"
                      >
                        <ContentCopy fontSize="small" />
                      </IconButton>

                      <Typography variant="subtitle2" gutterBottom>
                        Stack Trace:
                      </Typography>
                      <Typography
                        variant="body2"
                        component="pre"
                        sx={{
                          fontFamily: 'monospace',
                          fontSize: '0.75rem',
                          overflow: 'auto',
                          maxHeight: 200,
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {error.stack}
                      </Typography>

                      {errorInfo && (
                        <>
                          <Typography variant="subtitle2" gutterBottom sx={{ mt: 2 }}>
                            Component Stack:
                          </Typography>
                          <Typography
                            variant="body2"
                            component="pre"
                            sx={{
                              fontFamily: 'monospace',
                              fontSize: '0.75rem',
                              overflow: 'auto',
                              maxHeight: 200,
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                            }}
                          >
                            {errorInfo.componentStack}
                          </Typography>
                        </>
                      )}
                    </Paper>
                  </Box>
                </Collapse>
              </Box>

              <Typography variant="caption" color="text.secondary" align="center">
                Error ID: {Date.now().toString(36).toUpperCase()}
              </Typography>
            </Stack>
          </Paper>
        </Box>
      );
    }

    return children;
  }
}

export default ErrorBoundary;