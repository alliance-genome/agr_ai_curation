"""
Tool Call Analyzer
Extracts and organizes tool calls from trace observations

Supports two trace formats:
1. Old format (CrewAI): TOOL type observations
2. New format (OpenAI Agents SDK): GENERATION observations where output.type == "function_call"
   - Tool calls appear in output field with type="function_call"
   - Tool results appear in input arrays as type="function_call_output" with matching call_id
"""
import re
import sys
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from collections import defaultdict


class ToolResultParser:
    """
    Parse Python repr format tool results into structured JSON.

    Handles formats like:
    - summary='Found 5 chunks' hits=[ChunkHit(...), ...]
    - status='ok' data=[{...}] count=0 warnings=None
    """

    @classmethod
    def parse(cls, raw_result: str) -> Dict[str, Any]:
        """
        Parse a Python repr string into a structured dict.

        Returns:
            {
                "parsed": {...},  # Parsed structure or None if parsing failed
                "summary": "...",  # Human-readable summary
                "raw": "...",  # Original raw string
                "parse_status": "full" | "partial" | "unparsed"  # Parsing success indicator
            }
        """
        if not raw_result:
            return {"parsed": None, "summary": "No result", "raw": "", "parse_status": "unparsed"}

        result = {
            "parsed": None,
            "summary": "",
            "raw": raw_result,
            "parse_status": "unparsed"
        }

        try:
            # First, try to parse as JSON (for simple JSON results)
            if raw_result.strip().startswith("{") or raw_result.strip().startswith("["):
                try:
                    json_data = json.loads(raw_result)
                    result["parsed"] = {"json_data": json_data}
                    result["summary"] = cls._generate_json_summary(json_data)
                    result["parse_status"] = "full"
                    return result
                except json.JSONDecodeError:
                    pass  # Not valid JSON, continue with repr parsing

            # Try to parse the Python repr structure
            parsed = cls._parse_repr(raw_result)
            result["parsed"] = parsed
            result["summary"] = cls._generate_summary(parsed, raw_result)

            # Determine parse status based on what was extracted
            result["parse_status"] = cls._determine_parse_status(parsed, raw_result)
        except Exception:
            # If parsing fails, just use the raw string
            result["summary"] = raw_result[:100] + "..." if len(raw_result) > 100 else raw_result
            result["parse_status"] = "unparsed"

        return result

    @classmethod
    def _determine_parse_status(cls, parsed: Dict, raw: str) -> str:
        """
        Determine how well the parsing succeeded.

        Returns:
            - "full": Successfully extracted meaningful structured data
            - "partial": Got some data but might be missing pieces
            - "unparsed": Couldn't extract any structured data
        """
        if not parsed:
            return "unparsed"

        # Check if we have any meaningful parsed content
        has_hits = bool(parsed.get("hits"))
        has_data = bool(parsed.get("data"))
        has_section = bool(parsed.get("section"))
        has_subsection = bool(parsed.get("subsection"))
        has_json = bool(parsed.get("json_data"))
        has_summary = bool(parsed.get("summary"))
        has_status = bool(parsed.get("status"))
        has_count = "count" in parsed

        # If we have structured data (hits, data, section, subsection, json), it's full
        if has_hits or has_data or has_section or has_subsection or has_json:
            return "full"

        # Check for empty result case: status='ok' with data=[] and count=0
        # This is a fully parsed empty result, not a partial parse
        if has_status and has_count and "data=[]" in raw:
            return "full"

        # If we have summary (from search_document), it's full even without hits
        # e.g., "Found 0 chunks" is a valid complete result
        if has_summary and ("hits=[]" in raw or "Found 0" in parsed.get("summary", "")):
            return "full"

        # If we have at least summary or status, it's partial
        if has_summary or has_status or has_count:
            return "partial"

        # Check if raw has patterns we should have caught
        unparsed_patterns = [
            "ChunkHit(",  # Should have extracted hits
            "SectionContent(",  # Should have extracted section
            "SubsectionContent(",  # Should have extracted subsection
            "data=[{",  # Should have extracted data array (non-empty)
        ]
        for pattern in unparsed_patterns:
            if pattern in raw:
                return "partial"  # We missed something

        # Nothing meaningful extracted
        return "unparsed"

    @classmethod
    def _generate_json_summary(cls, json_data: Any) -> str:
        """Generate a summary for JSON data."""
        if isinstance(json_data, dict):
            # Handle common patterns
            if "assistant" in json_data:
                return f"Handoff to: {json_data['assistant']}"
            if "error" in json_data:
                return f"Error: {json_data['error']}"
            if "status" in json_data:
                return f"Status: {json_data['status']}"
            # Generic dict summary
            keys = list(json_data.keys())[:3]
            return f"JSON object with keys: {', '.join(keys)}"
        elif isinstance(json_data, list):
            return f"JSON array with {len(json_data)} items"
        else:
            return str(json_data)[:100]

    @classmethod
    def _parse_repr(cls, text: str) -> Dict[str, Any]:
        """
        Parse Python repr format into dict.

        Handles: field='value' field2=123 field3=[...] field4=ClassName(...)
        """
        result = {}

        # Pattern to match top-level field=value pairs
        # This handles: name='value', name=123, name=None, name=[...], name=ClassName(...)

        # First, extract simple key=value pairs at the top level
        # Match: word= followed by a value (string, number, None, list, or ClassName(...))

        # Extract summary if present
        summary_match = re.search(r"summary='([^']*)'", text)
        if summary_match:
            result["summary"] = summary_match.group(1)

        # Extract status if present
        status_match = re.search(r"status='([^']*)'", text)
        if status_match:
            result["status"] = status_match.group(1)

        # Extract count if present
        count_match = re.search(r"count=(\d+)", text)
        if count_match:
            result["count"] = int(count_match.group(1))

        # Extract warnings/message if None
        if "warnings=None" in text:
            result["warnings"] = None
        if "message=None" in text:
            result["message"] = None

        # Parse hits array for search_document
        if "hits=[" in text:
            result["hits"] = cls._parse_chunk_hits(text)

        # Parse data array for agr_curation_query
        if "data=[" in text:
            result["data"] = cls._parse_data_array(text)

        # Parse SectionContent for read_section
        if "SectionContent(" in text:
            section_data = cls._parse_section_content(text)
            if section_data:
                result["section"] = section_data

        # Parse SubsectionContent for read_subsection
        if "SubsectionContent(" in text:
            subsection_data = cls._parse_subsection_content(text)
            if subsection_data:
                result["subsection"] = subsection_data

        return result

    @classmethod
    def _parse_section_content(cls, text: str) -> Optional[Dict]:
        """Parse SectionContent from read_section results."""
        try:
            # Extract section_title
            title_match = re.search(r"section_title='([^']*)'", text)
            section_title = title_match.group(1) if title_match else None

            # Extract page_numbers array
            pages_match = re.search(r"page_numbers=\[([^\]]*)\]", text)
            page_numbers = []
            if pages_match:
                pages_str = pages_match.group(1)
                page_numbers = [int(p.strip()) for p in pages_str.split(",") if p.strip().isdigit()]

            # Extract chunk_count
            chunk_match = re.search(r"chunk_count=(\d+)", text)
            chunk_count = int(chunk_match.group(1)) if chunk_match else None

            # Extract full content
            content_start = text.find("content=\"")
            if content_start == -1:
                content_start = text.find("content='")
            full_content = ""
            content_preview = ""
            if content_start != -1:
                quote_char = text[content_start + 8]  # " or '
                content_start += 9
                # Find the end of content (before ", chunk_count or closing paren)
                remaining = text[content_start:]
                # Look for ending pattern
                end_patterns = [f"{quote_char}, chunk_count", f"{quote_char})"]
                content_end = len(remaining)
                for pattern in end_patterns:
                    idx = remaining.find(pattern)
                    if idx != -1 and idx < content_end:
                        content_end = idx
                full_content = remaining[:content_end].replace("\\n", "\n")
                content_preview = full_content[:500]

            if section_title or page_numbers or chunk_count:
                return {
                    "section_title": section_title,
                    "page_numbers": page_numbers,
                    "chunk_count": chunk_count,
                    "content_preview": content_preview,
                    "full_content": full_content
                }
        except Exception:
            pass
        return None

    @classmethod
    def _parse_subsection_content(cls, text: str) -> Optional[Dict]:
        """Parse SubsectionContent from read_subsection results."""
        try:
            # Extract parent_section
            parent_match = re.search(r"parent_section='([^']*)'", text)
            parent_section = parent_match.group(1) if parent_match else None

            # Extract subsection
            subsection_match = re.search(r"subsection='([^']*)'", text)
            subsection = subsection_match.group(1) if subsection_match else None

            # Extract page_numbers array
            pages_match = re.search(r"page_numbers=\[([^\]]*)\]", text)
            page_numbers = []
            if pages_match:
                pages_str = pages_match.group(1)
                page_numbers = [int(p.strip()) for p in pages_str.split(",") if p.strip().isdigit()]

            # Extract chunk_count
            chunk_match = re.search(r"chunk_count=(\d+)", text)
            chunk_count = int(chunk_match.group(1)) if chunk_match else None

            # Extract full content (same logic as SectionContent)
            content_start = text.find("content=\"")
            if content_start == -1:
                content_start = text.find("content='")
            full_content = ""
            content_preview = ""
            if content_start != -1:
                quote_char = text[content_start + 8]  # " or '
                content_start += 9
                remaining = text[content_start:]
                end_patterns = [f"{quote_char}, chunk_count", f"{quote_char})"]
                content_end = len(remaining)
                for pattern in end_patterns:
                    idx = remaining.find(pattern)
                    if idx != -1 and idx < content_end:
                        content_end = idx
                full_content = remaining[:content_end].replace("\\n", "\n")
                content_preview = full_content[:500]

            if parent_section or subsection or page_numbers or chunk_count:
                return {
                    "parent_section": parent_section,
                    "subsection": subsection,
                    "page_numbers": page_numbers,
                    "chunk_count": chunk_count,
                    "content_preview": content_preview,
                    "full_content": full_content
                }
        except Exception:
            pass
        return None

    @classmethod
    def _parse_chunk_hits(cls, text: str) -> List[Dict]:
        """Parse ChunkHit objects from hits=[...]"""
        hits = []

        # Find all ChunkHit(...) blocks
        # The content field can contain newlines (\n) and other escaped chars
        # Format: ChunkHit(chunk_id='...', section_title='...', page_number=N, score=F, content='...')
        # Match content by finding the end pattern: '), ChunkHit or ')]

        # First, find all ChunkHit blocks by splitting on 'ChunkHit('
        chunks = text.split('ChunkHit(')

        for chunk in chunks[1:]:  # Skip first empty/header part
            try:
                # Find chunk_id
                chunk_id_match = re.search(r"chunk_id='([^']*)'", chunk)
                section_match = re.search(r"section_title='([^']*)'", chunk)
                page_match = re.search(r"page_number=(\d+)", chunk)
                score_match = re.search(r"score=([\d.]+)", chunk)

                # Content is trickier - it starts after "content='" or 'content="' and ends before closing
                # Try both single and double quotes
                content = ""
                content_start = chunk.find("content='")
                quote_char = "'"
                if content_start == -1:
                    content_start = chunk.find('content="')
                    quote_char = '"'

                if content_start != -1:
                    content_start += len("content='")  # Same length for both quote types
                    remaining = chunk[content_start:]

                    # Find the closing quote by looking for quote + ), which marks end of content
                    # Handle: '), or ')]  for single quotes, or "), or ")]  for double quotes
                    content_end = -1
                    end_patterns = [
                        f"{quote_char}), ",  # End before next field or ChunkHit
                        f"{quote_char})]",   # End of hits array
                        f"{quote_char})",    # End of ChunkHit
                        f"{quote_char}, doc_items=",  # End before doc_items field
                    ]
                    for end_pattern in end_patterns:
                        idx = remaining.find(end_pattern)
                        if idx != -1 and (content_end == -1 or idx < content_end):
                            content_end = idx

                    if content_end != -1:
                        content = remaining[:content_end]
                    else:
                        content = remaining[:200]  # Fallback: first 200 chars

                if chunk_id_match and section_match and page_match and score_match:
                    hit = {
                        "chunk_id": chunk_id_match.group(1),
                        "section_title": section_match.group(1),
                        "page_number": int(page_match.group(1)),
                        "score": float(score_match.group(1)),
                        "content": content.replace("\\n", "\n")[:500]  # Limit content length
                    }
                    hits.append(hit)
            except Exception:
                continue  # Skip malformed entries

        return hits

    @classmethod
    def _parse_data_array(cls, text: str) -> List[Dict]:
        """Parse data=[{...}] array"""
        data = []

        # Find the data array content
        data_match = re.search(r"data=\[(.*?)\](?:\s+count=|\s*$)", text, re.DOTALL)
        if not data_match:
            return data

        data_content = data_match.group(1).strip()
        if not data_content:
            return data

        # Try to parse as JSON-like dicts
        # Find dict patterns {...}
        dict_pattern = r"\{([^{}]+)\}"
        for dict_match in re.finditer(dict_pattern, data_content):
            dict_str = "{" + dict_match.group(1) + "}"
            try:
                # Convert Python-style to JSON-style
                json_str = dict_str.replace("'", '"').replace("True", "true").replace("False", "false").replace("None", "null")
                parsed_dict = json.loads(json_str)
                data.append(parsed_dict)
            except json.JSONDecodeError:
                # If JSON parsing fails, try manual extraction
                item = {}
                for kv in re.finditer(r"'(\w+)':\s*'?([^',}]+)'?", dict_match.group(1)):
                    key, value = kv.group(1), kv.group(2).strip("'")
                    if value == "True":
                        value = True
                    elif value == "False":
                        value = False
                    elif value.isdigit():
                        value = int(value)
                    item[key] = value
                if item:
                    data.append(item)

        return data

    @classmethod
    def _generate_summary(cls, parsed: Dict, raw: str) -> str:
        """Generate a human-readable summary from parsed data."""
        parts = []

        # For search_document results
        if "summary" in parsed:
            parts.append(parsed["summary"])

        if "hits" in parsed and parsed["hits"]:
            pages = sorted(set(h.get("page_number", 0) for h in parsed["hits"]))
            if pages:
                parts.append(f"Pages: {', '.join(map(str, pages))}")

        # For read_section results with SectionContent
        if "section" in parsed and parsed["section"]:
            section = parsed["section"]
            if section.get("section_title"):
                parts.append(f"Section: {section['section_title']}")
            if section.get("chunk_count"):
                parts.append(f"{section['chunk_count']} chunks")
            if section.get("page_numbers"):
                parts.append(f"Pages: {', '.join(map(str, section['page_numbers']))}")

        # For read_subsection results with SubsectionContent
        if "subsection" in parsed and parsed["subsection"]:
            subsection = parsed["subsection"]
            if subsection.get("parent_section") and subsection.get("subsection"):
                parts.append(f"Subsection: {subsection['parent_section']} > {subsection['subsection']}")
            elif subsection.get("subsection"):
                parts.append(f"Subsection: {subsection['subsection']}")
            if subsection.get("chunk_count"):
                parts.append(f"{subsection['chunk_count']} chunks")
            if subsection.get("page_numbers"):
                parts.append(f"Pages: {', '.join(map(str, subsection['page_numbers']))}")

        # For agr_curation_query results
        if "status" in parsed:
            parts.append(f"Status: {parsed['status']}")

        if "count" in parsed:
            parts.append(f"Count: {parsed['count']}")

        if "data" in parsed and parsed["data"]:
            # Show first item's key info
            first = parsed["data"][0]
            if "symbol" in first:
                parts.append(f"Symbol: {first['symbol']}")
            elif "curie" in first:
                parts.append(f"Curie: {first['curie']}")

        return " | ".join(parts) if parts else raw[:100]


class ToolCallAnalyzer:
    """Analyzes tool calls in trace observations"""

    @staticmethod
    def _parse_time(time_val: Any) -> Optional[datetime]:
        """Parse timestamp string or return datetime object"""
        if not time_val:
            return None

        if isinstance(time_val, datetime):
            return time_val

        try:
            time_str = str(time_val)
            # Handle Z suffix for UTC
            if time_str.endswith('Z'):
                time_str = time_str[:-1] + '+00:00'
            return datetime.fromisoformat(time_str)
        except Exception:
            # Time parsing errors are expected for malformed trace data - silently return None
            return None

    @staticmethod
    def _calculate_duration(start: Any, end: Any) -> str:
        """Calculate duration string between two timestamps"""
        start_dt = ToolCallAnalyzer._parse_time(start)
        end_dt = ToolCallAnalyzer._parse_time(end)

        if not start_dt or not end_dt:
            return "N/A"

        duration = (end_dt - start_dt).total_seconds()
        if duration < 1:
            return f"{duration*1000:.0f}ms"
        return f"{duration:.2f}s"

    @staticmethod
    def _extract_tool_outputs(observations: List[Dict]) -> Dict[str, str]:
        """
        Extract tool outputs by call_id from all function_call_output entries.

        In OpenAI Agents SDK format, tool results appear in subsequent observation's
        input arrays as type="function_call_output" with a matching call_id.
        """
        outputs = {}
        for obs in observations:
            input_data = obs.get("input", [])
            if isinstance(input_data, list):
                for msg in input_data:
                    if msg.get("type") == "function_call_output":
                        call_id = msg.get("call_id")
                        output = msg.get("output", "")
                        if call_id and call_id not in outputs:
                            outputs[call_id] = output
        return outputs

    @staticmethod
    def _detect_duplicates(tool_calls: List[Dict]) -> Dict:
        """
        Detect duplicate tool calls (same tool + same arguments).

        Returns summary of duplicates with counts and call details.
        """
        grouped = defaultdict(list)
        for tc in tool_calls:
            name = tc.get("name", "unknown")
            # Serialize input for comparison
            input_str = json.dumps(tc.get("input", {}), sort_keys=True)
            key = f"{name}:{input_str}"
            grouped[key].append(tc)

        duplicates = []
        for key, calls in grouped.items():
            if len(calls) > 1:
                name = calls[0].get("name", "unknown")
                input_args = calls[0].get("input", {})
                duplicates.append({
                    "tool_name": name,
                    "arguments": input_args,
                    "count": len(calls),
                    "call_ids": [c.get("call_id", "N/A") for c in calls],
                    "timestamps": [c.get("time", "N/A") for c in calls]
                })

        return {
            "has_duplicates": len(duplicates) > 0,
            "total_duplicate_groups": len(duplicates),
            "total_wasted_calls": sum(d["count"] - 1 for d in duplicates),
            "duplicates": sorted(duplicates, key=lambda x: -x["count"])
        }

    @staticmethod
    def extract_tool_calls(observations: List[Dict]) -> Dict:
        """
        Extract all tool calls with reasoning, URLs, methods, and status

        Supports two formats:
        1. Old format: TOOL type observations
        2. New format (OpenTelemetry/Langfuse): GENERATION observations where output.type == "function_call"

        Args:
            observations: List of all observations from trace

        Returns:
            Dictionary with total_count, unique_tools, and tool_calls list
        """
        tool_calls = []

        # Helper to get start time safely for sorting
        def get_start_time(obs):
            val = obs.get("startTime") or obs.get("start_time")
            if val:
                return str(val)
            return ""

        # Sort observations by time to ensure we process GENERATION before TOOL
        sorted_obs = sorted(observations, key=get_start_time)

        # Keep track of generations for context
        # Key: observation_id, Value: Generation Observation
        generations_by_id = {}

        # Key: parentObservationId, Value: Generation Observation (most recent sibling)
        last_sibling_generation = {}

        for obs in sorted_obs:
            obs_type = obs.get("type")
            obs_id = obs.get("id")
            parent_id = obs.get("parentObservationId")

            if obs_type == "GENERATION":
                # Store this generation by ID (for parent-child relationship)
                generations_by_id[obs_id] = obs
                # Store as potential sibling (for sibling relationship)
                if parent_id:
                    last_sibling_generation[parent_id] = obs

                # NEW FORMAT: Check if this GENERATION contains function_call(s) in output
                output_data = obs.get("output", {})

                # Handle both formats:
                # 1. output is a dict with type="function_call"
                # 2. output is an array containing items with type="function_call"
                function_calls = []
                if isinstance(output_data, dict) and output_data.get("type") == "function_call":
                    function_calls = [output_data]
                elif isinstance(output_data, list):
                    function_calls = [item for item in output_data if isinstance(item, dict) and item.get("type") == "function_call"]

                for fc_data in function_calls:
                    # Extract tool call from GENERATION with function_call output
                    start_time = obs.get("startTime") or obs.get("start_time")
                    end_time = obs.get("endTime") or obs.get("end_time")

                    tool_name = fc_data.get("name", "unknown")
                    arguments_str = fc_data.get("arguments", "{}")

                    # Parse arguments JSON string
                    try:
                        import json
                        arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                    except:
                        arguments = {"raw": arguments_str}

                    # Extract URL and method from arguments if present
                    url = arguments.get("url", "N/A")
                    method = arguments.get("method", "N/A")

                    # Get model from the generation
                    model = obs.get("model", "N/A")

                    # Calculate duration
                    duration = ToolCallAnalyzer._calculate_duration(start_time, end_time)

                    tool_calls.append({
                        "time": start_time,
                        "duration": duration,
                        "model": model,
                        "id": obs_id,
                        "name": tool_name,
                        "url": url,
                        "method": method,
                        "thought": "N/A",  # Function calls don't have explicit reasoning
                        "status": fc_data.get("status", "N/A"),
                        "status_code": "N/A",
                        "input": arguments,  # Use parsed arguments as input
                        "output": fc_data,
                        "call_id": fc_data.get("call_id", "N/A")
                    })

            elif obs_type == "TOOL":
                # Extract key information
                input_data = obs.get("input", {})
                output_data = obs.get("output", {})
                
                # Get timestamps (try camelCase then snake_case)
                start_time = obs.get("startTime") or obs.get("start_time")
                end_time = obs.get("endTime") or obs.get("end_time")

                # Parse URL and method from calling string
                calling = input_data.get("calling", "")
                url = "N/A"
                method = "GET"

                if "url" in str(calling):
                    # Extract URL from arguments string
                    url_match = re.search(r"'url':\s*'([^']+)'", str(calling))
                    if url_match:
                        url = url_match.group(1)
                    method_match = re.search(r"'method':\s*'([^']+)'", str(calling))
                    if method_match:
                        method = method_match.group(1)

                # 1. Try to get reasoning and MODEL from related GENERATION (LLM thought)
                thought = "N/A"
                model = "N/A"
                gen_obs = None
                
                # Strategy A: Check if parent is the generation (Parent-Child)
                if parent_id and parent_id in generations_by_id:
                    gen_obs = generations_by_id[parent_id]
                # Strategy B: Check if there is a sibling generation (Sibling)
                elif parent_id and parent_id in last_sibling_generation:
                    gen_obs = last_sibling_generation[parent_id]
                
                if gen_obs:
                    gen_output = gen_obs.get("output")
                    # Extract thought from generation output if it contains "Action:"
                    if gen_output and isinstance(gen_output, dict) and "value" in gen_output:
                        text = gen_output["value"]
                        if "Action:" in text:
                            thought = text.split("Action:")[0].strip()
                    elif gen_output and isinstance(gen_output, str):
                        if "Action:" in gen_output:
                            thought = gen_output.split("Action:")[0].strip()
                            
                    # Extract Model
                    if gen_obs.get("model"):
                         model = gen_obs.get("model")
                    elif gen_obs.get("modelParameters") and isinstance(gen_obs.get("modelParameters"), dict):
                         model = gen_obs["modelParameters"].get("model", "N/A")
                    elif gen_obs.get("metadata", {}).get("model"):
                         model = gen_obs["metadata"]["model"]
                    elif gen_obs.get("metadata", {}).get("attributes", {}).get("llm.model_name"):
                         model = gen_obs["metadata"]["attributes"]["llm.model_name"]

                # 2. Fallback: Extract reasoning from tool_string in input (CrewAI specific)
                if thought == "N/A":
                    tool_string = input_data.get("tool_string", "")
                    if tool_string:
                        # Parse out the Thought line
                        lines = tool_string.split('\n')
                        for line in lines:
                            if line.strip().startswith('Thought:'):
                                thought = line.replace('Thought:', '').strip()
                                break
                        # If no explicit "Thought:" line, try taking text before Action
                        if thought == "N/A" and "Action:" in tool_string:
                            thought = tool_string.split("Action:")[0].strip()

                # Calculate duration
                duration = ToolCallAnalyzer._calculate_duration(start_time, end_time)

                tool_calls.append({
                    "time": start_time,
                    "duration": duration,
                    "model": model,
                    "id": obs.get("id"),
                    "name": obs.get("name"),
                    "url": url,
                    "method": method,
                    "thought": thought,
                    "status": output_data.get("status") if isinstance(output_data, dict) else "N/A",
                    "status_code": output_data.get("status_code") if isinstance(output_data, dict) else "N/A",
                    "input": input_data,
                    "output": output_data
                })

        # Sort by time
        tool_calls.sort(key=lambda x: str(x["time"] or ""))

        # Get unique tool names
        unique_tools = list(set(call["name"] for call in tool_calls if call["name"]))

        # Extract tool outputs from function_call_output entries
        tool_outputs = ToolCallAnalyzer._extract_tool_outputs(observations)

        # Add tool_result to each call by matching call_id
        for tc in tool_calls:
            call_id = tc.get("call_id")
            if call_id and call_id in tool_outputs:
                output_text = tool_outputs[call_id]

                # Parse the result into structured format
                parsed_result = ToolResultParser.parse(output_text)

                tc["tool_result"] = {
                    "summary": parsed_result["summary"],
                    "parsed": parsed_result["parsed"],
                    "raw": parsed_result["raw"],
                    "parse_status": parsed_result["parse_status"]
                }
                tc["tool_result_length"] = len(output_text)
            else:
                tc["tool_result"] = None
                tc["tool_result_length"] = 0

        # Detect duplicates
        duplicates_info = ToolCallAnalyzer._detect_duplicates(tool_calls)

        return {
            "total_count": len(tool_calls),
            "unique_tools": unique_tools,
            "tool_calls": tool_calls,
            "duplicates": duplicates_info
        }

    @staticmethod
    def group_by_tool_name(tool_calls: List[Dict]) -> Dict[str, List[Dict]]:
        """Group tool calls by tool name"""
        grouped = {}
        for call in tool_calls:
            name = call.get("name", "unknown")
            if name not in grouped:
                grouped[name] = []
            grouped[name].append(call)
        return grouped

    @staticmethod
    def filter_by_status(tool_calls: List[Dict], status: str) -> List[Dict]:
        """Filter tool calls by status (ok, error, etc.)"""
        return [call for call in tool_calls if call.get("status") == status]
