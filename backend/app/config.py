from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables."""

    database_url: str
    graphhopper_url: str = "http://localhost:8989"
    graphhopper_profile: str = "bike"
    graphhopper_timeout_seconds: float = 30.0
    cors_origins: list[str] = ["http://localhost:3000"]

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_timeout_seconds: float = 45.0

    geocoder_url: str = "https://nominatim.openstreetmap.org"
    geocoder_timeout_seconds: float = 10.0
