"""
REST API tool for OpenAI Agents SDK.

Supports domain allowlists and structured outputs for safer tool calling.
"""

import json
import logging
from typing import Optional, Dict, Any, List, Callable
from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from agents import function_tool

logger = logging.getLogger(__name__)


class HttpResponse(BaseModel):
    status: str
    status_code: Optional[int] = None
    data: Any = None
    message: Optional[str] = None


def _unrestricted_rest_api_call(
    url: str,
    method: str = "GET",
    headers_json: Optional[str] = None,
    body_json: Optional[str] = None
) -> HttpResponse:
    """
    Internal unrestricted REST call - NOT exposed as a tool.

    Use create_rest_api_tool() with domain allowlist for agent-facing tools.
    This function is kept for internal/testing use only.
    """
    return _rest_api_impl(url, method, headers_json, body_json, allowed_domains=None)


def _is_domain_allowed(url: str, allowed_domains: List[str]) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if ':' in host:
            host = host.split(':')[0]
        for allowed in allowed_domains:
            allowed = allowed.lower()
            if host == allowed or host.endswith('.' + allowed):
                return True
        return False
    except Exception:
        return False


def _rest_api_impl(
    url: str,
    method: str,
    headers_json: Optional[str],
    body_json: Optional[str],
    allowed_domains: Optional[List[str]] = None
) -> HttpResponse:
    # Parse headers/body
    headers: Optional[Dict[str, str]] = None
    body: Optional[Dict[str, Any]] = None

    if headers_json:
        try:
            headers = json.loads(headers_json)
        except json.JSONDecodeError:
            return HttpResponse(status="error", message="Invalid headers_json: must be valid JSON")

    if body_json:
        try:
            body = json.loads(body_json)
        except json.JSONDecodeError:
            return HttpResponse(status="error", message="Invalid body_json: must be valid JSON")

    if not url or not url.strip():
        return HttpResponse(status="error", message="URL must not be empty")

    url = url.strip()
    if not url.lower().startswith(('http://', 'https://')):
        return HttpResponse(status="error", message="URL must start with http:// or https://")

    valid_methods = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
    method = method.upper()
    if method not in valid_methods:
        return HttpResponse(status="error", message=f"Method must be one of: {', '.join(valid_methods)}")

    if allowed_domains and not _is_domain_allowed(url, allowed_domains):
        return HttpResponse(
            status="error",
            message=f"Domain not allowed. Allowed: {', '.join(allowed_domains)}"
        )

    final_headers = headers.copy() if headers else {}
    final_headers.setdefault('User-Agent', 'AI-Curation-Bot/1.0')

    logger.debug(f"[OpenAI Agents] REST API {method} {url}")

    try:
        request_kwargs = {
            'method': method,
            'url': url,
            'headers': final_headers,
            'timeout': 30
        }
        if body and method in ('POST', 'PUT', 'PATCH'):
            request_kwargs['json'] = body

        response = requests.request(**request_kwargs)
        response.raise_for_status()

        try:
            data = response.json()
        except json.JSONDecodeError:
            data = response.text

        return HttpResponse(status="ok", status_code=response.status_code, data=data)

    except requests.exceptions.HTTPError as e:
        logger.error(f"[OpenAI Agents] HTTP error: {e}")
        error_detail = str(e)
        if e.response is not None:
            try:
                error_data = e.response.json()
                error_detail = json.dumps(error_data)
            except json.JSONDecodeError:
                error_detail = e.response.text or str(e)

        return HttpResponse(
            status="error",
            status_code=e.response.status_code if e.response else None,
            message=f"HTTP {e.response.status_code if e.response else 'error'}: {error_detail}"
        )
    except requests.exceptions.Timeout:
        logger.error("[OpenAI Agents] Request timeout")
        return HttpResponse(status="error", message="Request timed out after 30 seconds")
    except requests.exceptions.ConnectionError:
        logger.error("[OpenAI Agents] Connection error")
        return HttpResponse(status="error", message="Connection error: Unable to reach API endpoint")
    except requests.exceptions.RequestException as e:
        logger.error(f"[OpenAI Agents] Request error: {e}")
        return HttpResponse(status="error", message=f"Request error: {str(e)}")
    except Exception as e:
        logger.error(f"[OpenAI Agents] Unexpected REST API error: {e}", exc_info=True)
        return HttpResponse(status="error", message=f"Unexpected error: {str(e)}")


def create_rest_api_tool(
    allowed_domains: List[str],
    tool_name: str = "rest_api_call",
    tool_description: Optional[str] = None
) -> Callable:
    """
    Create a domain-restricted REST API tool (recommended for production).
    """
    domains_str = ", ".join(allowed_domains)
    default_description = f"Make HTTP requests to REST APIs. Allowed domains: {domains_str}"
    description = tool_description or default_description

    @function_tool(name_override=tool_name, description_override=description)
    def restricted_rest_api_call(
        url: str,
        method: str = "GET",
        headers_json: Optional[str] = None,
        body_json: Optional[str] = None
    ) -> HttpResponse:
        return _rest_api_impl(
            url=url,
            method=method,
            headers_json=headers_json,
            body_json=body_json,
            allowed_domains=allowed_domains,
        )

    return restricted_rest_api_call
