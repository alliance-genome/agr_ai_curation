import unittest

from src.analyzers.conversation import ConversationAnalyzer
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


if __name__ == "__main__":
    unittest.main()
