"""
PDF Citations Analyzer
Extracts PDF citations with page numbers, relevance scores, and extracted content
"""
import json
import re
from collections import Counter
from typing import Dict, List, Set


_PDF_EVIDENCE_TOOL_NAMES = {"search_document", "read_section", "read_subsection"}
_REFERENCE_SECTION_RE = re.compile(
    r"\b(references|bibliography|literature\s+cited|works\s+cited)\b",
    re.IGNORECASE,
)
_REFERENCE_HEADING_RE = re.compile(
    r"(?im)(^|\n)\s*(references|bibliography|literature\s+cited|works\s+cited)\s*(?:\n|$)"
)
_BRACKETED_MARKER_RE = re.compile(
    r"(?<![\w])\[(\d{1,4}(?:\s*(?:,|;|-|–|—)\s*\d{1,4})*)\]"
)
_PARENTHETICAL_MARKER_RE = re.compile(
    r"(?<![\w])\((\d{1,3}(?:\s*(?:,|;|-|–|—)\s*\d{1,3})*)\)"
)
_HTML_SUP_MARKER_RE = re.compile(r"<sup[^>]*>\s*([^<]+?)\s*</sup>", re.IGNORECASE)
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPERSCRIPT_MARKER_RE = re.compile(
    rf"([{_SUPERSCRIPT_DIGITS}]+(?:\s*(?:,|;|-|–|—)\s*[{_SUPERSCRIPT_DIGITS}]+)*)"
)
_BIBLIOGRAPHY_ENTRY_RE = re.compile(
    r"(?m)(?:^|\n)\s*(?:\[(\d{1,4})\]|\((\d{1,4})\)|(\d{1,4})[.)])\s+\S"
)
_SUPERSCRIPT_TRANSLATION = str.maketrans(
    {
        "⁰": "0",
        "¹": "1",
        "²": "2",
        "³": "3",
        "⁴": "4",
        "⁵": "5",
        "⁶": "6",
        "⁷": "7",
        "⁸": "8",
        "⁹": "9",
    }
)


class PDFCitationsAnalyzer:
    """Analyzes PDF citations from PDF Specialist agent observations"""

    @classmethod
    def _empty_result(cls) -> Dict:
        return {
            "found": False,
            "total_citations": 0,
            "search_queries": [],
            "extracted_content": "",
            "citations": [],
            "total_chunks_found": 0,
            "tool_calls": [],
            "citation_number_diagnostics": cls._build_citation_number_diagnostics([], []),
        }

    @classmethod
    def analyze(cls, observations: List[Dict]) -> Dict:
        """
        Extract PDF citations from OpenAI Agents SDK format observations.

        Citations are found in function_call_output items within GENERATION input.
        The output is a JSON string containing an Answer model with citations.

        Returns:
            {
                "found": bool,
                "total_citations": int,
                "search_queries": List[str],
                "extracted_content": str,
                "citations": List[{
                    "chunk_id": str,
                    "section_title": str,
                    "page_number": int,
                    "source": str
                }],
                "total_chunks_found": int,
                "tool_calls": List[Dict]  # Metadata about tool calls
            }
        """
        all_citations = []
        all_answers = []
        diagnostic_texts = []
        bibliography_texts = []
        tool_calls_metadata = []

        for obs in observations:
            if obs.get("type") != "GENERATION":
                continue

            obs_input = obs.get("input")
            if not isinstance(obs_input, list):
                continue

            # Look for function_call_output items (responses from PDF specialist)
            for item in obs_input:
                if not isinstance(item, dict):
                    continue

                if item.get("type") == "function_call_output":
                    output_str = item.get("output", "")
                    call_id = item.get("call_id", "")

                    # Find the corresponding function_call to get the tool name and query
                    tool_name = "unknown"
                    query = ""
                    for fc_item in obs_input:
                        if (isinstance(fc_item, dict) and
                            fc_item.get("type") == "function_call" and
                            fc_item.get("call_id") == call_id):
                            tool_name = fc_item.get("name", "unknown")
                            # Parse arguments to get query
                            args_str = fc_item.get("arguments", "{}")
                            try:
                                args = json.loads(args_str)
                                query = args.get("query", "")
                            except (json.JSONDecodeError, TypeError):
                                pass
                            break

                    # Only process PDF specialist outputs
                    if not cls._is_pdf_related_tool(tool_name):
                        continue

                    # Parse the output JSON
                    try:
                        output_data = json.loads(output_str)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(output_data, dict):
                        continue

                    # Extract citations
                    citations = output_data.get("citations", [])
                    if not isinstance(citations, list):
                        raise TypeError("PDF citation output field 'citations' must be a list")
                    if citations:
                        all_citations.extend(citations)

                    # Extract answer text
                    answer = output_data.get("answer", "")
                    if answer:
                        all_answers.append(answer)
                    extracted_content = output_data.get("extracted_content", "")
                    if extracted_content:
                        all_answers.append(extracted_content)

                    cls._collect_diagnostic_texts(
                        output_data,
                        diagnostic_texts,
                        bibliography_texts,
                    )

                    # Store tool call metadata
                    tool_calls_metadata.append({
                        "tool_name": tool_name,
                        "query": query,
                        "citations_count": len(citations),
                        "call_id": call_id
                    })

        if not all_citations and not all_answers:
            return cls._empty_result()

        # Deduplicate citations by chunk_id
        seen_chunks = set()
        unique_citations = []
        for cit in all_citations:
            if not isinstance(cit, dict):
                continue
            chunk_id = cit.get("chunk_id", "")
            if chunk_id and chunk_id not in seen_chunks:
                seen_chunks.add(chunk_id)
                unique_citations.append(cit)
            elif not chunk_id:
                unique_citations.append(cit)

        # Sort by page number
        unique_citations.sort(key=lambda c: c.get("page_number", 0))

        # Collect unique queries
        unique_queries = []
        seen_queries = set()
        for tc in tool_calls_metadata:
            q = tc.get("query", "")
            if q and q not in seen_queries:
                unique_queries.append(q)
                seen_queries.add(q)

        # Deduplicate answers (strip whitespace for comparison)
        unique_answers = []
        seen_answers = set()
        for answer in all_answers:
            answer_normalized = answer.strip()
            if answer_normalized and answer_normalized not in seen_answers:
                seen_answers.add(answer_normalized)
                unique_answers.append(answer)

        # Deduplicate tool calls by call_id
        unique_tool_calls = []
        seen_call_ids = set()
        for tc in tool_calls_metadata:
            call_id = tc.get("call_id", "")
            if call_id and call_id not in seen_call_ids:
                seen_call_ids.add(call_id)
                unique_tool_calls.append(tc)
            elif not call_id:
                # If no call_id, deduplicate by query
                query = tc.get("query", "")
                if query not in seen_queries:
                    unique_tool_calls.append(tc)

        return {
            "found": True,
            "total_citations": len(unique_citations),
            "search_queries": unique_queries,
            "extracted_content": "\n\n".join(unique_answers),
            "citations": unique_citations,
            "total_chunks_found": len(unique_citations),
            "tool_calls": unique_tool_calls,
            "citation_number_diagnostics": cls._build_citation_number_diagnostics(
                diagnostic_texts,
                bibliography_texts,
            )
        }

    @classmethod
    def _is_pdf_related_tool(cls, tool_name: str) -> bool:
        normalized_name = tool_name.lower()
        return "pdf" in normalized_name or normalized_name in _PDF_EVIDENCE_TOOL_NAMES

    @classmethod
    def _collect_diagnostic_texts(
        cls,
        output_data: Dict,
        diagnostic_texts: List[str],
        bibliography_texts: List[str],
    ) -> None:
        start_index = len(diagnostic_texts)

        cls._append_text(diagnostic_texts, output_data.get("answer"))
        cls._append_text(diagnostic_texts, output_data.get("extracted_content"))

        for citation in cls._iter_dicts(output_data.get("citations")):
            if cls._is_reference_section(citation.get("section_title")):
                cls._append_text(bibliography_texts, citation.get("section_title"))
                for key in ("text", "content", "content_preview"):
                    cls._append_text(bibliography_texts, citation.get(key))
            else:
                cls._append_text(diagnostic_texts, citation.get("text"))
                cls._append_text(diagnostic_texts, citation.get("content"))
                cls._append_text(diagnostic_texts, citation.get("content_preview"))

        for hit in cls._iter_dicts(output_data.get("hits")):
            if cls._is_reference_section(hit.get("section_title")):
                cls._append_text(bibliography_texts, hit.get("section_title"))
                cls._append_text(bibliography_texts, hit.get("content"))
                cls._append_text(bibliography_texts, hit.get("text"))
            else:
                cls._append_text(diagnostic_texts, hit.get("content"))
                cls._append_text(diagnostic_texts, hit.get("text"))

        cls._collect_section_text(output_data.get("section"), diagnostic_texts, bibliography_texts)
        cls._collect_section_text(output_data.get("subsection"), diagnostic_texts, bibliography_texts)

        for key in ("bibliography", "references", "reference_list"):
            raw_references = output_data.get(key)
            if isinstance(raw_references, str):
                cls._append_text(bibliography_texts, raw_references)
            elif isinstance(raw_references, list):
                for entry in raw_references:
                    if isinstance(entry, str):
                        cls._append_text(bibliography_texts, entry)
                    elif isinstance(entry, dict):
                        entry_text = " ".join(
                            str(value).strip()
                            for value in entry.values()
                            if isinstance(value, (str, int, float)) and str(value).strip()
                        )
                        cls._append_text(bibliography_texts, entry_text)

        for text in diagnostic_texts[start_index:]:
            heading_match = _REFERENCE_HEADING_RE.search(text)
            if heading_match:
                cls._append_text(bibliography_texts, text[heading_match.start():])

    @classmethod
    def _collect_section_text(
        cls,
        section: object,
        diagnostic_texts: List[str],
        bibliography_texts: List[str],
    ) -> None:
        if not isinstance(section, dict):
            return

        section_title = section.get("section_title")
        subsection = section.get("subsection")
        section_texts = [
            section[key]
            for key in ("content", "full_content", "content_preview")
            if isinstance(section.get(key), str) and section[key].strip()
        ]
        if cls._is_reference_section(section_title) or cls._is_reference_section(subsection):
            cls._append_text(bibliography_texts, section_title)
            cls._append_text(bibliography_texts, subsection)
            for text in section_texts:
                cls._append_text(bibliography_texts, text)
        else:
            for text in section_texts:
                cls._append_text(diagnostic_texts, text)

    @classmethod
    def _build_citation_number_diagnostics(
        cls,
        diagnostic_texts: List[str],
        bibliography_texts: List[str],
    ) -> Dict:
        marker_occurrences = cls._detect_marker_occurrences(
            cls._without_reference_sections(diagnostic_texts)
        )
        marker_numbers = sorted({occurrence["number"] for occurrence in marker_occurrences})
        marker_styles = sorted({occurrence["style"] for occurrence in marker_occurrences})
        bibliography_found = bool(bibliography_texts)
        bibliography_counts = cls._detect_bibliography_entry_counts(bibliography_texts)
        bibliography_numbers = sorted(bibliography_counts)
        ambiguous_numbers = sorted(
            number for number, count in bibliography_counts.items() if count > 1 and number in marker_numbers
        )
        missing_marker_numbers = sorted(set(marker_numbers) - set(bibliography_numbers))
        mapped_numbers = sorted(set(marker_numbers) & set(bibliography_numbers))

        if not marker_numbers:
            mapping_status = "no_markers"
        elif not bibliography_found:
            mapping_status = "missing_bibliography"
        elif ambiguous_numbers or (bibliography_found and not bibliography_numbers):
            mapping_status = "ambiguous"
        elif missing_marker_numbers:
            mapping_status = "missing_entries"
        else:
            mapping_status = "mapped"

        return {
            "markers_found": bool(marker_numbers),
            "marker_numbers": marker_numbers,
            "marker_count": len(marker_occurrences),
            "marker_styles": marker_styles,
            "bibliography_found": bibliography_found,
            "bibliography_entry_numbers": bibliography_numbers,
            "bibliography_entry_count": len(bibliography_numbers),
            "mapping_status": mapping_status,
            "mapped_numbers": mapped_numbers,
            "missing_marker_numbers": missing_marker_numbers,
            "ambiguous_marker_numbers": ambiguous_numbers,
        }

    @classmethod
    def _detect_marker_occurrences(cls, texts: List[str]) -> List[Dict]:
        occurrences = []
        seen_occurrences: Set[tuple] = set()

        for text_index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                continue

            for style, pattern in (
                ("bracketed", _BRACKETED_MARKER_RE),
                ("parenthetical", _PARENTHETICAL_MARKER_RE),
            ):
                for match in pattern.finditer(text):
                    cls._append_marker_occurrences(
                        occurrences,
                        seen_occurrences,
                        text_index,
                        style,
                        match.group(1),
                        match.start(),
                    )

            for match in _HTML_SUP_MARKER_RE.finditer(text):
                cls._append_marker_occurrences(
                    occurrences,
                    seen_occurrences,
                    text_index,
                    "html_superscript",
                    match.group(1),
                    match.start(),
                )

            for match in _SUPERSCRIPT_MARKER_RE.finditer(text):
                cls._append_marker_occurrences(
                    occurrences,
                    seen_occurrences,
                    text_index,
                    "unicode_superscript",
                    match.group(1).translate(_SUPERSCRIPT_TRANSLATION),
                    match.start(),
                )

        return occurrences

    @classmethod
    def _without_reference_sections(cls, texts: List[str]) -> List[str]:
        marker_texts = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                continue
            heading_match = _REFERENCE_HEADING_RE.search(text)
            if heading_match:
                text = text[:heading_match.start()]
            cls._append_text(marker_texts, text)
        return marker_texts

    @classmethod
    def _append_marker_occurrences(
        cls,
        occurrences: List[Dict],
        seen_occurrences: Set[tuple],
        text_index: int,
        style: str,
        raw_numbers: str,
        offset: int,
    ) -> None:
        for number in cls._expand_number_expression(raw_numbers):
            occurrence_key = (text_index, style, number, offset)
            if occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(occurrence_key)
            occurrences.append({"number": number, "style": style})

    @classmethod
    def _detect_bibliography_entry_counts(cls, bibliography_texts: List[str]) -> Counter:
        entry_counts: Counter = Counter()
        seen_texts = set()
        for text in bibliography_texts:
            if not isinstance(text, str) or not text.strip():
                continue
            normalized_text = text.strip()
            if normalized_text in seen_texts:
                continue
            seen_texts.add(normalized_text)
            for match in _BIBLIOGRAPHY_ENTRY_RE.finditer(text):
                raw_number = next(group for group in match.groups() if group)
                entry_counts[int(raw_number)] += 1
        return entry_counts

    @classmethod
    def _expand_number_expression(cls, raw_value: str) -> List[int]:
        normalized = raw_value.translate(_SUPERSCRIPT_TRANSLATION)
        normalized = normalized.replace("–", "-").replace("—", "-").replace(";", ",")
        numbers: List[int] = []

        for part in normalized.split(","):
            token = part.strip()
            if not token:
                continue

            if "-" in token:
                start_raw, end_raw = token.split("-", 1)
                if start_raw.strip().isdigit() and end_raw.strip().isdigit():
                    start = int(start_raw)
                    end = int(end_raw)
                    if 0 < start <= end and end - start <= 50:
                        numbers.extend(range(start, end + 1))
                continue

            if token.isdigit():
                number = int(token)
                if number > 0:
                    numbers.append(number)

        return numbers

    @classmethod
    def _iter_dicts(cls, value: object) -> List[Dict]:
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
        return []

    @classmethod
    def _append_text(cls, texts: List[str], value: object) -> None:
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())

    @classmethod
    def _is_reference_section(cls, section_title: object) -> bool:
        return isinstance(section_title, str) and bool(_REFERENCE_SECTION_RE.search(section_title))
