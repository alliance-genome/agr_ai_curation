"""Read-time PDF search text for table-row evidence fragments.

A table-row quote is stored as its Markdown row (``| XS1 | 3 | 5 | 4.0 |``), but a
PDF's text layer has no pipes, so the viewer searches the row's cell text instead.
When evidence is served, every source fragment whose stored ``span_type`` is
``table_row`` gets a ``search_text`` field next to it, computed with the one shared
table-row splitter. The field is never stored and never replaces the quote; nothing
reads the quote's look to decide (the stored span type alone decides).
"""

from __future__ import annotations

from typing import Any

TABLE_ROW_SPAN_TYPE = "table_row"
SEARCH_TEXT_FIELD = "search_text"


def _fragment_with_search_text(fragment: Any) -> Any:
    if not isinstance(fragment, dict):
        return fragment
    served = {key: with_fragment_search_texts(value) for key, value in fragment.items()}
    served.pop(SEARCH_TEXT_FIELD, None)
    text = fragment.get("text")
    if fragment.get("span_type") == TABLE_ROW_SPAN_TYPE and isinstance(text, str):
        # Imported here: the pipeline package imports modules that serve evidence.
        from src.lib.pipeline.table_chunking import table_row_search_text

        search_text = table_row_search_text(text)
        if search_text:
            served[SEARCH_TEXT_FIELD] = search_text
    return served


def with_fragment_search_texts(value: Any) -> Any:
    """Return a copy of a served payload with ``search_text`` on table-row fragments.

    Walks dicts and lists; every ``source_fragments`` list found anywhere is served
    with the field. A table-row fragment with no text gets no field, so the viewer
    searches the exact quote, as for evidence served without the field. The input
    is never mutated, so the stored payload stays as it was.
    """

    if isinstance(value, list):
        return [with_fragment_search_texts(item) for item in value]
    if not isinstance(value, dict):
        return value
    served: dict[str, Any] = {}
    for key, item in value.items():
        if key == "source_fragments" and isinstance(item, list):
            served[key] = [_fragment_with_search_text(fragment) for fragment in item]
        else:
            served[key] = with_fragment_search_texts(item)
    return served


__all__ = ["SEARCH_TEXT_FIELD", "TABLE_ROW_SPAN_TYPE", "with_fragment_search_texts"]
