from pydantic_settings import BaseSettings
from functools import lru_cache
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

class Settings(BaseSettings):
    # API Keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Database (using SQLite for testing)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./test_database.db")
    
    # Model Settings
    default_model: str = "gpt-4o"
    max_tokens: int = 2048
    temperature: float = 0.7
    
    # Application Settings
    debug_mode: bool = False
    api_url: str = "http://localhost:8002"
    frontend_url: str = "http://localhost:3000"
    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()