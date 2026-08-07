from __future__ import annotations

from collections import Counter


PLATFORM_ORDER = ["google", "chat_gpt", "perplexity", "gemini", "claude"]
PLATFORM_LABELS = {
    "google": "Google AI",
    "chat_gpt": "ChatGPT",
    "perplexity": "Perplexity",
    "gemini": "Gemini",
    "claude": "Claude",
}
SUPPORTED_TARGET_COUNTRIES = [
    "India",
    "United States",
    "United Kingdom",
    "Canada",
    "Australia",
    "Germany",
    "France",
    "Japan",
    "Brazil",
    "Singapore",
    "UAE",
    "Saudi Arabia",
]
VALID_RESPONSE_STATUSES = {
    "success",
    "no_data",
    "authentication_error",
    "rate_limit",
    "timeout",
    "network_error",
    "invalid_response",
    "platform_unavailable",
    "unknown_error",
}
FULL_REPORT_SUCCESS_RATE = 1.0


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.replace("_", " ").title())


def is_valid_platform_result(result: dict) -> bool:
    return bool(result.get("has_valid_data"))


def retry_recommendation_for_status(status: str) -> str:
    mapping = {
        "success": "No retry needed.",
        "no_data": "Review the keyword set and rerun the audit.",
        "authentication_error": "Verify API credentials and access, then rerun the audit.",
        "rate_limit": "Wait for quota reset or increase limits, then retry.",
        "timeout": "Check network stability and retry the audit.",
        "network_error": "Check server connectivity and rerun the audit.",
        "invalid_response": "Review provider response format and retry later.",
        "platform_unavailable": "Check provider status and rerun the audit later.",
        "unknown_error": "Review diagnostics, then rerun the audit.",
    }
    return mapping.get(status, "Review diagnostics and rerun the audit.")


def safe_error_message_for_status(status: str, fallback: str = "") -> str:
    mapping = {
        "success": "Valid response collected.",
        "no_data": "The platform returned no usable content for this request.",
        "authentication_error": "The platform request could not be authenticated.",
        "rate_limit": "The platform rate limit or quota was exceeded.",
        "timeout": "The platform request timed out before completion.",
        "network_error": "The platform could not be reached from the server.",
        "invalid_response": "The platform response could not be validated.",
        "platform_unavailable": "The provider was unavailable for this request.",
        "unknown_error": "The platform request failed for an unknown reason.",
    }
    return fallback or mapping.get(status, "The platform request failed.")


def summarize_platform_statuses(results: list[dict]) -> list[dict]:
    summaries = []
    for platform in PLATFORM_ORDER:
        platform_results = [row for row in results if row.get("platform") == platform]
        if not platform_results:
            summaries.append({
                "platform": platform,
                "platform_label": platform_label(platform),
                "status": "unknown_error",
                "error_category": "unknown_error",
                "safe_error_message": "No platform results were recorded.",
                "retry_recommendation": retry_recommendation_for_status("unknown_error"),
                "success_count": 0,
                "failure_count": 0,
                "valid_keywords": [],
            })
            continue

        valid_rows = [row for row in platform_results if is_valid_platform_result(row)]
        invalid_rows = [row for row in platform_results if not is_valid_platform_result(row)]
        valid_keywords = sorted({row.get("keyword", "") for row in valid_rows if row.get("keyword")})

        if valid_rows and not invalid_rows:
            status = "success"
            error_category = "success"
            safe_message = "Valid responses were collected for every request."
        elif valid_rows:
            status = "success"
            error_counter = Counter((row.get("error_category") or row.get("response_status") or "unknown_error") for row in invalid_rows)
            error_category = error_counter.most_common(1)[0][0] if error_counter else "unknown_error"
            safe_message = f"Partial data collected; {len(invalid_rows)} request(s) failed."
        else:
            error_counter = Counter((row.get("error_category") or row.get("response_status") or "unknown_error") for row in invalid_rows)
            error_category = error_counter.most_common(1)[0][0] if error_counter else "unknown_error"
            status = error_category
            message_counter = Counter(
                safe_error_message_for_status(
                    row.get("response_status") or "unknown_error",
                    row.get("error_message") or "",
                )
                for row in invalid_rows
            )
            safe_message = message_counter.most_common(1)[0][0] if message_counter else safe_error_message_for_status(status)

        summaries.append({
            "platform": platform,
            "platform_label": platform_label(platform),
            "status": status,
            "error_category": error_category,
            "safe_error_message": safe_message,
            "retry_recommendation": retry_recommendation_for_status(error_category),
            "success_count": len(valid_rows),
            "failure_count": len(invalid_rows),
            "valid_keywords": valid_keywords,
        })
    return summaries


def evaluate_report_data_health(results: list[dict]) -> dict:
    total_platforms = len(results)
    successful_platforms = len([row for row in results if is_valid_platform_result(row)])
    failed_platforms = total_platforms - successful_platforms
    success_rate = round((successful_platforms / total_platforms), 4) if total_platforms else 0.0

    if successful_platforms == 0:
        report_mode = "technical_failure"
        warnings = ["No valid AI platform responses were collected."]
    elif failed_platforms > 0 or success_rate < FULL_REPORT_SUCCESS_RATE:
        report_mode = "partial"
        warnings = ["Results are incomplete because one or more platform requests failed."]
    else:
        report_mode = "full"
        warnings = []

    platform_summaries = summarize_platform_statuses(results)
    failure_summary = [row for row in platform_summaries if row["failure_count"] or row["status"] != "success"]

    return {
        "total_platforms": total_platforms,
        "successful_platforms": successful_platforms,
        "failed_platforms": failed_platforms,
        "success_rate": success_rate,
        "report_mode": report_mode,
        "failure_summary": failure_summary,
        "warnings": warnings,
        "platform_summaries": platform_summaries,
        "minimum_full_success_rate": FULL_REPORT_SUCCESS_RATE,
    }
