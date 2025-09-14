"""
Agent endpoints using PydanticAI

Replaces the old chat endpoints with structured agent-based interactions.
"""

import logging
import uuid
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import json

from ..database import get_db
from ..agents import (
    AgentFactory,
    BioCurationDependencies,
    BioCurationOutput,
    CurationContext,
)
from ..agents.models import (
    AgentRequest,
    AgentResponse,
    EntityExtractionOutput,
    StreamingUpdate,
)
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_core import to_jsonable_python

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
router = APIRouter()


@router.post("/biocurate", response_model=AgentResponse)
async def biocurate(
    request: AgentRequest,
    db: Session = Depends(get_db),
) -> AgentResponse:
    """
    Process a biocuration request using PydanticAI agent.

    This endpoint replaces the old /chat endpoint with structured outputs.
    """
    try:
        # Generate or use existing session ID
        session_id = request.session_id or str(uuid.uuid4())

        # Determine model to use
        model = request.model_preference or "openai:gpt-4o"

        # Get or create agent
        agent = AgentFactory.get_biocuration_agent(model)

        # Prepare dependencies
        deps = BioCurationDependencies(
            db_session=db,
            session_id=session_id,
            context=request.context,
            user_preferences={
                "include_entities": request.include_entities,
                "include_annotations": request.include_annotations,
            },
        )

        # Deserialize message history if provided
        message_history = None
        if request.message_history:
            try:
                message_history = ModelMessagesTypeAdapter.validate_python(
                    request.message_history
                )
            except Exception as e:
                logger.warning(f"Failed to deserialize message history: {e}")

        # Process request
        if request.stream:
            # For streaming, return a different response type
            return StreamingResponse(
                _stream_response(
                    agent, request.message, deps, session_id, message_history
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # Regular processing
            output, new_messages = await agent.process(
                request.message, deps, message_history=message_history
            )

            # Get usage if available
            usage = await agent.get_usage()

            # Serialize message history for response
            serialized_history = to_jsonable_python(new_messages)

            return AgentResponse(
                output=output,
                session_id=session_id,
                usage=usage,
                model=model,
                message_history=serialized_history,
            )

    except ValueError as e:
        logger.error(f"Configuration error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent configuration error: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Agent processing error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Agent processing error: {str(e)}",
        )


@router.post("/extract-entities", response_model=EntityExtractionOutput)
async def extract_entities(
    text: str,
    model: Optional[str] = None,
) -> EntityExtractionOutput:
    """
    Extract biological entities from text.

    Specialized endpoint for entity extraction only.
    """
    try:
        model = model or "openai:gpt-4o"
        agent = AgentFactory.get_entity_extraction_agent(model)

        result = await agent.run(text)
        return result.output

    except Exception as e:
        logger.error(f"Entity extraction error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Entity extraction failed: {str(e)}",
        )


@router.post("/biocurate/stream")
async def biocurate_stream(
    request: AgentRequest,
    db: Session = Depends(get_db),
):
    """
    Stream biocuration responses using Server-Sent Events.

    This endpoint replaces the old /chat/stream endpoint.
    """
    logger.info(
        f"Received stream request: message={request.message[:50]}..., model={request.model_preference}"
    )
    try:
        session_id = request.session_id or str(uuid.uuid4())
        model = request.model_preference or "openai:gpt-4o"

        logger.info(f"Creating agent with model: {model}")
        agent = AgentFactory.get_biocuration_agent(model)

        deps = BioCurationDependencies(
            db_session=db,
            session_id=session_id,
            context=request.context,
            user_preferences={
                "include_entities": request.include_entities,
                "include_annotations": request.include_annotations,
            },
        )
        logger.info(f"Dependencies created, session_id: {session_id}")

        # Deserialize message history if provided
        message_history = None
        if request.message_history:
            try:
                message_history = ModelMessagesTypeAdapter.validate_python(
                    request.message_history
                )
            except Exception as e:
                logger.warning(f"Failed to deserialize message history: {e}")

        return StreamingResponse(
            _stream_response(agent, request.message, deps, session_id, message_history),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as e:
        logger.error(f"Streaming error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Streaming failed: {str(e)}",
        )


async def _stream_response(
    agent,
    message: str,
    deps: BioCurationDependencies,
    session_id: Optional[str] = None,
    message_history: Optional[List] = None,
):
    """
    Generate SSE stream for agent responses.
    """
    logger.info(f"Starting SSE stream for message: {message[:50]}...")
    try:
        # Process with streaming
        update_count = 0
        async for update in agent._process_stream(message, deps, message_history):
            update_count += 1
            logger.debug(f"Sending update #{update_count}: type={update.type}")
            # Convert to SSE format
            data = {
                "type": update.type,
                "content": update.content,
                "metadata": update.metadata,
                "timestamp": update.timestamp.isoformat(),
                "session_id": session_id,
            }

            yield f"data: {json.dumps(data)}\n\n"

        # Send completion marker
        logger.info(f"Stream complete, sent {update_count} updates")
        completion_data = {
            "type": "complete",
            "session_id": session_id,
        }
        yield f"data: {json.dumps(completion_data)}\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {str(e)}", exc_info=True)
        error_data = {
            "type": "error",
            "error": str(e),
            "session_id": session_id,
        }
        yield f"data: {json.dumps(error_data)}\n\n"


@router.get("/models")
async def get_models() -> Dict[str, Any]:
    """
    Get available AI models for agents.

    Returns models grouped by provider.
    """
    try:
        models = AgentFactory.get_available_models()

        # Format for compatibility with old endpoint
        return {
            "openai": models.get("openai", []),
            "google": models.get("google", []),
            "anthropic": models.get("anthropic", []),
        }

    except Exception as e:
        logger.error(f"Error fetching models: {str(e)}")
        return {
            "openai": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"],
            "google": ["gemini-2.0-flash-exp", "gemini-1.5-pro", "gemini-1.5-flash"],
            "anthropic": [],
        }


@router.get("/agent-info/{model}")
async def get_agent_info(model: str) -> Dict[str, Any]:
    """
    Get information about a specific model/agent.

    Args:
        model: Model identifier (e.g., "openai:gpt-4o")

    Returns:
        Model configuration and capabilities
    """
    try:
        # Format model string if needed
        if ":" not in model:
            # Assume openai if no provider specified
            model = f"openai:{model}"

        info = AgentFactory.get_model_info(model)
        if not info:
            raise HTTPException(
                status_code=404,
                detail=f"Model {model} not found",
            )

        return info

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting model info: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get model info: {str(e)}",
        )


@router.post("/test-agent")
async def test_agent(
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Test if an agent/model is working correctly.

    Args:
        model: Model to test (defaults to "openai:gpt-4o")

    Returns:
        Test results
    """
    try:
        model = model or "openai:gpt-4o"
        success = await AgentFactory.test_model(model)

        return {
            "model": model,
            "success": success,
            "message": "Agent is working correctly" if success else "Agent test failed",
        }

    except Exception as e:
        logger.error(f"Agent test error: {str(e)}")
        return {
            "model": model,
            "success": False,
            "message": f"Test failed: {str(e)}",
        }
