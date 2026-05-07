"""Shared diagnostics for upstream service error responses."""

from __future__ import annotations

import re

_HEADER_OR_HTML_RE = re.compile(
    r"(?is)(x-robots-tag|x-content-type-options|referrer-policy|<!doctype|<html)"
)


def looks_like_header_or_html_response(message: str) -> bool:
    """Return True when upstream error text appears to contain headers or HTML."""

    return bool(_HEADER_OR_HTML_RE.search(message))
