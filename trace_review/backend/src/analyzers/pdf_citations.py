"""
PDF Citations Analyzer
Extracts PDF citations with page numbers, relevance scores, and extracted content
"""
import json
from typing import Dict, List


class PDFCitationsAnalyzer:
    """Analyzes PDF citations from PDF Specialist agent observations"""

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
                    if "pdf" not in tool_name.lower():
                        continue

                    # Parse the output JSON
                    try:
                        output_data = json.loads(output_str)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    # Extract citations
                    citations = output_data.get("citations", [])
                    if citations:
                        all_citations.extend(citations)

                    # Extract answer text
                    answer = output_data.get("answer", "")
                    if answer:
                        all_answers.append(answer)

                    # Store tool call metadata
                    tool_calls_metadata.append({
                        "tool_name": tool_name,
                        "query": query,
                        "citations_count": len(citations),
                        "call_id": call_id
                    })

        if not all_citations and not all_answers:
            return {"found": False, "total_citations": 0, "search_queries": [],
                    "extracted_content": "", "citations": [], "total_chunks_found": 0,
                    "tool_calls": []}

        # Deduplicate citations by chunk_id
        seen_chunks = set()
        unique_citations = []
        for cit in all_citations:
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
            "tool_calls": unique_tool_calls
        }
