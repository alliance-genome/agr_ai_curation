"""Section and subsection reads recorded before and after the numbered-passage release.

TraceReview reads Langfuse observations, which hold the model view of a read:
the old view joined a section into one ``content`` string, the new view lists
``passages`` whose ``text`` is numbered lines ``[span_index] sentence``. Both
shapes must stay readable, and span numbers must never be read as citations.
"""

import ast
import json
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock

from src.analyzers.document_read_results import (
    JOINED_CONTENT_SHAPE,
    NUMBERED_PASSAGES_SHAPE,
    numbered_view_spans,
    section_read_shape,
)
from src.analyzers.pdf_citations import PDFCitationsAnalyzer
from src.analyzers.tool_calls import ToolCallAnalyzer, ToolResultParser


def _numbered_section(**overrides):
    section = {
        "requested_section": "Methods",
        "matched_sections": ["Materials and Methods"],
        "page_numbers": [3, 4],
        "total_chunk_count": 2,
        "passages": [
            {
                "chunk_index": 4,
                "page_number": 3,
                "section": "Materials and Methods",
                "text": "[0] Flies were raised at 25 C.\n[1] Larvae were staged as described [2].",
            },
            {
                "chunk_index": 5,
                "page_number": 4,
                "section": "Materials and Methods",
                "text": "[0] Primers used:\n    GAL4-F and GAL4-R.",
            },
        ],
    }
    section.update(overrides)
    return section


def _joined_section():
    return {
        "section_title": "Methods",
        "page_numbers": [3, 4],
        "content": "Flies were raised at 25 C.\n\nLarvae were staged as described [3].",
        "chunk_count": 2,
        "returned_chunk_count": 2,
        "total_chunk_count": 2,
        "offset": 0,
        "next_offset": None,
        "truncated": False,
        "source_chunks": [{"chunk_index": 4, "page_number": 3, "char_count": 26}],
        "content_sha256": "0" * 64,
    }


def _read_observation(tool_name, arguments, output, *, call_id="call-read"):
    return [
        {
            "id": f"gen-{call_id}",
            "type": "GENERATION",
            "name": "OpenAI-generation",
            "startTime": "2026-11-17T00:00:03Z",
            "model": "gpt-5",
            "input": [
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": tool_name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                },
                {"type": "function_call_output", "call_id": call_id, "output": output},
            ],
            "output": {},
        }
    ]


def _with_specialist_answer(observations):
    """Citation diagnostics are reported for traces where the PDF specialist answered."""
    return observations + _read_observation(
        "ask_pdf_extraction_specialist", {"query": "methods"},
        json.dumps({"answer": "The specialist read the paper."}), call_id="call-pdf",
    )


def _failing_only_for(bad_output, error):
    """literal_eval that raises ``error`` for one recorded output and reads the rest."""
    real_literal_eval = ast.literal_eval

    def literal_eval(text):
        if text == bad_output.strip():
            raise error
        return real_literal_eval(text)

    return literal_eval


class NumberedViewSpanTests(unittest.TestCase):
    def test_numbered_lines_decode_to_exact_span_texts(self):
        self.assertEqual(
            numbered_view_spans("[0] Primers\n    [1] GAL4-F\n    [2] GAL4-R\n[1] Flies were kept."),
            ["Primers\n[1] GAL4-F\n[2] GAL4-R", "Flies were kept."],
        )

    def test_a_passage_with_no_sentences_decodes_to_no_spans(self):
        # The application renders a passage whose text has no sentences (for
        # example whitespace only) as an empty string.
        self.assertEqual(numbered_view_spans(""), [])

    def test_a_line_that_is_neither_numbered_nor_a_continuation_is_rejected(self):
        for text in ("Flies were kept.", "    continuation first", "[x] not a number", "[3]no space"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                numbered_view_spans(text)

    def test_shape_is_chosen_from_the_result_fields(self):
        self.assertEqual(section_read_shape(_numbered_section()), NUMBERED_PASSAGES_SHAPE)
        self.assertEqual(section_read_shape(_joined_section()), JOINED_CONTENT_SHAPE)
        self.assertIsNone(section_read_shape({"section_title": "Methods"}))


class SectionReadToolResultTests(unittest.TestCase):
    def test_numbered_section_read_is_parsed_from_the_sdk_repr(self):
        payload = {"summary": "Read 2 passages from Methods.", "section": _numbered_section()}

        result = ToolResultParser.parse(str(payload), tool_name="read_section")

        section = result["parsed"]["section"]
        self.assertEqual(result["parse_status"], "full")
        self.assertEqual(section["shape"], NUMBERED_PASSAGES_SHAPE)
        self.assertEqual(section["section_title"], "Methods")
        self.assertEqual(section["matched_sections"], ["Materials and Methods"])
        self.assertEqual(section["page_numbers"], [3, 4])
        self.assertEqual(section["chunk_count"], 2)
        self.assertEqual(section["total_chunk_count"], 2)
        self.assertIn("[1] Larvae were staged as described [2].", section["full_content"])
        self.assertIn("Section: Methods", result["summary"])
        self.assertIn("2 chunks", result["summary"])

    def test_joined_section_read_from_before_the_release_is_still_parsed(self):
        payload = {"summary": "Read Methods.", "section": _joined_section()}

        result = ToolResultParser.parse(json.dumps(payload), tool_name="read_section")

        section = result["parsed"]["section"]
        self.assertEqual(section["shape"], JOINED_CONTENT_SHAPE)
        self.assertEqual(section["section_title"], "Methods")
        self.assertEqual(section["chunk_count"], 2)
        self.assertEqual(section["full_content"], _joined_section()["content"])
        self.assertIn("Section: Methods", result["summary"])

    def test_numbered_subsection_read_names_both_requested_headings(self):
        subsection = _numbered_section(
            requested_subsection="Fly strains", matched_subsections=["Fly strains and genetics"]
        )
        payload = {"summary": "Read 2 passages.", "subsection": subsection}

        result = ToolResultParser.parse(str(payload), tool_name="read_subsection")

        parsed = result["parsed"]["subsection"]
        self.assertEqual(parsed["shape"], NUMBERED_PASSAGES_SHAPE)
        self.assertEqual(parsed["parent_section"], "Methods")
        self.assertEqual(parsed["subsection"], "Fly strains")
        self.assertEqual(parsed["matched_subsections"], ["Fly strains and genetics"])
        self.assertIn("Subsection: Methods > Fly strains", result["summary"])

    def test_joined_subsection_read_from_before_the_release_is_still_parsed(self):
        old = {
            "parent_section": "Methods",
            "subsection": "Fly strains",
            "page_numbers": [3],
            "content": "w1118 flies were used.",
            "chunk_count": 1,
            "returned_chunk_count": 1,
            "total_chunk_count": 1,
            "offset": 0,
            "truncated": False,
        }

        result = ToolResultParser.parse(json.dumps({"summary": "Read.", "subsection": old}),
                                        tool_name="read_subsection")

        parsed = result["parsed"]["subsection"]
        self.assertEqual(parsed["shape"], JOINED_CONTENT_SHAPE)
        self.assertEqual(parsed["parent_section"], "Methods")
        self.assertEqual(parsed["full_content"], "w1118 flies were used.")

    def test_only_section_reading_tools_get_section_fields(self):
        payload = str({"summary": "Read Methods.", "section": _joined_section()})

        read = ToolResultParser.parse(payload, tool_name="read_section")
        other = ToolResultParser.parse(payload, tool_name="inspect_flow")

        self.assertEqual(read["parsed"]["section"]["shape"], JOINED_CONTENT_SHAPE)
        self.assertNotIn("section", other["parsed"])

    def test_tool_call_analysis_passes_the_tool_name_to_the_parser(self):
        payload = {"summary": "Read 2 passages from Methods.", "section": _numbered_section()}
        observations = _read_observation("read_section", {"section_name": "Methods"}, str(payload))

        call = ToolCallAnalyzer.extract_tool_calls(observations)["tool_calls"][0]

        self.assertEqual(call["tool_result"]["parsed"]["section"]["shape"], NUMBERED_PASSAGES_SHAPE)


class SectionReadCitationTests(unittest.TestCase):
    def test_span_numbers_of_a_numbered_read_are_not_citation_markers(self):
        payload = {"summary": "Read 2 passages from Methods.", "section": _numbered_section()}
        observations = _with_specialist_answer(
            _read_observation("read_section", {"section_name": "Methods"}, str(payload)))

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertEqual(diagnostics["marker_numbers"], [2])
        self.assertEqual(diagnostics["marker_count"], 1)

    def test_span_numbers_of_a_numbered_reference_list_are_not_bibliography_entries(self):
        references = _numbered_section(
            requested_section="References",
            matched_sections=["References"],
            passages=[
                {
                    "chunk_index": 40,
                    "page_number": 12,
                    "section": "References",
                    "text": "[0] References\n[1] 7. Smith J. Hedgehog signalling.\n[2] 9. Jones K. Wingless.",
                }
            ],
        )
        methods = _numbered_section()
        observations = _with_specialist_answer(
            _read_observation("read_section", {"section_name": "References"},
                              str({"summary": "Read.", "section": references}))
            + _read_observation("read_section", {"section_name": "Methods"},
                                str({"summary": "Read.", "section": methods}), call_id="call-read-2")
        )

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertTrue(diagnostics["bibliography_found"])
        self.assertEqual(diagnostics["bibliography_entry_numbers"], [7, 9])
        self.assertEqual(diagnostics["marker_numbers"], [2])

    def test_joined_section_read_from_before_the_release_is_still_collected(self):
        # Real traces hold the SDK's repr of the returned dictionary, not JSON.
        payload = {"summary": "Read Methods.", "section": _joined_section()}
        observations = _with_specialist_answer(
            _read_observation("read_section", {"section_name": "Methods"}, str(payload)))

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertEqual(diagnostics["marker_numbers"], [3])

    def test_a_passage_with_no_sentences_is_read_as_empty(self):
        section = _numbered_section(passages=[
            {"chunk_index": 3, "page_number": 2, "section": "Methods", "text": ""},
            *_numbered_section()["passages"],
        ])
        observations = _with_specialist_answer(_read_observation(
            "read_section", {"section_name": "Methods"}, str({"summary": "Read.", "section": section})))

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertEqual(diagnostics["marker_numbers"], [2])
        self.assertEqual(diagnostics["undecodable_reads"], [])

    def test_an_undecodable_read_is_reported_and_the_rest_of_the_trace_is_analysed(self):
        broken_passage = str({"summary": "Read.", "section": _numbered_section(
            passages=[{"chunk_index": 4, "section": "Methods", "text": "no numbers here"}])})
        cases = {
            "bad repr": ("{'summary': 'Read.', 'section': ", None),
            "unhashable key": ("{[1]: 2}", None),
            "not a dictionary": ("Document read failed.", None),
            "unnumbered line": (broken_passage, None),
            "recursion": ("{'section': {}}", RecursionError("too deep")),
            "memory": ("{'section': {}}", MemoryError()),
        }
        for name, (output, literal_error) in cases.items():
            with self.subTest(name=name):
                observations = (
                    _read_observation("read_section", {"section_name": "Methods"}, output, call_id="call-bad")
                    + _with_specialist_answer(_read_observation(
                        "read_section", {"section_name": "Methods"},
                        str({"summary": "Read.", "section": _numbered_section()})))
                )
                patch = (mock.patch("src.analyzers.document_read_results.ast.literal_eval",
                                    side_effect=_failing_only_for(output, literal_error))
                         if literal_error else nullcontext())
                with patch:
                    data = PDFCitationsAnalyzer.analyze(observations)

                diagnostics = data["citation_number_diagnostics"]
                self.assertTrue(data["found"])
                self.assertEqual(diagnostics["marker_numbers"], [2])
                self.assertEqual(len(diagnostics["undecodable_reads"]), 1)
                reported = diagnostics["undecodable_reads"][0]
                self.assertEqual((reported["call_id"], reported["tool_name"]), ("call-bad", "read_section"))
                self.assertTrue(reported["reason"])

    def test_an_undecodable_read_is_reported_even_when_nothing_else_was_found(self):
        observations = _read_observation("read_section", {"section_name": "Methods"}, "{[1]: 2}")

        data = PDFCitationsAnalyzer.analyze(observations)

        self.assertFalse(data["found"])
        self.assertEqual(
            [(read["call_id"], read["tool_name"]) for read in data["citation_number_diagnostics"]["undecodable_reads"]],
            [("call-read", "read_section")],
        )

    def test_a_specialist_answer_in_plain_text_is_not_an_undecodable_read(self):
        observations = _read_observation(
            "ask_pdf_extraction_specialist", {"query": "methods"}, "The paper uses w1118 flies.")

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertEqual(diagnostics["undecodable_reads"], [])


_ROUND_TRIP_FIXTURE = Path(__file__).parent / "fixtures" / "numbered_document_view.json"


class ApplicationViewRoundTripTests(unittest.TestCase):
    """The application's real rendering, decoded back to the sentences it numbered.

    The fixture is written by the application's own document_result_view and
    checked against it by the backend unit test
    backend/tests/unit/lib/packages/alliance/test_numbered_view_fixture.py.
    """

    def test_the_applications_numbered_view_decodes_to_its_sentences(self):
        fixture = json.loads(_ROUND_TRIP_FIXTURE.read_text(encoding="utf-8"))
        observations = _read_observation("read_section", {"section_name": "Methods"},
                                         fixture["model_output"])

        call = ToolCallAnalyzer.extract_tool_calls(observations)["tool_calls"][0]
        section = call["tool_result"]["parsed"]["json_data"]["section"]
        decoded = [numbered_view_spans(passage["text"]) for passage in section["passages"]]

        self.assertEqual(decoded, fixture["sentences"])


class AnalysisCacheVersionTests(unittest.TestCase):
    def test_analyses_cached_before_the_numbered_read_change_are_recomputed(self):
        from src.api import claude, traces

        stale = {"analyzer_schema_version": "extraction_timeline_analyzer.v1"}
        self.assertFalse(traces._cache_schema_is_current(stale))
        self.assertFalse(claude._cache_schema_is_current(stale))


if __name__ == "__main__":
    unittest.main()
