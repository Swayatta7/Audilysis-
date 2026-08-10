from __future__ import annotations

import json

from agents.llm_client import openai_chat_completion
from agents.runtime_config import get_env_value


def build_tracker_interpretation_input(report_data: dict) -> dict:
    return {
        "run_id": report_data.get("run_id"),
        "brand": {
            "name": report_data["run"]["brand_name"],
            "domain": report_data["run"]["brand_domain"],
            "country": report_data["run"]["country"],
            "language": report_data["run"]["language"],
        },
        "keyword_groups": report_data.get("keyword_groups", {}),
        "metrics": {
            "total_checks": report_data.get("stat_total_checks"),
            "brand_mentions": report_data.get("stat_brand_mentions"),
            "share_of_voice": report_data.get("stat_brand_sov"),
            "api_health": report_data.get("stat_api_health"),
            "crawl_pages_crawled": report_data.get("metric_provenance", {}).get("crawl_pages_crawled", {}).get("value"),
            "pagespeed_performance_score": report_data.get("metric_provenance", {}).get("pagespeed_performance_score", {}).get("value"),
            "crux_lcp_ms": report_data.get("metric_provenance", {}).get("crux_lcp_ms", {}).get("value"),
            "crux_inp_ms": report_data.get("metric_provenance", {}).get("crux_inp_ms", {}).get("value"),
            "crux_cls": report_data.get("metric_provenance", {}).get("crux_cls", {}).get("value"),
        },
        "provider_status": report_data.get("source_provenance", {}),
        "platform_health": report_data.get("report_health", {}).get("platform_summaries", []),
        "top_competitor": {
            "name": report_data.get("top_competitor_name"),
            "mentions": report_data.get("top_competitor_mentions"),
        },
        "report_mode": report_data.get("report_mode"),
    }


def _parse_json_content(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def generate_tracker_interpretation(report_data: dict) -> dict:
    api_key = get_env_value("OPENAI_API_KEY")
    if not api_key:
        return {
            "provider": "openai",
            "status": "unavailable",
            "reason": "OPENAI_API_KEY is not configured.",
            "role": "interpretation_only",
            "payload": None,
        }

    verified_dataset = build_tracker_interpretation_input(report_data)
    prompt = (
        "Use only the supplied verified data. "
        "Do not invent, infer or estimate missing factual metrics. "
        "If data is unavailable, state Data Unavailable. "
        "Return valid JSON with keys: executive_summary, key_findings, recommendations, action_plan.\n"
        + json.dumps(verified_dataset, ensure_ascii=True)
    )
    try:
        response_payload, content = openai_chat_completion(api_key, prompt, model="gpt-4o-mini")
        if response_payload is None:
            return {
                "provider": "openai",
                "status": "failed",
                "reason": (content or "OpenAI interpretation request failed.").strip()[:240],
                "role": "interpretation_only",
                "payload": None,
            }
        if not content:
            return {
                "provider": "openai",
                "status": "failed",
                "reason": "OpenAI returned no interpretation content.",
                "role": "interpretation_only",
                "payload": None,
            }
        parsed = _parse_json_content(content)
        payload = {
            "executive_summary": parsed.get("executive_summary") or "Data Unavailable",
            "key_findings": parsed.get("key_findings") or [],
            "recommendations": parsed.get("recommendations") or [],
            "action_plan": parsed.get("action_plan") or [],
            "role": "interpretation_only",
        }
        return {
            "provider": "openai",
            "status": "success",
            "reason": None,
            "role": "interpretation_only",
            "payload": payload,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return {
            "provider": "openai",
            "status": "failed",
            "reason": "OpenAI returned invalid JSON.",
            "role": "interpretation_only",
            "payload": None,
        }
