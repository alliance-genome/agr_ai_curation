#!/usr/bin/env python3
"""
PreToolUse hook for detecting Unstructured.io code in files being edited.
When Unstructured patterns are detected, reminds the LLM to check documentation.
"""

import json
import os
import sys
import re


def load_triggers(trigger_file):
    """Load Unstructured trigger patterns from reference file."""
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


def check_for_unstructured(content, triggers):
    """Check if content contains Unstructured patterns.

    Enhanced to detect patterns regardless of import aliases.
    Focuses on actual function/class names and signatures.
    """
    # First check for any import of unstructured modules (with any alias)
    import_patterns = [
        r"from\s+unstructured[\w\.]*\s+import",  # from unstructured.X import Y
        r"import\s+unstructured[\w\.]*(?:\s+as\s+\w+)?",  # import unstructured as X
    ]

    has_unstructured_import = False
    for pattern in import_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            has_unstructured_import = True
            break

    # If no unstructured import found, skip detailed checks
    if not has_unstructured_import:
        # Still check for direct function/class usage patterns
        # These are distinctive enough to indicate Unstructured usage
        direct_patterns = [
            r"\bpartition_pdf\s*\(",
            r"\bpartition_docx\s*\(",
            r"\bpartition_html\s*\(",
            r"\bchunk_by_title\s*\(",
            r"\bTitle\s*\(",
            r"\bNarrativeText\s*\(",
            r"\bTable\s*\(",
            r"\bTableChunk\s*\(",
            r"\bFigureCaption\s*\(",
            r'strategy\s*=\s*["\'](?:hi_res|fast|ocr_only)["\']',
            r"infer_table_structure\s*=\s*(?:True|False)",
            r"extract_images_in_pdf\s*=\s*(?:True|False)",
            r"\.metadata\.page_number",
            r"\.metadata\.coordinates",
        ]

        for pattern in direct_patterns:
            if re.search(pattern, content):
                return True, pattern

    # If we have unstructured imports, check for any trigger patterns
    # Focus on function calls and distinctive patterns
    for trigger in triggers:
        # Clean up trigger for better matching
        clean_trigger = trigger.replace("\\", "")

        # Skip pure import patterns - we already know it's imported
        if clean_trigger.startswith(("from ", "import ")):
            continue

        # For function/class patterns, make them more flexible
        if "(" in clean_trigger:
            # Extract just the function/class name
            func_name = clean_trigger.split("(")[0].strip()
            # Match with word boundaries and optional whitespace before parenthesis
            flexible_pattern = r"\b" + re.escape(func_name) + r"\s*\("
            try:
                if re.search(flexible_pattern, content):
                    return True, trigger
            except re.error:
                pass
        else:
            # For other patterns, use as-is but with word boundaries
            try:
                if re.search(r"\b" + trigger + r"\b", content, re.IGNORECASE):
                    return True, trigger
            except re.error:
                pass

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
    trigger_file = os.path.join(hooks_dir, "unstructured_0.18.14_identifiers.txt")
    docs_file = os.path.join(hooks_dir, "unstructured_0.18.14_docs.txt")

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

    # Check for Unstructured patterns
    found, trigger = check_for_unstructured(content_to_check, triggers)

    if found:
        # Clean up the trigger pattern for display (remove escape characters)
        display_trigger = trigger.replace("\\", "")[:50]

        # Output JSON with systemMessage for Claude Code UI
        output = {
            "systemMessage": f"🔍 Unstructured.io hook detected pattern: '{display_trigger}...'",
            "suppressOutput": False,  # Show in transcript mode
        }
        print(json.dumps(output))

    # Exit code 0 allows the tool to continue
    sys.exit(0)


if __name__ == "__main__":
    main()
