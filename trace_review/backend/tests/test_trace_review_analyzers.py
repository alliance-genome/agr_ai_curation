import json
import unittest

from src.analyzers.conversation import ConversationAnalyzer
from src.analyzers.pdf_citations import PDFCitationsAnalyzer
from src.analyzers.tool_calls import ToolCallAnalyzer
from src.analyzers.trace_summary import TraceSummaryAnalyzer
from src.utils.trace_output import extract_trace_response_text, is_trace_output_cacheable


class TraceReviewAnalyzerTests(unittest.TestCase):
    def _make_trace(self, output):
        return {
            "id": "trace-1234",
            "name": "chat: Example",
            "timestamp": "2026-03-26T00:00:00Z",
            "latency": 12.5,
            "input": {"message": "What genes are mentioned?"},
            "output": output,
            "metadata": {},
        }

    def _make_observations(self):
        return [
            {
                "id": "gen-1",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:01Z",
                "model": "gpt-4o",
                "input": [
                    {"role": "user", "content": "What genes are mentioned?"},
                    {
                        "type": "function_call",
                        "call_id": "call-search",
                        "name": "search_document",
                        "arguments": "{\"query\":\"gene symbols\"}",
                        "status": "completed",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-search",
                        "output": "{'summary':'Found 1 chunks','hits':[]}",
                    },
                ],
                "output": {},
            },
            {
                "id": "gen-2",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:02Z",
                "model": "gpt-4o",
                "input": [
                    {"role": "user", "content": "What genes are mentioned?"},
                    {
                        "type": "function_call",
                        "call_id": "call-search",
                        "name": "search_document",
                        "arguments": "{\"query\":\"gene symbols\"}",
                        "status": "completed",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-search",
                        "output": "{'summary':'Found 1 chunks','hits':[]}",
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-read",
                        "name": "read_section",
                        "arguments": "{\"section_name\":\"Methods\"}",
                        "status": "completed",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-read",
                        "output": "{'section': {'section_title': 'Methods'}}",
                    },
                ],
                "output": {},
            },
        ]

    def _make_pdf_observation(self, payload):
        return [
            {
                "id": "gen-pdf",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:03Z",
                "model": "gpt-4o",
                "input": [
                    {
                        "type": "function_call",
                        "call_id": "call-pdf",
                        "name": "ask_pdf_extraction_specialist",
                        "arguments": "{\"query\":\"methods citations\"}",
                        "status": "completed",
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call-pdf",
                        "output": json.dumps(payload),
                    },
                ],
                "output": {},
            }
        ]

    def test_tool_calls_extract_from_generation_inputs_and_dedupe_repeated_calls(self):
        data = ToolCallAnalyzer.extract_tool_calls(self._make_observations())

        self.assertEqual(data["total_count"], 2)
        self.assertCountEqual(data["unique_tools"], ["search_document", "read_section"])

        by_call_id = {entry["call_id"]: entry for entry in data["tool_calls"]}
        self.assertEqual(by_call_id["call-search"]["name"], "search_document")
        self.assertEqual(by_call_id["call-read"]["name"], "read_section")
        self.assertIsNotNone(by_call_id["call-search"]["tool_result"])
        self.assertGreater(by_call_id["call-read"]["tool_result_length"], 0)

    def test_tool_calls_keep_repeated_no_id_calls_from_separate_generations(self):
        observations = [
            {
                "id": "gen-1",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:01Z",
                "input": [
                    {
                        "type": "function_call",
                        "name": "search_document",
                        "arguments": "{\"query\":\"gene symbols\"}",
                        "status": "completed",
                    }
                ],
                "output": {},
            },
            {
                "id": "gen-2",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:02Z",
                "input": [
                    {
                        "type": "function_call",
                        "name": "search_document",
                        "arguments": "{\"query\":\"gene symbols\"}",
                        "status": "completed",
                    }
                ],
                "output": {},
            },
        ]

        data = ToolCallAnalyzer.extract_tool_calls(observations)

        self.assertEqual(data["total_count"], 2)
        self.assertTrue(data["duplicates"]["has_duplicates"])
        self.assertEqual(data["duplicates"]["total_duplicate_groups"], 1)

    def test_conversation_prefers_trace_response_text(self):
        conversation = ConversationAnalyzer.extract_conversation(
            self._make_trace({"response": "Final grounded answer", "response_length": 22}),
            self._make_observations(),
        )

        self.assertEqual(conversation["user_input"], "What genes are mentioned?")
        self.assertEqual(conversation["assistant_response"], "Final grounded answer")

    def test_conversation_prefers_trace_response_over_partial_generation_retry_text(self):
        observations = self._make_observations() + [
            {
                "id": "gen-3",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:03Z",
                "model": "gpt-4o",
                "input": [
                    {"role": "user", "content": "What genes are mentioned?"},
                    {
                        "role": "assistant",
                        "content": "It seems there was an issue retrieving the gene information.",
                    },
                ],
                "output": {},
            }
        ]

        conversation = ConversationAnalyzer.extract_conversation(
            self._make_trace(
                {
                    "response": "It seems there was an issue retrieving the gene information. Final grounded answer.",
                    "response_length": 86,
                }
            ),
            observations,
        )

        self.assertEqual(
            conversation["assistant_response"],
            "It seems there was an issue retrieving the gene information. Final grounded answer.",
        )

    def test_conversation_ignores_summary_only_trace_output(self):
        conversation = ConversationAnalyzer.extract_conversation(
            self._make_trace({"response_length": 22, "tool_calls": 2}),
            self._make_observations(),
        )

        self.assertEqual(conversation["assistant_response"], "N/A")

    def test_conversation_prefers_generation_output_over_partial_input_without_trace_output(self):
        observations = [
            {
                "id": "gen-1",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:03Z",
                "model": "gpt-4o",
                "input": [
                    {"role": "user", "content": "What genes are mentioned?"},
                    {"role": "assistant", "content": "Partial retry sentence"},
                ],
                "output": {"response": "Full final answer"},
            }
        ]

        conversation = ConversationAnalyzer.extract_conversation(
            self._make_trace({"response_length": 18}),
            observations,
        )

        self.assertEqual(conversation["assistant_response"], "Full final answer")

    def test_trace_summary_uses_v4_tool_call_extraction(self):
        summary = TraceSummaryAnalyzer.analyze(
            {"raw_trace": self._make_trace({"response": "Final grounded answer"})},
            self._make_observations(),
        )

        self.assertEqual(summary["tool_summary"]["total_tool_calls"], 2)
        self.assertCountEqual(
            summary["tool_summary"]["unique_tools"],
            ["search_document", "read_section"],
        )

    def test_cache_policy_only_caches_finished_trace_outputs(self):
        self.assertTrue(is_trace_output_cacheable({"response": "final answer"}))
        self.assertTrue(is_trace_output_cacheable({"error": "boom"}))
        self.assertFalse(is_trace_output_cacheable({"response_length": 100, "tool_calls": 1}))

    def test_trace_output_parses_stringified_json_payloads(self):
        payload = '{"response":"Final grounded answer","response_length":22}'

        self.assertEqual(extract_trace_response_text(payload), "Final grounded answer")

    def test_trace_output_unwraps_structured_response_values(self):
        payload = {
            "response": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Final grounded answer"},
                    ],
                }
            ]
        }

        self.assertEqual(extract_trace_response_text(payload), "Final grounded answer")

    def test_conversation_does_not_treat_history_assistant_turn_as_final_output(self):
        observations = [
            {
                "id": "gen-1",
                "type": "GENERATION",
                "name": "OpenAI-generation",
                "startTime": "2026-03-26T00:00:03Z",
                "model": "gpt-4o",
                "input": [
                    {"role": "user", "content": "Earlier question"},
                    {"role": "assistant", "content": "Earlier answer"},
                    {"role": "user", "content": "Current question"},
                ],
                "output": {},
            }
        ]

        conversation = ConversationAnalyzer.extract_conversation(
            {
                "id": "trace-1234",
                "name": "chat: Example",
                "timestamp": "2026-03-26T00:00:00Z",
                "latency": 12.5,
                "input": {"message": "Current question"},
                "output": {"response_length": 12},
                "metadata": {},
            },
            observations,
        )

        self.assertEqual(conversation["assistant_response"], "N/A")

    def test_pdf_citations_detects_numeric_and_superscript_markers_with_mapped_bibliography(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The methods cite prior protocols¹ and antibody prep [2,3].",
                "citations": [
                    {
                        "chunk_id": "methods-1",
                        "section_title": "Methods",
                        "page_number": 4,
                        "source": "pdf",
                        "text": "Larvae were staged as described<sup>1</sup> and stained [2,3].",
                    },
                    {
                        "chunk_id": "refs-1",
                        "section_title": "References",
                        "page_number": 9,
                        "source": "pdf",
                        "text": (
                            "References\n"
                            "1. Brand AH and Perrimon N. Targeted gene expression.\n"
                            "2. Smith J. Antibody preparation.\n"
                            "3. Jones K. Imaging protocol."
                        ),
                    },
                ],
            }
        )

        data = PDFCitationsAnalyzer.analyze(observations)
        diagnostics = data["citation_number_diagnostics"]

        self.assertTrue(data["found"])
        self.assertEqual(diagnostics["marker_numbers"], [1, 2, 3])
        self.assertEqual(diagnostics["bibliography_entry_numbers"], [1, 2, 3])
        self.assertEqual(diagnostics["mapping_status"], "mapped")
        self.assertCountEqual(
            diagnostics["marker_styles"],
            ["bracketed", "html_superscript", "unicode_superscript"],
        )

    def test_pdf_citations_reports_missing_bibliography_for_numeric_markers(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The methods cite a previous protocol [4].",
                "citations": [
                    {
                        "chunk_id": "methods-1",
                        "section_title": "Methods",
                        "page_number": 4,
                        "source": "pdf",
                        "text": "The protocol follows a published staining method [4].",
                    }
                ],
            }
        )

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertTrue(diagnostics["markers_found"])
        self.assertFalse(diagnostics["bibliography_found"])
        self.assertEqual(diagnostics["marker_numbers"], [4])
        self.assertEqual(diagnostics["missing_marker_numbers"], [4])
        self.assertEqual(diagnostics["mapping_status"], "missing_bibliography")

    def test_pdf_citations_rejects_malformed_citations_payload(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The PDF specialist returned malformed citation data [1].",
                "citations": "not-a-list",
            }
        )

        with self.assertRaisesRegex(TypeError, "citations.*list"):
            PDFCitationsAnalyzer.analyze(observations)

    def test_pdf_citations_ignores_parenthetical_years(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The strain was curated in 2024 (2024) using protocol (12).",
                "citations": [
                    {
                        "chunk_id": "methods-1",
                        "section_title": "Methods",
                        "page_number": 4,
                        "source": "pdf",
                        "text": "The revised workflow was published in 2024 (2024) and reused protocol (12).",
                    },
                    {
                        "chunk_id": "refs-1",
                        "section_title": "References",
                        "page_number": 9,
                        "source": "pdf",
                        "text": "References\n12. Smith J. Protocol details.",
                    },
                ],
            }
        )

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertEqual(diagnostics["marker_numbers"], [12])
        self.assertEqual(diagnostics["mapping_status"], "mapped")

    def test_pdf_citations_rejects_invalid_marker_ranges(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The methods include an invalid marker range [5-3] and valid range [6-7].",
                "citations": [
                    {
                        "chunk_id": "methods-1",
                        "section_title": "Methods",
                        "page_number": 4,
                        "source": "pdf",
                        "text": "The invalid marker [5-3] should not map, but [6-7] should.",
                    },
                    {
                        "chunk_id": "refs-1",
                        "section_title": "References",
                        "page_number": 9,
                        "source": "pdf",
                        "text": (
                            "References\n"
                            "6. Chen L. Valid protocol.\n"
                            "7. Patel R. Valid imaging."
                        ),
                    },
                ],
            }
        )

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertEqual(diagnostics["marker_numbers"], [6, 7])
        self.assertEqual(diagnostics["mapping_status"], "mapped")

    def test_pdf_citations_reports_ambiguous_bibliography_status(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The methods cite prior work [2].",
                "citations": [
                    {
                        "chunk_id": "methods-1",
                        "section_title": "Methods",
                        "page_number": 3,
                        "source": "pdf",
                        "text": "The strain construction followed prior work [2].",
                    },
                    {
                        "chunk_id": "refs-1",
                        "section_title": "References",
                        "page_number": 8,
                        "source": "pdf",
                        "text": (
                            "References\n"
                            "2. Smith J. Strain construction.\n"
                            "2. Smyth J. Similar numbered entry."
                        ),
                    },
                ],
            }
        )

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertTrue(diagnostics["bibliography_found"])
        self.assertEqual(diagnostics["marker_numbers"], [2])
        self.assertEqual(diagnostics["ambiguous_marker_numbers"], [2])
        self.assertEqual(diagnostics["mapping_status"], "ambiguous")

    def test_pdf_citations_keeps_empty_diagnostics_for_traces_without_citation_data(self):
        data = PDFCitationsAnalyzer.analyze([])

        self.assertFalse(data["found"])
        self.assertEqual(data["total_citations"], 0)
        self.assertEqual(
            data["citation_number_diagnostics"]["mapping_status"],
            "no_markers",
        )

    def test_pdf_citations_does_not_count_bibliography_entries_as_markers(self):
        observations = self._make_pdf_observation(
            {
                "answer": "The PDF specialist found the reference list.",
                "citations": [
                    {
                        "chunk_id": "refs-1",
                        "section_title": "References",
                        "page_number": 8,
                        "source": "pdf",
                        "text": "References\n[1] Smith J. A numbered bibliography entry.",
                    }
                ],
            }
        )

        diagnostics = PDFCitationsAnalyzer.analyze(observations)["citation_number_diagnostics"]

        self.assertFalse(diagnostics["markers_found"])
        self.assertTrue(diagnostics["bibliography_found"])
        self.assertEqual(diagnostics["bibliography_entry_numbers"], [1])
        self.assertEqual(diagnostics["mapping_status"], "no_markers")


if __name__ == "__main__":
    unittest.main()
