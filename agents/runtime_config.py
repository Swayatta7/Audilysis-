import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


REQUIRED_ENV_KEYS = [
    "DATAFORSEO_LOGIN",
    "DATAFORSEO_PASSWORD",
    "PAGESPEED_API_KEY",
    "CRUX_API_KEY",
    "SERPAPI_KEY",
    "SEMRUSH_API_KEY",
    "AHREFS_API_KEY",
    "MOZ_API_KEY",
    "OPENAI_API_KEY",
    "FLASK_SECRET_KEY",
    "FLASK_DEBUG",
    "GOOGLE_TRANSLATE_API_KEY",
    "AUDILYSIS_ALLOW_DEV_SESSION_OWNERSHIP",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REDIRECT_URI",
    "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY",
    "HUGGINGFACE_TOKEN",
    "WEBSHARE_PROXY",
    "WEBSHARE_PROXY_USERNAME",
    "WEBSHARE_PROXY_PASSWORD",
    "WEBSHARE_PROXY_HOST",
    "WEBSHARE_PROXY_PORT",
    "YOUTUBE_PROXY_HTTP_URL",
    "YOUTUBE_PROXY_HTTPS_URL",
    "AUDILYSIS_TRANSCRIPT_DEBUG",
]


def load_env_config() -> dict:
    return {key: os.getenv(key, "").strip() for key in REQUIRED_ENV_KEYS}


def get_env_value(key: str) -> str:
    return os.getenv(key, "").strip()
