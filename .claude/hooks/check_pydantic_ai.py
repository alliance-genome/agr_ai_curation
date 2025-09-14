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

    # Only check for Edit, MultiEdit, Write, and Update tools
    if tool_name not in ["Edit", "MultiEdit", "Write", "Update"]:
        sys.exit(0)

    # Get the project directory relative to this script
    # This script is in .claude/hooks/, so project root is two levels up
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.environ.get(
        "CLAUDE_PROJECT_DIR",
        os.path.dirname(
            os.path.dirname(script_dir)
        ),  # Go up two levels from script location
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
    elif tool_name == "Update":
        # Update tool has different parameters
        content_to_check = tool_input.get("content", "")
        file_path = tool_input.get("path", "")

    # Check if this is a Python file
    if not file_path.endswith((".py", ".pyx")):
        sys.exit(0)

    # Check for PydanticAI patterns
    found, trigger = check_for_pydantic_ai(content_to_check, triggers)

    if found:
        # Clean up the trigger pattern for display (remove escape characters)
        display_trigger = trigger.replace("\\", "")[:50]

        # Construct reminder message - use print directly for better formatting
        print("WARNING: PydanticAI code detected!")
        print(f"Pattern found: '{display_trigger}...'")
        print("")
        print("MANDATORY DOCUMENTATION CHECK:")
        print(f"You MUST search the local PydanticAI docs at:")
        print(f"  {docs_file}")
        print("")
        print("REQUIRED ACTIONS:")
        print(
            "1. Use Grep to search for the specific PydanticAI object/function/pattern"
        )
        print(
            "2. Use sufficient context flags (-A 30 -B 30 or more) to read COMPLETE sections"
        )
        print("3. For complex topics, search multiple related terms")
        print("")
        print("SEARCH STRATEGY:")
        clean_pattern = trigger.replace("\\", "")[:30]
        print(f'- Start with: grep -A 50 -B 50 "{clean_pattern}" {docs_file}')
        print("- For multiline examples, increase context (-A 100 -B 20)")
        print("- Search related terms if needed")
        print("")
        print("Common v1.0.6 patterns to verify:")
        print("- Agent initialization with 'output_type' (not 'result_type')")
        print("- Streaming with 'stream_text(delta=True)'")
        print("- Built-in message history support")
        print("- Tool registration patterns")
        print("")
        print(
            "WARNING: DO NOT proceed with edits until you've reviewed the relevant documentation!"
        )

    # Exit code 0 allows the tool to continue
    sys.exit(0)


if __name__ == "__main__":
    main()
