import { v4 as uuidv4 } from 'uuid';

export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'fatal';

export interface LogContext {
  correlationId?: string;
  userId?: string;
  sessionId?: string;
  component?: string;
  action?: string;
  metadata?: Record<string, unknown>;
}

export interface LogEntry {
  timestamp: string;
  level: LogLevel;
  message: string;
  correlationId: string;
  context?: LogContext;
  error?: {
    name: string;
    message: string;
    stack?: string;
  };
  metadata?: Record<string, unknown>;
}

class Logger {
  private static instance: Logger;
  private correlationId: string;
  private sessionId: string;
  private buffer: LogEntry[] = [];
  private maxBufferSize = 100;
  private logEndpoint = '/api/logs';
  private flushInterval = 30000; // 30 seconds
  private flushTimer: NodeJS.Timeout | null = null;

  private constructor() {
    this.correlationId = uuidv4();
    this.sessionId = this.generateSessionId();
    this.startFlushTimer();

    // Send logs on page unload
    if (typeof window !== 'undefined') {
      window.addEventListener('beforeunload', () => {
        this.flush();
      });
    }
  }

  static getInstance(): Logger {
    if (!Logger.instance) {
      Logger.instance = new Logger();
    }
    return Logger.instance;
  }

  private generateSessionId(): string {
    return `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  }

  private startFlushTimer(): void {
    this.flushTimer = setInterval(() => {
      this.flush();
    }, this.flushInterval);
  }

  newCorrelationId(): string {
    this.correlationId = uuidv4();
    return this.correlationId;
  }

  getCorrelationId(): string {
    return this.correlationId;
  }

  getSessionId(): string {
    return this.sessionId;
  }

  private formatLogEntry(
    level: LogLevel,
    message: string,
    context?: LogContext,
    error?: Error
  ): LogEntry {
    return {
      timestamp: new Date().toISOString(),
      level,
      message,
      correlationId: context?.correlationId || this.correlationId,
      context: {
        ...context,
        sessionId: this.sessionId,
      },
      ...(error && {
        error: {
          name: error.name,
          message: error.message,
          stack: error.stack,
        },
      }),
      metadata: {
        ...context?.metadata,
        userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : undefined,
        url: typeof window !== 'undefined' ? window.location.href : undefined,
        environment: process.env.NODE_ENV,
      },
    };
  }

  private log(
    level: LogLevel,
    message: string,
    context?: LogContext,
    error?: Error
  ): void {
    const logEntry = this.formatLogEntry(level, message, context, error);

    // Always log to console in development
    if (process.env.NODE_ENV === 'development') {
      this.logToConsole(logEntry);
    }

    // Add to buffer for remote logging
    this.buffer.push(logEntry);

    // Flush if buffer is full
    if (this.buffer.length >= this.maxBufferSize) {
      this.flush();
    }

    // Immediately send critical errors
    if (level === 'fatal' || level === 'error') {
      this.flush();
    }
  }

  private logToConsole(entry: LogEntry): void {
    const style = this.getConsoleStyle(entry.level);
    const prefix = `[${entry.timestamp}] [${entry.correlationId.slice(0, 8)}] [${entry.level.toUpperCase()}]`;

    console.groupCollapsed(`%c${prefix} ${entry.message}`, style);

    if (entry.context) {
      console.log('Context:', entry.context);
    }

    if (entry.error) {
      console.error('Error:', entry.error);
    }

    if (entry.metadata) {
      console.log('Metadata:', entry.metadata);
    }

    console.groupEnd();
  }

  private getConsoleStyle(level: LogLevel): string {
    const styles = {
      debug: 'color: #888; font-weight: normal;',
      info: 'color: #2196F3; font-weight: normal;',
      warn: 'color: #FF9800; font-weight: bold;',
      error: 'color: #F44336; font-weight: bold;',
      fatal: 'color: #FFFFFF; background-color: #F44336; font-weight: bold; padding: 2px 4px;',
    };
    return styles[level];
  }

  async flush(): Promise<void> {
    if (this.buffer.length === 0) return;

    const logsToSend = [...this.buffer];
    this.buffer = [];

    try {
      // In production, send logs to backend
      if (process.env.NODE_ENV === 'production') {
        await fetch(this.logEndpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            logs: logsToSend,
          }),
        });
      }
    } catch (error) {
      // If logging fails, at least log to console
      console.error('Failed to send logs to server:', error);
      // Re-add logs to buffer for retry
      this.buffer = [...logsToSend, ...this.buffer].slice(-this.maxBufferSize);
    }
  }

  // Public logging methods
  debug(message: string, context?: LogContext): void {
    this.log('debug', message, context);
  }

  info(message: string, context?: LogContext): void {
    this.log('info', message, context);
  }

  warn(message: string, context?: LogContext): void {
    this.log('warn', message, context);
  }

  error(message: string, error?: Error, context?: LogContext): void {
    this.log('error', message, context, error);
  }

  fatal(message: string, error?: Error, context?: LogContext): void {
    this.log('fatal', message, context, error);
  }

  // Performance logging
  startTimer(label: string): () => void {
    const start = performance.now();
    return () => {
      const duration = performance.now() - start;
      this.info(`Performance: ${label}`, {
        component: 'performance',
        metadata: {
          duration: `${duration.toFixed(2)}ms`,
          label,
        },
      });
    };
  }

  // API call logging
  logApiCall(
    method: string,
    url: string,
    status?: number,
    duration?: number,
    error?: Error
  ): void {
    const message = `API ${method} ${url} - ${status || 'pending'}`;
    const context: LogContext = {
      component: 'api',
      action: method.toLowerCase(),
      metadata: {
        url,
        method,
        status,
        duration: duration ? `${duration}ms` : undefined,
      },
    };

    if (error || (status && status >= 400)) {
      this.error(message, error, context);
    } else {
      this.info(message, context);
    }
  }

  // React component lifecycle logging
  logComponentMount(componentName: string, props?: Record<string, unknown>): void {
    this.debug(`Component mounted: ${componentName}`, {
      component: componentName,
      action: 'mount',
      metadata: props,
    });
  }

  logComponentUnmount(componentName: string): void {
    this.debug(`Component unmounted: ${componentName}`, {
      component: componentName,
      action: 'unmount',
    });
  }

  logComponentError(
    componentName: string,
    error: Error,
    errorInfo?: { componentStack?: string | null }
  ): void {
    this.error(`Component error in ${componentName}`, error, {
      component: componentName,
      action: 'error',
      metadata: errorInfo,
    });
  }

  // User action logging
  logUserAction(action: string, details?: Record<string, unknown>): void {
    this.info(`User action: ${action}`, {
      component: 'user-interaction',
      action,
      metadata: details,
    });
  }

  // Clean up
  destroy(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
    this.flush();
  }
}

// Export singleton instance
export const logger = Logger.getInstance();

// React hook for component logging
export function useLogger(componentName: string) {
  const correlationId = logger.getCorrelationId();

  const logDebug = (message: string, metadata?: Record<string, unknown>) => {
    logger.debug(message, {
      component: componentName,
      correlationId,
      metadata,
    });
  };

  const logInfo = (message: string, metadata?: Record<string, unknown>) => {
    logger.info(message, {
      component: componentName,
      correlationId,
      metadata,
    });
  };

  const logWarn = (message: string, metadata?: Record<string, unknown>) => {
    logger.warn(message, {
      component: componentName,
      correlationId,
      metadata,
    });
  };

  const logError = (message: string, error?: Error, metadata?: Record<string, unknown>) => {
    logger.error(message, error, {
      component: componentName,
      correlationId,
      metadata,
    });
  };

  const logAction = (action: string, details?: Record<string, unknown>) => {
    logger.logUserAction(action, {
      ...details,
      component: componentName,
    });
  };

  return {
    logDebug,
    logInfo,
    logWarn,
    logError,
    logAction,
    correlationId,
  };
}

export default logger;