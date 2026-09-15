"""Bounded, read-only report: python -m src.services.cost_report_cli --help."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .cost_report import build_report, report_csv, utc_time


def fetch_window(extractor, start: str, end: str, max_requests: int, page_limit: int):
    """Discover by call timestamps, then fetch ancestry within the same budget."""
    from .trace_extractor import OBSERVATION_FIELDS
    from ..config import get_langfuse_request_timeout_seconds

    filters = [
        {"type": "datetime", "column": "startTime", "operator": ">=", "value": utc_time(start).isoformat()},
        {"type": "datetime", "column": "startTime", "operator": "<", "value": utc_time(end).isoformat()},
    ]
    cursor = None
    seen = set()
    trace_ids = set()
    requests = 0
    complete = False
    discovered = {}
    while requests < max_requests:
        response = extractor.client.api.observations.get_many(
            fields=OBSERVATION_FIELDS.replace(",io", ""), limit=page_limit,
            cursor=cursor, filter=json.dumps(filters),
            request_options={"timeout_in_seconds": get_langfuse_request_timeout_seconds()},
        )
        requests += 1
        for item in response.data or []:
            observation = extractor._normalize_v2_observation(item)
            trace_id = observation.get("traceId")
            if trace_id:
                trace_ids.add(trace_id)
                discovered.setdefault(trace_id, []).append(observation)
        cursor = getattr(response.meta, "cursor", None) if response.meta else None
        if not cursor:
            complete = True
            break
        if cursor in seen or not response.data:
            raise RuntimeError("Invalid Langfuse cost-report pagination")
        seen.add(cursor)
    traces = []
    for trace_id in sorted(trace_ids):
        if requests < max_requests:
            observations, used, done = extractor._get_observations_bounded(
                trace_id, fields=OBSERVATION_FIELDS.replace(",io", ""),
                max_requests=max_requests - requests,
            )
            requests += used
            complete = complete and done
            # Keep the date-window events even if ancestry fetch was truncated.
            by_id = {o.get("id"): o for o in [*discovered[trace_id], *observations]}
            observations = list(by_id.values())
        else:
            observations = discovered[trace_id]
            complete = False
        traces.append({"trace_id": trace_id, "observations": observations})
    return traces, {"complete": complete, "requests": requests, "trace_count": len(trace_ids)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Recorded AI Curation model cost, excluding other services")
    parser.add_argument("--start", required=True, help="Inclusive timestamp with UTC offset")
    parser.add_argument("--end", required=True, help="Exclusive timestamp with UTC offset")
    parser.add_argument("--input", type=Path, help="Offline JSON {traces: [...], source_complete: bool}")
    parser.add_argument("--source", choices=("remote", "local"), default="remote")
    parser.add_argument("--group-by", default="agent_id,agent_name")
    parser.add_argument("--filter", action="append", default=[], metavar="DIMENSION=VALUE")
    parser.add_argument("--paper", nargs=2, metavar=("NAMESPACE", "ID"))
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    parser.add_argument("--organization-cost", help="Comparable USD amount; residual is not forced to zero")
    parser.add_argument("--model-definitions", type=Path, help="Explicit Langfuse Models API export for labeled retrospective estimates")
    parser.add_argument("--reconstruct-all", action="store_true", help="Re-estimate even stored costs; retain originals on each event")
    parser.add_argument("--assume-service-tier", help="Explicit retrospective assumption ONLY where tier was not recorded")
    parser.add_argument("--max-requests", type=int, default=int(os.getenv("COST_REPORT_MAX_REQUESTS", "200")))
    parser.add_argument("--page-limit", type=int, default=int(os.getenv("COST_REPORT_PAGE_LIMIT", "1000")))
    args = parser.parse_args(argv)
    if (args.reconstruct_all or args.assume_service_tier) and not args.model_definitions:
        parser.error("retrospective reconstruction/assumptions require --model-definitions")
    if args.max_requests < 1 or args.page_limit < 1:
        parser.error("request and page limits must be positive")
    if utc_time(args.end) <= utc_time(args.start):
        parser.error("end must be after start")
    filters = dict(item.split("=", 1) for item in args.filter)
    if args.paper:
        filters["paper"] = json.dumps(args.paper, separators=(",", ":"))
    if args.input:
        payload = json.loads(args.input.read_text())
        traces = payload["traces"]
        source = {"complete": payload.get("source_complete", False), "kind": "offline"}
    else:
        from .trace_extractor import TraceExtractor
        extractor = TraceExtractor(args.source)
        traces, source = fetch_window(extractor, args.start, args.end, args.max_requests, args.page_limit)
    report = build_report(traces, start=args.start, end=args.end,
                          group_by=args.group_by.split(","), filters=filters,
                          source_complete=source["complete"], organization_cost=args.organization_cost,
                          model_definitions=json.loads(args.model_definitions.read_text()) if args.model_definitions else None,
                          reconstruct_all=args.reconstruct_all, assumed_service_tier=args.assume_service_tier)
    report["source"] = source
    if report["totals"]["unpriced_calls"]:
        print(f"Cost coverage incomplete: {report['totals']['unpriced_calls']} unpriced calls; "
              f"{report['totals']['missing_usage_calls']} missing usage.", file=sys.stderr)
    print(report_csv(report) if args.format == "csv" else json.dumps(report, indent=2, default=str))
    return 0 if report["source_complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
