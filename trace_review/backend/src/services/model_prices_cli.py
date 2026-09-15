"""Supported Langfuse Models HTTP API; inspect by default, never rewrite traces.

The HTTP API avoids coupling operational model definitions to the older Python
SDK's condition enum. Prices must come from a reviewed upstream export, not code.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def api(method, path, payload=None):
    from ..config import get_langfuse_request_timeout_seconds
    auth = base64.b64encode((os.environ["LANGFUSE_PUBLIC_KEY"] + ":" + os.environ["LANGFUSE_SECRET_KEY"]).encode()).decode()
    request = Request(
        os.environ["LANGFUSE_HOST"].rstrip("/") + "/api/public/models" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method, headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=get_langfuse_request_timeout_seconds()) as response:
        return json.load(response)


def export_models():
    max_requests = int(os.getenv("COST_REPORT_MAX_REQUESTS", "200"))
    # The supported Models API protocol caps limit at 100.
    page_limit = min(100, int(os.getenv("COST_REPORT_PAGE_LIMIT", "1000")))
    if max_requests < 1 or page_limit < 1:
        raise ValueError("Cost report limits must be positive")
    result = []
    for page in range(1, max_requests + 1):
        response = api("GET", f"?limit={page_limit}&page={page}")
        result.extend(response["data"])
        if page >= response["meta"]["totalPages"]:
            return result
    raise RuntimeError("Model catalog exceeded COST_REPORT_MAX_REQUESTS")


def creation_payload(definition):
    return {key: definition[key] for key in (
        "modelName", "matchPattern", "startDate", "unit", "inputPrice",
        "outputPrice", "totalPrice", "pricingTiers", "tokenizerId", "tokenizerConfig",
    ) if definition.get(key) is not None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definitions", type=Path, help="Reviewed upstream definitions to install if absent")
    parser.add_argument("--model", action="append", default=[], help="Active model to check; repeat for the configured catalog")
    parser.add_argument("--at", required=True, help="Timestamp for effective-date matching")
    parser.add_argument("--apply", action="store_true", help="Explicitly create missing definitions; release-time action")
    args = parser.parse_args(argv)
    from .model_prices import matching_definition
    existing = export_models()
    supplied = json.loads(args.definitions.read_text()) if args.definitions else []
    pending = []
    missing = []
    for name in args.model:
        if matching_definition(existing, name, args.at):
            continue
        definition = matching_definition(supplied, name, args.at)
        if definition is None:
            missing.append(name)
        else:
            pending.append(creation_payload(definition))
    if args.apply:
        if missing:
            parser.error("No supplied price for active model(s): " + ", ".join(missing))
        for payload in pending:
            api("POST", "", payload)
        existing = export_models()
        if any(not matching_definition(existing, name, args.at) for name in args.model):
            raise RuntimeError("Model creation verification failed")
    print(json.dumps({"models": existing, "pending": pending, "unpriced_models": missing,
                      "applied": args.apply}, indent=2))
    return 2 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
