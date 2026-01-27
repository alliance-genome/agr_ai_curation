import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { logger } from '../services/logger';
import { getEnvFlag } from '../utils/env';

/**
 * User data from Cognito token (interface name kept as OktaUser for backward compatibility)
 * Maps to User schema from backend - okta_id column now stores Cognito sub
 */
export interface OktaUser {
  uid: string;           // User ID from Cognito (mapped from okta_id column which stores Cognito sub)
  email?: string;        // Email address (nullable per contract)
  name?: string;         // Display name (mapped from display_name, nullable per contract)
}

/**
 * Authentication state and actions
 */
interface AuthState {
  isAuthenticated: boolean;
  user: OktaUser | null;
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
 * AuthProvider: Manages authentication state using AWS Cognito OAuth2 flow with httpOnly cookies
 *
 * Design:
 * - Tokens stored in httpOnly cookies (set by backend on /auth/callback)
 * - No client-side token storage (XSS protection)
 * - Auth state fetched from /users/me endpoint
 * - Login redirects to backend /auth/login (which redirects to Cognito Hosted UI)
 * - Logout calls /auth/logout endpoint and redirects to login
 *
 * DEV MODE:
 * - When REACT_APP_DEV_MODE=true, bypasses authentication checks
 * - Returns mock user without calling backend
 * - Allows local development without Cognito configuration
 *
 * NOTE: Backend handles complete OAuth2 flow - no client-side SDK needed
 * - Backend manages Authorization Code flow with PKCE
 * - Security maintained: httpOnly cookies + PKCE + CSRF protection via state parameter
 * - Token refresh handled automatically by backend
 */
export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  // DEV MODE BYPASS: Auto-authenticate with mock user
  const devMode = isDevMode();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(devMode);
  const [user, setUser] = useState<OktaUser | null>(
    devMode ? { uid: 'dev-user-123', email: 'dev@localhost', name: 'Dev User' } : null
  );
  const [isLoading, setIsLoading] = useState<boolean>(!devMode); // Skip loading in dev mode

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
        // Use auth_sub (Cognito 'sub' claim) as the unique user identifier
        // Previously used okta_id before Cognito migration
        const newUserId = userData.auth_sub;

        // Check if this is a different user than the one whose data might be in localStorage
        // Clear chat data to prevent data leakage between users and ensure fresh start
        const storedUserId = localStorage.getItem('chat-user-id');
        if (storedUserId !== newUserId) {
          logger.debug('New user detected, clearing chat localStorage', {
            component: 'AuthContext',
            action: 'checkAuthStatus',
            metadata: { previousUser: storedUserId ? 'exists' : 'none', newUser: newUserId },
          });
          localStorage.removeItem('chat-messages');
          localStorage.removeItem('chat-session-id');
          localStorage.removeItem('chat-active-document');
          localStorage.setItem('chat-user-id', newUserId);
        }

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
        const storedSessionId = localStorage.getItem('chat-session-id');
        const storedMessages = localStorage.getItem('chat-messages');
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
                localStorage.removeItem('chat-messages');
                localStorage.removeItem('chat-session-id');
                localStorage.removeItem('chat-active-document');
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
   * (like monitoring token expiration from JWT claims) would require the
   * OktaAuth SDK, which we're deferring per plan.md Implementation Notes.
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
    checkAuthStatus();
  }, []);

  /**
   * Login: Redirect to backend auth endpoint
   * Backend will redirect to Cognito Hosted UI, then redirect back to /auth/callback
   * /auth/callback sets httpOnly cookie and redirects to app
   */
  const login = (): void => {
    logger.info('Redirecting to login', {
      component: 'AuthContext',
      action: 'login',
    });

    // Redirect to backend login endpoint
    // Backend will handle Cognito OAuth2 redirect flow
    window.location.href = '/api/auth/login';
  };

  /**
   * Logout: Call backend logout endpoint to clear httpOnly cookie
   * Then redirect to Cognito logout endpoint to clear provider session
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

      // Clear client-side state
      setUser(null);
      setIsAuthenticated(false);

      // Clear all chat localStorage - important for security and user experience
      // When a user logs out, their chat history should not persist for the next user
      logger.debug('Clearing chat localStorage on logout', { component: 'AuthContext', action: 'logout' });
      localStorage.removeItem('chat-messages');
      localStorage.removeItem('chat-session-id');
      localStorage.removeItem('chat-active-document');
      localStorage.removeItem('chat-user-id');

      // Set flag to prevent auto-login after Cognito redirects back
      sessionStorage.setItem('justLoggedOut', 'true');

      logger.info('Logout successful', {
        component: 'AuthContext',
        action: 'logout',
      });

      // Redirect to Cognito logout endpoint if provided, otherwise go to home
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

      // Even if logout fails, clear local state and redirect to home
      setUser(null);
      setIsAuthenticated(false);
      // Clear all chat localStorage even on logout failure
      localStorage.removeItem('chat-messages');
      localStorage.removeItem('chat-session-id');
      localStorage.removeItem('chat-active-document');
      localStorage.removeItem('chat-user-id');
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
