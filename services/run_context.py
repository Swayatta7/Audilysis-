from __future__ import annotations

from datetime import datetime
from typing import Any
import json

from db.storage import get_agent_results_for_run, get_competitor_metrics, get_mention_results, get_run, get_run_for_user, get_run_provider_results
from services.report_health import PLATFORM_LABELS, PLATFORM_ORDER, evaluate_report_data_health, is_valid_platform_result


def build_metric_record(
    value: Any,
    *,
    source: str,
    run_id: int,
    reason: str | None = None,
    collected_at: str | None = None,
) -> dict:
    return {
        "value": value,
        "status": "available" if value is not None else "unavailable",
        "source": source,
        "reason": reason if value is None else None,
        "run_id": run_id,
        "collected_at": collected_at,
    }


def is_dataforseo_skipped(status: str | None) -> bool:
    return (status or "").strip().lower() == "skipped_by_user"


def display_total_checks_value(total_checks: int, *, dataforseo_status: str | None) -> int | None:
    if is_dataforseo_skipped(dataforseo_status) and total_checks == 0:
        return None
    return total_checks


def build_heatmap_data(results: list[dict], keywords: list[str], *, dataforseo_status: str | None) -> dict:
    skipped = is_dataforseo_skipped(dataforseo_status) and not results
    heatmap = {
        kw: {
            plat: (
                {
                    "status": "skipped_by_user",
                    "display": "Not Run",
                    "reason": "DataForSEO was disabled for this run.",
                }
                if skipped
                else None
            )
            for plat in PLATFORM_ORDER
        }
        for kw in keywords
    }
    for row in results:
        kw = row["keyword"]
        plat = row["platform"]
        heatmap[kw][plat] = {
            "mentioned": row["mentioned"],
            "position": row["mention_position"],
            "status": row.get("response_status"),
            "error_message": row.get("error_message"),
            "has_valid_data": row.get("has_valid_data"),
        } if row.get("has_valid_data") else None
    return heatmap


def build_visibility_summary_text(run: dict, *, report_mode: str, dataforseo_status: str | None) -> str:
    if is_dataforseo_skipped(dataforseo_status):
        return (
            "AI visibility measurement was not performed because DataForSEO was disabled for this run. "
            "The report contains verified data from the remaining enabled providers."
        )

    summary = (
        f"This report presents brand visibility analysis for <b>{run['brand_name']}</b> across major AI and search engines. "
        f"We audited the presence of the brand domain <b>{run['brand_domain']}</b> and brand name keywords across Google AI Overviews "
        "and conversational LLMs (ChatGPT, Perplexity, Gemini, Claude)."
    )
    if report_mode == "partial":
        summary += " <b>Warning:</b> one or more platform requests failed, so the analytics below are based only on validated responses."
    return summary


def _safe_timestamp(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError:
        return raw


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


SEO_AGENT_STATUS_META = [
    ("technical_audit", "Technical Audit Agent"),
    ("competitor_analysis", "Competitor Analysis Agent"),
    ("keyword_research", "Keyword Research Agent"),
    ("keyword_clustering", "Keyword Clustering Agent"),
    ("content_gap", "Content Gap Agent"),
    ("serp_analysis", "SERP Analysis Agent"),
    ("rank_tracking", "Rank Tracking Agent"),
    ("on_page_optimizer", "On-page Optimizer Agent"),
    ("schema_agent", "Schema Agent"),
    ("internal_linking", "Internal Linking Agent"),
    ("backlink_prospecting", "Backlink Prospecting Agent"),
    ("outreach", "Outreach Agent"),
    ("backlink_verification", "Backlink Verification Agent"),
    ("weekly_report", "Weekly Report Agent"),
    ("monthly_report", "Monthly Report Agent"),
    ("strategy", "Strategy Agent"),
]


def _normalize_agent_status(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "success"}:
        return "completed"
    if normalized in {"failed", "error"}:
        return "failed"
    if normalized == "partial":
        return "partial"
    return "not_run"


def _build_agent_statuses(agent_results: list[dict]) -> dict:
    latest = {}
    for row in agent_results:
        latest.setdefault(row.get("agent_name"), row)
    statuses = {}
    for agent_id, label in SEO_AGENT_STATUS_META:
        row = latest.get(agent_id)
        status = _normalize_agent_status(row.get("status") if row else None)
        statuses[agent_id] = {
            "agent_id": agent_id,
            "label": label,
            "status": status,
            "updated_at": row.get("updated_at") if row else None,
            "result_id": row.get("id") if row else None,
        }
    return statuses


def _provider_metric_record(provider_map: dict, provider: str, key: str, run_id: int, *, reason: str) -> dict:
    record = provider_map.get(provider) or {}
    payload = record.get("payload") or {}
    value = payload.get(key) if isinstance(payload, dict) else None
    collected_at = _safe_timestamp(record.get("collected_at"))
    return build_metric_record(
        value,
        source=provider,
        run_id=run_id,
        reason=reason if value is None else None,
        collected_at=collected_at,
    )


def load_run_analysis_context(run_id: int | str | None, *, user_id: int | None = None) -> dict | None:
    if not run_id:
        return None
    try:
        normalized_run_id = int(run_id)
    except (TypeError, ValueError):
        return None

    run = get_run_for_user(normalized_run_id, user_id) if user_id is not None else get_run(normalized_run_id)
    if not run:
        return None
    run["use_dataforseo"] = bool(run.get("use_dataforseo", 1))
    run["high_volume_keywords"] = _parse_json_list(run.get("high_volume_keywords"))
    run["brand_keywords"] = _parse_json_list(run.get("brand_keywords"))
    run["keywords"] = run["high_volume_keywords"] + [kw for kw in run["brand_keywords"] if kw not in run["high_volume_keywords"]]

    collected_at = _safe_timestamp(run.get("run_date"))
    results = get_mention_results(normalized_run_id)
    metrics = get_competitor_metrics(normalized_run_id)
    provider_rows = get_run_provider_results(normalized_run_id)
    agent_results = get_agent_results_for_run(normalized_run_id, user_id=user_id)
    agent_statuses = _build_agent_statuses(agent_results)
    provider_result_map = {row["provider"]: row for row in provider_rows}
    report_health = evaluate_report_data_health(results)
    valid_results = [row for row in results if is_valid_platform_result(row)]
    non_dataforseo_success = any(
        (provider_result_map.get(provider) or {}).get("status") == "success"
        for provider in ("crawl", "pagespeed", "crux")
    )
    effective_report_mode = report_health["report_mode"]
    if effective_report_mode == "technical_failure" and non_dataforseo_success:
        effective_report_mode = "partial"
        report_health = {
            **report_health,
            "report_mode": "partial",
            "warnings": ["Visibility data is unavailable, but other verified provider data was collected for this run."],
        }

    total_checks = len(results)
    successful_checks = len(valid_results)
    brand_mentions = sum(1 for row in valid_results if row.get("mentioned"))

    brand_mentions_metric = build_metric_record(
        None if successful_checks == 0 else brand_mentions,
        source="database",
        run_id=normalized_run_id,
        reason="No valid DataForSEO visibility responses were collected for this run." if successful_checks == 0 else None,
        collected_at=collected_at,
    )
    share_of_voice_metric = build_metric_record(
        round((brand_mentions / successful_checks) * 100, 1) if successful_checks > 0 else None,
        source="database",
        run_id=normalized_run_id,
        reason="No valid provider responses were collected for this run." if successful_checks == 0 else None,
        collected_at=collected_at,
    )
    api_health_metric = build_metric_record(
        round((successful_checks / total_checks) * 100, 1) if total_checks > 0 else None,
        source="database",
        run_id=normalized_run_id,
        reason="No platform checks were stored for this run." if total_checks == 0 else None,
        collected_at=collected_at,
    )

    top_competitor = None
    competitor_metrics = [item for item in metrics if item["domain"].lower() != run["brand_domain"].lower()]
    if competitor_metrics:
        top_competitor = max(competitor_metrics, key=lambda item: item["total_mentions"])

    dataforseo_row = provider_result_map.get("dataforseo") or {}
    if dataforseo_row:
        dataforseo_payload = dataforseo_row.get("payload") or {}
        dataforseo_status = dataforseo_row.get("status", "unavailable")
        dataforseo_reason = dataforseo_row.get("reason")
        dataforseo_authentication = dataforseo_payload.get("authentication")
        dataforseo_enabled = bool(dataforseo_payload.get("enabled", run["use_dataforseo"]))
    else:
        if run["use_dataforseo"]:
            if successful_checks > 0:
                dataforseo_status = "success"
                dataforseo_reason = None
            else:
                dataforseo_status = "failed"
                if total_checks > 0:
                    dataforseo_reason = "DataForSEO was enabled for this run, but no valid provider responses were collected."
                else:
                    dataforseo_reason = "DataForSEO was enabled for this run, but no provider requests completed successfully."
            dataforseo_authentication = "unknown"
            dataforseo_enabled = True
        else:
            dataforseo_status = "skipped_by_user"
            dataforseo_reason = "DataForSEO was intentionally disabled for this run."
            dataforseo_authentication = "not_required"
            dataforseo_enabled = False

    provider_provenance = {
        "dataforseo": {
            "enabled": dataforseo_enabled,
            "status": dataforseo_status,
            "source": "dataforseo",
            "authentication": dataforseo_authentication,
            "reason": dataforseo_reason,
            "run_id": normalized_run_id,
            "collected_at": _safe_timestamp(dataforseo_row.get("collected_at")) if dataforseo_row else collected_at,
        },
        "database": {
            "enabled": True,
            "status": "success",
            "source": "database",
            "reason": None,
            "run_id": normalized_run_id,
            "collected_at": collected_at,
        },
    }
    for provider in ("crawl", "pagespeed", "crux"):
        provider_row = provider_result_map.get(provider)
        provider_provenance[provider] = {
            "enabled": True,
            "status": (provider_row or {}).get("status", "unavailable"),
            "source": provider,
            "reason": (provider_row or {}).get("reason"),
            "run_id": normalized_run_id,
            "collected_at": _safe_timestamp((provider_row or {}).get("collected_at")),
        }
    openai_row = provider_result_map.get("openai")
    provider_provenance["openai"] = {
        "enabled": True,
        "status": (openai_row or {}).get("status", "unavailable"),
        "source": "openai",
        "reason": (openai_row or {}).get("reason"),
        "role": "interpretation_only",
        "run_id": normalized_run_id,
        "collected_at": _safe_timestamp((openai_row or {}).get("collected_at")),
    }

    platform_summaries = [
        {
            "platform": item["platform"],
            "platform_label": PLATFORM_LABELS.get(item["platform"], item["platform"]),
            "status": item["status"],
            "error_category": item["error_category"],
            "safe_error_message": item["safe_error_message"],
            "retry_recommendation": item["retry_recommendation"],
        }
        for item in report_health["platform_summaries"]
    ]
    if not run["use_dataforseo"] and total_checks == 0:
        platform_summaries = [
            {
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "status": "skipped_by_user",
                "error_category": "skipped_by_user",
                "safe_error_message": "DataForSEO was not enabled for this run.",
                "retry_recommendation": "Enable DataForSEO for this run if you want AI visibility collection.",
            }
            for platform in PLATFORM_ORDER
        ]

    provider_metrics = {
        "crawl_pages_crawled": _provider_metric_record(
            provider_result_map,
            "crawl",
            "pages_crawled",
            normalized_run_id,
            reason="Crawl data was unavailable for this run.",
        ),
        "crawl_indexable_pages": _provider_metric_record(
            provider_result_map,
            "crawl",
            "indexable_pages",
            normalized_run_id,
            reason="Indexable page count was unavailable for this run.",
        ),
        "pagespeed_performance_score": _provider_metric_record(
            provider_result_map,
            "pagespeed",
            "performance_score",
            normalized_run_id,
            reason="PageSpeed performance data was unavailable for this run.",
        ),
        "pagespeed_seo_score": _provider_metric_record(
            provider_result_map,
            "pagespeed",
            "seo_score",
            normalized_run_id,
            reason="PageSpeed SEO score was unavailable for this run.",
        ),
        "crux_lcp_ms": _provider_metric_record(
            provider_result_map,
            "crux",
            "largest_contentful_paint_ms",
            normalized_run_id,
            reason="CrUX LCP data was unavailable for this run.",
        ),
        "crux_inp_ms": _provider_metric_record(
            provider_result_map,
            "crux",
            "interaction_to_next_paint_ms",
            normalized_run_id,
            reason="CrUX INP data was unavailable for this run.",
        ),
        "crux_cls": _provider_metric_record(
            provider_result_map,
            "crux",
            "cumulative_layout_shift",
            normalized_run_id,
            reason="CrUX CLS data was unavailable for this run.",
        ),
    }

    return {
        "run": run,
        "run_id": normalized_run_id,
        "collected_at": collected_at,
        "results": results,
        "metrics": metrics,
        "provider_results": provider_rows,
        "agent_results": agent_results,
        "agent_statuses": agent_statuses,
        "agent_status_summary": {
            "available": len(SEO_AGENT_STATUS_META),
            "completed": sum(1 for item in agent_statuses.values() if item["status"] == "completed"),
            "failed": sum(1 for item in agent_statuses.values() if item["status"] == "failed"),
            "partial": sum(1 for item in agent_statuses.values() if item["status"] == "partial"),
            "not_run": sum(1 for item in agent_statuses.values() if item["status"] == "not_run"),
        },
        "completed_agent_results": [row for row in agent_results if _normalize_agent_status(row.get("status")) == "completed"],
        "valid_results": valid_results,
        "report_health": report_health,
        "report_mode": effective_report_mode,
        "total_checks": total_checks,
        "successful_checks": successful_checks,
        "brand_mentions_metric": brand_mentions_metric,
        "share_of_voice_metric": share_of_voice_metric,
        "api_health_metric": api_health_metric,
        "provider_metrics": provider_metrics,
        "platform_summaries": platform_summaries,
        "top_competitor": top_competitor,
        "openai_interpretation": (openai_row or {}).get("payload"),
        "provider_provenance": provider_provenance,
    }
