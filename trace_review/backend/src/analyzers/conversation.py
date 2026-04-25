"""
Conversation Analyzer
Extracts user input and assistant response from traces
"""
import json
from typing import Dict, List, Optional

from ..utils.trace_output import (
    extract_observation_response_text,
    extract_trace_response_text,
)


class ConversationAnalyzer:
    """Analyzes traces to extract conversation data"""

    @staticmethod
    def _extract_text_from_openai_agents_format(data) -> Optional[str]:
        """
        Extract clean text from OpenAI Agents SDK output format.

        The format is a list like:
        [
            {'type': 'reasoning', ...},
            {'type': 'message', 'content': [{'text': 'actual response', 'type': 'output_text'}], ...}
        ]
        """
        if not isinstance(data, list):
            return None

        # Look for the message item with content
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message" and item.get("content"):
                content = item.get("content")
                if isinstance(content, list):
                    # Extract text from content items
                    texts = []
                    for content_item in content:
                        if isinstance(content_item, dict):
                            if content_item.get("type") == "output_text":
                                text = content_item.get("text", "")
                                if text:
                                    texts.append(text)
                    if texts:
                        return "\n\n".join(texts)
        return None

    @staticmethod
    def _extract_text_from_input_array(input_data) -> Optional[str]:
        """
        Extract assistant response from GENERATION input array (OpenAI Agents format).

        The input array contains function calls and their outputs, plus final message.
        Look for the last 'message' type item with 'output_text' content.
        """
        if not isinstance(input_data, list):
            return None

        # Traverse in reverse to find the newest assistant output that appears
        # after the latest user input. Historical assistant turns are included in
        # conversation history and should not be mistaken for the current response.
        for item in reversed(input_data):
            if not isinstance(item, dict):
                continue
            if item.get("role") == "user":
                break
            if item.get("role") == "assistant":
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
            # Check for message type with content array
            if item.get("type") == "message" and item.get("content"):
                content = item.get("content")
                if isinstance(content, list):
                    texts = []
                    for content_item in content:
                        if isinstance(content_item, dict) and content_item.get("type") == "output_text":
                            text = content_item.get("text", "")
                            if text:
                                texts.append(text)
                    if texts:
                        return "\n\n".join(texts)
        return None

    @staticmethod
    def _extract_user_message_from_input_array(input_data) -> Optional[str]:
        """
        Extract user message from GENERATION input array (OpenAI Agents format).

        The input array contains messages in the format:
        [{"role": "user", "content": "user's question"}, ...]

        Returns the LAST user message found (most recent in conversation).
        """
        if not isinstance(input_data, list):
            return None

        last_user_message = None
        for item in input_data:
            if not isinstance(item, dict):
                continue
            # Look for role: user messages
            if item.get("role") == "user":
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    last_user_message = content.strip()

        return last_user_message

    @classmethod
    def extract_conversation(cls, trace: Dict, observations: List[Dict]) -> Dict:
        """
        Extract clean user input and assistant response

        Args:
            trace: Complete trace data from Langfuse
            observations: List of all observations

        Returns:
            Dictionary with user_input, assistant_response, and metadata
        """
        # Sort observations by startTime to ensure chronological order
        sorted_observations = sorted(
            observations,
            key=lambda x: x.get("startTime") or ""
        )

        # Extract user input from the FIRST generation's input array
        # (OpenAI Agents format stores user message there)
        user_message = "N/A"
        for obs in sorted_observations:
            if obs.get("type") == "GENERATION":
                obs_input = obs.get("input")
                if obs_input:
                    extracted = cls._extract_user_message_from_input_array(obs_input)
                    if extracted:
                        user_message = extracted
                        break

        # Fallback: try trace metadata if not found in observations
        if user_message == "N/A":
            trace_input = trace.get("input") or {}
            if isinstance(trace_input, dict):
                user_message = trace_input.get("message", trace_input.get("query", "N/A"))
            elif trace_input:
                user_message = str(trace_input)

        # Prefer the trace-level output when present. In current Langfuse/OpenAI Agents
        # traces, this is the authoritative final response, while generation inputs may
        # still contain partial assistant retry messages from intermediate turns.
        final_response = "N/A"
        extracted = extract_trace_response_text(trace.get("output"))
        if extracted:
            final_response = extracted

        # Check observations only if the trace itself does not yet expose a final response.
        if final_response == "N/A":
            # Look for synthesis/final response in observations (reversed)
            for obs in reversed(sorted_observations):
                extracted = extract_observation_response_text(obs)
                if extracted:
                    final_response = extracted
                    break

                if obs.get("type") == "GENERATION":
                    output = obs.get("output")
                    if output:
                        # Prefer the generation output when available. Retry flows can leave
                        # partial assistant text in the input array while the output still
                        # contains the fuller final message for that generation.
                        if isinstance(output, list):
                            extracted = cls._extract_text_from_openai_agents_format(output)
                            if extracted:
                                final_response = extracted
                                break
                        elif isinstance(output, dict):
                            extracted = extract_trace_response_text(output)
                            if extracted:
                                final_response = extracted
                                break
                        elif isinstance(output, str):
                            extracted = extract_trace_response_text(output)
                            if extracted:
                                final_response = extracted
                                break

                    # First, check the input array for OpenAI Agents format
                    # (the final message is often in the input, not output)
                    obs_input = obs.get("input")
                    if obs_input:
                        extracted = cls._extract_text_from_input_array(obs_input)
                        if extracted:
                            final_response = extracted
                            break

        return {
            "user_input": user_message,
            "assistant_response": final_response,
            "trace_id": trace.get("id"),
            "trace_name": trace.get("name"),
            "session_id": trace.get("sessionId"),
            "timestamp": trace.get("timestamp")
        }
