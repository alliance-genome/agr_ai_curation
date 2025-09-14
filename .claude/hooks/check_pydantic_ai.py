#!/usr/bin/env python3
"""
PreToolUse hook for detecting PydanticAI code in files being edited.
When PydanticAI patterns are detected, reminds the LLM to check documentation.
"""

import json
import os
import sys
import re


def load_triggers(trigger_file):
    """Load PydanticAI trigger patterns from reference file."""
    triggers = []
    if os.path.exists(trigger_file):
        with open(trigger_file, "r") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith("#"):
                    # Escape special regex characters but keep the pattern recognizable
                    pattern = (
                        re.escape(line).replace(r"\[", r"\[").replace(r"\]", r"\]")
                    )
                    triggers.append(pattern)
    return triggers


def check_for_pydantic_ai(content, triggers):
    """Check if content contains PydanticAI patterns."""
    for trigger in triggers:
        try:
            if re.search(trigger, content, re.IGNORECASE):
                return True, trigger
        except re.error:
            # Skip invalid regex patterns
            continue
    return False, None


def main():
    # Read the JSON input
    try:
        input_data = json.loads(input())
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON input"}))
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Only check for Edit, MultiEdit, and Write tools
    if tool_name not in ["Edit", "MultiEdit", "Write"]:
        sys.exit(0)

    # Get the project directory
    project_dir = os.environ.get(
        "CLAUDE_PROJECT_DIR", "/home/ctabone/Programming/agr_ai_curation"
    )
    hooks_dir = os.path.join(project_dir, ".claude", "hooks")
    trigger_file = os.path.join(hooks_dir, "pydantic_ai_1.0.6_identifiers.txt")
    docs_file = os.path.join(hooks_dir, "pydantic_ai_1.0.6_docs.txt")

    # Load trigger patterns
    triggers = load_triggers(trigger_file)
    if not triggers:
        sys.exit(0)

    # Check the content being edited/written
    content_to_check = ""

    if tool_name == "Edit":
        content_to_check = tool_input.get("old_string", "") + tool_input.get(
            "new_string", ""
        )
        file_path = tool_input.get("file_path", "")
    elif tool_name == "MultiEdit":
        edits = tool_input.get("edits", [])
        for edit in edits:
            content_to_check += edit.get("old_string", "") + edit.get("new_string", "")
        file_path = tool_input.get("file_path", "")
    elif tool_name == "Write":
        content_to_check = tool_input.get("content", "")
        file_path = tool_input.get("file_path", "")

    # Check if this is a Python file
    if not file_path.endswith((".py", ".pyx")):
        sys.exit(0)

    # Check for PydanticAI patterns
    found, trigger = check_for_pydantic_ai(content_to_check, triggers)

    if found:
        # Construct reminder message
        message = {
            "user_message": f"""⚠️ PydanticAI code detected (pattern: '{trigger[:50]}...')

You are editing code that uses PydanticAI. Please ensure you're using the modern PydanticAI 1.0.6+ API.

IMPORTANT: Use the Grep tool to search through .claude/hooks/pydantic_ai_1.0.6_docs.txt for the correct API usage patterns. Common changes in v1.0.6:
- Use 'output_type' instead of 'result_type' in Agent initialization
- Use 'stream_text(delta=True)' for delta streaming
- Message history is now built-in, not a separate handler
- Check the documentation file for more details about the specific pattern you're working with.

Example search commands:
- Grep for 'stream_text' in .claude/hooks/pydantic_ai_1.0.6_docs.txt
- Grep for 'output_type' in .claude/hooks/pydantic_ai_1.0.6_docs.txt
- Grep for the specific pattern you're implementing

Continue with your edit, but please verify the API usage is correct."""
        }
        print(json.dumps(message))

    # Exit code 0 allows the tool to continue
    sys.exit(0)


if __name__ == "__main__":
    main()
