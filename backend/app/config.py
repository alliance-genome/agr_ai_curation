from pydantic_settings import BaseSettings
from pydantic import ConfigDict, Field
from functools import lru_cache
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class Settings(BaseSettings):
    # API Keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # LangSmith Configuration
    langsmith_api_key: str = Field(default="", env="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="ai-curation-dev", env="LANGSMITH_PROJECT")
    langsmith_enabled: bool = Field(default=False, env="LANGSMITH_ENABLED")
    langsmith_tracing_sampling_rate: float = Field(
        default=1.0, env="LANGSMITH_TRACING_SAMPLING_RATE"
    )

    # Database (using SQLite for testing)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test_database.db")

    # Model Settings
    default_model: str = "gpt-4o"
    max_tokens: int = 2048
    temperature: float = 0.7

    # Embedding Settings
    embedding_model_name: str = os.getenv(
        "EMBEDDING_MODEL_NAME", "text-embedding-3-small"
    )
    embedding_model_version: str = os.getenv("EMBEDDING_MODEL_VERSION", "1.0")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", 1536))
    embedding_max_batch_size: int = int(os.getenv("EMBEDDING_MAX_BATCH_SIZE", 128))
    embedding_default_batch_size: int = int(
        os.getenv("EMBEDDING_DEFAULT_BATCH_SIZE", 64)
    )

    ontology_embedding_model_name: str = os.getenv("ONTOLOGY_EMBEDDING_MODEL_NAME", "")
    ontology_embedding_model_version: str = os.getenv(
        "ONTOLOGY_EMBEDDING_MODEL_VERSION", ""
    )
    ontology_embedding_batch_size: int = int(
        os.getenv("ONTOLOGY_EMBEDDING_BATCH_SIZE", 0)
    )
    ontology_embedding_dimensions: int = int(
        os.getenv("ONTOLOGY_EMBEDDING_DIMENSIONS", 0)
    )
    ontology_embedding_max_batch_size: int = int(
        os.getenv("ONTOLOGY_EMBEDDING_MAX_BATCH_SIZE", 0)
    )

    # Retrieval/Reranking Settings
    rag_rerank_top_k: int = int(os.getenv("RAG_RERANK_TOP_K", 5))
    rag_confidence_threshold: float = float(os.getenv("RAG_CONFIDENCE_THRESHOLD", 0.2))
    hybrid_vector_k: int = int(os.getenv("HYBRID_VECTOR_K", 50))
    hybrid_lexical_k: int = int(os.getenv("HYBRID_LEXICAL_K", 50))
    hybrid_max_results: int = int(os.getenv("HYBRID_MAX_RESULTS", 100))
    mmr_lambda: float = float(os.getenv("MMR_LAMBDA", 0.7))

    uploads_dir: str = os.getenv("UPLOADS_DIR", "/tmp/uploads")
    pdf_extraction_strategy: str = os.getenv("PDF_EXTRACTION_STRATEGY", "fast")
    disease_ontology_path: str = os.getenv("DISEASE_ONTOLOGY_PATH", "doid.obo.txt")

    # Application Settings
    debug_mode: bool = False
    api_url: str = "http://localhost:8002"
    frontend_url: str = "http://localhost:3000"

    model_config = ConfigDict(env_file=".env", extra="ignore")

    @property
    def langsmith_is_configured(self) -> bool:
        """Check if LangSmith is properly configured."""
        return bool(self.langsmith_enabled and self.langsmith_api_key)


@lru_cache()
def get_settings():
    return Settings()
