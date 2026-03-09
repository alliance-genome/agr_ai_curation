"""Tests for prompt suggestion submission service."""

import pytest

from src.lib.agent_studio import suggestion_service as svc


def _build_suggestion(agent_id: str | None = "gene_expression_extractor"):
    return svc.PromptSuggestion(
        agent_id=agent_id,
        suggestion_type=svc.SuggestionType.IMPROVEMENT,
        summary="Improve exclusion handling",
        detailed_reasoning="Need better handling for marker-only statements.",
        proposed_change="Add explicit marker exclusion rule.",
        group_id="WB",
        trace_id="trace-123",
        conversation_context="Curator asked to remove marker-only annotation.",
    )


def test_prompt_suggestion_accepts_legacy_mod_specific_alias():
    suggestion = svc.PromptSuggestion(
        agent_id="gene",
        suggestion_type="mod_specific",
        summary="Legacy alias",
        detailed_reasoning="Should normalize to group_specific.",
        mod_id="WB",
    )

    assert suggestion.suggestion_type == svc.SuggestionType.GROUP_SPECIFIC
    assert suggestion.group_id == "WB"


def test_format_suggestion_email_includes_optional_sections():
    message = {
        "suggestion_id": "s-1",
        "submitted_at": "2026-01-01T00:00:00",
        "submitted_by": "curator@example.org",
        "source": "manual",
        "agent_id": "gene_expression_extractor",
        "group_id": "WB",
        "suggestion_type": "improvement",
        "summary": "Improve extraction specificity",
        "detailed_reasoning": "Marker-only lines should be excluded.",
        "proposed_change": "Add marker exclusion bullet to prompt.",
        "trace_id": "trace-1",
        "conversation_context": "Sample context",
    }

    rendered = svc._format_suggestion_email(message)

    assert "PROMPT IMPROVEMENT SUGGESTION" in rendered
    assert "Agent:           gene_expression_extractor" in rendered
    assert "Group:           WB" in rendered
    assert "PROPOSED CHANGE" in rendered
    assert "DEBUG INFO" in rendered
    assert "CONVERSATION CONTEXT" in rendered


def test_format_suggestion_email_omits_optional_sections_when_missing():
    message = {
        "suggestion_id": "s-2",
        "submitted_at": "2026-01-01T00:00:00",
        "submitted_by": "curator@example.org",
        "source": "manual",
        "agent_id": "general",
        "suggestion_type": "general",
        "summary": "General note",
        "detailed_reasoning": "No optional fields should render.",
    }

    rendered = svc._format_suggestion_email(message)
    assert "PROPOSED CHANGE" not in rendered
    assert "DEBUG INFO" not in rendered
    assert "CONVERSATION CONTEXT" not in rendered


@pytest.mark.asyncio
async def test_submit_suggestion_logs_when_sns_disabled(monkeypatch):
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "false")
    monkeypatch.delenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", raising=False)

    result = await svc.submit_suggestion_sns(_build_suggestion(), submitted_by="curator@example.org")

    assert result["status"] == "success"
    assert result["sns_status"] == "disabled"
    assert "suggestion_id" in result


@pytest.mark.asyncio
async def test_submit_suggestion_uses_default_boto3_client_when_no_profile(monkeypatch):
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
    monkeypatch.setenv("SNS_REGION", "us-west-2")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    published = {}

    class FakeClient:
        def publish(self, **kwargs):
            published.update(kwargs)
            return {"MessageId": "msg-1"}

    monkeypatch.setattr(svc.boto3, "client", lambda service, region_name: FakeClient())

    result = await svc.submit_suggestion_sns(_build_suggestion(), submitted_by="curator@example.org")

    assert result["status"] == "success"
    assert result["sns_message_id"] == "msg-1"
    assert published["TopicArn"] == "arn:aws:sns:us-east-1:123:topic"
    assert published["MessageAttributes"]["type"]["StringValue"] == "prompt_suggestion"
    assert published["MessageAttributes"]["agent_id"]["StringValue"] == "gene_expression_extractor"
    assert published["Subject"].startswith("[Prompt Suggestion] improvement: gene_expression_extractor")
    assert len(published["Subject"]) <= 100


@pytest.mark.asyncio
async def test_submit_suggestion_uses_profiled_session_when_profile_present(monkeypatch):
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
    monkeypatch.setenv("SNS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_PROFILE", "ctabone")

    session_args = {}

    class FakeClient:
        def publish(self, **_kwargs):
            return {"MessageId": "msg-2"}

    class FakeSession:
        def __init__(self, profile_name):
            session_args["profile_name"] = profile_name

        def client(self, service, region_name):
            session_args["service"] = service
            session_args["region_name"] = region_name
            return FakeClient()

    monkeypatch.setattr(svc.boto3, "Session", FakeSession)
    monkeypatch.setattr(
        svc.boto3,
        "client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("default client should not be used")),
    )

    result = await svc.submit_suggestion_sns(_build_suggestion(), submitted_by="curator@example.org")

    assert result["sns_message_id"] == "msg-2"
    assert session_args == {
        "profile_name": "ctabone",
        "service": "sns",
        "region_name": "us-east-1",
    }


@pytest.mark.asyncio
async def test_submit_suggestion_falls_back_to_log_mode_when_publish_fails(monkeypatch):
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "true")
    monkeypatch.setenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123:topic")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    class FakeClient:
        def publish(self, **_kwargs):
            raise RuntimeError("sns unavailable")

    monkeypatch.setattr(svc.boto3, "client", lambda *_args, **_kwargs: FakeClient())

    result = await svc.submit_suggestion_sns(_build_suggestion(agent_id=None), submitted_by="curator@example.org")

    assert result["status"] == "success"
    assert result["sns_status"] == "failed"
    assert result["message"] == "SNS failed, logged locally"


@pytest.mark.asyncio
async def test_submit_suggestion_treats_missing_topic_as_failed_when_enabled(monkeypatch):
    monkeypatch.setenv("PROMPT_SUGGESTIONS_USE_SNS", "true")
    monkeypatch.delenv("PROMPT_SUGGESTIONS_SNS_TOPIC_ARN", raising=False)

    result = await svc.submit_suggestion_sns(_build_suggestion(), submitted_by="curator@example.org")

    assert result["status"] == "success"
    assert result["sns_status"] == "failed"
