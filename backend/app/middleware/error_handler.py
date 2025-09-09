"""
Global error handling middleware for the application
Provides consistent error responses and logging
"""

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import traceback
from typing import Callable

logger = logging.getLogger(__name__)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware for handling exceptions globally"""

    async def dispatch(self, request: Request, call_next: Callable):
        """Process requests and handle exceptions"""
        try:
            response = await call_next(request)
            return response

        except HTTPException as exc:
            # Re-raise HTTP exceptions (they have proper status codes)
            logger.warning(f"HTTP Exception: {exc.status_code} - {exc.detail}")
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": exc.detail,
                    "status_code": exc.status_code,
                    "type": "http_error",
                },
            )

        except ValueError as exc:
            # Handle validation errors
            logger.error(f"Validation error: {str(exc)}")
            return JSONResponse(
                status_code=400,
                content={
                    "error": str(exc),
                    "status_code": 400,
                    "type": "validation_error",
                },
            )

        except ConnectionError as exc:
            # Handle connection errors (DB, external services)
            logger.error(f"Connection error: {str(exc)}")
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Service temporarily unavailable",
                    "status_code": 503,
                    "type": "connection_error",
                    "detail": (
                        str(exc) if request.url.path.startswith("/api/") else None
                    ),
                },
            )

        except Exception as exc:
            # Handle all other exceptions
            logger.error(f"Unhandled exception: {str(exc)}\n{traceback.format_exc()}")

            # Don't expose internal errors in production
            error_message = "An unexpected error occurred"
            if request.url.path.startswith("/api/") and request.app.debug:
                error_message = str(exc)

            return JSONResponse(
                status_code=500,
                content={
                    "error": error_message,
                    "status_code": 500,
                    "type": "internal_error",
                },
            )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple rate limiting middleware for AI endpoints"""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.request_counts = {}  # Simple in-memory store

    async def dispatch(self, request: Request, call_next: Callable):
        """Apply rate limiting to chat endpoints"""

        # Only rate limit chat endpoints
        if not request.url.path.startswith("/api/chat"):
            return await call_next(request)

        # Get client identifier (use IP for simplicity)
        client_id = request.client.host if request.client else "unknown"

        # Simple rate limiting logic (production should use Redis)
        # This is a placeholder implementation
        import time

        current_time = time.time()

        if client_id not in self.request_counts:
            self.request_counts[client_id] = []

        # Remove old requests outside the window
        self.request_counts[client_id] = [
            timestamp
            for timestamp in self.request_counts[client_id]
            if current_time - timestamp < self.window_seconds
        ]

        # Check rate limit
        if len(self.request_counts[client_id]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for client: {client_id}")
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded. Please try again later.",
                    "status_code": 429,
                    "type": "rate_limit_error",
                    "retry_after": self.window_seconds,
                },
            )

        # Add current request
        self.request_counts[client_id].append(current_time)

        # Process request
        return await call_next(request)


class APIKeyValidationMiddleware(BaseHTTPMiddleware):
    """Middleware to validate API keys are configured"""

    async def dispatch(self, request: Request, call_next: Callable):
        """Check if API keys are configured for AI endpoints"""

        # Only check for chat endpoints
        if not request.url.path.startswith("/api/chat"):
            return await call_next(request)

        # Skip for model listing endpoint
        if request.url.path == "/api/chat/models":
            return await call_next(request)

        # Check if at least one API key is configured
        import os

        openai_key = os.getenv("OPENAI_API_KEY")
        gemini_key = os.getenv("GEMINI_API_KEY")

        if not openai_key and not gemini_key:
            logger.error("No AI API keys configured")
            return JSONResponse(
                status_code=503,
                content={
                    "error": "AI service not configured. Please contact administrator.",
                    "status_code": 503,
                    "type": "configuration_error",
                },
            )

        return await call_next(request)
