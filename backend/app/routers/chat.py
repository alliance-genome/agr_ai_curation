from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import List, Optional, AsyncGenerator
from sqlalchemy.orm import Session
import json
import uuid
import logging

from ..database import get_db
from ..config import get_settings
from ..models import ChatHistory
from ..ai_config import (
    ChatRequest,
    ChatResponse,
    StreamingChatResponse,
    ModelListResponse,
    ChatMessage as ChatMessageModel,
)
from ..services.ai_service_factory import AIServiceFactory

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    """Process chat messages with AI"""
    try:
        # Generate or use existing session ID
        session_id = request.session_id or str(uuid.uuid4())

        # Store user message in history
        user_msg = ChatHistory(
            session_id=session_id,
            role="user",
            content=request.message,
            model_provider=None,  # User messages don't have model info
            model_name=None,
        )
        db.add(user_msg)
        db.flush()  # Flush to get ID but don't commit yet

        # Generate AI response
        response_text = await AIServiceFactory.generate_response(
            message=request.message,
            provider=request.provider,
            model=request.model,
            history=request.history,
            temperature=0.7,
            max_tokens=4096,
        )

        # Store assistant response in history with model metadata
        assistant_msg = ChatHistory(
            session_id=session_id,
            role="assistant",
            content=response_text,
            model_provider=request.provider.value if request.provider else "openai",
            model_name=request.model
            or AIServiceFactory.get_default_model(request.provider or "openai"),
        )
        db.add(assistant_msg)
        db.commit()

        return ChatResponse(
            response=response_text,
            session_id=session_id,
            provider=request.provider.value if request.provider else "openai",
            model=request.model
            or AIServiceFactory.get_default_model(request.provider or "openai"),
            is_streaming=False,
        )

    except ValueError as e:
        # Handle missing API keys or configuration errors
        logger.error(f"Configuration error: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"AI service configuration error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Chat endpoint error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)}")


@router.post("/stream")
async def chat_stream(request: ChatRequest, db: Session = Depends(get_db)):
    """Stream chat responses using Server-Sent Events"""

    async def generate_sse_response() -> AsyncGenerator[str, None]:
        try:
            # Generate or use existing session ID
            session_id = request.session_id or str(uuid.uuid4())

            # Store user message in history
            user_msg = ChatHistory(
                session_id=session_id,
                role="user",
                content=request.message,
                model_provider=None,
                model_name=None,
            )
            db.add(user_msg)
            db.flush()

            # Collect full response for storage
            full_response = ""

            # Stream response from AI service
            async for chunk in AIServiceFactory.generate_streaming_response(
                message=request.message,
                provider=request.provider,
                model=request.model,
                history=request.history,
                temperature=0.7,
                max_tokens=4096,
            ):
                full_response += chunk

                # Send SSE chunk
                chunk_data = StreamingChatResponse(
                    delta=chunk,
                    session_id=session_id,
                    provider=request.provider.value if request.provider else "openai",
                    model=request.model
                    or AIServiceFactory.get_default_model(request.provider or "openai"),
                    is_complete=False,
                )

                yield f"data: {chunk_data.json()}\n\n"

            # Send completion marker
            completion_data = StreamingChatResponse(
                delta="",
                session_id=session_id,
                provider=request.provider.value if request.provider else "openai",
                model=request.model
                or AIServiceFactory.get_default_model(request.provider or "openai"),
                is_complete=True,
            )
            yield f"data: {completion_data.json()}\n\n"

            # Store complete assistant response in history
            assistant_msg = ChatHistory(
                session_id=session_id,
                role="assistant",
                content=full_response,
                model_provider=request.provider.value if request.provider else "openai",
                model_name=request.model
                or AIServiceFactory.get_default_model(request.provider or "openai"),
            )
            db.add(assistant_msg)
            db.commit()

        except Exception as e:
            logger.error(f"Streaming error: {str(e)}")
            error_data = {
                "error": str(e),
                "session_id": session_id if "session_id" in locals() else None,
            }
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        generate_sse_response(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )


@router.get("/models", response_model=ModelListResponse)
async def get_models():
    """Get available AI models for each provider"""
    try:
        models = AIServiceFactory.get_available_models()
        return ModelListResponse(**models)
    except Exception as e:
        logger.error(f"Error fetching models: {str(e)}")
        # Return default models even if service initialization fails
        return ModelListResponse()
