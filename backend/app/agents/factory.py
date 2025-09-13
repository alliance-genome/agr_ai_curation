"""
Agent Factory for managing different AI agents and models

This factory creates and manages PydanticAI agents with different models
and configurations, replacing the old AI service factory.
"""

import os
import logging
from typing import Dict, Optional, Union, List
from enum import Enum

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.google import GoogleModel

from .biocuration_agent import BioCurationAgent, BioCurationDependencies
from .models import BioCurationOutput, EntityExtractionOutput

logger = logging.getLogger(__name__)


class ModelProvider(str, Enum):
    """Supported model providers"""

    OPENAI = "openai"
    GOOGLE = "google"
    ANTHROPIC = "anthropic"


class ModelConfig:
    """Configuration for different models"""

    MODELS = {
        # OpenAI models
        "openai:gpt-4o": {
            "provider": ModelProvider.OPENAI,
            "name": "gpt-4o",
            "description": "OpenAI GPT-4 Optimized",
            "supports_streaming": True,
            "supports_tools": True,
            "max_tokens": 128000,
        },
        "openai:gpt-4o-mini": {
            "provider": ModelProvider.OPENAI,
            "name": "gpt-4o-mini",
            "description": "OpenAI GPT-4 Mini",
            "supports_streaming": True,
            "supports_tools": True,
            "max_tokens": 128000,
        },
        "openai:gpt-3.5-turbo": {
            "provider": ModelProvider.OPENAI,
            "name": "gpt-3.5-turbo",
            "description": "OpenAI GPT-3.5 Turbo",
            "supports_streaming": True,
            "supports_tools": True,
            "max_tokens": 16385,
        },
        # Google models
        "google-gla:gemini-2.0-flash-exp": {
            "provider": ModelProvider.GOOGLE,
            "name": "gemini-2.0-flash-exp",
            "description": "Google Gemini 2.0 Flash Experimental",
            "supports_streaming": True,
            "supports_tools": True,
            "max_tokens": 1048576,
        },
        "google-gla:gemini-1.5-pro": {
            "provider": ModelProvider.GOOGLE,
            "name": "gemini-1.5-pro",
            "description": "Google Gemini 1.5 Pro",
            "supports_streaming": True,
            "supports_tools": True,
            "max_tokens": 2097152,
        },
        "google-gla:gemini-1.5-flash": {
            "provider": ModelProvider.GOOGLE,
            "name": "gemini-1.5-flash",
            "description": "Google Gemini 1.5 Flash",
            "supports_streaming": True,
            "supports_tools": True,
            "max_tokens": 1048576,
        },
    }

    @classmethod
    def get_available_models(cls) -> Dict[str, List[str]]:
        """Get available models grouped by provider"""
        models = {}
        for model_id, config in cls.MODELS.items():
            provider = config["provider"].value
            if provider not in models:
                models[provider] = []
            models[provider].append(model_id.split(":")[1])
        return models

    @classmethod
    def get_default_model(cls, provider: str = "openai") -> str:
        """Get default model for a provider"""
        defaults = {
            "openai": "gpt-4o",
            "google": "gemini-2.0-flash-exp",
            "anthropic": "claude-3-sonnet",
        }
        return defaults.get(provider, "gpt-4o")


class AgentFactory:
    """
    Factory for creating and managing PydanticAI agents.
    Replaces the old AIServiceFactory with PydanticAI-based agents.
    """

    _agents: Dict[str, BioCurationAgent] = {}
    _specialized_agents: Dict[str, Agent] = {}

    @classmethod
    def get_biocuration_agent(
        cls,
        model: str = "openai:gpt-4o",
        force_new: bool = False,
    ) -> BioCurationAgent:
        """
        Get or create a BioCuration agent.

        Args:
            model: Model identifier (e.g., "openai:gpt-4o")
            force_new: Force creation of a new agent

        Returns:
            BioCurationAgent instance
        """
        if not force_new and model in cls._agents:
            return cls._agents[model]

        # Validate model
        if model not in ModelConfig.MODELS:
            available = ", ".join(ModelConfig.MODELS.keys())
            raise ValueError(f"Unknown model: {model}. Available models: {available}")

        # Set up API keys based on provider
        provider = model.split(":")[0]
        cls._setup_api_keys(provider)

        # Create agent
        agent = BioCurationAgent(model=model)
        cls._agents[model] = agent

        logger.info(f"Created BioCuration agent with model: {model}")
        return agent

    @classmethod
    def get_entity_extraction_agent(
        cls,
        model: str = "openai:gpt-4o",
    ) -> Agent:
        """
        Get a specialized agent for entity extraction only.

        Args:
            model: Model identifier

        Returns:
            Agent configured for entity extraction
        """
        agent_key = f"entity_extraction_{model}"

        if agent_key not in cls._specialized_agents:
            cls._setup_api_keys(model.split(":")[0])

            agent = Agent(
                model,
                output_type=EntityExtractionOutput,
                system_prompt="""
You are a specialized entity extraction agent focused on identifying
biological entities in scientific text. Extract:
- Genes and gene products
- Proteins
- Diseases and disorders
- Phenotypes
- Chemical compounds
- Pathways
- Organisms
- Cell types
- Anatomical structures

Provide normalized forms and database IDs when possible.
Rate your confidence based on context clarity.
""",
            )

            cls._specialized_agents[agent_key] = agent
            logger.info(f"Created entity extraction agent with model: {model}")

        return cls._specialized_agents[agent_key]

    @classmethod
    def create_custom_agent(
        cls,
        model: str,
        output_type: type,
        system_prompt: str,
        deps_type: Optional[type] = None,
    ) -> Agent:
        """
        Create a custom agent with specific configuration.

        Args:
            model: Model identifier
            output_type: Pydantic model for output validation
            system_prompt: System prompt for the agent
            deps_type: Optional dependency type

        Returns:
            Configured Agent instance
        """
        cls._setup_api_keys(model.split(":")[0])

        agent = Agent(
            model,
            output_type=output_type,
            system_prompt=system_prompt,
            deps_type=deps_type,
        )

        return agent

    @classmethod
    def _setup_api_keys(cls, provider: str):
        """
        Set up API keys for the provider if not already set.

        Args:
            provider: Provider name (openai, google, anthropic)
        """
        if provider == "openai":
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                raise ValueError("OPENAI_API_KEY not found in environment")
            # PydanticAI will automatically use OPENAI_API_KEY from env

        elif provider in ["google", "google-gla"]:
            key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not key:
                raise ValueError("GEMINI_API_KEY not found in environment")
            # Set for PydanticAI to use
            os.environ["GOOGLE_API_KEY"] = key

        elif provider == "anthropic":
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise ValueError("ANTHROPIC_API_KEY not found in environment")
            # PydanticAI will automatically use ANTHROPIC_API_KEY from env

    @classmethod
    def get_available_models(cls) -> Dict[str, List[str]]:
        """Get all available models grouped by provider"""
        return ModelConfig.get_available_models()

    @classmethod
    def get_model_info(cls, model: str) -> Dict[str, any]:
        """
        Get information about a specific model.

        Args:
            model: Model identifier

        Returns:
            Model configuration dict
        """
        return ModelConfig.MODELS.get(model, {})

    @classmethod
    def validate_model(cls, model: str) -> bool:
        """
        Validate if a model is supported.

        Args:
            model: Model identifier

        Returns:
            True if model is supported
        """
        return model in ModelConfig.MODELS

    @classmethod
    def clear_cache(cls):
        """Clear all cached agents"""
        cls._agents.clear()
        cls._specialized_agents.clear()
        logger.info("Cleared agent cache")

    @classmethod
    async def test_model(cls, model: str) -> bool:
        """
        Test if a model is working correctly.

        Args:
            model: Model identifier

        Returns:
            True if model responds successfully
        """
        try:
            agent = cls.get_biocuration_agent(model, force_new=True)
            result = await agent.process("Test message: respond with 'OK'")
            return "OK" in result.response.upper()
        except Exception as e:
            logger.error(f"Model test failed for {model}: {e}")
            return False
