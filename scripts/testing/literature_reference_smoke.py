#!/usr/bin/env python3
"""Read-only release gate for the configured Alliance literature package tool.

Run in the backend runtime with --identifier <known reference CURIE>. Missing
configuration is a failure, never a skipped test. No settings or data are changed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys


def missing_settings(environ):
    return [key for key in ("ELASTICSEARCH_HOST", "ELASTICSEARCH_SCHEME", "ELASTICSEARCH_PORT", "ELASTICSEARCH_INDEX")
            if not str(environ.get(key, "")).strip()]


async def lookup(identifier):
    from agents.tool_context import ToolContext
    from agr_ai_curation_alliance.tools.literature_references import agr_literature_reference_lookup

    arguments = json.dumps({"method": "get_literature_reference", "identifier": identifier,
                            "query": None, "exact_match": True, "limit": None})
    result = await agr_literature_reference_lookup.on_invoke_tool(
        ToolContext(context=None, tool_name="agr_literature_reference_lookup",
                    tool_call_id="release-literature-smoke", tool_arguments=arguments), arguments)
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    elif isinstance(result, str):
        result = json.loads(result)
    reference = result.get("resolved_reference") or {}
    return {"status": result.get("status"), "lookup_status": result.get("lookup_status"),
            "reference_curie": reference.get("curie"),
            "passed": result.get("status") == "ok" and result.get("lookup_status") == "success" and bool(reference)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifier", required=True)
    parser.add_argument("--package-src", type=Path, default=Path(__file__).resolve().parents[2] / "packages/alliance/python/src")
    args = parser.parse_args()
    missing = missing_settings(os.environ)
    if missing:
        print(json.dumps({"passed": False, "missing_settings": missing}))
        return 1
    sys.path.insert(0, str(args.package_src))
    try:
        result = asyncio.run(lookup(args.identifier))
    except Exception as exc:
        import traceback
        print(json.dumps({"passed": False, "error_type": type(exc).__name__,
                          "frames": [{"function": frame.name, "line": frame.lineno}
                                     for frame in traceback.extract_tb(exc.__traceback__)]}))
        return 1
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
