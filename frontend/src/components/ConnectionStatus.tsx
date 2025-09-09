import React, { useState, useEffect } from "react";
import {
  Alert,
  Snackbar,
  LinearProgress,
  Box,
  Typography,
} from "@mui/material";
import { WifiOff, Wifi, Sync } from "@mui/icons-material";

interface ConnectionStatusProps {
  isConnected: boolean;
  isRetrying: boolean;
  retryCount: number;
  maxRetries: number;
  nextRetryIn?: number;
  onRetry?: () => void;
}

const ConnectionStatus: React.FC<ConnectionStatusProps> = ({
  isConnected,
  isRetrying,
  retryCount,
  maxRetries,
  nextRetryIn,
  onRetry,
}) => {
  const [showStatus, setShowStatus] = useState(false);
  const [countdown, setCountdown] = useState(nextRetryIn || 0);

  useEffect(() => {
    // Show status when disconnected or retrying
    setShowStatus(!isConnected || isRetrying);
  }, [isConnected, isRetrying]);

  useEffect(() => {
    // Update countdown
    if (nextRetryIn && nextRetryIn > 0) {
      setCountdown(Math.ceil(nextRetryIn / 1000));

      const interval = setInterval(() => {
        setCountdown((prev) => {
          if (prev <= 1) {
            clearInterval(interval);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);

      return () => clearInterval(interval);
    }
  }, [nextRetryIn]);

  const getSeverity = () => {
    if (isConnected) return "success";
    if (retryCount >= maxRetries) return "error";
    return "warning";
  };

  const getMessage = () => {
    if (isConnected && !isRetrying) {
      return "Connected to AI service";
    }

    if (retryCount >= maxRetries) {
      return "Unable to connect to AI service. Please check your settings and try again.";
    }

    if (isRetrying) {
      return `Reconnecting... (Attempt ${retryCount}/${maxRetries})`;
    }

    if (countdown > 0) {
      return `Connection lost. Retrying in ${countdown} seconds...`;
    }

    return "Disconnected from AI service";
  };

  const getIcon = () => {
    if (isConnected && !isRetrying) {
      return <Wifi />;
    }
    if (isRetrying) {
      return <Sync className="animate-spin" />;
    }
    return <WifiOff />;
  };

  return (
    <>
      <Snackbar
        open={showStatus}
        anchorOrigin={{ vertical: "top", horizontal: "center" }}
        onClose={() => setShowStatus(false)}
        autoHideDuration={isConnected ? 3000 : null}
      >
        <Alert
          severity={getSeverity()}
          icon={getIcon()}
          action={
            retryCount >= maxRetries && onRetry ? (
              <Typography
                component="button"
                onClick={onRetry}
                sx={{
                  cursor: "pointer",
                  textDecoration: "underline",
                  background: "none",
                  border: "none",
                  color: "inherit",
                  fontSize: "inherit",
                }}
              >
                Retry Now
              </Typography>
            ) : undefined
          }
        >
          <Box>
            <Typography variant="body2">{getMessage()}</Typography>
            {isRetrying && (
              <LinearProgress
                sx={{ mt: 1, width: 200 }}
                variant="indeterminate"
              />
            )}
          </Box>
        </Alert>
      </Snackbar>
    </>
  );
};

export default ConnectionStatus;

/**
 * Hook for managing connection status
 */
export function useConnectionStatus() {
  const [isConnected, setIsConnected] = useState(true);
  const [isRetrying, setIsRetrying] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [nextRetryIn, setNextRetryIn] = useState(0);

  const maxRetries = 3;

  const checkConnection = async (): Promise<boolean> => {
    try {
      const response = await fetch("/api/health", {
        method: "GET",
        signal: AbortSignal.timeout(5000),
      });
      return response.ok;
    } catch {
      return false;
    }
  };

  const handleConnectionError = async () => {
    setIsConnected(false);

    if (retryCount >= maxRetries) {
      setIsRetrying(false);
      return;
    }

    setIsRetrying(true);
    setRetryCount((prev) => prev + 1);

    // Calculate delay with exponential backoff
    const delay = Math.min(1000 * Math.pow(2, retryCount), 10000);
    setNextRetryIn(delay);

    await new Promise((resolve) => setTimeout(resolve, delay));

    const connected = await checkConnection();

    if (connected) {
      setIsConnected(true);
      setIsRetrying(false);
      setRetryCount(0);
      setNextRetryIn(0);
    } else {
      await handleConnectionError();
    }
  };

  const manualRetry = async () => {
    setRetryCount(0);
    await handleConnectionError();
  };

  // Monitor connection periodically
  useEffect(() => {
    const interval = setInterval(async () => {
      if (!isRetrying) {
        const connected = await checkConnection();
        if (connected !== isConnected) {
          setIsConnected(connected);
          if (!connected) {
            await handleConnectionError();
          }
        }
      }
    }, 30000); // Check every 30 seconds

    return () => clearInterval(interval);
  }, [isConnected, isRetrying]);

  return {
    isConnected,
    isRetrying,
    retryCount,
    maxRetries,
    nextRetryIn,
    handleConnectionError,
    manualRetry,
  };
}
