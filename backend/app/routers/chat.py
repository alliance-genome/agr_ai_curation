from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
import openai
from ..database import get_db
from ..config import get_settings
from ..models import ChatHistory
import uuid

router = APIRouter()
settings = get_settings()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatMessage]] = []
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str

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
            content=request.message
        )
        db.add(user_msg)
        
        # For now, return a stub response
        # TODO: Implement actual OpenAI/Anthropic integration
        response_text = f"I received your message: '{request.message}'. This is a stub response. AI integration will be implemented soon."
        
        # Store assistant response in history
        assistant_msg = ChatHistory(
            session_id=session_id,
            role="assistant",
            content=response_text
        )
        db.add(assistant_msg)
        db.commit()
        
        return ChatResponse(
            response=response_text,
            session_id=session_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))