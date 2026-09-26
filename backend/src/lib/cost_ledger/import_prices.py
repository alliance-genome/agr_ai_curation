"""Import a reviewed provider-scoped Langfuse export, never rewrite usage.

Usage: python -m src.lib.cost_ledger.import_prices /path/reviewed-prices.json
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path

from src.lib.cost_ledger.pricing import import_snapshot
from src.lib.openai_agents.config import get_cost_price_import_max_bytes
from src.models.sql.database import SessionLocal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    args = parser.parse_args()
    with args.catalog.open("rb") as stream:
        raw = stream.read(get_cost_price_import_max_bytes() + 1)
    if len(raw) > get_cost_price_import_max_bytes():
        parser.error("Catalog exceeds COST_PRICE_IMPORT_MAX_BYTES")
    payload = json.loads(raw, parse_float=Decimal)
    with SessionLocal() as db:
        identifier = import_snapshot(db, payload)
        db.commit()
    print(json.dumps({"pricing_snapshot_id": identifier}))


if __name__ == "__main__":
    main()
