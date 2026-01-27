import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';

/**
 * User data from Cognito token
 */
export interface CognitoUser {
  sub: string;           // User ID from Cognito
  email?: string;        // Email address
  name?: string;         // Display name
}

/**
 * Authentication state and actions
 */
interface AuthState {
  isAuthenticated: boolean;
  user: CognitoUser | null;
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
const isDevMode = (): boolean => {
  return import.meta.env.VITE_DEV_MODE === 'true';
};

/**
 * Get the API base path based on environment
 * Local dev: /api (proxied by vite)
 * Production: /trace-review/api (full path)
 */
const getApiBasePath = (): string => {
  return import.meta.env.DEV ? '/api' : '/trace-review/api';
};

/**
 * AuthProvider: Manages authentication state using AWS Cognito OAuth2 flow with httpOnly cookies
 *
 * Design:
 * - Tokens stored in httpOnly cookies (set by backend on /auth/callback)
 * - No client-side token storage (XSS protection)
 * - Auth state fetched from /auth/me endpoint
 * - Login redirects to backend /auth/login (which redirects to Cognito Hosted UI)
 * - Logout calls /auth/logout endpoint and redirects to login
 *
 * DEV MODE:
 * - When VITE_DEV_MODE=true, bypasses authentication checks
 * - Returns mock user without calling backend
 * - Allows local development without Cognito configuration
 */
export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  // DEV MODE BYPASS: Auto-authenticate with mock user
  const devMode = isDevMode();
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(devMode);
  const [user, setUser] = useState<CognitoUser | null>(
    devMode ? { sub: 'dev-user-123', email: 'dev@localhost', name: 'Dev User' } : null
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
      console.log('[AuthContext] DEV MODE: Skipping auth check, using mock user');
      setIsLoading(false);
      return;
    }

    try {
      const apiBasePath = getApiBasePath();
      const response = await fetch(`${apiBasePath}/auth/me`, {
        method: 'GET',
        credentials: 'include', // Include httpOnly cookies
      });

      if (response.ok) {
        const userData = await response.json();
        setUser({
          sub: userData.sub,
          email: userData.email || undefined,
          name: userData.name || undefined,
        });
        setIsAuthenticated(true);

        console.log('[AuthContext] User authenticated:', userData.sub);
      } else if (response.status === 401) {
        // Not authenticated - clear state
        setUser(null);
        setIsAuthenticated(false);

        console.log('[AuthContext] User not authenticated');
      } else {
        throw new Error(`Unexpected status: ${response.status}`);
      }
    } catch (error) {
      console.error('[AuthContext] Failed to check auth status:', error);

      setUser(null);
      setIsAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  };

  /**
   * Token refresh logic: Periodically check authentication status
   * Backend handles token refresh automatically via refresh tokens
   */
  useEffect(() => {
    if (!isAuthenticated || devMode) {
      return; // Don't poll if not authenticated or in dev mode
    }

    // Set up periodic auth check (every 5 minutes)
    const refreshInterval = setInterval(() => {
      console.log('[AuthContext] Periodic auth check triggered');
      checkAuthStatus();
    }, 5 * 60 * 1000); // 5 minutes

    return () => {
      clearInterval(refreshInterval);
    };
  }, [isAuthenticated, devMode]);

  /**
   * Initialize auth state on mount
   */
  useEffect(() => {
    checkAuthStatus();
  }, []);

  /**
   * Login: Redirect to backend auth endpoint
   * Backend will redirect to Cognito Hosted UI, then redirect back to /auth/callback
   */
  const login = (): void => {
    console.log('[AuthContext] Redirecting to login');
    const apiBasePath = getApiBasePath();
    window.location.href = `${apiBasePath}/auth/login`;
  };

  /**
   * Logout: Call backend logout endpoint to clear httpOnly cookie
   * Then redirect to login page
   */
  const logout = async (): Promise<void> => {
    try {
      console.log('[AuthContext] Logging out');

      const apiBasePath = getApiBasePath();
      const response = await fetch(`${apiBasePath}/auth/logout`, {
        method: 'GET',
        credentials: 'include', // Include cookie to be cleared
      });

      if (!response.ok) {
        throw new Error(`Logout failed: ${response.status}`);
      }

      // Clear client-side state
      setUser(null);
      setIsAuthenticated(false);

      console.log('[AuthContext] Logout successful');

      // Redirect to login
      login();
    } catch (error) {
      console.error('[AuthContext] Logout failed:', error);

      // Even if logout fails, clear local state and redirect
      setUser(null);
      setIsAuthenticated(false);
      login();
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
