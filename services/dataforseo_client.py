from __future__ import annotations

import logging
import time

import requests
from requests.auth import HTTPBasicAuth


logger = logging.getLogger(__name__)

DATAFORSEO_BASE_URL = "https://api.dataforseo.com/v3"
SUCCESS_TASK_CODES = {20000, 20100}


def build_dataforseo_provider_payload(
    *,
    enabled: bool,
    status: str,
    authentication: str,
    endpoint: str | None = None,
    run_id: int | None = None,
    details: dict | None = None,
) -> dict:
    payload = {
        "provider": "dataforseo",
        "enabled": enabled,
        "status": status,
        "authentication": authentication,
        "endpoint": endpoint,
        "run_id": run_id,
    }
    if details:
        payload["details"] = details
    return payload


def build_skipped_dataforseo_payload(*, run_id: int | None = None) -> dict:
    return build_dataforseo_provider_payload(
        enabled=False,
        status="skipped_by_user",
        authentication="not_required",
        run_id=run_id,
    )


def post_dataforseo_task(
    endpoint: str,
    payload: list,
    credentials: dict,
    *,
    timeout: int = 60,
    max_retries: int = 0,
    retry_delay: int = 2,
    purpose: str = "request",
) -> dict:
    login = (credentials or {}).get("login", "").strip()
    password = (credentials or {}).get("password", "").strip()
    if not login or not password:
        return {
            "ok": False,
            "status": "authentication_failed",
            "http_status": None,
            "message": "Missing DataForSEO credentials.",
            "data": None,
            "endpoint": endpoint,
        }

    url = f"{DATAFORSEO_BASE_URL}/{endpoint.lstrip('/')}"
    attempts = max(max_retries, 0) + 1
    current_delay = retry_delay

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                auth=HTTPBasicAuth(login, password),
                timeout=timeout,
            )
        except requests.exceptions.Timeout:
            status = "timeout"
            message = "The DataForSEO request timed out."
            if attempt < attempts:
                logger.warning(
                    "dataforseo_request_retry purpose=%s endpoint=%s status=%s attempt=%s",
                    purpose,
                    endpoint,
                    status,
                    attempt,
                )
                time.sleep(current_delay)
                current_delay *= 2
                continue
            return {
                "ok": False,
                "status": status,
                "http_status": None,
                "message": message,
                "data": None,
                "endpoint": endpoint,
            }
        except requests.exceptions.ConnectionError:
            return {
                "ok": False,
                "status": "unavailable",
                "http_status": None,
                "message": "The server could not reach DataForSEO.",
                "data": None,
                "endpoint": endpoint,
            }
        except requests.exceptions.RequestException as exc:
            return {
                "ok": False,
                "status": "failed",
                "http_status": None,
                "message": f"Network request failed: {exc.__class__.__name__}.",
                "data": None,
                "endpoint": endpoint,
            }

        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "status": "authentication_failed",
                "http_status": response.status_code,
                "message": f"DataForSEO rejected the credentials (HTTP {response.status_code}).",
                "data": None,
                "endpoint": endpoint,
            }
        if response.status_code == 429:
            if attempt < attempts:
                logger.warning(
                    "dataforseo_request_retry purpose=%s endpoint=%s status=rate_limited attempt=%s",
                    purpose,
                    endpoint,
                    attempt,
                )
                time.sleep(current_delay)
                current_delay *= 2
                continue
            return {
                "ok": False,
                "status": "rate_limited",
                "http_status": 429,
                "message": "DataForSEO rate limit or quota was reached.",
                "data": None,
                "endpoint": endpoint,
            }
        if response.status_code >= 500:
            return {
                "ok": False,
                "status": "unavailable",
                "http_status": response.status_code,
                "message": f"DataForSEO was unavailable (HTTP {response.status_code}).",
                "data": None,
                "endpoint": endpoint,
            }
        if response.status_code != 200:
            return {
                "ok": False,
                "status": "failed",
                "http_status": response.status_code,
                "message": f"DataForSEO returned HTTP {response.status_code}.",
                "data": None,
                "endpoint": endpoint,
            }

        try:
            data = response.json()
        except ValueError:
            return {
                "ok": False,
                "status": "failed",
                "http_status": response.status_code,
                "message": "DataForSEO returned non-JSON content.",
                "data": None,
                "endpoint": endpoint,
            }

        return {
            "ok": True,
            "status": "connected",
            "http_status": response.status_code,
            "message": "Authenticated DataForSEO response received.",
            "data": data,
            "endpoint": endpoint,
        }

    return {
        "ok": False,
        "status": "failed",
        "http_status": None,
        "message": "The DataForSEO request failed after retries.",
        "data": None,
        "endpoint": endpoint,
    }


def extract_first_task_result(data: dict) -> tuple[dict | None, str | None]:
    tasks = (data or {}).get("tasks") or []
    if not tasks:
        return None, "API returned empty task list."
    task = tasks[0] or {}
    status_code = int(task.get("status_code") or 0)
    if status_code not in SUCCESS_TASK_CODES:
        return None, str(task.get("status_message") or "The DataForSEO task did not complete successfully.")
    result = task.get("result") or []
    if not result:
        return None, "Task returned empty result."
    first = result[0]
    if first is None:
        return None, "Task returned empty result."
    return first, None


def verify_dataforseo_credentials(
    credentials: dict,
    *,
    keyword: str,
    country: str,
    language: str,
    run_id: int | None = None,
) -> dict:
    endpoint = "serp/google/organic/live/advanced"
    verification_keyword = (keyword or "").strip() or "brand visibility"
    verification_payload = [{
        "keyword": verification_keyword,
        "location_name": country or "United States",
        "language_code": language or "en",
        "depth": 1,
    }]
    response = post_dataforseo_task(
        endpoint,
        verification_payload,
        credentials,
        timeout=30,
        max_retries=0,
        purpose="verification",
    )
    if not response["ok"]:
        return {
            "connected": False,
            "status": response["status"],
            "message": response["message"],
            "provider_payload": build_dataforseo_provider_payload(
                enabled=True,
                status=response["status"],
                authentication="failed",
                endpoint=endpoint,
                run_id=run_id,
            ),
        }

    first_result, task_error = extract_first_task_result(response["data"])
    if task_error:
        lowered = task_error.lower()
        if "auth" in lowered or "credential" in lowered:
            status = "authentication_failed"
            authentication = "failed"
        elif "limit" in lowered or "quota" in lowered or "too many" in lowered:
            status = "rate_limited"
            authentication = "failed"
        else:
            status = "failed"
            authentication = "failed"
        return {
            "connected": False,
            "status": status,
            "message": task_error,
            "provider_payload": build_dataforseo_provider_payload(
                enabled=True,
                status=status,
                authentication=authentication,
                endpoint=endpoint,
                run_id=run_id,
            ),
        }

    details = {
        "verification_keyword": verification_keyword,
        "result_items_count": len((first_result or {}).get("items") or []),
    }
    return {
        "connected": True,
        "status": "connected",
        "message": "DataForSEO credentials verified successfully.",
        "provider_payload": build_dataforseo_provider_payload(
            enabled=True,
            status="connected",
            authentication="verified",
            endpoint=endpoint,
            run_id=run_id,
            details=details,
        ),
    }
