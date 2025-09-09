"""
OpenAI service client for chat completions
Handles both standard and streaming responses
"""

import os
import logging
from typing import List, AsyncGenerator, Optional
from openai import AsyncOpenAI
from ..ai_config import ChatMessage, AIConfiguration

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for OpenAI API interactions"""

    def __init__(self, api_key: Optional[str] = None):
        """Initialize OpenAI client"""
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")

        self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate_response(
        self,
        message: str,
        model: str = "gpt-4o",
        history: Optional[List[ChatMessage]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a non-streaming response"""
        try:
            messages = self._prepare_messages(message, history)

            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise

    async def generate_streaming_response(
        self,
        message: str,
        model: str = "gpt-4o",
        history: Optional[List[ChatMessage]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming response"""
        try:
            messages = self._prepare_messages(message, history)

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
            logger.error(f"OpenAI streaming error: {str(e)}")
            raise

    def _prepare_messages(
        self, message: str, history: Optional[List[ChatMessage]] = None
    ) -> List[dict]:
        """Prepare messages for API call"""
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
        """Get list of available OpenAI models"""
        return ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]
