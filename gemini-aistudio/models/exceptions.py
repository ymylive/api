"""
AI Studio Proxy API - Exception Hierarchy

Provides a comprehensive, hierarchical exception system with:
- HTTP status code awareness
- Rich error context (req_id, timestamp, custom fields)
- Easy conversion to FastAPI HTTPException
- Retry hint support
- Domain-specific error categorization

Usage:
    try:
        result = await operation()
    except PageNotReadyError as e:
        # Convert to HTTP response
        raise e.to_http_exception()
"""

import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

# ==================== BASE EXCEPTION ====================


class AIStudioProxyError(Exception):
    """
    Base exception for all AI Studio Proxy errors.

    Attributes:
        message: Human-readable error message
        req_id: Request ID for tracing
        http_status: HTTP status code for this error
        retry_after: Seconds to wait before retry (optional)
        context: Additional error context (timestamps, state, etc.)
        timestamp: Unix timestamp when error occurred
    """

    def __init__(
        self,
        message: str,
        req_id: Optional[str] = None,
        http_status: int = 500,
        retry_after: Optional[int] = None,
        **context: Any,
    ):
        self.message = message
        self.req_id = req_id
        self.http_status = http_status
        self.retry_after = retry_after
        self.context = context
        self.timestamp = time.time()

        # Call parent Exception with formatted message
        formatted_msg = f"[{req_id}] {message}" if req_id else message
        super().__init__(formatted_msg)

    def to_http_exception(self) -> HTTPException:
        """
        Convert to FastAPI HTTPException for HTTP responses.

        Returns:
            HTTPException with appropriate status code and headers
        """
        headers: Dict[str, str] = {}
        if self.retry_after is not None:
            headers["Retry-After"] = str(self.retry_after)

        detail = f"[{self.req_id}] {self.message}" if self.req_id else self.message
        return HTTPException(
            status_code=self.http_status,
            detail=detail,
            headers=headers if headers else None,
        )

    def __repr__(self) -> str:
        context_str = f", context={self.context}" if self.context else ""
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"req_id={self.req_id!r}, "
            f"http_status={self.http_status}"
            f"{context_str})"
        )


# ==================== BROWSER/PLAYWRIGHT ERRORS (503) ====================


class BrowserError(AIStudioProxyError):
    """
    Base for browser automation errors.

    HTTP Status: 503 Service Unavailable
    Retry After: 30 seconds (browser may recover)

    Common causes:
    - Browser process crashed
    - Page not initialized
    - Selector not found
    - Navigation failed
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 503)
        kwargs.setdefault("retry_after", 30)
        super().__init__(message, **kwargs)


class PageNotReadyError(BrowserError):
    """Page is not initialized or connection lost."""

    pass


class BrowserCrashedError(BrowserError):
    """Browser process has crashed or is unresponsive."""

    def __init__(self, message: str = "Browser crashed unexpectedly", **kwargs: Any):
        super().__init__(message, **kwargs)


class NavigationError(BrowserError):
    """Failed to navigate to AI Studio page."""

    pass


class SelectorNotFoundError(BrowserError):
    """Required page element not found (selector timeout)."""

    def __init__(self, selector: str, **kwargs: Any):
        message = f"Selector not found: {selector}"
        super().__init__(message, selector=selector, **kwargs)


class ElementInteractionError(BrowserError):
    """Failed to interact with page element (click, fill, etc.)."""

    pass


# ==================== MODEL ERRORS (422) ====================


class ModelError(AIStudioProxyError):
    """
    Base for model-related errors.

    HTTP Status: 422 Unprocessable Entity

    Common causes:
    - Invalid model name
    - Model switch failed
    - Model list unavailable
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 422)
        super().__init__(message, **kwargs)


class InvalidModelError(ModelError):
    """Requested model does not exist or is not available."""

    def __init__(
        self, model_id: str, available_models: Optional[List[str]] = None, **kwargs: Any
    ):
        message = f"Invalid model '{model_id}'"
        if available_models:
            message += f". Available: {', '.join(available_models)}"
        super().__init__(
            message, model_id=model_id, available_models=available_models, **kwargs
        )


class ModelSwitchError(ModelError):
    """Failed to switch to requested model."""

    def __init__(
        self, target_model: str, current_model: Optional[str] = None, **kwargs: Any
    ):
        message = f"Failed to switch to model '{target_model}'"
        if current_model:
            message += f" from '{current_model}'"
        super().__init__(
            message, target_model=target_model, current_model=current_model, **kwargs
        )


class ModelListError(ModelError):
    """Failed to fetch or parse model list from AI Studio."""

    def __init__(self, message: str = "Failed to fetch model list", **kwargs: Any):
        super().__init__(message, **kwargs)


# ==================== CLIENT ERRORS (499) ====================


class ClientDisconnectedError(AIStudioProxyError):
    """
    Client disconnected during request processing.

    HTTP Status: 499 Client Closed Request

    Attributes:
        stage: Processing stage when disconnect detected
    """

    def __init__(self, stage: str = "", req_id: Optional[str] = None, **kwargs: Any):
        message = (
            f"Client disconnected at stage: {stage}" if stage else "Client disconnected"
        )
        kwargs["http_status"] = 499
        super().__init__(message, req_id=req_id, **kwargs)
        self.stage = stage


# ==================== VALIDATION ERRORS (400) ====================


class ValidationError(AIStudioProxyError):
    """
    Base for request validation errors.

    HTTP Status: 400 Bad Request

    Common causes:
    - Missing required field
    - Invalid parameter type
    - Out of range value
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 400)
        super().__init__(message, **kwargs)


class InvalidRequestError(ValidationError):
    """Request format or structure is invalid."""

    pass


class MissingParameterError(ValidationError):
    """Required parameter is missing from request."""

    def __init__(self, parameter: str, **kwargs: Any):
        message = f"Missing required parameter: {parameter}"
        super().__init__(message, parameter=parameter, **kwargs)


class InvalidParameterError(ValidationError):
    """Parameter value is invalid or out of range."""

    def __init__(self, parameter: str, value: Any, reason: str = "", **kwargs: Any):
        message = f"Invalid value for parameter '{parameter}': {value}"
        if reason:
            message += f" ({reason})"
        super().__init__(message, parameter=parameter, value=value, **kwargs)


# ==================== STREAM/PROXY ERRORS (502) ====================


class StreamError(AIStudioProxyError):
    """
    Base for stream proxy errors.

    HTTP Status: 502 Bad Gateway

    Common causes:
    - Proxy connection failed
    - Stream timeout
    - Interception failed
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 502)
        super().__init__(message, **kwargs)


class ProxyConnectionError(StreamError):
    """Failed to connect to stream proxy."""

    def __init__(self, proxy_url: Optional[str] = None, **kwargs: Any):
        message = "Failed to connect to stream proxy"
        if proxy_url:
            message += f" at {proxy_url}"
        super().__init__(message, proxy_url=proxy_url, **kwargs)


class StreamTimeoutError(StreamError):
    """Stream response timeout."""

    def __init__(self, timeout_seconds: Optional[float] = None, **kwargs: Any):
        message = "Stream response timeout"
        if timeout_seconds:
            message += f" after {timeout_seconds}s"
        super().__init__(message, timeout_seconds=timeout_seconds, **kwargs)


# ==================== RESOURCE ERRORS (503) ====================


class ResourceError(AIStudioProxyError):
    """
    Base for resource exhaustion errors.

    HTTP Status: 503 Service Unavailable
    Retry After: 60 seconds (wait for resources)

    Common causes:
    - Queue full
    - Browser init failed
    - Memory exhausted
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 503)
        kwargs.setdefault("retry_after", 60)
        super().__init__(message, **kwargs)


class QueueFullError(ResourceError):
    """Request queue is at capacity."""

    def __init__(self, queue_size: Optional[int] = None, **kwargs: Any):
        message = "Request queue is full"
        if queue_size:
            message += f" ({queue_size} requests)"
        super().__init__(message, queue_size=queue_size, **kwargs)


class BrowserInitError(ResourceError):
    """Failed to initialize browser or Playwright."""

    def __init__(self, message: str = "Browser initialization failed", **kwargs: Any):
        super().__init__(message, **kwargs)


# ==================== UPSTREAM ERRORS (502) ====================


class UpstreamError(AIStudioProxyError):
    """
    Base for errors from upstream AI Studio service.

    HTTP Status: 502 Bad Gateway
    Retry After: 10 seconds (AI Studio may recover quickly)

    Common causes:
    - AI Studio returned error
    - Quota exceeded
    - Network failure
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 502)
        kwargs.setdefault("retry_after", 10)
        super().__init__(message, **kwargs)


class AIStudioError(UpstreamError):
    """AI Studio returned an error response."""

    def __init__(
        self, error_message: str, status_code: Optional[int] = None, **kwargs: Any
    ):
        message = f"AI Studio error: {error_message}"
        super().__init__(message, ai_studio_status=status_code, **kwargs)


class QuotaExceededError(UpstreamError):
    """AI Studio API quota exceeded."""

    def __init__(self, message: str = "AI Studio quota exceeded", **kwargs: Any):
        kwargs["retry_after"] = 3600  # Retry after 1 hour for quota
        super().__init__(message, **kwargs)


class EmptyResponseError(UpstreamError):
    """AI Studio returned empty response (possible quota issue)."""

    def __init__(self, message: str = "Empty response from AI Studio", **kwargs: Any):
        super().__init__(message, **kwargs)


# ==================== TIMEOUT ERRORS (504) ====================


class TimeoutError(AIStudioProxyError):
    """
    Base for operation timeout errors.

    HTTP Status: 504 Gateway Timeout

    Common causes:
    - Response generation too slow
    - Browser operation timeout
    - Network timeout
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 504)
        super().__init__(message, **kwargs)


class ResponseTimeoutError(TimeoutError):
    """Waiting for AI response timed out."""

    def __init__(self, timeout_seconds: Optional[float] = None, **kwargs: Any):
        message = "Response generation timeout"
        if timeout_seconds:
            message += f" ({timeout_seconds}s)"
        super().__init__(message, timeout_seconds=timeout_seconds, **kwargs)


class ProcessingTimeoutError(TimeoutError):
    """Request processing exceeded maximum time."""

    def __init__(self, timeout_seconds: Optional[float] = None, **kwargs: Any):
        message = "Request processing timeout"
        if timeout_seconds:
            message += f" ({timeout_seconds}s)"
        super().__init__(message, timeout_seconds=timeout_seconds, **kwargs)


# ==================== CONFIGURATION ERRORS (500) ====================


class ConfigurationError(AIStudioProxyError):
    """
    Base for configuration-related errors.

    HTTP Status: 500 Internal Server Error

    Common causes:
    - Missing environment variable
    - Invalid configuration value
    - Required file not found
    """

    def __init__(self, message: str, **kwargs: Any):
        kwargs.setdefault("http_status", 500)
        super().__init__(message, **kwargs)


class MissingConfigError(ConfigurationError):
    """Required configuration is missing."""

    def __init__(self, config_key: str, **kwargs: Any):
        message = f"Missing required configuration: {config_key}"
        super().__init__(message, config_key=config_key, **kwargs)


class InvalidConfigError(ConfigurationError):
    """Configuration value is invalid."""

    def __init__(self, config_key: str, value: Any, reason: str = "", **kwargs: Any):
        message = f"Invalid configuration '{config_key}': {value}"
        if reason:
            message += f" ({reason})"
        super().__init__(message, config_key=config_key, value=value, **kwargs)


# ==================== BACKWARD COMPATIBILITY ====================

# Keep old ClientDisconnectedError as alias for existing code
# This ensures we don't break existing imports
__all__ = [
    # Base
    "AIStudioProxyError",
    # Browser errors
    "BrowserError",
    "PageNotReadyError",
    "BrowserCrashedError",
    "NavigationError",
    "SelectorNotFoundError",
    "ElementInteractionError",
    # Model errors
    "ModelError",
    "InvalidModelError",
    "ModelSwitchError",
    "ModelListError",
    # Client errors
    "ClientDisconnectedError",
    # Validation errors
    "ValidationError",
    "InvalidRequestError",
    "MissingParameterError",
    "InvalidParameterError",
    # Stream errors
    "StreamError",
    "ProxyConnectionError",
    "StreamTimeoutError",
    # Resource errors
    "ResourceError",
    "QueueFullError",
    "BrowserInitError",
    # Upstream errors
    "UpstreamError",
    "AIStudioError",
    "QuotaExceededError",
    "EmptyResponseError",
    # Timeout errors
    "TimeoutError",
    "ResponseTimeoutError",
    "ProcessingTimeoutError",
    # Configuration errors
    "ConfigurationError",
    "MissingConfigError",
    "InvalidConfigError",
]
