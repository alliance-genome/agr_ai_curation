"""
Gemini service client using OpenAI compatibility mode
Leverages OpenAI SDK with Gemini endpoints for unified interface
"""

import os
import logging
from typing import List, AsyncGenerator, Optional
from openai import AsyncOpenAI
from ..ai_config import ChatMessage, AIConfiguration

logger = logging.getLogger(__name__)


class GeminiService:
    """Service for Gemini API interactions via OpenAI compatibility"""

    # Gemini OpenAI compatibility endpoint
    GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Gemini client using OpenAI SDK"""
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API key not configured")

        # Use OpenAI client with Gemini endpoint
        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.GEMINI_BASE_URL)

    async def generate_response(
        self,
        message: str,
        model: str = "gemini-2.0-flash",
        history: Optional[List[ChatMessage]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a non-streaming response using Gemini via OpenAI compatibility"""
        try:
            messages = self._prepare_messages(message, history)

            # Use OpenAI-compatible API
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"Gemini API error: {str(e)}")
            raise

    async def generate_streaming_response(
        self,
        message: str,
        model: str = "gemini-2.0-flash",
        history: Optional[List[ChatMessage]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming response using Gemini via OpenAI compatibility"""
        try:
            messages = self._prepare_messages(message, history)

            # Use OpenAI-compatible streaming
            stream = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"Gemini streaming error: {str(e)}")
            raise

    def _prepare_messages(
        self, message: str, history: Optional[List[ChatMessage]] = None
    ) -> List[dict]:
        """Prepare messages for API call (same format as OpenAI)"""
        messages = []

        # Add system message for context
        messages.append(
            {
                "role": "system",
                "content": "You are a helpful AI assistant for biological paper curation. Provide clear, accurate, and relevant information.",
            }
        )

        # Add conversation history
        if history:
            for msg in history:
                messages.append({"role": msg.role, "content": msg.content})

        # Add current message
        messages.append({"role": "user", "content": message})

        return messages

    @staticmethod
    def get_available_models() -> List[str]:
        """Get list of available Gemini models"""
        return ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"]
