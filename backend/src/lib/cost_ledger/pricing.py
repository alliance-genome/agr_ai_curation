"""Durable, content-addressed reviewed Langfuse price snapshots.

Imports are explicit operator actions, never a background price scraper.
Usage remains solely in the canonical ledger. Repricing creates no money rows.
"""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re

from agr_cost_pricing import ALGORITHM_REVISION, estimate
from sqlalchemy.dialects.postgresql import insert

from src.models.sql.cost_ledger import CostPriceSnapshot


def _canonical_values(value):
    if isinstance(value, (Decimal, float)):
        return str(value)
    if isinstance(value, dict):
        return {key: _canonical_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_values(item) for item in value]
    return value


def canonical_snapshot(payload: dict) -> tuple[str, str]:
    if payload.get("schema_version") != 1 or payload.get("currency") != "USD":
        raise ValueError("Expected version 1 USD pricing catalog")
    if not isinstance(payload.get("source"), str) or not payload["source"].strip():
        raise ValueError("Pricing source provenance is required")
    captured = datetime.fromisoformat(payload["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise ValueError("Catalog capture timestamp requires timezone")
    providers = payload.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ValueError("Provider-scoped definitions are required")
    for provider, definitions in providers.items():
        if not isinstance(provider, str) or not provider.strip() or not isinstance(definitions, list):
            raise ValueError("Invalid provider definitions")
        for definition in definitions:
            if definition.get("unit") != "TOKENS" or not definition.get("id"):
                raise ValueError("Token model definition and stable ID required")
            re.compile(definition["matchPattern"])
            effective = datetime.fromisoformat(definition["startDate"].replace("Z", "+00:00"))
            if effective.tzinfo is None:
                raise ValueError("Price effective date requires timezone")
            tiers = definition.get("pricingTiers") or [{"prices": definition.get("prices") or {
                "input": definition.get("inputPrice"), "output": definition.get("outputPrice")}}]
            for tier in tiers:
                for value in tier.get("prices", {}).values():
                    if value is not None and (not Decimal(str(value)).is_finite() or Decimal(str(value)) < 0):
                        raise ValueError("Price must be finite and nonnegative")
    encoded = json.dumps(_canonical_values(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest(), encoded


def import_snapshot(db, payload: dict) -> str:
    identifier, encoded = canonical_snapshot(payload)
    db.execute(insert(CostPriceSnapshot).values(id=identifier, payload=encoded).on_conflict_do_nothing())
    if db.get(CostPriceSnapshot, identifier).payload != encoded:
        raise ValueError("Pricing content hash conflict")
    return identifier


def load_snapshot(db, identifier: str | None):
    if not identifier:
        return None
    row = db.get(CostPriceSnapshot, identifier)
    if row is None:
        raise LookupError("Pricing snapshot not found")
    payload = json.loads(row.payload, parse_float=Decimal)
    if canonical_snapshot(payload)[0] != identifier:
        raise ValueError("Pricing snapshot integrity failure")
    return payload


def value_usage(usage, *, provider, model, timestamp, snapshot):
    if snapshot is None:
        return {"estimate_unavailable_reason": "no_pricing_snapshot"}
    if not model:
        return {"estimate_unavailable_reason": "model_not_recorded"}
    event = {"usage_status": usage.status, "model": model, "timestamp": timestamp.isoformat(),
             "service_tier": "unknown", "usage": {**asdict(usage),
             "uncached_input_tokens": usage.uncached_input_tokens}}
    try:
        result = estimate(event, snapshot["providers"].get(provider, []))
    except (ValueError, KeyError, TypeError):
        return {"estimate_unavailable_reason": "invalid_or_ambiguous_price_definition"}
    return {**result, "currency": snapshot["currency"], "valuation_algorithm": ALGORITHM_REVISION}
