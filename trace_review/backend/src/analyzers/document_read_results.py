"""Section and subsection read results, in both recorded shapes.

TraceReview reads Langfuse observations. A document read there is the model
view of the result (the ``function_call_output`` the model received), never the
canonical stored result, which stays in the application's run history.

Two model views exist, and traces from both must stay readable:

* ``joined_content`` (before the numbered-passage release): the section is one
  ``content`` string, with ``section_title`` (or ``parent_section`` and
  ``subsection``), ``chunk_count`` and paging fields.
* ``numbered_passages`` (after it): the section lists ``passages``. Each
  passage's ``text`` is numbered lines ``[span_index] sentence``; a sentence's
  own line breaks are kept and its later lines are indented four spaces, so
  only a real span number starts a line.

The span numbers are reading aids added by the application, not paper text.
Anything that looks for citation numbers must read the decoded sentences, never
the numbered lines.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Dict, List, Optional

JOINED_CONTENT_SHAPE = "joined_content"
NUMBERED_PASSAGES_SHAPE = "numbered_passages"

SECTION_READ_RESULT_KEYS = {"read_section": "section", "read_subsection": "subsection"}

# Must match the application's model view renderer
# (backend/src/lib/openai_agents/document_result_view.py).
_CONTINUATION_INDENT = "    "
_PREVIEW_CHARS = 500


class UndecodableReadError(ValueError):
    """A recorded document read whose output TraceReview cannot decode."""


def decode_tool_output(output: Any) -> Any:
    """A recorded tool output as JSON, or as the SDK's repr of a returned dictionary.

    The Agents SDK sends a tool's returned dictionary to the model as its Python
    repr, so document reads are recorded that way; agent answers are JSON text.
    Anything else raises UndecodableReadError; callers decide whether that is
    worth reporting (a document read) or ordinary (free-text answers).
    """
    if not isinstance(output, str):
        raise UndecodableReadError(f"output is {type(output).__name__}, not text")
    try:
        return json.loads(output)
    except (json.JSONDecodeError, RecursionError):
        pass
    if not output.lstrip().startswith("{"):
        raise UndecodableReadError("output is neither JSON nor a dictionary repr")
    try:
        return ast.literal_eval(output.strip())
    except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError) as exc:
        raise UndecodableReadError(
            f"dictionary repr could not be read ({type(exc).__name__})"
        ) from exc


def section_read_shape(section: Any) -> Optional[str]:
    """Which recorded shape a section or subsection result has, if either."""
    if not isinstance(section, dict):
        return None
    if isinstance(section.get("passages"), list):
        return NUMBERED_PASSAGES_SHAPE
    if isinstance(section.get("content"), str):
        return JOINED_CONTENT_SHAPE
    return None


def numbered_view_spans(text: str) -> List[str]:
    """Exact sentence texts of one passage's numbered lines, without the numbers.

    This reverses the application's rendering exactly. A line that is neither a
    numbered line nor an indented continuation means the text is not a numbered
    view, and is reported rather than guessed at.
    """
    if text == "":
        # A passage with no sentences is rendered as empty text.
        return []
    spans: List[str] = []
    for line in text.split("\n"):
        if line.startswith(_CONTINUATION_INDENT):
            if not spans:
                raise UndecodableReadError("Numbered passage text starts with a continuation line")
            spans[-1] += "\n" + line[len(_CONTINUATION_INDENT):]
            continue
        number, separator, sentence = line.partition("] ")
        if not line.startswith("[") or not separator or not number[1:].isdigit():
            raise UndecodableReadError("Numbered passage text has a line without a span number")
        spans.append(sentence)
    return spans


def passage_source_text(passage: Dict[str, Any]) -> str:
    """A numbered passage's paper text, one sentence per line."""
    text = passage.get("text")
    if not isinstance(text, str):
        raise UndecodableReadError("Numbered passage has no text")
    return "\n".join(numbered_view_spans(text))


def section_read_display(result_key: str, section: Any) -> Optional[Dict[str, Any]]:
    """Display fields for one section/subsection read, from either shape."""
    shape = section_read_shape(section)
    if shape == NUMBERED_PASSAGES_SHAPE:
        return _numbered_display(result_key, section)
    if shape == JOINED_CONTENT_SHAPE:
        return _joined_display(result_key, section)
    return None


def _headings(result_key: str, section: Dict[str, Any], section_name: Any) -> Dict[str, Any]:
    if result_key == "subsection":
        return {"parent_section": section_name, "subsection": section.get("subsection")}
    return {"section_title": section_name}


def _joined_display(result_key: str, section: Dict[str, Any]) -> Dict[str, Any]:
    content = section["content"]
    name = section.get("parent_section") if result_key == "subsection" else section.get("section_title")
    return {
        "shape": JOINED_CONTENT_SHAPE,
        **_headings(result_key, section, name),
        "page_numbers": section.get("page_numbers") or [],
        "chunk_count": section.get("chunk_count"),
        "content_preview": content[:_PREVIEW_CHARS],
        "full_content": content,
    }


def _numbered_display(result_key: str, section: Dict[str, Any]) -> Dict[str, Any]:
    passages = [passage for passage in section["passages"] if isinstance(passage, dict)]
    # Shown as the model saw it, so a reviewer can follow the span numbers it used.
    blocks = []
    for passage in passages:
        label = f"Passage {passage.get('chunk_index')}"
        if passage.get("page_number") is not None:
            label += f", page {passage['page_number']}"
        blocks.append(f"{label}\n{passage.get('text') or ''}")
    full_content = "\n\n".join(blocks)
    display = {
        "shape": NUMBERED_PASSAGES_SHAPE,
        **_headings(result_key, section, section.get("requested_section")),
        "matched_sections": section.get("matched_sections") or [],
        "page_numbers": section.get("page_numbers") or [],
        "chunk_count": len(passages),
        "total_chunk_count": section.get("total_chunk_count"),
        "content_preview": full_content[:_PREVIEW_CHARS],
        "full_content": full_content,
    }
    if result_key == "subsection":
        display["subsection"] = section.get("requested_subsection")
        display["matched_subsections"] = section.get("matched_subsections") or []
    for key in ("text_contains", "matched_chunk_count"):
        if section.get(key) is not None:
            display[key] = section[key]
    return display
