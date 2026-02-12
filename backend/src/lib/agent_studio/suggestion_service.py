"""
Prompt Suggestion Service.

Handles submission of prompt improvement suggestions via SNS,
similar to the feedback system. Suggestions can be submitted
manually by users or triggered by Opus when it detects
actionable improvements during conversation.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

import boto3
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _format_suggestion_email(message: dict) -> str:
    """
    Format a suggestion message as a human-readable email body.

    Args:
        message: The raw suggestion message dict

    Returns:
        Formatted string for email body
    """
    lines = []

    # Header
    lines.append("=" * 60)
    lines.append("PROMPT IMPROVEMENT SUGGESTION")
    lines.append("=" * 60)
    lines.append("")

    # Basic info section
    lines.append(f"Suggestion ID: {message.get('suggestion_id', 'N/A')}")
    lines.append(f"Submitted At:  {message.get('submitted_at', 'N/A')}")
    lines.append(f"Submitted By:  {message.get('submitted_by', 'N/A')}")
    lines.append(f"Source:        {message.get('source', 'N/A')}")
    lines.append("")

    # Target info
    lines.append("-" * 40)
    lines.append("TARGET")
    lines.append("-" * 40)
    lines.append(f"Agent:           {message.get('agent_id', 'N/A')}")
    if message.get('mod_id'):
        lines.append(f"MOD:             {message.get('mod_id')}")
    lines.append(f"Suggestion Type: {message.get('suggestion_type', 'N/A')}")
    lines.append("")

    # Summary
    lines.append("-" * 40)
    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(message.get('summary', 'No summary provided'))
    lines.append("")

    # Detailed reasoning
    lines.append("-" * 40)
    lines.append("DETAILED REASONING")
    lines.append("-" * 40)
    lines.append(message.get('detailed_reasoning', 'No detailed reasoning provided'))
    lines.append("")

    # Proposed change (if provided)
    if message.get('proposed_change'):
        lines.append("-" * 40)
        lines.append("PROPOSED CHANGE")
        lines.append("-" * 40)
        lines.append(message.get('proposed_change'))
        lines.append("")

    # Trace ID for debugging (if provided)
    if message.get('trace_id'):
        lines.append("-" * 40)
        lines.append("DEBUG INFO")
        lines.append("-" * 40)
        lines.append(f"Trace ID: {message.get('trace_id')}")
        lines.append("")

    # Conversation context (if provided)
    if message.get('conversation_context'):
        lines.append("-" * 40)
        lines.append("CONVERSATION CONTEXT")
        lines.append("-" * 40)
        lines.append(message.get('conversation_context'))
        lines.append("")

    lines.append("=" * 60)

    return "\n".join(lines)


class SuggestionType(str, Enum):
    """Types of prompt suggestions."""
    IMPROVEMENT = "improvement"  # General improvement to prompt
    BUG = "bug"  # Prompt produces incorrect behavior
    CLARIFICATION = "clarification"  # Prompt is ambiguous
    MOD_SPECIFIC = "mod_specific"  # MOD-specific rule addition/change
    MISSING_CASE = "missing_case"  # Prompt doesn't handle a case
    GENERAL = "general"  # General feedback based on conversation/trace (no specific agent)


class PromptSuggestion(BaseModel):
    """A prompt improvement suggestion."""
    agent_id: Optional[str] = Field(None, description="Which agent's prompt this applies to (optional for general feedback)")
    suggestion_type: SuggestionType = Field(..., description="Type of suggestion")
    summary: str = Field(..., description="Brief summary of the suggestion (1-2 sentences)")
    detailed_reasoning: str = Field(..., description="Full explanation of why this change is needed")
    proposed_change: Optional[str] = Field(None, description="Specific text change if applicable")
    mod_id: Optional[str] = Field(None, description="MOD ID if this is MOD-specific")
    trace_id: Optional[str] = Field(None, description="Related trace ID for context")
    conversation_context: Optional[str] = Field(None, description="Recent conversation excerpt")


class SuggestionSubmission(BaseModel):
    """Full submission record for a suggestion."""
    suggestion_id: str
    suggestion: PromptSuggestion
    submitted_by: str  # Curator email/ID
    submitted_at: datetime
    source: str  # "manual" or "opus_tool"


async def submit_suggestion_sns(
    suggestion: PromptSuggestion,
    submitted_by: str,
    source: str = "manual"
) -> dict:
    """
    Submit a prompt suggestion via SNS.

    Args:
        suggestion: The suggestion details
        submitted_by: Curator email/ID
        source: How it was submitted ("manual" or "opus_tool")

    Returns:
        dict with suggestion_id and status
    """
    suggestion_id = str(uuid.uuid4())
    submitted_at = datetime.utcnow()

    # Build the SNS message
    message = {
        "type": "prompt_suggestion",
        "suggestion_id": suggestion_id,
        "agent_id": suggestion.agent_id,
        "mod_id": suggestion.mod_id,
        "suggestion_type": suggestion.suggestion_type.value,
        "summary": suggestion.summary,
        "detailed_reasoning": suggestion.detailed_reasoning,
        "proposed_change": suggestion.proposed_change,
        "trace_id": suggestion.trace_id,
        "conversation_context": suggestion.conversation_context,
        "submitted_by": submitted_by,
        "submitted_at": submitted_at.isoformat(),
        "source": source,
    }

    # Check if SNS is configured (uses separate topic from user feedback)
    sns_topic_arn = os.getenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN")
    use_sns = os.getenv("PROMPT_SUGGESTIONS_USE_SNS", "false").lower() == "true"

    if use_sns and sns_topic_arn:
        try:
            sns_region = os.getenv("SNS_REGION", "us-east-1")
            # Use AWS_PROFILE if set (for local dev), otherwise use default credential chain
            aws_profile = os.getenv("AWS_PROFILE")
            if aws_profile:
                session = boto3.Session(profile_name=aws_profile)
                sns_client = session.client("sns", region_name=sns_region)
            else:
                sns_client = boto3.client("sns", region_name=sns_region)

            # Format subject for email (use 'General' if no agent_id)
            agent_label = suggestion.agent_id or "General"
            subject = f"[Prompt Suggestion] {suggestion.suggestion_type.value}: {agent_label}"
            if suggestion.mod_id:
                subject += f" ({suggestion.mod_id})"

            # Format message for human readability
            formatted_message = _format_suggestion_email(message)

            # Build message attributes (agent_id only if present)
            message_attrs = {
                "type": {
                    "DataType": "String",
                    "StringValue": "prompt_suggestion"
                },
                "suggestion_type": {
                    "DataType": "String",
                    "StringValue": suggestion.suggestion_type.value
                }
            }
            if suggestion.agent_id:
                message_attrs["agent_id"] = {
                    "DataType": "String",
                    "StringValue": suggestion.agent_id
                }

            # Send to SNS
            response = sns_client.publish(
                TopicArn=sns_topic_arn,
                Subject=subject[:100],  # SNS subject limit
                Message=formatted_message,
                MessageAttributes=message_attrs
            )

            logger.info('Suggestion %s sent to SNS: %s', suggestion_id, response['MessageId'])

            return {
                "status": "success",
                "suggestion_id": suggestion_id,
                "sns_message_id": response["MessageId"],
            }

        except Exception as e:
            logger.error('Failed to send suggestion to SNS: %s', e, exc_info=True)
            # Fall through to log-only mode

    # Log-only mode (SNS not configured or failed)
    logger.info('Prompt suggestion received (SNS disabled): %s', json.dumps(message, indent=2))

    return {
        "status": "success",
        "suggestion_id": suggestion_id,
        "sns_status": "disabled" if not use_sns else "failed",
        "message": "Suggestion logged locally (SNS not configured)" if not use_sns else "SNS failed, logged locally"
    }


# Tool definition for Opus
SUBMIT_SUGGESTION_TOOL = {
    "name": "submit_prompt_suggestion",
    "description": """Submit a prompt improvement suggestion to the development team.

Use this tool when:
1. You've identified a concrete improvement to an agent's prompt or general feedback
2. The user agrees the suggestion should be submitted
3. You have enough detail to make an actionable recommendation

Always ask the user for confirmation before submitting unless they explicitly
told you to submit suggestions automatically.

The suggestion will be sent to the development team for review.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The agent ID whose prompt this suggestion applies to (e.g., 'gene_expression', 'supervisor'). Optional for general feedback based on trace/conversation."
            },
            "suggestion_type": {
                "type": "string",
                "enum": ["improvement", "bug", "clarification", "mod_specific", "missing_case", "general"],
                "description": "Type of suggestion: improvement (general enhancement), bug (incorrect behavior), clarification (ambiguous), mod_specific (MOD rule change), missing_case (unhandled scenario), general (feedback based on trace/conversation not tied to specific prompt)"
            },
            "summary": {
                "type": "string",
                "description": "Brief 1-2 sentence summary of the suggestion"
            },
            "detailed_reasoning": {
                "type": "string",
                "description": "Full explanation of why this change is needed and what problem it solves"
            },
            "proposed_change": {
                "type": "string",
                "description": "Optional: The specific text or structural change you're proposing"
            }
        },
        "required": ["suggestion_type", "summary", "detailed_reasoning"]
    }
}
