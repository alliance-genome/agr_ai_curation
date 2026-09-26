"""Consumer-independent aggregation of canonical ledger revisions."""

from collections import Counter
from dataclasses import fields
from decimal import Decimal
from itertools import groupby
from sqlalchemy.orm import Session
from src.lib.openai_agents.config import get_cost_ledger_read_page_size
from src.schemas.cost_ledger import KnownTokenTotal, RecordedChargeTotal
from .facts import RecordedCharge, TokenUsage, enrich_charge, enrich_usage
from .decimal_math import add_exact


def fold_fact_revisions(revisions):
    """One canonical fold for totals, drill-down and exported valuations."""
    usage, charge, last_revision = TokenUsage(), None, 0
    for revision in revisions:
        if revision is None:
            continue
        usage = enrich_usage(usage, TokenUsage(**{
            field.name: getattr(revision, field.name) for field in fields(TokenUsage)
        }))
        if revision.billed_amount is not None:
            charge = enrich_charge(charge, RecordedCharge(
                revision.billed_amount, revision.billed_unit, revision.billed_source,
            ))
        last_revision = max(last_revision, revision.revision)
    return usage, charge, last_revision


def summarize_fact_rows(session: Session, query) -> dict:
    """Aggregate unique, ordered attempt/revision rows without stored totals."""
    totals, known, amounts, charge_counts = Counter(), Counter(), {}, Counter()
    count = inconsistent = unknown_charge = 0
    # SQL distinct attempts avoids counting one billable call twice when it has
    # multiple verified source references. Server cursor bounds fetched rows.
    rows = session.execute(query.execution_options(yield_per=get_cost_ledger_read_page_size()))
    try:
        for _, revisions in groupby(rows, key=lambda row: row[0]):
            usage, charge, _ = fold_fact_revisions(revision for _, revision in revisions)
            count += 1
            inconsistent += bool(usage.issues)
            for field in fields(TokenUsage):
                value = getattr(usage, field.name)
                if value is not None:
                    totals[field.name] += value
                    known[field.name] += 1
            if charge is None:
                unknown_charge += 1
            else:
                key = (charge.unit, charge.source)
                amounts[key] = add_exact(amounts.get(key, Decimal(0)), charge.amount)
                charge_counts[key] += 1
    finally:
        rows.close()
    return dict(
        attempt_count=count,
        usage={field.name: KnownTokenTotal(
            known_total=totals[field.name] if known[field.name] else None,
            known_attempts=known[field.name], unknown_attempts=count - known[field.name],
        ) for field in fields(TokenUsage)},
        inconsistent_usage_attempts=inconsistent, unknown_charge_attempts=unknown_charge,
        recorded_charges=tuple(RecordedChargeTotal(unit=unit, source=source, amount=amount,
                                                 attempts=charge_counts[(unit, source)])
                               for (unit, source), amount in sorted(amounts.items())),
    )
