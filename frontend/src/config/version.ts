/**
 * Application version configuration
 * Values are injected at build time via Vite environment variables
 */

/** Application version from package.json (e.g., "0.1.0") */
export const APP_VERSION = import.meta.env.VITE_APP_VERSION || '0.0.0-dev';

/** Git commit SHA for build traceability (e.g., "abc1234") */
export const GIT_SHA = import.meta.env.VITE_GIT_SHA || 'unknown';

/** Whether this is a development build */
export const IS_DEV_BUILD = APP_VERSION === '0.0.0-dev' || APP_VERSION.includes('dev');

/**
 * Get formatted version string for display
 * Shows version with optional git SHA in development builds
 */
export function getVersionDisplay(): string {
  if (IS_DEV_BUILD && GIT_SHA !== 'unknown') {
    return `v${APP_VERSION} (${GIT_SHA})`;
  }
  return `v${APP_VERSION}`;
}

/**
 * Get full version info for tooltips or detailed display
 */
export function getFullVersionInfo(): string {
  return `Version: ${APP_VERSION}\nBuild: ${GIT_SHA}`;
}
