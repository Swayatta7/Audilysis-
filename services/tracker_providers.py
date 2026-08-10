from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from agents.crawl_utils import crawl_site, ensure_url
from agents.runtime_config import get_env_value


PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE)


def normalize_domain(value: str) -> str | None:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.netloc or parsed.path or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if ":" in host:
        host = host.split(":", 1)[0]
    if not DOMAIN_RE.match(host):
        return None
    return host


def normalize_competitor_domains(values: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        host = normalize_domain(value)
        if host and host not in seen:
            normalized.append(host)
            seen.add(host)
    return normalized


def combine_keywords(high_volume_keywords: list[str] | None, brand_keywords: list[str] | None) -> list[str]:
    seen: set[str] = set()
    combined: list[str] = []
    for keyword in (high_volume_keywords or []) + (brand_keywords or []):
        cleaned = (keyword or "").strip()
        if cleaned and cleaned.lower() not in seen:
            combined.append(cleaned)
            seen.add(cleaned.lower())
    return combined


def collect_crawl_provider(website_url: str) -> dict:
    try:
        crawl = crawl_site(
            ensure_url(website_url),
            depth=0,
            limit=1,
            broken_link_scope="internal",
            max_link_checks_per_page=5,
            audit_timeout=12,
            audit_retries=0,
            link_check_timeout=4,
        )
        pages = [page for page in crawl.get("pages", []) if page.get("http_status")]
        if not pages:
            return {
                "provider": "crawl",
                "status": "failed",
                "reason": "No crawlable pages were returned for this website.",
                "payload": None,
            }
        primary = pages[0]
        payload = {
            "pages_crawled": len(pages),
            "indexable_pages": len(
                [
                    page
                    for page in pages
                    if page.get("http_status") == 200
                    and "noindex" not in str(page.get("meta_robots") or "").lower()
                ]
            ),
            "https": bool(primary.get("https")),
            "http_status": primary.get("http_status"),
            "title_present": bool(primary.get("title")),
            "meta_description_present": bool(primary.get("meta_description")),
            "h1_count": len(primary.get("h1") or []),
            "internal_links": len(primary.get("internal_links") or []),
            "response_time_ms": primary.get("response_time_ms"),
            "page_size_bytes": primary.get("page_size_bytes"),
        }
        return {"provider": "crawl", "status": "success", "reason": None, "payload": payload}
    except requests.RequestException as exc:
        return {"provider": "crawl", "status": "failed", "reason": f"Crawl failed: {exc}", "payload": None}
    except Exception as exc:
        return {"provider": "crawl", "status": "failed", "reason": f"Crawl failed: {exc}", "payload": None}


def collect_pagespeed_provider(website_url: str) -> dict:
    api_key = get_env_value("PAGESPEED_API_KEY")
    if not api_key:
        return {
            "provider": "pagespeed",
            "status": "unavailable",
            "reason": "PAGESPEED_API_KEY is not configured.",
            "payload": None,
        }
    try:
        response = requests.get(
            PAGESPEED_ENDPOINT,
            params={
                "url": website_url,
                "key": api_key,
                "strategy": "mobile",
                "category": ["PERFORMANCE", "ACCESSIBILITY", "BEST_PRACTICES", "SEO"],
            },
            timeout=60,
        )
        if response.status_code != 200:
            return {
                "provider": "pagespeed",
                "status": "unavailable" if response.status_code in {401, 403, 429} else "failed",
                "reason": f"PageSpeed returned HTTP {response.status_code}.",
                "payload": None,
            }
        payload = response.json()
        lighthouse = payload.get("lighthouseResult", {})
        categories = lighthouse.get("categories", {})
        audits = lighthouse.get("audits", {})
        loading = payload.get("loadingExperience", {}).get("metrics", {})
        return {
            "provider": "pagespeed",
            "status": "success",
            "reason": None,
            "payload": {
                "performance_score": None if categories.get("performance", {}).get("score") is None else categories.get("performance", {}).get("score") * 100,
                "accessibility_score": None if categories.get("accessibility", {}).get("score") is None else categories.get("accessibility", {}).get("score") * 100,
                "best_practices_score": None if categories.get("best-practices", {}).get("score") is None else categories.get("best-practices", {}).get("score") * 100,
                "seo_score": None if categories.get("seo", {}).get("score") is None else categories.get("seo", {}).get("score") * 100,
                "first_contentful_paint_ms": (audits.get("first-contentful-paint", {}) or {}).get("numericValue"),
                "largest_contentful_paint_ms": loading.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile"),
                "total_blocking_time_ms": (audits.get("total-blocking-time", {}) or {}).get("numericValue"),
                "speed_index": (audits.get("speed-index", {}) or {}).get("numericValue"),
                "cumulative_layout_shift": loading.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile"),
            },
        }
    except requests.Timeout:
        return {"provider": "pagespeed", "status": "failed", "reason": "PageSpeed request timed out.", "payload": None}
    except requests.RequestException as exc:
        return {"provider": "pagespeed", "status": "failed", "reason": f"PageSpeed request failed: {exc}", "payload": None}


def collect_crux_provider(website_url: str) -> dict:
    api_key = get_env_value("CRUX_API_KEY")
    if not api_key:
        return {
            "provider": "crux",
            "status": "unavailable",
            "reason": "CRUX_API_KEY is not configured.",
            "payload": None,
        }
    try:
        response = requests.post(
            f"{CRUX_ENDPOINT}?key={api_key}",
            json={"url": website_url, "formFactor": "PHONE"},
            timeout=60,
        )
        if response.status_code == 404:
            return {
                "provider": "crux",
                "status": "unavailable",
                "reason": "insufficient_field_data",
                "payload": None,
            }
        if response.status_code != 200:
            return {
                "provider": "crux",
                "status": "unavailable" if response.status_code in {401, 403, 429} else "failed",
                "reason": f"Chrome UX Report returned HTTP {response.status_code}.",
                "payload": None,
            }
        record = response.json().get("record", {})
        metrics = record.get("metrics", {})
        if not metrics:
            return {
                "provider": "crux",
                "status": "unavailable",
                "reason": "insufficient_field_data",
                "payload": None,
            }
        return {
            "provider": "crux",
            "status": "success",
            "reason": None,
            "payload": {
                "largest_contentful_paint_ms": (metrics.get("largest_contentful_paint") or {}).get("percentiles", {}).get("p75"),
                "cumulative_layout_shift": (metrics.get("cumulative_layout_shift") or {}).get("percentiles", {}).get("p75"),
                "interaction_to_next_paint_ms": (metrics.get("interaction_to_next_paint") or {}).get("percentiles", {}).get("p75"),
                "first_contentful_paint_ms": (metrics.get("first_contentful_paint") or {}).get("percentiles", {}).get("p75"),
            },
        }
    except requests.Timeout:
        return {"provider": "crux", "status": "failed", "reason": "Chrome UX Report request timed out.", "payload": None}
    except requests.RequestException as exc:
        return {"provider": "crux", "status": "failed", "reason": f"Chrome UX Report request failed: {exc}", "payload": None}


def collect_tracker_provider_bundle(website_url: str) -> list[dict]:
    return [
        collect_crawl_provider(website_url),
        collect_pagespeed_provider(website_url),
        collect_crux_provider(website_url),
    ]
