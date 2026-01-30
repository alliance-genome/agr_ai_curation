"""
Example REST API Tool Template.

This template shows how to create a tool that integrates with an external
REST API. Copy this file to ../custom/ and customize for your API.

To use this template:
    1. Copy to ../custom/my_api_tool.py
    2. Update the function name and decorator
    3. Implement your API integration logic
    4. Reference the tool name in your agent's agent.yaml

Key patterns demonstrated:
    - Using @function_tool decorator for registration
    - Async HTTP requests with httpx
    - Environment variable configuration
    - Error handling and graceful failures
    - Data transformation before returning
"""

import os
from typing import Optional

import httpx
from agents import function_tool


# -----------------------------------------------------------------------------
# Configuration from environment variables
# -----------------------------------------------------------------------------
# Always load sensitive configuration from environment variables, never hardcode
API_BASE_URL = os.getenv("EXAMPLE_API_URL", "https://api.example.com")
API_KEY = os.getenv("EXAMPLE_API_KEY", "")
API_TIMEOUT = int(os.getenv("EXAMPLE_API_TIMEOUT", "30"))


# -----------------------------------------------------------------------------
# Tool Implementation
# -----------------------------------------------------------------------------
@function_tool(
    # name_override: The name agents use to reference this tool in agent.yaml
    name_override="example_rest_api",
    # description_override: Shown to the LLM to help it decide when to use this tool
    description_override="Query the Example API for data lookup and validation"
)
async def example_rest_api(
    query: str,
    category: Optional[str] = None,
    limit: int = 10
) -> dict:
    """
    Query the Example REST API.

    This tool connects to an external API to fetch and return data.
    The tool handles authentication, request formatting, and response
    transformation internally.

    Args:
        query: The search query string
        category: Optional category filter (e.g., "genes", "diseases")
        limit: Maximum number of results to return (default: 10)

    Returns:
        Dict containing:
            - results: List of matching items
            - total: Total count of matches
            - query: The query that was executed
            - error: Error message if the request failed (None if successful)
    """
    # Build request parameters
    params = {
        "q": query,
        "limit": limit,
    }
    if category:
        params["category"] = category

    # Build headers with authentication
    headers = {
        "Accept": "application/json",
        "User-Agent": "AI-Curation-Tool/1.0",
    }
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    try:
        # Make the API request
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(
                f"{API_BASE_URL}/search",
                params=params,
                headers=headers
            )
            response.raise_for_status()

            # Parse response
            data = response.json()

            # Transform to clean output structure
            # Don't return raw API responses - clean and normalize the data
            results = [
                {
                    "id": item.get("id"),
                    "name": item.get("name") or item.get("title"),
                    "description": item.get("description"),
                    "category": item.get("category"),
                    "url": item.get("url"),
                }
                for item in data.get("items", [])
            ]

            return {
                "results": results,
                "total": data.get("total_count", len(results)),
                "query": query,
                "error": None,
            }

    except httpx.HTTPStatusError as e:
        # Handle HTTP errors (4xx, 5xx responses)
        return {
            "results": [],
            "total": 0,
            "query": query,
            "error": f"API error: {e.response.status_code} - {e.response.text[:200]}",
        }
    except httpx.RequestError as e:
        # Handle connection errors, timeouts, etc.
        return {
            "results": [],
            "total": 0,
            "query": query,
            "error": f"Request failed: {str(e)}",
        }
    except Exception as e:
        # Handle unexpected errors
        return {
            "results": [],
            "total": 0,
            "query": query,
            "error": f"Unexpected error: {str(e)}",
        }
