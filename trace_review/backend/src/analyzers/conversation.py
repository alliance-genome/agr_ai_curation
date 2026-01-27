"""
Conversation Analyzer
Extracts user input and assistant response from traces
"""
import json
from typing import Dict, List, Optional


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

        # Traverse in reverse to find the last message with output_text
        for item in reversed(input_data):
            if not isinstance(item, dict):
                continue
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

        # Find final response (last GENERATION or direct response)
        final_response = "N/A"

        # Look for synthesis/final response in observations (reversed)
        for obs in reversed(sorted_observations):
            if obs.get("type") == "GENERATION":
                # First, check the input array for OpenAI Agents format
                # (the final message is often in the input, not output)
                obs_input = obs.get("input")
                if obs_input:
                    extracted = cls._extract_text_from_input_array(obs_input)
                    if extracted:
                        final_response = extracted
                        break

                output = obs.get("output")
                if output:
                    # Try OpenAI Agents format (list with message items)
                    if isinstance(output, list):
                        extracted = cls._extract_text_from_openai_agents_format(output)
                        if extracted:
                            final_response = extracted
                            break
                    elif isinstance(output, dict):
                        # Try to get response from various possible fields
                        final_response = output.get("response", output.get("text", output.get("content", "N/A")))
                        if final_response != "N/A":
                            break
                    elif isinstance(output, str):
                        # Try parsing as JSON in case it's a stringified list
                        try:
                            parsed = json.loads(output)
                            if isinstance(parsed, list):
                                extracted = cls._extract_text_from_openai_agents_format(parsed)
                                if extracted:
                                    final_response = extracted
                                    break
                        except (json.JSONDecodeError, TypeError):
                            pass
                        final_response = output
                        break

        # If still not found, try the trace output
        if final_response == "N/A":
            trace_output = trace.get("output")
            if trace_output:
                if isinstance(trace_output, list):
                    extracted = cls._extract_text_from_openai_agents_format(trace_output)
                    if extracted:
                        final_response = extracted
                elif isinstance(trace_output, dict):
                    final_response = trace_output.get("response", trace_output.get("text", str(trace_output)))
                else:
                    final_response = str(trace_output)

        return {
            "user_input": user_message,
            "assistant_response": final_response,
            "trace_id": trace.get("id"),
            "trace_name": trace.get("name"),
            "session_id": trace.get("sessionId"),
            "timestamp": trace.get("timestamp")
        }
