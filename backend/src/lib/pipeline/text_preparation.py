"""The version of how imported text is cleaned and cut into pieces.

Benchmarks record it on every frozen paper they prepare, so a benchmark's
details can say which way its inputs were cut. Change the value whenever the
text clean-up (``markdown_plain_text``), table cutting (``table_chunking``),
quotable-line rules (``evidence_spans``) or the piece sizes change.

Version 1 is everything before release 0.12.0; it was never recorded.
Version 2 is 0.12.0: literal characters kept (P1), whole-row table pieces (P3)
and one quotable line per table row (P4), all of KANBAN-1976.
"""

TEXT_PREPARATION_VERSION = "2"
