from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    # --- LLM (OpenRouter via OpenAI-compatible API) ---
    agent_provider: str = Field(
        "openai-compatible",
        validation_alias=AliasChoices(
            "ARGENTURA_CONVERSATION_AGENT_PROVIDER", "agent_provider"
        ),
    )
    agent_endpoint: str = Field(
        "https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("ARGENTURA_AGENT_ENDPOINT", "agent_endpoint"),
    )
    agent_model: str = Field(
        "x-ai/grok-4.3",
        validation_alias=AliasChoices("ARGENTURA_AGENT_MODEL", "agent_model"),
    )
    agent_api_key: str = Field(
        "",
        validation_alias=AliasChoices("ARGENTURA_AGENT_API_KEY", "agent_api_key"),
    )

    # --- Argentura Suite Integration ---
    argentura_base_url: str = Field(
        "https://www.management-suite.com",
        validation_alias=AliasChoices("ARGENTURA_BASE_URL", "argentura_base_url"),
    )
    argentura_username: str = Field(
        "Test",
        validation_alias=AliasChoices("ARGENTURA_USERNAME", "argentura_username"),
    )
    argentura_pin: str = Field(
        "258036",
        validation_alias=AliasChoices("ARGENTURA_PIN", "argentura_pin"),
    )
    argentura_lady_name: str = Field(
        "Julia",
        validation_alias=AliasChoices("ARGENTURA_LADY_NAME", "argentura_lady_name"),
    )

    # --- Generation ---
    temperature: float = 0.9
    max_tokens: int = 400
    max_input_chars: int = 2000

    # --- Persona ---
    persona_path: Path = BASE_DIR / "prompts" / "luna_system_prompt.md"

    # --- Short-term chat history ---
    history_turns: int = 20
    session_ttl_seconds: int = 21600  # 6 hours context window
    redis_url: str = ""

    # --- Limits ---
    rate_limit_per_minute: int = 12
    daily_message_cap: int = 300

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""

    # --- Web ---
    cors_origins: str = "*"
    app_url: str = "http://localhost:8000"

    @property
    def llm_base_url(self) -> str:
        """LangChain appends /chat/completions itself, so strip it if present."""
        url = self.agent_endpoint.strip().rstrip("/")
        suffix = "/chat/completions"
        if url.endswith(suffix):
            url = url[: -len(suffix)]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
