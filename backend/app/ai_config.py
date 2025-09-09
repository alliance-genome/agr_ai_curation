"""
AI Configuration models for managing AI provider settings
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Literal
from enum import Enum


class AIProvider(str, Enum):
    """Supported AI providers"""

    OPENAI = "openai"
    GEMINI = "gemini"


class AIModel(str, Enum):
    """Available AI models"""

    # OpenAI models
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_35_TURBO = "gpt-3.5-turbo"

    # Gemini models (via OpenAI compatibility)
    GEMINI_20_FLASH = "gemini-2.0-flash"
    GEMINI_15_PRO = "gemini-1.5-pro"
    GEMINI_15_FLASH = "gemini-1.5-flash"


class ChatMessage(BaseModel):
    """Individual chat message"""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    """Enhanced chat request with AI provider selection"""

    message: str = Field(..., min_length=1, max_length=10000)
    history: Optional[List[ChatMessage]] = Field(default_factory=list)
    session_id: Optional[str] = None
    provider: Optional[AIProvider] = AIProvider.OPENAI
    model: Optional[str] = AIModel.GPT_4O
    stream: Optional[bool] = False

    @validator("model")
    def validate_model_provider_combination(cls, v, values):
        """Ensure model is compatible with selected provider"""
        provider = values.get("provider")

        openai_models = [AIModel.GPT_4O, AIModel.GPT_4O_MINI, AIModel.GPT_35_TURBO]
        gemini_models = [
            AIModel.GEMINI_20_FLASH,
            AIModel.GEMINI_15_PRO,
            AIModel.GEMINI_15_FLASH,
        ]

        if provider == AIProvider.OPENAI and v not in [m.value for m in openai_models]:
            # Auto-correct to default OpenAI model
            return AIModel.GPT_4O.value
        elif provider == AIProvider.GEMINI and v not in [
            m.value for m in gemini_models
        ]:
            # Auto-correct to default Gemini model
            return AIModel.GEMINI_20_FLASH.value

        return v


class ChatResponse(BaseModel):
    """Enhanced chat response with provider metadata"""

    response: str
    session_id: str
    provider: str
    model: str
    is_streaming: bool = False


class StreamingChatResponse(BaseModel):
    """Streaming response chunk for Server-Sent Events"""

    delta: str
    session_id: str
    provider: str
    model: str
    is_complete: bool = False


class ModelListResponse(BaseModel):
    """Response for available models endpoint"""

    openai: List[str] = Field(
        default=[
            AIModel.GPT_4O.value,
            AIModel.GPT_4O_MINI.value,
            AIModel.GPT_35_TURBO.value,
        ]
    )
    gemini: List[str] = Field(
        default=[
            AIModel.GEMINI_20_FLASH.value,
            AIModel.GEMINI_15_PRO.value,
            AIModel.GEMINI_15_FLASH.value,
        ],
        description="Gemini models accessed via OpenAI-compatible endpoints",
    )


class AIConfiguration(BaseModel):
    """Runtime AI configuration (not persisted to database)"""

    provider: AIProvider
    model: str
    api_key: Optional[str] = Field(None, exclude=True)  # Never serialize API key
    base_url: Optional[str] = None
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = 0.7

    @validator("base_url", always=True)
    def set_base_url(cls, v, values):
        """Set base URL based on provider"""
        if v:
            return v

        provider = values.get("provider")
        if provider == AIProvider.GEMINI:
            return "https://generativelanguage.googleapis.com/v1beta/openai/"

        return None  # OpenAI uses default
