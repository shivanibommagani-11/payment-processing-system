import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "password")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "payments_db")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", 5432))

    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    CACHE_DIR: str = os.getenv("CACHE_DIR", "./.cache")

    class Config:
        env_file = ".env"

settings = Settings()
