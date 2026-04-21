import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { logger } from '../services/logger';
import { getEnvFlag } from '../utils/env';
import {
  clearAllNamespacedChatLocalStorage,
  clearChatLocalStorageForUser,
  clearLegacyChatLocalStorage,
  getChatLocalStorageKeys,
} from '../lib/chatCacheKeys';

/** User data resolved from backend auth session. */
export interface AuthUser {
  uid: string;           // User ID from auth subject claim
  email?: string;        // Email address (nullable per contract)
  name?: string;         // Display name (mapped from display_name, nullable per contract)
}

/**
 * Authentication state and actions
 */
interface AuthState {
  isAuthenticated: boolean;
  user: AuthUser | null;
  isLoading: boolean;
  login: () => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

/**
 * Check if running in development mode (auth bypass)
 */
const isDevMode = (): boolean => getEnvFlag(['VITE_DEV_MODE', 'REACT_APP_DEV_MODE', 'DEV_MODE'], false);

/**
 * AuthProvider: Manages authentication state via backend OAuth session and httpOnly cookies.
 *
 * Design:
 * - No client-side token storage (XSS protection)
 * - Auth state fetched from /api/users/me endpoint
 * - Login redirects to backend /api/auth/login
 * - Logout calls /api/auth/logout endpoint
 *
 * DEV MODE:
 * - When REACT_APP_DEV_MODE=true, bypasses authentication checks
 * - Returns mock user without calling backend
 */
export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  // DEV MODE BYPASS: Auto-authenticate with mock user
  const devMode = isDevMode();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(devMode);
  const [user, setUser] = useState<AuthUser | null>(
    devMode ? { uid: 'dev-user-123', email: 'dev@localhost', name: 'Dev User' } : null
  );
  const [isLoading, setIsLoading] = useState<boolean>(!devMode); // Skip loading in dev mode
  const setJustLoggedOutFlag = (): void => {
    sessionStorage.setItem('justLoggedOut', 'true');
  };

  /**
   * Check authentication status by fetching user info from backend
   * Backend validates httpOnly cookie and returns user data if valid
   *
   * In DEV MODE: Skips API call and maintains mock user state
   */
  const checkAuthStatus = async (): Promise<void> => {
    // DEV MODE BYPASS: Don't call backend, just keep mock user
    if (devMode) {
      logger.debug('DEV MODE: Skipping auth check, using mock user', {
        component: 'AuthContext',
        action: 'checkAuthStatus',
      });
      setIsLoading(false);
      return;
    }

    try {
      const response = await fetch('/api/users/me', {
        method: 'GET',
        credentials: 'include', // Include httpOnly cookies
      });

      if (response.ok) {
        const userData = await response.json();
        // Use auth_sub (provider subject claim) as the unique user identifier
        const newUserId = userData.auth_sub;
        const chatStorageKeys = getChatLocalStorageKeys(newUserId);

        setUser({
          uid: newUserId,
          email: userData.email || undefined,
          name: userData.display_name || undefined,
        });
        setIsAuthenticated(true);

        logger.debug('User authenticated', {
          component: 'AuthContext',
          action: 'checkAuthStatus',
          metadata: { userId: newUserId },
        });

        // Sync localStorage and backend memory state to ensure consistency
        // Handles two scenarios:
        // 1. Backend restarted (no sessions) but localStorage has stale data -> clear localStorage
        // 2. localStorage cleared but backend has sessions (memory) -> reset backend for clean slate
        const storedSessionId = localStorage.getItem(chatStorageKeys.sessionId);
        const storedMessages = localStorage.getItem(chatStorageKeys.messages);
        try {
          const historyResponse = await fetch('/api/chat/history', {
            method: 'GET',
            credentials: 'include',
          });
          if (historyResponse.ok) {
            const historyData = await historyResponse.json();

            if (storedSessionId && storedMessages) {
              // Case 1: localStorage has data - check if backend still has matching sessions
              if (historyData.total_sessions === 0) {
                logger.debug('Backend has no sessions, clearing stale localStorage chat data', {
                  component: 'AuthContext',
                  action: 'checkAuthStatus',
                  metadata: { storedSessionId },
                });
                clearChatLocalStorageForUser(newUserId);
              }
            } else if (historyData.total_sessions > 0) {
              // Case 2: localStorage is empty but backend has sessions
              // Reset backend memory to ensure clean slate (user expects fresh start)
              logger.debug('localStorage empty but backend has sessions, resetting backend memory', {
                component: 'AuthContext',
                action: 'checkAuthStatus',
                metadata: { backendSessions: historyData.total_sessions },
              });
              await fetch('/api/chat/conversation/reset', {
                method: 'POST',
                credentials: 'include',
              });
            }
          }
        } catch (historyError) {
          // Non-critical - log but don't fail auth
          logger.debug('Failed to validate chat history, continuing', {
            component: 'AuthContext',
            action: 'checkAuthStatus',
          });
        }
      } else if (response.status === 401) {
        // Not authenticated - clear state
        setUser(null);
        setIsAuthenticated(false);

        logger.debug('User not authenticated', {
          component: 'AuthContext',
          action: 'checkAuthStatus',
        });
      } else {
        throw new Error(`Unexpected status: ${response.status}`);
      }
    } catch (error) {
      logger.error('Failed to check auth status', error as Error, {
        component: 'AuthContext',
        action: 'checkAuthStatus',
      });

      setUser(null);
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * T046: Token refresh logic
   * Periodically check authentication status to trigger token refresh
   *
   * Design:
   * - Check auth status every 5 minutes (300,000ms) during active use
   * - Backend handles token refresh automatically via refresh tokens
   * - If 401 response received, user will be redirected to login
   * - Only runs when user is authenticated
   *
   * Note: This is a simple polling approach. More sophisticated approaches
   * (like monitoring token expiration from JWT claims) could be implemented
   * with a client-side JWT library, but polling is simpler and sufficient.
   */
  useEffect(() => {
    if (!isAuthenticated) {
      return; // Don't poll if not authenticated
    }

    // Set up periodic auth check (every 5 minutes)
    const refreshInterval = setInterval(() => {
      logger.debug('Periodic auth check triggered', {
        component: 'AuthContext',
        action: 'tokenRefresh',
      });

      checkAuthStatus();
    }, 5 * 60 * 1000); // 5 minutes

    // Cleanup interval on unmount or when auth state changes
    return () => {
      clearInterval(refreshInterval);
    };
  }, [isAuthenticated]);

  /**
   * Initialize auth state on mount
   */
  useEffect(() => {
    // Clear pre-namespaced chat keys once during frontend bootstrap before auth-specific startup runs.
    clearLegacyChatLocalStorage();
    checkAuthStatus();
  }, []);

  /**
   * Login: Redirect to backend auth endpoint.
   */
  const login = (): void => {
    logger.info('Redirecting to login', {
      component: 'AuthContext',
      action: 'login',
    });

    // Redirect to backend login endpoint; backend handles provider-specific flow.
    window.location.href = '/api/auth/login';
  };

  /**
   * Logout: Call backend logout endpoint to clear httpOnly cookie.
   */
  const logout = async (): Promise<void> => {
    try {
      logger.info('Logging out', {
        component: 'AuthContext',
        action: 'logout',
        metadata: { userId: user?.uid },
      });

      // Note: Backend conversation memory is now user-isolated (FR-014)
      // No need to reset - each user's memory is stored separately

      const response = await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include', // Include cookie to be cleared
      });

      if (!response.ok) {
        throw new Error(`Logout failed: ${response.status}`);
      }

      const data = await response.json();

      // Set this before auth state flips so ProtectedRoutes can suppress auto-login.
      setJustLoggedOutFlag();

      // Clear client-side state
      setUser(null);
      setIsAuthenticated(false);

      // Clear auth-bound browser state so no chat or viewer data survives logout.
      logger.debug('Clearing auth-bound chat browser state on logout', { component: 'AuthContext', action: 'logout' });
      clearAllNamespacedChatLocalStorage();

      logger.info('Logout successful', {
        component: 'AuthContext',
        action: 'logout',
      });

      // Redirect to provider logout endpoint if provided, otherwise go to home.
      if (data.logout_url) {
        window.location.href = data.logout_url;
      } else {
        window.location.href = '/';
      }
    } catch (error) {
      logger.error('Logout failed', error as Error, {
        component: 'AuthContext',
        action: 'logout',
      });

      setJustLoggedOutFlag();

      // Even if logout fails, clear local state and redirect to home
      setUser(null);
      setIsAuthenticated(false);
      clearAllNamespacedChatLocalStorage();
      window.location.href = '/';
    }
  };

  const value: AuthState = {
    isAuthenticated,
    user,
    isLoading,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

/**
 * useAuth hook: Access authentication state and actions
 * Must be used within AuthProvider
 */
export const useAuth = (): AuthState => {
  const context = useContext(AuthContext);

  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }

  return context;
};

/**
 * useAuthErrorHandler: Custom hook to handle authentication errors in React Query
 * This hook provides an onError callback that automatically redirects to login on 401 errors
 *
 * Usage in components:
 * ```
 * const handleAuthError = useAuthErrorHandler();
 * useQuery({
 *   queryKey: ['data'],
 *   queryFn: fetchData,
 *   onError: handleAuthError,
 * });
 * ```
 */
export const useAuthErrorHandler = () => {
  const { login } = useAuth();

  return (error: unknown) => {
    // Check if this is an AuthenticationError
    if (error instanceof Error && error.name === 'AuthenticationError') {
      logger.info('Authentication error detected, redirecting to login', {
        component: 'useAuthErrorHandler',
        action: 'handleError',
      });

      // Redirect to login
      login();
    }
  };
};
