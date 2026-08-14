from __future__ import annotations

import traceback
from typing import Callable, Iterable

from agents.agent_manager import get_agent_metadata, run_agent
from agents.runtime_config import get_env_value
from db.storage import upsert_agent_result
from services.run_context import load_run_analysis_context


SEO_AGENT_ORDER = [
    "technical_audit",
    "competitor_analysis",
    "keyword_research",
    "keyword_clustering",
    "content_gap",
    "serp_analysis",
    "rank_tracking",
    "on_page_optimizer",
    "schema_agent",
    "internal_linking",
    "backlink_prospecting",
    "outreach",
    "backlink_verification",
    "weekly_report",
    "monthly_report",
    "strategy",
]

SENSITIVE_RESULT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "authorization_header",
    "basic_auth",
    "cookie",
    "cookiefile",
    "cookies",
    "credentials",
    "password",
    "proxy",
    "proxy_url",
    "refresh_token",
    "secret",
    "sender_password",
    "smtp_password",
    "token",
}


def sanitize_agent_result_payload(value):
    """Remove secrets before storing run-scoped agent output."""
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if any(sensitive in normalized for sensitive in SENSITIVE_RESULT_KEYS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize_agent_result_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_agent_result_payload(item) for item in value]
    return value


def _agent_label(agent_id: str) -> str:
    metadata = get_agent_metadata(agent_id) or {}
    return metadata.get("name") or agent_id


def _result_status(result: dict) -> str:
    if result.get("status") == "not_run":
        return "not_run"
    if result.get("status") == "partial":
        return "partial"
    if result.get("success") is True and result.get("status") != "error":
        return "completed"
    return "failed"


def persist_agent_run_result(run_id, user_id, agent_id, result):
    if not run_id or user_id is None or not agent_id:
        return None
    status = _result_status(result or {})
    safe_result = sanitize_agent_result_payload(result or {})
    provenance = {
        "agent_id": agent_id,
        "agent_label": (result or {}).get("agent") or (result or {}).get("agent_name") or _agent_label(agent_id),
        "status": status,
        "reason_code": (result or {}).get("reason_code"),
        "data_source": ((result or {}).get("data") or {}).get("data_source") or (result or {}).get("data_source"),
        "api_used": ((result or {}).get("data") or {}).get("api_used") or (result or {}).get("api_used") or [],
    }
    return upsert_agent_result(
        run_id=int(run_id),
        user_id=int(user_id),
        agent_name=agent_id,
        status=status,
        result=safe_result,
        provenance=sanitize_agent_result_payload(provenance),
    )


def build_not_run_result(agent_id: str, reason_code: str, message: str, *, data: dict | None = None) -> dict:
    return {
        "success": False,
        "status": "not_run",
        "agent": _agent_label(agent_id),
        "agent_id": agent_id,
        "reason_code": reason_code,
        "message": message,
        "summary": message,
        "recommendations": [],
        "data": {
            "reason_code": reason_code,
            "data_source": "not_run",
            "api_used": [],
            "missing_api_keys": [],
            **(data or {}),
        },
    }


def _run_keywords(run: dict) -> list[str]:
    return (run.get("high_volume_keywords") or []) + [
        keyword for keyword in (run.get("brand_keywords") or []) if keyword not in (run.get("high_volume_keywords") or [])
    ]


def _has_success_provider(run_context: dict, provider: str) -> bool:
    return any(row.get("provider") == provider and row.get("status") == "success" for row in run_context.get("provider_results") or [])


def _has_dataforseo(run_context: dict) -> bool:
    return any(row.get("provider") == "dataforseo" and row.get("status") == "success" for row in run_context.get("provider_results") or [])


def _has_openai() -> bool:
    return bool(get_env_value("OPENAI_API_KEY"))


def _base_payload(run_context: dict, user_id: int, credentials: dict | None) -> dict:
    run = run_context["run"]
    competitors = run.get("competitors") or []
    keywords = _run_keywords(run)
    website_url = f"https://{run['brand_domain']}"
    payload = {
        "run_id": run_context["run_id"],
        "_user_id": user_id,
        "_tracker_run_context": run_context,
        "website_url": website_url,
        "target_page_url": website_url,
        "brand_website": website_url,
        "brand_name": run["brand_name"],
        "country": run["country"],
        "location": run["country"],
        "target_country": run["country"],
        "language": run["language"],
        "competitors": competitors,
        "competitor_urls": competitors,
        "competitor_page_urls": competitors,
        "keyword_list": keywords,
        "target_keywords": keywords,
        "target_keyword": keywords[0] if keywords else "",
        "keyword": keywords[0] if keywords else "",
        "seed_keyword": keywords[0] if keywords else "",
        "focus_keyword": keywords[0] if keywords else "",
        "business_goal": "Improve verified organic visibility from this tracker run.",
        "outreach_goal": "Draft backlink outreach copy only; do not send messages.",
        "maximum_pages": 8,
        "maximum_pages_per_site": 3,
        "crawl_depth": 1,
    }
    if credentials:
        payload["credentials"] = credentials
    return payload


def evaluate_agent_eligibility(agent_id: str, run_context: dict) -> tuple[bool, str | None, str | None]:
    run = run_context["run"]
    competitors = run.get("competitors") or []
    keywords = _run_keywords(run)
    dataforseo_ready = _has_dataforseo(run_context)
    openai_ready = _has_openai()
    crawl_ready = _has_success_provider(run_context, "crawl")

    if agent_id == "technical_audit":
        return (True, None, None) if crawl_ready else (False, "provider_unavailable", "Technical Audit requires a successful crawl provider row.")
    if agent_id == "competitor_analysis":
        return (True, None, None) if competitors else (False, "missing_required_input", "No competitor domains were provided for this run.")
    if agent_id == "keyword_research":
        return (True, None, None) if keywords else (False, "missing_required_input", "No tracker keywords were provided for this run.")
    if agent_id == "keyword_clustering":
        return (True, None, None) if len(keywords) >= 2 else (False, "missing_required_input", "At least two tracker keywords are required for clustering.")
    if agent_id == "content_gap":
        return (True, None, None) if competitors else (False, "missing_required_input", "Content Gap requires competitor domains.")
    if agent_id in {"serp_analysis", "rank_tracking"}:
        return (True, None, None) if dataforseo_ready else (False, "provider_unavailable", "A verified DataForSEO connection is required for live SERP/rank data.")
    if agent_id in {"on_page_optimizer", "schema_agent", "internal_linking"}:
        return (True, None, None) if crawl_ready else (False, "provider_unavailable", f"{_agent_label(agent_id)} requires a successful crawl provider row.")
    if agent_id == "backlink_prospecting":
        return (True, None, None) if competitors else (False, "missing_required_input", "Backlink Prospecting requires competitor domains.")
    if agent_id == "outreach":
        return (True, None, None) if openai_ready else (False, "provider_unavailable", "OpenAI is required to draft outreach copy.")
    if agent_id == "backlink_verification":
        return False, "missing_required_input", "No verified backlink URLs exist in this tracker run."
    if agent_id == "weekly_report":
        return False, "insufficient_history", "Weekly Report requires enough historical/run-period data."
    if agent_id == "monthly_report":
        return False, "insufficient_history", "Monthly Report requires enough monthly historical data."
    if agent_id == "strategy":
        if not openai_ready:
            return False, "provider_unavailable", "OpenAI is required for the Strategy Agent interpretation layer."
        if not (crawl_ready or run_context.get("valid_results")):
            return False, "insufficient_history", "Strategy requires verified factual run data."
        return True, None, None
    return False, "not_applicable", "This agent is not part of tracker orchestration."


def execute_agent_for_run(agent_id: str, run_id: int, user_id: int, credentials: dict | None = None) -> dict:
    run_context = load_run_analysis_context(run_id, user_id=user_id)
    if not run_context:
        result = build_not_run_result(agent_id, "run_not_found", "The tracker run was not found for this user.")
        persist_agent_run_result(run_id, user_id, agent_id, result)
        return result
    eligible, reason_code, reason = evaluate_agent_eligibility(agent_id, run_context)
    if not eligible:
        result = build_not_run_result(agent_id, reason_code or "not_run", reason or "This agent was not eligible for automatic execution.")
        persist_agent_run_result(run_id, user_id, agent_id, result)
        return result

    payload = _base_payload(run_context, user_id, credentials)
    try:
        result = run_agent(agent_id, payload)
    except Exception as exc:
        result = {
            "success": False,
            "agent": _agent_label(agent_id),
            "agent_id": agent_id,
            "status": "error",
            "message": f"{_agent_label(agent_id)} failed during automatic tracker orchestration.",
            "error": type(exc).__name__,
            "safe_traceback": traceback.format_exc(limit=3),
            "data": {"data_source": "automatic_tracker_orchestration", "api_used": []},
        }
    persist_agent_run_result(run_id, user_id, agent_id, result)
    return result


def orchestrate_seo_agents_for_run(
    run_id: int,
    user_id: int,
    credentials: dict | None = None,
    *,
    progress_callback: Callable[[dict], None] | None = None,
    agent_ids: Iterable[str] | None = None,
) -> list[dict]:
    results = []
    selected = list(agent_ids or SEO_AGENT_ORDER)
    total = len(selected)
    for index, agent_id in enumerate(selected, start=1):
        if progress_callback:
            progress_callback({"event": "start", "agent_id": agent_id, "agent_label": _agent_label(agent_id), "index": index, "total": total})
        result = execute_agent_for_run(agent_id, run_id, user_id, credentials)
        status = _result_status(result)
        results.append({"agent_id": agent_id, "status": status, "result": result})
        if progress_callback:
            progress_callback({
                "event": "finish",
                "agent_id": agent_id,
                "agent_label": _agent_label(agent_id),
                "index": index,
                "total": total,
                "status": status,
                "reason_code": result.get("reason_code"),
            })
    return results
