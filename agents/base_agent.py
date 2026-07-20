"""
base_agent.py — Abstract base class for all SEO AI Agents.

Provides shared utilities:
- Input validation
- Standardized JSON result formatting
- DataForSEO HTTP helper
- Honest missing-data / missing-key errors
"""
import time
import requests
from abc import ABC, abstractmethod
from requests.auth import HTTPBasicAuth

from agents.runtime_config import get_env_value, load_env_config


class BaseAgent(ABC):
    """
    Abstract base for every SEO Agent.
    Each subclass must implement:
      - NAME      : str  — display name
      - DESCRIPTION: str — one-liner description
      - ICON      : str  — FontAwesome class (e.g. "fa-magnifying-glass-chart")
      - CATEGORY  : str  — grouping label
      - INPUT_SCHEMA: list[dict] — extra input fields beyond the common ones
      - run(input_data) -> dict
    """
    NAME = "Base Agent"
    DESCRIPTION = "Base SEO agent."
    ICON = "fa-robot"
    CATEGORY = "General"
    INPUT_SCHEMA = []  # Agent-specific extra fields

    DATAFORSEO_BASE = "https://api.dataforseo.com/v3"

    @abstractmethod
    def run(self, input_data: dict) -> dict:
        """Execute the agent task and return structured JSON output."""
        pass

    # ── Shared Helpers ──────────────────────────────────────────────────────

    def validate_input(self, input_data: dict, required_fields: list) -> str | None:
        """Returns an error message string if a required field is missing, else None."""
        for field in required_fields:
            val = input_data.get(field)
            if not val or (isinstance(val, str) and not val.strip()):
                return f"Field '{field}' is required."
        return None

    def build_result(self, status: str, data: dict, message: str = "") -> dict:
        """Standardized response envelope."""
        return {
            "status": status,        # "success" | "error"
            "message": message,
            "agent": self.NAME,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "data": data
        }

    def build_structured_response(self, input_data: dict, summary: str, recommendations: list, data: dict, success: bool = True, message: str = "") -> dict:
        """Return a consistent JSON response for the Flask API and UI."""
        return {
            "success": success,
            "agent": self.NAME,
            "input": {
                "website_url": input_data.get("website_url") or input_data.get("website") or "",
                "brand_name": input_data.get("brand_name") or "",
                "industry": input_data.get("industry") or "",
                "target_audience": input_data.get("target_audience") or "",
                "keyword": input_data.get("keyword") or input_data.get("target_keywords") or "",
                "topic": input_data.get("topic") or "",
                "platform": input_data.get("platform") or "",
                "tone": input_data.get("tone") or "",
                "location": input_data.get("location") or "",
                "country": input_data.get("country") or input_data.get("location") or "",
                "language": input_data.get("language") or "",
                "content_type": input_data.get("content_type") or "",
                "competitors": input_data.get("competitors") or [],
                "business_goal": input_data.get("business_goal") or ""
            },
            "summary": summary,
            "recommendations": recommendations,
            "data": data,
            "message": message or ("Completed successfully." if success else "Unable to complete the request."),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def parse_keywords(self, raw: str | list) -> list:
        """Converts newline/comma-separated keyword string to a clean list."""
        if isinstance(raw, list):
            return [k.strip() for k in raw if k.strip()]
        if isinstance(raw, str):
            parts = raw.replace(",", "\n").split("\n")
            return [p.strip() for p in parts if p.strip()]
        return []

    def parse_competitors(self, raw: str | list) -> list:
        """Same as parse_keywords but for competitor domains."""
        return self.parse_keywords(raw)

    def get_env_config(self) -> dict:
        return load_env_config()

    def get_dataforseo_credentials(self, input_data: dict) -> dict:
        creds = input_data.get("credentials", {}) or {}
        return {
            "login": (creds.get("login") or get_env_value("DATAFORSEO_LOGIN")).strip(),
            "password": (creds.get("password") or get_env_value("DATAFORSEO_PASSWORD")).strip(),
        }

    def missing_key_response(self, missing_key: str, input_data: dict | None = None) -> dict:
        payload = {
            "status": "error",
            "success": False,
            "error": "Missing API key",
            "missing_key": missing_key,
            "message": "Configure this key to run this agent with genuine data.",
        }
        if input_data is not None:
            payload.update({
                "agent": self.NAME,
                "input": {
                    "website_url": input_data.get("website_url") or "",
                    "keyword": input_data.get("keyword") or input_data.get("target_keywords") or "",
                },
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
        return payload

    def missing_input_response(self, field: str, input_data: dict | None = None, detail: str | None = None) -> dict:
        return {
            "status": "error",
            "success": False,
            "agent": self.NAME,
            "error": "Missing input",
            "missing_field": field,
            "message": detail or f"Provide '{field}' to run this agent with genuine data.",
            "input": input_data or {},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def dataforseo_post(self, endpoint: str, payload: list, credentials: dict) -> tuple:
        """
        Make an authenticated POST to DataForSEO.
        Returns (data_dict, error_string).
        """
        login = credentials.get("login", "")
        password = credentials.get("password", "")
        url = f"{self.DATAFORSEO_BASE}/{endpoint}"

        try:
            response = requests.post(
                url,
                json=payload,
                auth=HTTPBasicAuth(login, password),
                timeout=60
            )
            if response.status_code == 429:
                return None, "Rate limited by DataForSEO (HTTP 429). Try again in a moment."
            if response.status_code != 200:
                return None, f"DataForSEO returned HTTP {response.status_code}."
            data = response.json()
            tasks = data.get("tasks", [])
            if not tasks:
                return None, "API returned empty task list."
            task = tasks[0]
            if task.get("status_code") not in [20000, 20100]:
                return None, f"Task error: {task.get('status_message', 'Unknown error')}"
            result = task.get("result", [])
            if not result:
                return None, "Task returned empty result."
            return result[0], None
        except requests.exceptions.Timeout:
            return None, "DataForSEO request timed out after 60 seconds."
        except Exception as e:
            return None, f"Request failed: {str(e)}"
