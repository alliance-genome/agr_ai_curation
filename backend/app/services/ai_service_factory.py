"""
AI Service Factory for unified access to different AI providers
Manages OpenAI and Gemini services through a common interface
"""

import os
import logging
from typing import List, AsyncGenerator, Optional, Union
from ..ai_config import ChatMessage, AIProvider, AIConfiguration
from .openai_service import OpenAIService
from .gemini_service import GeminiService

logger = logging.getLogger(__name__)


class AIServiceFactory:
    """Factory for creating and managing AI service instances"""

    _services = {}

    @classmethod
    def get_service(
        cls, provider: Union[AIProvider, str], api_key: Optional[str] = None
    ) -> Union[OpenAIService, GeminiService]:
        """Get or create an AI service instance"""

        # Convert string to enum if needed
        if isinstance(provider, str):
            provider = AIProvider(provider)

        # Check cache
        if provider in cls._services and not api_key:
            return cls._services[provider]

        # Create new service
        if provider == AIProvider.OPENAI:
            service = OpenAIService(api_key)
        elif provider == AIProvider.GEMINI:
            service = GeminiService(api_key)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

        # Cache if using default key
        if not api_key:
            cls._services[provider] = service

        return service

    @classmethod
    async def generate_response(
        cls,
        message: str,
        provider: Union[AIProvider, str] = AIProvider.OPENAI,
        model: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_key: Optional[str] = None,
    ) -> str:
        """Generate a response using the specified provider"""

        service = cls.get_service(provider, api_key)

        # Use default model if not specified
        if not model:
            model = cls.get_default_model(provider)

        return await service.generate_response(
            message=message,
            model=model,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @classmethod
    async def generate_streaming_response(
        cls,
        message: str,
        provider: Union[AIProvider, str] = AIProvider.OPENAI,
        model: Optional[str] = None,
        history: Optional[List[ChatMessage]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        api_key: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming response using the specified provider"""

        service = cls.get_service(provider, api_key)

        # Use default model if not specified
        if not model:
            model = cls.get_default_model(provider)

        async for chunk in service.generate_streaming_response(
            message=message,
            model=model,
            history=history,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    @classmethod
    def get_available_models(
        cls, provider: Optional[Union[AIProvider, str]] = None
    ) -> dict:
        """Get available models for all or specific provider"""

        models = {}

        if provider:
            # Get models for specific provider
            if isinstance(provider, str):
                provider = AIProvider(provider)

            if provider == AIProvider.OPENAI:
                models["openai"] = OpenAIService.get_available_models()
            elif provider == AIProvider.GEMINI:
                models["gemini"] = GeminiService.get_available_models()
        else:
            # Get all models
            models["openai"] = OpenAIService.get_available_models()
            models["gemini"] = GeminiService.get_available_models()

        return models

    @classmethod
    def get_default_model(cls, provider: Union[AIProvider, str]) -> str:
        """Get default model for a provider"""

        if isinstance(provider, str):
            provider = AIProvider(provider)

        if provider == AIProvider.OPENAI:
            return "gpt-4o"
        elif provider == AIProvider.GEMINI:
            return "gemini-2.0-flash"
        else:
            raise ValueError(f"No default model for provider: {provider}")

    @classmethod
    def validate_model_provider_combination(
        cls, provider: Union[AIProvider, str], model: str
    ) -> bool:
        """Validate that a model is compatible with a provider"""

        if isinstance(provider, str):
            provider = AIProvider(provider)

        available_models = cls.get_available_models(provider)
        provider_key = provider.value

        return model in available_models.get(provider_key, [])

    @classmethod
    def auto_correct_model(cls, provider: Union[AIProvider, str], model: str) -> str:
        """Auto-correct model if incompatible with provider"""

        if cls.validate_model_provider_combination(provider, model):
            return model

        # Return default model for provider
        return cls.get_default_model(provider)

    @classmethod
    def get_provider_from_environment(cls) -> AIProvider:
        """Get default provider from environment variables"""

        default_provider = os.getenv("DEFAULT_AI_PROVIDER", "openai")

        try:
            return AIProvider(default_provider)
        except ValueError:
            logger.warning(
                f"Invalid default provider: {default_provider}, using OpenAI"
            )
            return AIProvider.OPENAI

    @classmethod
    def get_model_from_environment(cls) -> str:
        """Get default model from environment variables"""

        return os.getenv("DEFAULT_AI_MODEL", "gpt-4o")
