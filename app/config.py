import os
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/data/store_intelligence.db")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()


STORE_METADATA = {
    "store_1": {
        "store_id": "store_1",
        "store_name": "Store 1",
        "store_location": "Brigade Bangalore",
        "city": "Bangalore"
    },
    "store_2": {
        "store_id": "store_2",
        "store_name": "Store 2",
        "store_location": "Colaba Mumbai",
        "city": "Mumbai"
    }
}


def get_store_info(store_id: str) -> dict:
    """Returns store metadata dict for the given store_id, falling back gracefully."""
    return STORE_METADATA.get(store_id, {
        "store_id": store_id,
        "store_name": store_id,
        "store_location": "Unknown Location",
        "city": "Unknown City"
    })

