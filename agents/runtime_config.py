import os


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
]


def load_env_config() -> dict:
    return {key: os.getenv(key, "").strip() for key in REQUIRED_ENV_KEYS}


def get_env_value(key: str) -> str:
    return os.getenv(key, "").strip()
