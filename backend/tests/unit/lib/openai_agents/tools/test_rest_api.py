"""Unit tests for REST API helper validation and error mapping."""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests


def _load_rest_api_module():
    # The local unit-test environment may not have the OpenAI Agents SDK installed.
    # Provide a minimal decorator stub so this module can be imported in isolation.
    if "agents" not in sys.modules:
        stub = types.ModuleType("agents")

        def function_tool(*args, **kwargs):
            if args and callable(args[0]) and len(args) == 1 and not kwargs:
                return args[0]

            def _decorator(fn):
                return fn

            return _decorator

        stub.function_tool = function_tool
        sys.modules["agents"] = stub

    module_path = Path(__file__).resolve().parents[5] / "src/lib/openai_agents/tools/rest_api.py"
    spec = importlib.util.spec_from_file_location("test_rest_api_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


rest_api = _load_rest_api_module()


@pytest.mark.parametrize(
    ("url", "allowed", "expected"),
    [
        ("https://api.example.org/v1/items", ["api.example.org"], True),
        ("https://sub.api.example.org/v1/items", ["api.example.org"], True),
        ("https://api.example.org:8443/v1/items", ["api.example.org"], True),
        ("https://evil.example.com/v1/items", ["api.example.org"], False),
        ("not-a-url", ["api.example.org"], False),
    ],
)
def test_is_domain_allowed(url, allowed, expected):
    """Domain allowlist should support exact host, subdomains, and host:port."""
    assert rest_api._is_domain_allowed(url, allowed) is expected


def test_rest_api_rejects_invalid_headers_json():
    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json="{bad",
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "Invalid headers_json: must be valid JSON"


def test_rest_api_rejects_invalid_body_json():
    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="POST",
        headers_json=None,
        body_json="{bad",
    )

    assert result.status == "error"
    assert result.message == "Invalid body_json: must be valid JSON"


def test_rest_api_rejects_empty_url():
    result = rest_api._rest_api_impl(url="   ", method="GET", headers_json=None, body_json=None)

    assert result.status == "error"
    assert result.message == "URL must not be empty"


def test_rest_api_rejects_non_http_url():
    result = rest_api._rest_api_impl(
        url="ftp://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "URL must start with http:// or https://"


def test_rest_api_rejects_invalid_method():
    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="OPTIONS",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "Method must be one of: GET, POST, PUT, PATCH, DELETE"


def test_rest_api_rejects_non_allowlisted_domain():
    result = rest_api._rest_api_impl(
        url="https://evil.example.com/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
        allowed_domains=["api.example.org"],
    )

    assert result.status == "error"
    assert result.message == "Domain not allowed. Allowed: api.example.org"


def test_rest_api_success_json_response_and_get_ignores_body_json(monkeypatch):
    captured = {}
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True}
    response.status_code = 200

    def _fake_request(**kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(rest_api.requests, "request", _fake_request)

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json='{"X-Test":"1"}',
        body_json='{"ignored":"on_get"}',
    )

    assert result.status == "ok"
    assert result.status_code == 200
    assert result.data == {"ok": True}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.example.org/v1/items"
    assert captured["timeout"] == 30
    assert captured["headers"]["X-Test"] == "1"
    assert captured["headers"]["User-Agent"] == "AI-Curation-Bot/1.0"
    assert "json" not in captured


def test_rest_api_post_sends_json_body(monkeypatch):
    captured = {}
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"created": True}
    response.status_code = 201

    def _fake_request(**kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(rest_api.requests, "request", _fake_request)

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="POST",
        headers_json=None,
        body_json='{"name":"new-item"}',
    )

    assert result.status == "ok"
    assert result.status_code == 201
    assert result.data == {"created": True}
    assert captured["json"] == {"name": "new-item"}


def test_rest_api_success_falls_back_to_text_when_json_decode_fails(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    response.text = "plain text payload"
    response.status_code = 200

    monkeypatch.setattr(rest_api.requests, "request", lambda **_: response)

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "ok"
    assert result.status_code == 200
    assert result.data == "plain text payload"


def test_rest_api_maps_http_error_with_json_detail(monkeypatch):
    response = Mock()
    response.status_code = 404
    response.json.return_value = {"detail": "not found"}
    response.raise_for_status.side_effect = requests.exceptions.HTTPError("not found", response=response)

    monkeypatch.setattr(rest_api.requests, "request", lambda **_: response)

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items/404",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.status_code == 404
    assert result.message == 'HTTP 404: {"detail": "not found"}'


def test_rest_api_maps_http_error_with_text_fallback(monkeypatch):
    response = Mock()
    response.status_code = 500
    response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    response.text = "server exploded"
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "internal server error",
        response=response,
    )

    monkeypatch.setattr(rest_api.requests, "request", lambda **_: response)

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.status_code == 500
    assert result.message == "HTTP 500: server exploded"


def test_rest_api_maps_timeout_error(monkeypatch):
    monkeypatch.setattr(
        rest_api.requests,
        "request",
        lambda **_: (_ for _ in ()).throw(requests.exceptions.Timeout()),
    )

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "Request timed out after 30 seconds"


def test_rest_api_maps_connection_error(monkeypatch):
    monkeypatch.setattr(
        rest_api.requests,
        "request",
        lambda **_: (_ for _ in ()).throw(requests.exceptions.ConnectionError()),
    )

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "Connection error: Unable to reach API endpoint"


def test_rest_api_maps_generic_request_exception(monkeypatch):
    monkeypatch.setattr(
        rest_api.requests,
        "request",
        lambda **_: (_ for _ in ()).throw(requests.exceptions.RequestException("bad request object")),
    )

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "Request error: bad request object"


def test_rest_api_maps_unexpected_exception(monkeypatch):
    monkeypatch.setattr(
        rest_api.requests,
        "request",
        lambda **_: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )

    result = rest_api._rest_api_impl(
        url="https://api.example.org/v1/items",
        method="GET",
        headers_json=None,
        body_json=None,
    )

    assert result.status == "error"
    assert result.message == "Unexpected error: kaboom"
