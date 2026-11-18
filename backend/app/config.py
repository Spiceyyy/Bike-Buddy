from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables."""

    database_url: str
    graphhopper_url: str = "http://localhost:8989"
    # "scenic" routes like "bike" until the scoring pipeline has written zones.
    graphhopper_profile: str = "scenic"
    graphhopper_timeout_seconds: float = 30.0
    cors_origins: list[str] = ["http://localhost:3000"]

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_timeout_seconds: float = 45.0

    geocoder_url: str = "https://nominatim.openstreetmap.org"
    geocoder_timeout_seconds: float = 10.0

    weather_url: str = "https://api.open-meteo.com"
    weather_timeout_seconds: float = 5.0

    # Requests per client address; 0 turns a limit off (the default, for local use).
    plan_requests_per_minute: int = 0
    plan_requests_per_day: int = 0
    feedback_requests_per_minute: int = 0
