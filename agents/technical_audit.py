from collections import Counter
import time

import requests
from bs4 import BeautifulSoup

from agents.base_agent import BaseAgent
from agents.crawl_utils import crawl_site, ensure_url
from agents.llm_client import openai_chat_completion
from agents.runtime_config import get_env_value


PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"


class TechnicalAuditAgent(BaseAgent):
    NAME = "Technical Audit Agent"
    DESCRIPTION = "Audit technical SEO fundamentals for a website."
    ICON = "fa-magnifying-glass-chart"
    CATEGORY = "Technical SEO"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "crawl_depth", "label": "Crawl Depth", "type": "number", "placeholder": "1", "required": False, "min": 0, "max": 2, "default": 1, "help_text": "How many link levels deep to crawl (0-2)."},
        {"id": "maximum_pages", "label": "Maximum Pages", "type": "number", "placeholder": "10", "required": False, "min": 1, "max": 50, "default": 10},
        {"id": "device", "label": "Device", "type": "select", "required": False, "default": "mobile", "options": [{"value": "mobile", "label": "Mobile"}, {"value": "desktop", "label": "Desktop"}]},
        {"id": "sitemap_url", "label": "Sitemap URL", "type": "url", "placeholder": "https://example.com/sitemap.xml", "required": False},
        {"id": "robots_txt_url", "label": "Robots.txt URL", "type": "url", "placeholder": "https://example.com/robots.txt", "required": False},
        {"id": "force_refresh", "label": "Force Refresh", "type": "checkbox", "required": False, "default": False, "help_text": "Ignore cached crawl data and re-crawl the site."},
    ]
    SCORE_BANDS = [(90, "Excellent"), (80, "Good"), (70, "Needs Attention"), (50, "Poor"), (0, "Critical")]

    def _score_status(self, score: int) -> str:
        for threshold, label in self.SCORE_BANDS:
            if score >= threshold:
                return label
        return "Critical"

    def _source(self, name: str, connected: bool, detail: str) -> dict:
        return {"name": name, "status": "Connected" if connected else "Not Connected", "detail": detail}

    def _premium_api_status(self) -> list[dict]:
        return [{"service": "Semrush", "status": "Not Connected"}, {"service": "Ahrefs", "status": "Not Connected"}, {"service": "Moz", "status": "Not Connected"}, {"service": "DataForSEO", "status": "Not Connected"}]

    def _beautifulsoup_snapshot(self, url: str) -> tuple[dict, str | None]:
        try:
            response = requests.get(ensure_url(url), timeout=20, headers={"User-Agent": "AudylysisBot/1.0 (+https://localhost)"})
            soup = BeautifulSoup(response.text or "", "html.parser")
            return {
                "title": (soup.title.string or "").strip() if soup.title and soup.title.string else "",
                "meta_description": (soup.find("meta", attrs={"name": "description"}) or {}).get("content", "") if soup.find("meta", attrs={"name": "description"}) else "",
                "h1_count": len(soup.find_all("h1")),
                "h2_count": len(soup.find_all("h2")),
                "canonical": bool(soup.find("link", rel=lambda value: value and "canonical" in str(value).lower())),
                "meta_robots": bool(soup.find("meta", attrs={"name": "robots"})),
                "structured_data_count": len(soup.find_all("script", attrs={"type": "application/ld+json"})),
                "images_total": len(soup.find_all("img")),
                "images_missing_alt": len([img for img in soup.find_all("img") if not (img.get("alt") or "").strip()]),
            }, None
        except requests.RequestException as exc:
            return {}, f"BeautifulSoup parse unavailable: {exc}"

    def _pagespeed_insights(self, website_url: str) -> tuple[dict | None, str | None]:
        api_key = get_env_value("PAGESPEED_API_KEY")
        if not api_key:
            return None, "PageSpeed unavailable: PAGESPEED_API_KEY not connected."
        try:
            response = requests.get(
                PAGESPEED_ENDPOINT,
                params={"url": website_url, "key": api_key, "strategy": "mobile", "category": ["PERFORMANCE", "ACCESSIBILITY", "BEST_PRACTICES", "SEO"]},
                timeout=60,
            )
            if response.status_code != 200:
                return None, f"PageSpeed unavailable: HTTP {response.status_code}."
            payload = response.json()
            lighthouse = payload.get("lighthouseResult", {})
            categories = lighthouse.get("categories", {})
            audits = lighthouse.get("audits", {})
            loading = payload.get("loadingExperience", {}).get("metrics", {})
            return {
                "performance_score": None if categories.get("performance", {}).get("score") is None else categories.get("performance", {}).get("score") * 100,
                "accessibility_score": None if categories.get("accessibility", {}).get("score") is None else categories.get("accessibility", {}).get("score") * 100,
                "best_practices_score": None if categories.get("best-practices", {}).get("score") is None else categories.get("best-practices", {}).get("score") * 100,
                "seo_score": None if categories.get("seo", {}).get("score") is None else categories.get("seo", {}).get("score") * 100,
                "core_web_vitals": {
                    "largest_contentful_paint_ms": loading.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile"),
                    "cumulative_layout_shift": loading.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile"),
                    "interaction_to_next_paint_ms": loading.get("INTERACTION_TO_NEXT_PAINT", {}).get("percentile"),
                    "first_contentful_paint_ms": (audits.get("first-contentful-paint", {}) or {}).get("numericValue"),
                    "total_blocking_time_ms": (audits.get("total-blocking-time", {}) or {}).get("numericValue"),
                },
            }, None
        except requests.RequestException as exc:
            return None, f"PageSpeed unavailable: {exc}"

    def _crux_data(self, website_url: str) -> tuple[dict | None, str | None]:
        api_key = get_env_value("CRUX_API_KEY")
        if not api_key:
            return None, "Chrome UX Report unavailable: CRUX_API_KEY not connected."
        try:
            response = requests.post(f"{CRUX_ENDPOINT}?key={api_key}", json={"url": website_url, "formFactor": "PHONE"}, timeout=60)
            if response.status_code != 200:
                return None, f"Chrome UX Report unavailable: HTTP {response.status_code}."
            record = response.json().get("record", {})
            metrics = record.get("metrics", {})
            return {
                "largest_contentful_paint_ms": (metrics.get("largest_contentful_paint") or {}).get("percentiles", {}).get("p75"),
                "cumulative_layout_shift": (metrics.get("cumulative_layout_shift") or {}).get("percentiles", {}).get("p75"),
                "interaction_to_next_paint_ms": (metrics.get("interaction_to_next_paint") or {}).get("percentiles", {}).get("p75"),
                "first_contentful_paint_ms": (metrics.get("first_contentful_paint") or {}).get("percentiles", {}).get("p75"),
            }, None
        except requests.RequestException as exc:
            return None, f"Chrome UX Report unavailable: {exc}"

    def _llm_summary(self, api_key: str, metrics: dict) -> tuple[str | None, list[str]]:
        prompt = (
            "Using only the provided audit metrics, write JSON with keys executive_summary and recommendations. "
            "Do not invent metrics.\n"
            f"{metrics}"
        )
        _, content = openai_chat_completion(api_key, prompt, model="gpt-4o-mini")
        if not content:
            return None, []
        try:
            import json
            parsed = json.loads(content)
            return parsed.get("executive_summary"), parsed.get("recommendations", [])
        except Exception:
            return None, []

    def _issue_group(self, title: str, severity: str, affected_pages: list[str], affected_count: int, examples: list[str], summary: str, fix: str, impact: str, eta: str) -> dict:
        return {"issue": title, "severity": severity, "affected_pages": len(affected_pages), "affected_count": affected_count, "examples": examples[:3], "summary": summary, "recommended_fix": fix, "estimated_seo_impact": impact, "estimated_time_to_fix": eta}

    def run(self, input_data: dict) -> dict:
        started = time.perf_counter()
        website_url = ensure_url(input_data.get("website_url") or "")
        if not website_url:
            return self.missing_input_response("website_url", input_data)

        raw_crawl_depth = input_data.get("crawl_depth")
        crawl_depth = 1 if raw_crawl_depth in (None, "") else max(0, min(int(raw_crawl_depth), 2))
        raw_maximum_pages = input_data.get("maximum_pages")
        maximum_pages = 10 if raw_maximum_pages in (None, "") else max(1, min(int(raw_maximum_pages), 50))
        crawl = crawl_site(website_url, depth=crawl_depth, limit=maximum_pages, broken_link_scope="internal", max_link_checks_per_page=10, audit_timeout=12, audit_retries=0, link_check_timeout=4)
        pages = [page for page in crawl["pages"] if page.get("http_status")]

        pagespeed_data, pagespeed_note = self._pagespeed_insights(website_url)
        crux_data, crux_note = self._crux_data(website_url)
        bs_snapshot, bs_note = self._beautifulsoup_snapshot(website_url)
        openai_key = get_env_value("OPENAI_API_KEY")

        connected_sources = [
            self._source("Real Crawl Engine", True, "Live crawl of the target site."),
            self._source("Requests", True, "HTTP fetching for crawl and source verification."),
            self._source("BeautifulSoup", bool(bs_snapshot), bs_note or "HTML verification and supplemental parsing."),
            self._source("Internal SEO Scoring", True, "Deterministic scoring from measured checks."),
            self._source("Google PageSpeed Insights", bool(pagespeed_data), pagespeed_note or "Lab performance and SEO scoring."),
            self._source("Chrome UX Report", bool(crux_data), crux_note or "Field data from real Chrome users."),
            self._source("OpenAI", bool(openai_key), "Used only for executive summary and recommendations." if openai_key else "Not connected."),
        ]
        api_used = ["Real Crawl Engine", "Requests", "BeautifulSoup", "Internal SEO Scoring"]
        if pagespeed_data:
            api_used.append("Google PageSpeed Insights")
        if crux_data:
            api_used.append("Chrome UX Report")

        if not pages:
            return {
                "success": False,
                "agent": self.NAME,
                "error": "Crawl failed",
                "message": f"Unable to crawl {website_url}. Verify the URL is reachable.",
                "data": {
                    "data_source": "real_crawl",
                    "data_sources": connected_sources,
                    "api_used": api_used,
                    "premium_apis": self._premium_api_status(),
                    "missing_api_keys": [key for key in ["PAGESPEED_API_KEY", "CRUX_API_KEY", "OPENAI_API_KEY"] if not get_env_value(key)],
                    "unavailable_metrics": ["PageSpeed / Core Web Vitals" if not pagespeed_data else "", "Chrome UX field data" if not crux_data else ""],
                },
            }

        title_counter = Counter(page["title"].strip() for page in pages if page.get("title"))
        meta_counter = Counter(page["meta_description"].strip() for page in pages if page.get("meta_description"))
        missing_h1_pages = [page["url"] for page in pages if not page["h1"]]
        missing_h2_pages = [page["url"] for page in pages if not page["h2"]]
        missing_title_pages = [page["url"] for page in pages if not page["title"]]
        missing_meta_pages = [page["url"] for page in pages if not page["meta_description"]]
        duplicate_title_pages = [page["url"] for page in pages if page.get("title") and title_counter[page["title"].strip()] > 1]
        duplicate_meta_pages = [page["url"] for page in pages if page.get("meta_description") and meta_counter[page["meta_description"].strip()] > 1]
        missing_canonical_pages = [page["url"] for page in pages if not page["canonical"]]
        missing_meta_robots_pages = [page["url"] for page in pages if not page["meta_robots"]]
        structured_data_missing_pages = [page["url"] for page in pages if not page["structured_data"]]
        redirect_chain_pages = [page["url"] for page in pages if len(page["redirect_chain"]) > 1]
        slow_pages = [page["url"] for page in pages if page["response_time_ms"] > 2000]
        non_https_pages = [page["url"] for page in pages if not page["https"]]
        no_internal_link_pages = [page["url"] for page in pages if not page["internal_links"]]
        broken_links = crawl["broken_links"]
        images_missing_alt = sorted({image for page in pages for image in page["images_missing_alt"]})
        image_alt_pages = [page["url"] for page in pages if page["images_missing_alt"]]
        total_images = sum(len(page.get("images", [])) for page in pages)
        robots_ok = any(page["robots_txt"].get("status_code") == 200 for page in pages)
        sitemap_ok = any(page["sitemap_xml"].get("status_code") == 200 for page in pages)
        response_times = [page["response_time_ms"] for page in pages]
        page_sizes = [page["page_size_bytes"] for page in pages]
        internal_counts = [len(page["internal_links"]) for page in pages]
        external_counts = [len(page["external_links"]) for page in pages]
        indexable_pages = len([page for page in pages if page["http_status"] == 200 and "noindex" not in (page.get("meta_robots") or "").lower()])

        critical_errors = []
        warnings = []
        if broken_links:
            critical_errors.append(self._issue_group("Broken Links", "Critical", sorted({item["source_url"] for item in broken_links}), len(broken_links), [item["url"] for item in broken_links], f"{len(broken_links)} internal link target(s) returned 4xx/5xx responses.", "Repair or remove broken internal links.", "High SEO Impact", "30-90 minutes"))
        if non_https_pages:
            critical_errors.append(self._issue_group("HTTPS / SSL", "Critical", non_https_pages, len(non_https_pages), non_https_pages, "Some crawled pages are not served over HTTPS.", "Force HTTPS and validate SSL coverage.", "High SEO Impact", "1-2 hours"))
        issue_map = [
            ("Missing Title Tags", "High", missing_title_pages, len(missing_title_pages), missing_title_pages, "Add unique title tags.", "High SEO Impact", "15-45 minutes"),
            ("Duplicate Titles", "High", sorted(set(duplicate_title_pages)), len(duplicate_title_pages), sorted(set(duplicate_title_pages)), "Rewrite duplicate titles.", "Medium SEO Impact", "30-60 minutes"),
            ("Missing Meta Descriptions", "Medium", missing_meta_pages, len(missing_meta_pages), missing_meta_pages, "Write missing meta descriptions.", "Medium SEO Impact", "20-45 minutes"),
            ("Duplicate Meta Descriptions", "Medium", sorted(set(duplicate_meta_pages)), len(duplicate_meta_pages), sorted(set(duplicate_meta_pages)), "Replace duplicated meta descriptions.", "Medium SEO Impact", "20-45 minutes"),
            ("Missing H1", "High", missing_h1_pages, len(missing_h1_pages), missing_h1_pages, "Add one clear H1 per page.", "Medium SEO Impact", "15-30 minutes"),
            ("Missing H2 Structure", "Low", missing_h2_pages, len(missing_h2_pages), missing_h2_pages, "Add meaningful H2 sections.", "Low SEO Impact", "20-60 minutes"),
            ("Canonical Issues", "High", missing_canonical_pages, len(missing_canonical_pages), missing_canonical_pages, "Add self-referencing canonicals.", "High SEO Impact", "20-60 minutes"),
            ("Meta Robots Coverage", "Low", missing_meta_robots_pages, len(missing_meta_robots_pages), missing_meta_robots_pages, "Set explicit robots directives where supported.", "Low SEO Impact", "15-30 minutes"),
            ("Structured Data Coverage", "Medium", structured_data_missing_pages, len(structured_data_missing_pages), structured_data_missing_pages, "Add schema markup to key templates.", "Medium SEO Impact", "1-3 hours"),
            ("Images Missing ALT Text", "Medium", image_alt_pages, len(images_missing_alt), images_missing_alt, "Add descriptive ALT attributes.", "Medium SEO Impact", "30-60 minutes"),
            ("Redirect Chains", "Medium", redirect_chain_pages, len(redirect_chain_pages), redirect_chain_pages, "Update links to final URLs.", "Medium SEO Impact", "30-60 minutes"),
            ("Slow Response Time", "Medium", slow_pages, len(slow_pages), slow_pages, "Optimize heavy assets and server response time.", "Medium SEO Impact", "1-4 hours"),
            ("Internal Link Coverage", "Low", no_internal_link_pages, len(no_internal_link_pages), no_internal_link_pages, "Add contextual internal links.", "Low SEO Impact", "20-45 minutes"),
        ]
        for title, severity, affected_pages, count, examples, fix, impact, eta in issue_map:
            if affected_pages or count:
                warnings.append(self._issue_group(title, severity, affected_pages, count, examples, f"{count} issue(s) detected for {title.lower()}.", fix, impact, eta))
        if not robots_ok:
            warnings.append(self._issue_group("Robots.txt Availability", "High", [website_url], 1, [website_url], "robots.txt was not available.", "Publish robots.txt at the site root.", "High SEO Impact", "10-20 minutes"))
        if not sitemap_ok:
            warnings.append(self._issue_group("XML Sitemap Availability", "High", [website_url], 1, [website_url], "sitemap.xml was not available.", "Generate and submit an XML sitemap.", "High SEO Impact", "20-45 minutes"))
        if pagespeed_note:
            warnings.append(self._issue_group("PageSpeed Coverage", "Low", [website_url], 1, [website_url], pagespeed_note, "Connect PageSpeed Insights to add performance data.", "Low SEO Impact", "5-10 minutes"))
        if crux_note:
            warnings.append(self._issue_group("Chrome UX Report Coverage", "Low", [website_url], 1, [website_url], crux_note, "Connect CrUX for field data.", "Low SEO Impact", "5-10 minutes"))

        cwv = pagespeed_data.get("core_web_vitals", {}) if pagespeed_data else {}
        cwv_values_available = all(
            cwv.get(key) is not None for key in ["largest_contentful_paint_ms", "cumulative_layout_shift", "interaction_to_next_paint_ms"]
        )
        cwv_passed = bool(
            cwv_values_available
            and cwv.get("largest_contentful_paint_ms") <= 2500
            and cwv.get("cumulative_layout_shift") <= 0.1
            and cwv.get("interaction_to_next_paint_ms") <= 200
        )
        score_components = [
            {"label": "HTTP Status", "max_points": 10, "earned_points": 10 if all(page["http_status"] < 400 for page in pages) else max(0, 10 - len([page for page in pages if page["http_status"] >= 400]) * 5)},
            {"label": "HTTPS", "max_points": 5, "earned_points": 5 if not non_https_pages else 0},
            {"label": "SSL", "max_points": 5, "earned_points": 5 if not non_https_pages else 0},
            {"label": "Robots.txt", "max_points": 5, "earned_points": 5 if robots_ok else 0},
            {"label": "Sitemap.xml", "max_points": 5, "earned_points": 5 if sitemap_ok else 0},
            {"label": "Title Coverage", "max_points": 5, "earned_points": max(0, 5 - len(missing_title_pages) - min(len(duplicate_title_pages), 2))},
            {"label": "Meta Description Coverage", "max_points": 5, "earned_points": max(0, 5 - len(missing_meta_pages) - min(len(duplicate_meta_pages), 2))},
            {"label": "Canonical Coverage", "max_points": 5, "earned_points": max(0, 5 - len(missing_canonical_pages))},
            {"label": "Meta Robots Coverage", "max_points": 5, "earned_points": max(0, 5 - min(len(missing_meta_robots_pages), 5))},
            {"label": "Heading Structure", "max_points": 10, "earned_points": max(0, 10 - len(missing_h1_pages) * 2 - min(len(missing_h2_pages), 4))},
            {"label": "ALT Coverage", "max_points": 10, "earned_points": 10 if total_images == 0 else max(0, round(10 * (1 - (len(images_missing_alt) / max(total_images, 1)))))},
            {"label": "Broken Links", "max_points": 10, "earned_points": 10 if not broken_links else max(0, 10 - min(len(broken_links), 10))},
            {"label": "Structured Data", "max_points": 10, "earned_points": 10 if len(structured_data_missing_pages) < len(pages) else 0},
            {"label": "Response Time", "max_points": 5, "earned_points": 5 if not slow_pages else max(0, 5 - len(slow_pages))},
            {"label": "Redirect Hygiene", "max_points": 5, "earned_points": 5 if not redirect_chain_pages else max(0, 5 - len(redirect_chain_pages))},
        ]
        if pagespeed_data:
            pagespeed_component_score = pagespeed_data.get("performance_score")
            score_components.extend([
                {"label": "PageSpeed Performance", "max_points": 5, "earned_points": round(pagespeed_component_score / 20) if pagespeed_component_score is not None else None},
                {"label": "Core Web Vitals", "max_points": 5, "earned_points": 5 if cwv_passed else (2 if cwv_values_available else None)},
            ])
        available_score_components = [item for item in score_components if item["earned_points"] is not None]
        score = round(sum(item["earned_points"] for item in available_score_components) / sum(item["max_points"] for item in available_score_components) * 100)
        score_status = self._score_status(score)

        technical_metrics = {
            "pages_crawled": len(pages),
            "pages_indexable": indexable_pages,
            "broken_links": len(broken_links),
            "redirect_chains": len(redirect_chain_pages),
            "canonical_errors": len(missing_canonical_pages),
            "duplicate_titles": len(duplicate_title_pages),
            "duplicate_meta_descriptions": len(duplicate_meta_pages),
            "missing_h1": len(missing_h1_pages),
            "missing_h2": len(missing_h2_pages),
            "images_total": total_images,
            "images_missing_alt": len(images_missing_alt),
            "structured_data_pages": len(pages) - len(structured_data_missing_pages),
            "average_response_time_ms": round(sum(response_times) / len(response_times), 2) if response_times else None,
            "average_page_size_kb": round((sum(page_sizes) / len(page_sizes)) / 1024, 2) if page_sizes else None,
            "average_internal_links": round(sum(internal_counts) / len(internal_counts), 2) if internal_counts else None,
            "average_external_links": round(sum(external_counts) / len(external_counts), 2) if external_counts else None,
            "robots_txt": "Found" if robots_ok else "Missing",
            "xml_sitemap": "Found" if sitemap_ok else "Missing",
            "ssl": "Valid" if not non_https_pages else "Issues Detected",
            "http_status": "Healthy" if all(page["http_status"] < 400 for page in pages) else "Errors Detected",
            "core_web_vitals": "Passed" if cwv_passed else ("Unavailable" if not cwv_values_available else "Needs Attention"),
        }
        recommendations = [
            "Compress large hero images and reduce render-blocking resources on slow pages." if slow_pages else "",
            "Repair broken internal links first to restore crawl paths." if broken_links else "",
            "Add structured data to commercial and editorial templates." if structured_data_missing_pages else "",
            "Add descriptive ALT text to unlabeled imagery." if images_missing_alt else "",
            "Connect Google PageSpeed Insights for lab performance data." if pagespeed_note else "",
            "Connect Chrome UX Report for field performance data." if crux_note else "",
        ]
        recommendations = [item for item in dict.fromkeys([item for item in recommendations if item])]

        llm_summary = None
        llm_recommendations = []
        if openai_key:
            llm_summary, llm_recommendations = self._llm_summary(openai_key, {
                "seo_score": score,
                "status": score_status,
                "pages_crawled": len(pages),
                "critical_errors": [item["issue"] for item in critical_errors],
                "warnings": [item["issue"] for item in warnings[:6]],
                "pagespeed": pagespeed_data or {},
                "crux": crux_data or {},
            })
            if llm_summary:
                api_used.append("OpenAI")
            recommendations.extend([item for item in llm_recommendations if item not in recommendations])

        crawl_duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return self.build_structured_response(
            input_data,
            llm_summary or f"Technical SEO audit completed for {website_url} across {len(pages)} crawled page(s). The site scored {score}/100 ({score_status}) based on measured crawl and API signals.",
            recommendations,
            {
                "website_url": website_url,
                "seo_score": score,
                "seo_status": score_status,
                "overview": {"pages_crawled": len(pages), "critical_issue_groups": len(critical_errors), "warning_groups": len(warnings), "priority_fix_count": min(6, len(critical_errors) + len(warnings)), "crawl_duration_ms": crawl_duration_ms},
                "data_sources": connected_sources,
                "api_used": api_used,
                "premium_apis": self._premium_api_status(),
                "critical_errors": critical_errors,
                "warnings": warnings,
                "passed_checks": [
                    {"label": "HTTPS Enabled", "passed": not non_https_pages, "detail": "All crawled pages resolved over HTTPS." if not non_https_pages else f"{len(non_https_pages)} page(s) were not HTTPS."},
                    {"label": "Robots.txt Found", "passed": robots_ok, "detail": "robots.txt responded successfully." if robots_ok else "robots.txt was unavailable."},
                    {"label": "XML Sitemap Found", "passed": sitemap_ok, "detail": "A sitemap.xml endpoint was discovered." if sitemap_ok else "No sitemap.xml endpoint responded with HTTP 200."},
                    {"label": "Canonical Tags Present", "passed": not missing_canonical_pages, "detail": "Canonical tags were present on crawled pages." if not missing_canonical_pages else f"{len(missing_canonical_pages)} page(s) were missing canonicals."},
                    {"label": "Structured Data Detected", "passed": len(structured_data_missing_pages) < len(pages), "detail": "Structured data was detected on at least one crawled page." if len(structured_data_missing_pages) < len(pages) else "No structured data was detected."},
                    {"label": "Core Web Vitals Passed", "passed": cwv_passed, "detail": "Available Core Web Vitals are within recommended thresholds." if cwv_passed else (pagespeed_note or "Core Web Vitals require improvement or are unavailable.")},
                ],
                "technical_metrics": technical_metrics,
                "score_breakdown": score_components,
                "metric_provenance": {
                    "pagespeed": {
                        "status": "available" if pagespeed_data else "unavailable",
                        "source": "pagespeed",
                        "reason": pagespeed_note,
                    },
                    "crux": {
                        "status": "available" if crux_data else "unavailable",
                        "source": "crux",
                        "reason": crux_note,
                    },
                },
                "priority_fixes": [{"issue": item["issue"], "severity": item["severity"], "affected_pages": item["affected_pages"], "estimated_seo_impact": item["estimated_seo_impact"], "recommended_fix": item["recommended_fix"], "estimated_time_to_fix": item["estimated_time_to_fix"]} for item in (critical_errors + warnings)[:6]],
                "crawled_urls": crawl["crawled_urls"],
                "broken_links": broken_links,
                "images_missing_alt": images_missing_alt,
                "recommended_fixes": recommendations,
                "pagespeed": pagespeed_data or {},
                "crux": crux_data or {},
                "data_source": "real_crawl_and_api" if (pagespeed_data or crux_data or openai_key) else "real_crawl",
                "unavailable_metrics": [{"metric": "PageSpeed / Core Web Vitals", "status": "unavailable", "reason": pagespeed_note}] * (0 if pagespeed_data else 1) + [{"metric": "Chrome UX field data", "status": "unavailable", "reason": crux_note}] * (0 if crux_data else 1),
                "missing_api_keys": [key for key in ["PAGESPEED_API_KEY", "CRUX_API_KEY", "OPENAI_API_KEY"] if not get_env_value(key)],
                "technical_summary": {"score_status": score_status, "top_issue": (critical_errors + warnings)[0]["issue"] if (critical_errors + warnings) else "No major issues detected", "executive_note": "This report focuses on technical SEO health only and does not include competitor comparisons.", "pages_crawled": len(pages), "crawl_duration_ms": crawl_duration_ms},
                "pagespeed_note": pagespeed_note,
                "crux_note": crux_note,
                "beautifulsoup_note": bs_note,
            },
        )
