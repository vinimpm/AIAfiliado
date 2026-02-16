from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- App ---
    ENV: str = "development"

    # --- Database ---
    DATABASE_URL: str = "postgresql://aiafiliado:localdev@localhost:5432/aiafiliado"
    DATABASE_SSL: bool = False

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TLS: bool = False

    # --- HeyGen ---
    HEYGEN_API_KEY: str = ""
    HEYGEN_API_BASE_URL: str = "https://api.heygen.com"
    HEYGEN_AVATAR_ID: str = ""
    HEYGEN_VOICE_ID: str = "pt_br_female_01"
    HEYGEN_VOICE_SPEED: float = 1.1
    HEYGEN_VIDEO_WIDTH: int = 720
    HEYGEN_VIDEO_HEIGHT: int = 1280
    HEYGEN_BACKGROUND_COLORS: list[str] = ["#FFFFFF", "#F5F5DC", "#E8E8E8", "#FFF0F5"]
    HEYGEN_API_ENABLED: bool = False

    # --- LLM ---
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.8
    LLM_MAX_TOKENS: int = 500

    # --- TikTok ---
    TIKTOK_CLIENT_KEY: str = ""
    TIKTOK_CLIENT_SECRET: str = ""
    TIKTOK_ACCESS_TOKEN: str = ""

    # --- Instagram ---
    INSTAGRAM_ACCESS_TOKEN: str = ""
    INSTAGRAM_USER_ID: str = ""

    # --- YouTube ---
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""
    YOUTUBE_REFRESH_TOKEN: str = ""

    # --- Affiliate: Hotmart ---
    HOTMART_CLIENT_ID: str = ""
    HOTMART_CLIENT_SECRET: str = ""
    HOTMART_ACCESS_TOKEN: str = ""

    # --- Affiliate: Amazon ---
    AMAZON_ACCESS_KEY: str = ""
    AMAZON_SECRET_KEY: str = ""
    AMAZON_ASSOCIATE_TAG: str = ""

    # --- Affiliate: Shopee ---
    SHOPEE_APP_ID: str = ""
    SHOPEE_SECRET: str = ""

    # --- S3 Storage ---
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "sa-east-1"
    S3_BUCKET_NAME: str = "aiafiliado-videos"
    S3_ENDPOINT_URL: str = ""

    # --- Budget & Limits ---
    DAILY_BUDGET_USD: float = 5.00
    MAX_VIDEOS_PER_DAY: int = 5
    MAX_VIDEOS_PER_PRODUCT_DAY: int = 2
    MAX_VIDEOS_PER_PRODUCT_WEEK: int = 5
    WINNER_SALES_WINDOW_DAYS: int = 3
    RETIRE_AFTER_DAYS_NO_SALES: int = 7

    # --- Trend Scoring ---
    TREND_MIN_SCORE: float = 25.0
    SIMILARITY_THRESHOLD: float = 0.85
    RECENT_SCRIPTS_COMPARE: int = 10
    MAX_RETRIES_ON_DUPLICATE: int = 3

    # --- Product Criteria ---
    MIN_COMMISSION_BRL: float = 5.00
    MIN_COMMISSION_PCT: float = 10.0
    MIN_RATING: float = 4.3
    PRICE_RANGE_PHYSICAL_MIN: float = 30.0
    PRICE_RANGE_PHYSICAL_MAX: float = 150.0
    PRICE_RANGE_DIGITAL_MIN: float = 30.0
    PRICE_RANGE_DIGITAL_MAX: float = 297.0
    ALLOWED_CATEGORIES: list[str] = [
        "beauty",
        "skincare",
        "fashion",
        "accessories",
        "home",
        "kitchen",
        "tech_accessories",
        "fitness",
        "health",
        "education",
        "pet",
    ]
    PLATFORM_PRIORITY: list[str] = [
        "tiktok_shop",
        "amazon",
        "shopee",
        "hotmart",
        "monetizze",
    ]

    # --- Schedule ---
    INSTAGRAM_DELAY_HOURS: int = 24
    YOUTUBE_DELAY_HOURS: int = 48

    # --- Account Health Weights ---
    AH_WEIGHT_REMOVALS: float = 0.35
    AH_WEIGHT_STRIKES: float = 0.25
    AH_WEIGHT_REACH_DROP: float = 0.15
    AH_WEIGHT_SIMILARITY: float = 0.15
    AH_WEIGHT_CADENCE: float = 0.10

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
