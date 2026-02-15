from pydantic_settings import BaseSettings


class DashboardSettings(BaseSettings):
    REFRESH_INTERVAL_SECONDS: int = 60
    CACHE_TTL_SECONDS: int = 300
    DEFAULT_PERIOD_DAYS: int = 7
    PLOTLY_TEMPLATE: str = "plotly_dark"
    PAGE_TITLE: str = "AIAfiliado Dashboard"
    PAGE_ICON: str = "📊"
    AUTH_USER: str = "admin"
    AUTH_PASSWORD: str = ""

    model_config = {"env_prefix": "DASH_", "env_file": ".env", "env_file_encoding": "utf-8"}


dash_settings = DashboardSettings()
