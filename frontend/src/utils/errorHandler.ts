/**
 * Error handling utilities for the frontend application
 * Provides consistent error messages and retry logic
 */

export interface APIError {
  error: string;
  status_code: number;
  type: string;
  detail?: string;
  retry_after?: number;
}

export class ChatError extends Error {
  public statusCode: number;
  public errorType: string;
  public retryAfter?: number;
  public canRetry: boolean;

  constructor(
    message: string,
    statusCode: number,
    errorType: string,
    retryAfter?: number,
  ) {
    super(message);
    this.name = "ChatError";
    this.statusCode = statusCode;
    this.errorType = errorType;
    this.retryAfter = retryAfter;

    // Determine if error is retryable
    this.canRetry = [408, 429, 502, 503, 504].includes(statusCode);
  }
}

/**
 * Parse error response from API
 */
export function parseAPIError(error: any): ChatError {
  // Handle network errors
  if (!error.response) {
    return new ChatError(
      "Network error. Please check your connection.",
      0,
      "network_error",
    );
  }

  // Handle API errors
  const data = error.response.data as APIError;

  if (data && data.error) {
    return new ChatError(
      data.error,
      data.status_code || error.response.status,
      data.type || "api_error",
      data.retry_after,
    );
  }

  // Handle unexpected errors
  return new ChatError(
    error.message || "An unexpected error occurred",
    error.response.status || 500,
    "unknown_error",
  );
}

/**
 * Get user-friendly error message
 */
export function getUserFriendlyErrorMessage(error: ChatError): string {
  switch (error.errorType) {
    case "network_error":
      return "Unable to connect to the server. Please check your internet connection.";

    case "rate_limit_error":
      return `Too many requests. Please wait ${error.retryAfter || 60} seconds before trying again.`;

    case "configuration_error":
      return "The AI service is not properly configured. Please contact support.";

    case "validation_error":
      return "Invalid request. Please check your input and try again.";

    case "connection_error":
      return "Service temporarily unavailable. Please try again in a moment.";

    case "http_error":
      if (error.statusCode === 401) {
        return "Authentication required. Please log in.";
      }
      if (error.statusCode === 403) {
        return "You do not have permission to perform this action.";
      }
      if (error.statusCode === 404) {
        return "The requested resource was not found.";
      }
      return error.message;

    default:
      return error.message || "An error occurred. Please try again.";
  }
}

/**
 * Retry configuration for API calls
 */
export interface RetryConfig {
  maxRetries: number;
  initialDelay: number;
  maxDelay: number;
  backoffMultiplier: number;
}

export const defaultRetryConfig: RetryConfig = {
  maxRetries: 3,
  initialDelay: 1000,
  maxDelay: 10000,
  backoffMultiplier: 2,
};

/**
 * Execute function with exponential backoff retry
 */
export async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  config: RetryConfig = defaultRetryConfig,
  onRetry?: (attempt: number, delay: number) => void,
): Promise<T> {
  let lastError: any;
  let delay = config.initialDelay;

  for (let attempt = 0; attempt <= config.maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error: any) {
      lastError = error;

      // Check if error is retryable
      const chatError =
        error instanceof ChatError ? error : parseAPIError(error);

      if (!chatError.canRetry || attempt === config.maxRetries) {
        throw chatError;
      }

      // Calculate next delay
      if (chatError.retryAfter) {
        delay = chatError.retryAfter * 1000;
      } else {
        delay = Math.min(delay * config.backoffMultiplier, config.maxDelay);
      }

      // Notify about retry
      if (onRetry) {
        onRetry(attempt + 1, delay);
      }

      // Wait before retrying
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }

  throw lastError;
}

/**
 * Handle SSE (Server-Sent Events) errors
 */
export function handleSSEError(error: Event): ChatError {
  if (error instanceof ErrorEvent) {
    return new ChatError(
      "Connection to AI service lost",
      503,
      "connection_error",
    );
  }

  return new ChatError("Streaming error occurred", 500, "streaming_error");
}

/**
 * Validate API response
 */
export function validateAPIResponse(response: any): boolean {
  if (!response) {
    return false;
  }

  // Check for required fields based on endpoint
  if (response.response !== undefined) {
    return typeof response.response === "string";
  }

  if (response.delta !== undefined) {
    return typeof response.delta === "string";
  }

  return false;
}

/**
 * Error recovery strategies
 */
export const recoveryStrategies = {
  // Switch to non-streaming mode on streaming errors
  fallbackToNonStreaming: (error: ChatError): boolean => {
    return error.errorType === "streaming_error";
  },

  // Switch provider on specific errors
  switchProvider: (
    error: ChatError,
    currentProvider: string,
  ): string | null => {
    if (error.statusCode === 503 && error.errorType === "configuration_error") {
      return currentProvider === "openai" ? "gemini" : "openai";
    }
    return null;
  },

  // Clear cache on certain errors
  shouldClearCache: (error: ChatError): boolean => {
    return error.statusCode === 401 || error.statusCode === 403;
  },
};

/**
 * Log error for debugging (production would send to monitoring service)
 */
export function logError(error: ChatError, context?: any): void {
  console.error("Chat Error:", {
    message: error.message,
    statusCode: error.statusCode,
    errorType: error.errorType,
    canRetry: error.canRetry,
    context,
    timestamp: new Date().toISOString(),
  });
}
