"""Backward-compatible shim for legacy docling parser imports.

Use `src.lib.pipeline.pdfx_parser` for all new code.
"""

from .pdfx_parser import *  # noqa: F401,F403
