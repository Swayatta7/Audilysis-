import importlib
import importlib.util
import logging
import os
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

from werkzeug.middleware.proxy_fix import ProxyFix

from agents.runtime_config import get_env_value


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
TRUTHY = {"1", "true", "yes", "on"}
DEV_FALLBACK_SECRET_KEY = "audilysis-dev-insecure-secret-key"

REQUIRED_ENV_VARS = {
    "FLASK_SECRET_KEY": {"group": "Core"},
    "OPENAI_API_KEY": {"group": "OpenAI"},
    "DATAFORSEO_LOGIN": {"group": "DataForSEO"},
    "DATAFORSEO_PASSWORD": {"group": "DataForSEO"},
    "PAGESPEED_API_KEY": {"group": "PageSpeed / CrUX"},
    "CRUX_API_KEY": {"group": "PageSpeed / CrUX"},
    "GOOGLE_TRANSLATE_API_KEY": {"group": "Google Translate"},
    "HUGGINGFACE_TOKEN": {"group": "Hugging Face"},
    "GOOGLE_ADS_DEVELOPER_TOKEN": {"group": "Google Ads"},
    "GOOGLE_ADS_CLIENT_ID": {"group": "Google Ads"},
    "GOOGLE_ADS_CLIENT_SECRET": {"group": "Google Ads"},
    "GOOGLE_ADS_REDIRECT_URI": {"group": "Google Ads"},
    "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY": {"group": "Google Ads"},
    "WEBSHARE_PROXY_USERNAME": {"group": "Proxy"},
    "WEBSHARE_PROXY_PASSWORD": {"group": "Proxy"},
    "WEBSHARE_PROXY_HOST": {"group": "Proxy"},
    "WEBSHARE_PROXY_PORT": {"group": "Proxy"},
}


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def is_debug_environment(testing: bool = False) -> bool:
    return bool(testing) or truthy(get_env_value("FLASK_DEBUG"))


def is_local_host(host: str) -> bool:
    lowered = str(host or "").strip().lower()
    return (
        lowered == "localhost"
        or lowered.startswith("localhost:")
        or lowered == "127.0.0.1"
        or lowered.startswith("127.0.0.1:")
        or lowered == "[::1]"
        or lowered.startswith("[::1]:")
    )


def classify_env_value(name: str, value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw:
        return "missing", "Value is not configured."

    if name == "FLASK_SECRET_KEY" and len(raw) < 16:
        return "malformed", "Use a longer secret key for production."
    if name == "GOOGLE_ADS_CLIENT_ID" and ".apps.googleusercontent.com" not in raw:
        return "malformed", "Expected a Google OAuth client ID."
    if name == "GOOGLE_ADS_REDIRECT_URI":
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "malformed", "Redirect URI must be a valid absolute URL."
        if is_local_host(parsed.netloc):
            return "configured", "Configured with a local redirect URI."
        return "configured", "Configured with a non-local redirect URI."
    if name == "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY":
        try:
            from cryptography.fernet import Fernet

            Fernet(raw.encode("utf-8"))
        except Exception:
            return "malformed", "Value is not a valid Fernet key."
    if name == "WEBSHARE_PROXY_PORT":
        try:
            port = int(raw)
        except ValueError:
            return "malformed", "Proxy port must be numeric."
        if not (1 <= port <= 65535):
            return "malformed", "Proxy port must be between 1 and 65535."
    return "configured", "Configured."


def get_environment_audit() -> dict:
    integrations = {}
    for env_name, meta in REQUIRED_ENV_VARS.items():
        status, detail = classify_env_value(env_name, get_env_value(env_name))
        integrations[env_name] = {
            "group": meta["group"],
            "status": status,
            "detail": detail,
        }
    return integrations


def runtime_directories() -> dict:
    report_dir = BASE_DIR / "data" / "negative_keyword_reports"
    db_path = BASE_DIR / "data" / "tracker.db"
    return {
        "base_dir": BASE_DIR,
        "data_dir": BASE_DIR / "data",
        "db_path": db_path,
        "db_dir": db_path.parent,
        "report_dir": report_dir,
        "scripts_dir": BASE_DIR / "scripts",
    }


def get_runtime_path_diagnostics() -> dict:
    diagnostics = {}
    for name, path in runtime_directories().items():
        target = Path(path)
        exists = target.exists()
        parent = target if target.is_dir() else target.parent
        diagnostics[name] = {
            "path": str(target),
            "exists": exists,
            "is_dir": target.is_dir() if exists else name.endswith("_dir"),
            "writable": os.access(parent, os.W_OK) if parent.exists() else False,
        }
    return diagnostics


def connectivity_targets() -> dict:
    return {
        "OpenAI": ("api.openai.com", 443),
        "DataForSEO": ("api.dataforseo.com", 443),
        "Google APIs": ("translation.googleapis.com", 443),
        "Google Ads": ("googleads.googleapis.com", 443),
        "YouTube": ("www.youtube.com", 443),
        "Hugging Face": ("huggingface.co", 443),
        "Webshare Proxy": (get_env_value("WEBSHARE_PROXY_HOST"), int(get_env_value("WEBSHARE_PROXY_PORT") or 0) if str(get_env_value("WEBSHARE_PROXY_PORT") or "").isdigit() else 0),
        "PageSpeed": ("www.googleapis.com", 443),
        "CrUX": ("chromeuxreport.googleapis.com", 443),
    }


def diagnose_connectivity(host: str, port: int, timeout: float = 3.0) -> dict:
    if not host or not port:
        return {"status": "missing_configuration", "detail": "Host or port is not configured."}
    try:
        socket.getaddrinfo(host, port)
    except socket.gaierror as exc:
        return {"status": "dns_failure", "detail": str(exc)}
    except Exception as exc:
        return {"status": "unknown_error", "detail": f"{exc.__class__.__name__}: {exc}"}

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "reachable", "detail": "TCP connection succeeded."}
    except socket.timeout:
        return {"status": "timeout", "detail": "TCP connection timed out."}
    except OSError as exc:
        message = str(exc).lower()
        if "ssl" in message or "tls" in message:
            return {"status": "tls_failure", "detail": str(exc)}
        return {"status": "connection_failure", "detail": str(exc)}


def package_diagnostics() -> dict:
    packages = {}
    for module_name in [
        "flask",
        "requests",
        "reportlab",
        "matplotlib",
        "dotenv",
        "openpyxl",
        "cryptography",
        "google.ads.googleads",
        "google_auth_oauthlib",
        "youtube_transcript_api",
        "yt_dlp",
        "pyannote.audio",
    ]:
        packages[module_name] = importlib.util.find_spec(module_name) is not None
    packages["ffmpeg"] = bool(shutil.which("ffmpeg"))
    packages["gunicorn"] = bool(shutil.which("gunicorn")) or importlib.util.find_spec("gunicorn") is not None
    return packages


def apply_runtime_settings(app):
    production_like = not is_debug_environment(app.testing)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = production_like
    app.config["PREFERRED_URL_SCHEME"] = "https" if production_like else "http"
    app.config["AUDILYSIS_PRODUCTION_LIKE"] = production_like


def startup_configuration_summary() -> dict:
    env_audit = get_environment_audit()
    malformed = [name for name, item in env_audit.items() if item["status"] == "malformed"]
    missing = [name for name, item in env_audit.items() if item["status"] == "missing"]
    return {
        "production_like": not is_debug_environment(),
        "missing": missing,
        "malformed": malformed,
        "paths": get_runtime_path_diagnostics(),
    }


def log_startup_configuration_summary() -> None:
    summary = startup_configuration_summary()
    logger.info(
        "startup_configuration production_like=%s missing=%s malformed=%s",
        summary["production_like"],
        ",".join(summary["missing"]) or "none",
        ",".join(summary["malformed"]) or "none",
    )
