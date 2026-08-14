from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from agents.base_agent import BaseAgent
from agents.crawl_utils import ensure_url, fetch_url


BACKLINK_COLLECTION_KEYS = {
    "backlink_records",
    "backlink_sources",
    "backlink_urls",
    "backlinks",
    "existing_backlinks",
    "referring_pages",
    "referring_urls",
}
SOURCE_URL_KEYS = {"backlink_url", "source_url", "source", "referring_url", "referring_page", "url"}
TARGET_URL_KEYS = {"target_url", "destination_url", "linked_url", "link_url"}


class BacklinkHTMLParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links = []
        self._active_link = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attrs_dict = {str(key).lower(): value for key, value in attrs}
        href = (attrs_dict.get("href") or "").strip()
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        if not absolute.startswith(("http://", "https://")):
            return
        rel = str(attrs_dict.get("rel") or "").lower().split()
        self._active_link = {
            "href": absolute,
            "rel": rel,
            "anchor_parts": [],
        }

    def handle_data(self, data):
        if self._active_link is not None:
            self._active_link["anchor_parts"].append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._active_link is None:
            return
        anchor_text = " ".join(" ".join(self._active_link["anchor_parts"]).split())
        self.links.append({
            "href": self._active_link["href"],
            "rel": self._active_link["rel"],
            "anchor_text": anchor_text,
        })
        self._active_link = None


def _domain(value: str) -> str:
    parsed = urlparse(ensure_url(value))
    host = (parsed.netloc or parsed.path or "").lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    return host.split(":", 1)[0].removeprefix("www.")


def _domain_matches(candidate_url: str, target_domain: str) -> bool:
    candidate = _domain(candidate_url)
    target = _domain(target_domain)
    return bool(candidate and target and (candidate == target or candidate.endswith(f".{target}")))


def _normalize_url_list(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        parts = raw.replace(",", "\n").splitlines()
    elif isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        return []
    urls = []
    for item in parts:
        value = ensure_url(str(item).strip())
        if value and value not in urls:
            urls.append(value)
    return urls


def _candidate_from_dict(item: dict, default_target_url: str, default_target_domain: str) -> dict | None:
    source_url = ""
    for key in SOURCE_URL_KEYS:
        if item.get(key):
            source_url = ensure_url(str(item.get(key)))
            break
    if not source_url:
        return None

    target_url = default_target_url
    for key in TARGET_URL_KEYS:
        if item.get(key):
            target_url = ensure_url(str(item.get(key)))
            break
    target_domain = str(item.get("target_domain") or default_target_domain or _domain(target_url)).strip()
    return {
        "source_url": source_url,
        "target_url": target_url,
        "target_domain": target_domain,
        "expected_anchor_text": str(item.get("expected_anchor_text") or item.get("anchor_text") or "").strip(),
    }


def _extract_candidates_from_payload(payload, default_target_url: str, default_target_domain: str, *, in_backlink_collection: bool = False) -> list[dict]:
    candidates = []
    if isinstance(payload, dict):
        if in_backlink_collection:
            candidate = _candidate_from_dict(payload, default_target_url, default_target_domain)
            if candidate:
                candidates.append(candidate)
        for key, value in payload.items():
            normalized_key = str(key).lower()
            if normalized_key in BACKLINK_COLLECTION_KEYS:
                candidates.extend(_extract_candidates_from_payload(value, default_target_url, default_target_domain, in_backlink_collection=True))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, str) and in_backlink_collection:
                for source_url in _normalize_url_list([item]):
                    candidates.append({
                        "source_url": source_url,
                        "target_url": default_target_url,
                        "target_domain": default_target_domain,
                        "expected_anchor_text": "",
                    })
            else:
                candidates.extend(_extract_candidates_from_payload(item, default_target_url, default_target_domain, in_backlink_collection=in_backlink_collection))
    elif isinstance(payload, str) and in_backlink_collection:
        for source_url in _normalize_url_list(payload):
            candidates.append({
                "source_url": source_url,
                "target_url": default_target_url,
                "target_domain": default_target_domain,
                "expected_anchor_text": "",
            })
    return candidates


def extract_backlink_candidates(input_data: dict) -> list[dict]:
    target_url = ensure_url(input_data.get("target_url") or input_data.get("website_url") or input_data.get("brand_website") or "")
    target_domain = str(input_data.get("target_domain") or _domain(target_url)).strip()
    candidates = []

    manual_urls = []
    manual_urls.extend(_normalize_url_list(input_data.get("backlink_url")))
    manual_urls.extend(_normalize_url_list(input_data.get("backlink_urls")))
    manual_urls.extend(_normalize_url_list(input_data.get("source_urls")))
    for source_url in manual_urls:
        candidates.append({
            "source_url": source_url,
            "target_url": target_url,
            "target_domain": target_domain,
            "expected_anchor_text": str(input_data.get("expected_anchor_text") or "").strip(),
        })

    for item in input_data.get("backlinks") or []:
        if isinstance(item, dict):
            candidate = _candidate_from_dict(item, target_url, target_domain)
            if candidate:
                candidates.append(candidate)

    run_context = input_data.get("_tracker_run_context") or {}
    for row in run_context.get("provider_results") or []:
        payload = row.get("payload")
        candidates.extend(_extract_candidates_from_payload(payload, target_url, target_domain))

    deduped = []
    seen = set()
    for candidate in candidates:
        source_url = candidate.get("source_url")
        if not source_url or source_url in seen:
            continue
        seen.add(source_url)
        deduped.append(candidate)
    return deduped


def _classify_found_link(link: dict) -> tuple[str, str]:
    rel = set(link.get("rel") or [])
    if "sponsored" in rel:
        return "SPONSORED", "sponsored"
    if "ugc" in rel:
        return "UGC", "ugc"
    if "nofollow" in rel:
        return "NOFOLLOW", "nofollow"
    return "VERIFIED", "follow"


def verify_backlink_source(candidate: dict, *, timeout: int = 15) -> dict:
    source_url = ensure_url(candidate.get("source_url") or "")
    target_url = ensure_url(candidate.get("target_url") or "")
    target_domain = str(candidate.get("target_domain") or _domain(target_url)).strip()
    checked_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    base_result = {
        "source_url": source_url,
        "target_url": target_url,
        "target_domain": target_domain,
        "backlink_found": False,
        "anchor_text": "",
        "link_rel": [],
        "link_type": None,
        "http_status": None,
        "final_url": None,
        "redirected": False,
        "verification_status": "UNREACHABLE",
        "checked_at": checked_at,
        "error": None,
    }

    try:
        fetched = fetch_url(source_url, timeout=timeout, retries=0)
    except requests.exceptions.Timeout:
        return {**base_result, "verification_status": "UNREACHABLE", "error": "timeout"}
    except requests.RequestException as exc:
        return {**base_result, "verification_status": "UNREACHABLE", "error": type(exc).__name__}

    result = {
        **base_result,
        "http_status": fetched.get("status_code"),
        "final_url": fetched.get("final_url"),
        "redirected": fetched.get("final_url") != fetched.get("url"),
    }
    if int(fetched.get("status_code") or 0) >= 400:
        return {**result, "verification_status": "BROKEN_SOURCE"}

    parser = BacklinkHTMLParser(fetched.get("final_url") or source_url)
    parser.feed(fetched.get("text") or "")
    matching_links = [link for link in parser.links if _domain_matches(link.get("href") or "", target_domain)]
    if not matching_links:
        return {**result, "verification_status": "TARGET_NOT_FOUND"}

    selected = matching_links[0]
    status, link_type = _classify_found_link(selected)
    return {
        **result,
        "target_url": selected.get("href") or target_url,
        "backlink_found": True,
        "anchor_text": selected.get("anchor_text") or "",
        "expected_anchor_text": str(candidate.get("expected_anchor_text") or ""),
        "anchor_text_matched": (
            not candidate.get("expected_anchor_text")
            or str(candidate.get("expected_anchor_text")).strip().lower() in (selected.get("anchor_text") or "").lower()
        ),
        "link_rel": selected.get("rel") or [],
        "link_type": link_type,
        "verification_status": status,
    }


class BacklinkVerificationAgent(BaseAgent):
    NAME = "Backlink Verification Agent"
    DESCRIPTION = "Verify real backlink source URLs against the target site."
    ICON = "fa-check-double"
    CATEGORY = "Link Building"
    INPUT_SCHEMA = [
        {"id": "backlink_url", "label": "Backlink URL", "type": "url", "placeholder": "https://referring-site.com/article", "required": False},
        {"id": "backlink_urls", "label": "Backlink URLs", "type": "url_list", "required": False, "help_text": "One genuine backlink source URL per line."},
        {"id": "target_url", "label": "Target URL", "type": "url", "placeholder": "https://example.com/landing-page", "required": False},
        {"id": "expected_anchor_text", "label": "Expected Anchor Text", "type": "text", "required": False},
        {"id": "project_name", "label": "Project Name", "type": "text", "required": False},
        {"id": "verification_frequency", "label": "Verification Frequency", "type": "select", "required": False, "default": "once", "options": [{"value": "once", "label": "Once"}, {"value": "daily", "label": "Daily"}, {"value": "weekly", "label": "Weekly"}, {"value": "monthly", "label": "Monthly"}]},
        {"id": "force_javascript_rendering", "label": "Force JavaScript Rendering", "type": "checkbox", "required": False, "default": False},
    ]

    def run(self, input_data: dict) -> dict:
        candidates = extract_backlink_candidates(input_data)
        if not candidates:
            return {
                "success": False,
                "status": "not_run",
                "agent": self.NAME,
                "agent_id": "backlink_verification",
                "reason_code": "missing_required_input",
                "message": "No genuine backlink source URLs were supplied or found for this run.",
                "summary": "Backlink Verification was not run because no existing backlink URLs were available.",
                "recommendations": ["Add real backlink source URLs, or connect a backlink provider that returns existing referring pages."],
                "data": {
                    "reason_code": "missing_required_input",
                    "data_source": "not_run",
                    "api_used": [],
                    "missing_api_keys": [],
                    "backlinks": [],
                },
            }

        checked = [verify_backlink_source(candidate) for candidate in candidates[:100]]
        verified = [item for item in checked if item["backlink_found"]]
        broken = [item for item in checked if item["verification_status"] in {"BROKEN_SOURCE", "UNREACHABLE"}]
        nofollow = [item for item in checked if item["link_type"] == "nofollow"]
        lost = [item for item in checked if item["verification_status"] == "TARGET_NOT_FOUND"]
        unreachable = [item for item in checked if item["verification_status"] == "UNREACHABLE"]
        status = "partial" if broken and verified else "completed"
        success = len(unreachable) < len(checked)

        response = self.build_structured_response(
            input_data,
            f"Checked {len(checked)} real backlink source URL(s); {len(verified)} currently link to the target domain.",
            [
                "Review LOST or TARGET_NOT_FOUND rows before requesting link restoration.",
                "Treat nofollow, sponsored, and UGC attributes as factual link attributes, not authority metrics.",
            ],
            {
                "status": status,
                "total_backlinks_checked": len(checked),
                "verified_count": len(verified),
                "lost_count": len(lost),
                "broken_or_unreachable_count": len(broken),
                "follow_count": sum(1 for item in checked if item["link_type"] == "follow"),
                "nofollow_count": len(nofollow),
                "backlinks": checked,
                "data_source": "real_backlink_source_crawl",
                "api_used": [],
                "missing_api_keys": [],
                "unavailable_metrics": ["domain_authority", "domain_rating", "traffic_estimate"],
            },
            success=success,
            message="Backlink source verification completed." if success else "All backlink source URLs were unreachable.",
        )
        response["status"] = status if success else "failed"
        return response
