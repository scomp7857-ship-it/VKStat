from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://vkstat:vkstat@postgres:5432/vkstat"
    redis_url: str = "redis://redis:6379/0"
    vk_tokens: str = ""
    vk_api_version: str = "5.199"
    vk_requests_per_second: float = 3.0
    scrape_page_size: int = 100
    scrape_batch_size: int = 25
    fresh_window_days: int = 30
    queue_default: str = "default"
    queue_scrape: str = "scrape"
    queue_metrics: str = "metrics"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def vk_token_list(self) -> list[str]:
        return [t.strip() for t in self.vk_tokens.split(",") if t.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """Normalize DATABASE_URL for SQLAlchemy 2.x + psycopg3.

        Render / Heroku hand out `postgres://...` or `postgresql://...` without
        a driver. We need the explicit `postgresql+psycopg://` scheme.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
