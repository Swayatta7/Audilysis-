import logging

from services.report_health import (
    VALID_RESPONSE_STATUSES,
    retry_recommendation_for_status,
    safe_error_message_for_status,
)
from services.dataforseo_client import post_dataforseo_task


logger = logging.getLogger(__name__)


def query_platform(platform, keyword, credentials, brand_domain, brand_name, competitor_domains, country="United States", language="en"):
    """
    Query DataForSEO for a single platform/keyword pair and return a normalized result.
    """
    login = credentials.get("login")
    password = credentials.get("password")

    if not login or not password:
        return build_failure_result(platform, keyword, "authentication_error", "Missing DataForSEO credentials.")

    if platform == "google":
        endpoint = "serp/google/ai_mode/live/advanced"
        payload = [{
            "keyword": keyword,
            "location_name": country,
            "language_code": language,
        }]
    else:
        endpoints = {
            "chat_gpt": "chat_gpt",
            "perplexity": "perplexity",
            "gemini": "gemini",
            "claude": "claude",
        }
        models = {
            "chat_gpt": "gpt-4.1-mini",
            "perplexity": "sonar",
            "gemini": "gemini-2.0-flash",
            "claude": "claude-haiku-4-5",
        }
        endpoint = f"ai_optimization/{endpoints.get(platform)}/llm_responses/live"
        payload = [{
            "user_prompt": keyword,
            "model_name": models.get(platform),
            "web_search": True,
            "max_output_tokens": 1000,
        }]

    logger.info("platform_request_start platform=%s keyword=%s", platform, keyword)
    response = post_dataforseo_task(
        endpoint,
        payload,
        credentials,
        timeout=120,
        max_retries=3,
        retry_delay=2,
        purpose=f"platform:{platform}",
    )
    if not response["ok"]:
        status_map = {
            "authentication_failed": "authentication_error",
            "rate_limited": "rate_limit",
            "timeout": "timeout",
            "unavailable": "platform_unavailable",
            "failed": "invalid_response",
        }
        return build_failure_result(
            platform,
            keyword,
            status_map.get(response["status"], "unknown_error"),
            response["message"],
        )

    normalized = parse_dataforseo_response(platform, keyword, response["data"])
    logger.info(
        "platform_request_%s platform=%s keyword=%s status=%s valid=%s",
        "success" if normalized["has_valid_data"] else "failure",
        platform,
        keyword,
        normalized["response_status"],
        normalized["has_valid_data"],
    )
    return normalized


def parse_dataforseo_response(platform: str, keyword: str, data: dict) -> dict:
    tasks = data.get("tasks", [])
    if not tasks:
        return build_failure_result(platform, keyword, "invalid_response", "DataForSEO returned an empty task list.")

    task = tasks[0] or {}
    task_status = int(task.get("status_code") or 0)
    if task_status not in {20000, 20100}:
        message = str(task.get("status_message") or "The platform task did not complete successfully.")
        lowered = message.lower()
        if "auth" in lowered or "credential" in lowered:
            return build_failure_result(platform, keyword, "authentication_error", "The platform request could not be authenticated.")
        if "limit" in lowered or "quota" in lowered or "too many" in lowered:
            return build_failure_result(platform, keyword, "rate_limit", "The platform rate limit or quota was exceeded.")
        return build_failure_result(platform, keyword, "platform_unavailable", message)

    result = task.get("result", [])
    if not result or not result[0]:
        return build_failure_result(platform, keyword, "no_data", "The platform returned no result payload.")

    items = result[0].get("items", [])
    if not items or not items[0]:
        return build_failure_result(platform, keyword, "no_data", "The platform returned no items.")

    item = items[0] or {}
    text = ""
    sources = []

    if platform == "google":
        text = (item.get("markdown") or "").strip()
        refs = item.get("references") or []
        for ref in refs:
            if isinstance(ref, dict) and ref.get("url"):
                sources.append(ref["url"])
            elif isinstance(ref, str):
                sources.append(ref)
    else:
        sections = item.get("sections") or []
        text_parts = []
        for sec in sections:
            sec_text = (sec.get("text") or "").strip()
            if sec_text:
                text_parts.append(sec_text)
            for ann in sec.get("annotations") or []:
                if isinstance(ann, dict) and ann.get("url"):
                    sources.append(ann["url"])
        text = "\n".join(text_parts).strip()

    if not is_usable_content(text):
        return build_failure_result(platform, keyword, "no_data", "The platform returned no usable response content.")

    return {
        "platform": platform,
        "keyword": keyword,
        "response_status": "success",
        "error_category": "success",
        "error_message": "",
        "retry_recommendation": retry_recommendation_for_status("success"),
        "text": text,
        "sources_cited": sorted(set(filter(None, sources))),
        "has_valid_data": True,
    }


def is_usable_content(text: str) -> bool:
    cleaned = str(text or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    placeholder_markers = [
        "no response data",
        "api call failed",
        "data unavailable",
        "error:",
        "task failed",
    ]
    return not any(marker in lowered for marker in placeholder_markers)


def build_failure_result(platform: str, keyword: str, status: str, message: str) -> dict:
    normalized_status = status if status in VALID_RESPONSE_STATUSES else "unknown_error"
    safe_message = safe_error_message_for_status(normalized_status, message)
    logger.warning(
        "platform_request_failure platform=%s keyword=%s status=%s message=%s",
        platform,
        keyword,
        normalized_status,
        safe_message,
    )
    return {
        "platform": platform,
        "keyword": keyword,
        "response_status": normalized_status,
        "error_category": normalized_status,
        "error_message": safe_message,
        "retry_recommendation": retry_recommendation_for_status(normalized_status),
        "text": "",
        "sources_cited": [],
        "has_valid_data": False,
    }
