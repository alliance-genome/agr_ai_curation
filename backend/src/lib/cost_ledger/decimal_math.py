"""Exact addition for validated finite, nonnegative recorded charges."""

from decimal import Decimal, localcontext


def add_exact(left: Decimal, right: Decimal) -> Decimal:
    # Size precision from decimal positions so a tiny charge is never rounded
    # out of a larger total by the process-wide Decimal context.
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted()) - min(
            int(left.as_tuple().exponent), int(right.as_tuple().exponent),
        ) + 2
        return left + right
