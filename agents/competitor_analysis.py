import time

import requests
from bs4 import BeautifulSoup

from agents.base_agent import BaseAgent
from agents.crawl_utils import audit_single_page, ensure_url
from agents.llm_client import openai_chat_completion
from agents.runtime_config import get_env_value


PAGESPEED_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
CRUX_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"


class CompetitorAnalysisAgent(BaseAgent):
    NAME = "Competitor Analysis Agent"
    DESCRIPTION = "Compare a website against its main competitors."
    ICON = "fa-users-viewfinder"
    CATEGORY = "Competitive Research"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "competitor_urls", "label": "Competitor URLs", "type": "url_list", "placeholder": "https://competitor1.com\nhttps://competitor2.com", "required": True, "help_text": "One competitor URL per line."},
        {"id": "industry", "label": "Industry", "type": "text", "placeholder": "SaaS, ecommerce, healthcare", "required": False},
        {"id": "country", "label": "Country", "type": "text", "placeholder": "United States", "required": False},
        {"id": "language", "label": "Language", "type": "text", "placeholder": "en", "required": False},
        {"id": "crawl_depth", "label": "Crawl Depth", "type": "number", "required": False, "min": 0, "max": 2, "default": 1},
        {"id": "maximum_pages_per_site", "label": "Maximum Pages per Site", "type": "number", "required": False, "min": 1, "max": 20, "default": 5},
        {"id": "device", "label": "Device", "type": "select", "required": False, "default": "mobile", "options": [{"value": "mobile", "label": "Mobile"}, {"value": "desktop", "label": "Desktop"}]},
    ]
    PREMIUM_APIS = {"Semrush": ["SEMRUSH_API_KEY"], "Ahrefs": ["AHREFS_API_KEY"], "Moz": ["MOZ_API_KEY"], "DataForSEO": ["DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD"]}

    def _source(self, name: str, connected: bool, detail: str) -> dict:
        return {"name": name, "status": "Connected" if connected else "Not Connected", "detail": detail}

    def _processing_stack(self, pagespeed_used: bool, crux_used: bool, openai_used: bool) -> list[str]:
        stack = ["Real Crawl Engine", "Requests", "BeautifulSoup", "Internal SEO Scoring"]
        if pagespeed_used:
            stack.append("Google PageSpeed Insights")
        if crux_used:
            stack.append("Chrome UX Report")
        if openai_used:
            stack.append("OpenAI")
        return stack

    def _premium_api_status(self) -> tuple[list[dict], list[str]]:
        services, missing = [], []
        for service, keys in self.PREMIUM_APIS.items():
            connected = all(get_env_value(key) for key in keys)
            if not connected:
                missing.extend(keys)
            services.append({"service": service, "status": "Connected" if connected else "Not Connected"})
        return services, missing

    def _crawl_site_or_warning(self, site: str) -> tuple[dict | None, dict | None]:
        try:
            return audit_single_page(site, timeout=45, retries=1), None
        except requests.exceptions.Timeout:
            return None, {"website": site, "status": "timeout", "message": "Website could not be crawled within 45 seconds."}
        except requests.exceptions.ConnectionError:
            return None, {"website": site, "status": "connection_error", "message": "Website could not be reached because the connection failed."}
        except requests.exceptions.SSLError:
            return None, {"website": site, "status": "ssl_error", "message": "Website could not be crawled because the SSL handshake failed."}
        except requests.exceptions.RequestException as exc:
            return None, {"website": site, "status": "request_error", "message": str(exc)}

    def _beautifulsoup_snapshot(self, url: str) -> dict:
        try:
            response = requests.get(ensure_url(url), timeout=20, headers={"User-Agent": "AudylysisBot/1.0 (+https://localhost)"})
            soup = BeautifulSoup(response.text or "", "html.parser")
            return {
                "title_length": len((soup.title.string or "").strip()) if soup.title and soup.title.string else 0,
                "meta_description_length": len((soup.find("meta", attrs={"name": "description"}) or {}).get("content", "")) if soup.find("meta", attrs={"name": "description"}) else 0,
                "h2_count": len(soup.find_all("h2")),
                "images": len(soup.find_all("img")),
            }
        except requests.RequestException:
            return {}

    def _pagespeed(self, url: str) -> tuple[dict | None, str | None]:
        key = get_env_value("PAGESPEED_API_KEY")
        if not key:
            return None, "Not Connected"
        try:
            response = requests.get(PAGESPEED_ENDPOINT, params={"url": url, "key": key, "strategy": "mobile", "category": ["PERFORMANCE", "SEO"]}, timeout=60)
            if response.status_code != 200:
                return None, f"HTTP {response.status_code}"
            payload = response.json()
            lighthouse = payload.get("lighthouseResult", {})
            categories = lighthouse.get("categories", {})
            audits = lighthouse.get("audits", {})
            return {
                "performance_score": (categories.get("performance", {}).get("score") or 0) * 100,
                "seo_score": (categories.get("seo", {}).get("score") or 0) * 100,
                "lcp_ms": (audits.get("largest-contentful-paint", {}) or {}).get("numericValue"),
                "fcp_ms": (audits.get("first-contentful-paint", {}) or {}).get("numericValue"),
                "tbt_ms": (audits.get("total-blocking-time", {}) or {}).get("numericValue"),
            }, None
        except requests.RequestException as exc:
            return None, str(exc)

    def _crux(self, url: str) -> tuple[dict | None, str | None]:
        key = get_env_value("CRUX_API_KEY")
        if not key:
            return None, "Not Connected"
        try:
            response = requests.post(f"{CRUX_ENDPOINT}?key={key}", json={"url": url, "formFactor": "PHONE"}, timeout=60)
            if response.status_code != 200:
                return None, f"HTTP {response.status_code}"
            metrics = response.json().get("record", {}).get("metrics", {})
            return {
                "lcp_ms": (metrics.get("largest_contentful_paint") or {}).get("percentiles", {}).get("p75"),
                "cls": (metrics.get("cumulative_layout_shift") or {}).get("percentiles", {}).get("p75"),
                "inp_ms": (metrics.get("interaction_to_next_paint") or {}).get("percentiles", {}).get("p75"),
            }, None
        except requests.RequestException as exc:
            return None, str(exc)

    def _llm_summary(self, payload: dict) -> tuple[str | None, list[str]]:
        key = get_env_value("OPENAI_API_KEY")
        if not key:
            return None, []
        prompt = "Using only the supplied comparison metrics, return JSON with executive_summary and recommendations. Do not invent metrics.\n" + str(payload)
        _, content = openai_chat_completion(key, prompt, model="gpt-4o-mini")
        if not content:
            return None, []
        try:
            import json
            parsed = json.loads(content)
            return parsed.get("executive_summary"), parsed.get("recommendations", [])
        except Exception:
            return None, []

    def run(self, input_data: dict) -> dict:
        started = time.perf_counter()
        website_url = ensure_url(input_data.get("website_url") or "")
        competitors = self.parse_competitors(input_data.get("competitor_urls") or input_data.get("competitors") or [])
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        if not competitors:
            return self.missing_input_response("competitor_urls", input_data, "Provide at least one valid competitor URL.")

        services, missing_premium = self._premium_api_status()
        rows, warnings = [], []
        for index, site in enumerate([website_url] + competitors):
            page, warning = self._crawl_site_or_warning(site)
            if warning:
                warnings.append(warning)
                if index == 0:
                    return {"success": False, "agent": self.NAME, "error": "Primary site crawl failed", "message": warning["message"], "data": {"competitor_comparison_table": [], "warnings": warnings, "data_source": "real_crawl", "api_used": self._processing_stack(False, False, False), "data_sources": [self._source("Real Crawl Engine", True, "Live crawl of the compared sites."), self._source("Requests", True, "HTTP fetching for crawl and API calls."), self._source("BeautifulSoup", True, "HTML verification and supplemental parsing."), self._source("Internal SEO Scoring", True, "Deterministic comparison logic."), self._source("Google PageSpeed Insights", bool(get_env_value("PAGESPEED_API_KEY")), "Connected" if get_env_value("PAGESPEED_API_KEY") else "Not Connected"), self._source("Chrome UX Report", bool(get_env_value("CRUX_API_KEY")), "Connected" if get_env_value("CRUX_API_KEY") else "Not Connected"), self._source("OpenAI", bool(get_env_value("OPENAI_API_KEY")), "Connected" if get_env_value("OPENAI_API_KEY") else "Not Connected")], "premium_apis": services, "missing_api_keys": missing_premium + [key for key in ["PAGESPEED_API_KEY", "CRUX_API_KEY", "OPENAI_API_KEY"] if not get_env_value(key)]}}
                continue

            bs = self._beautifulsoup_snapshot(site)
            ps, _ = self._pagespeed(site)
            crux, _ = self._crux(site)
            rows.append({
                "website": page["url"],
                "status_code": page["http_status"],
                "https": "Yes" if page["https"] else "No",
                "title": page["title"] or "Missing",
                "title_length": bs.get("title_length", len(page["title"] or "")),
                "meta": "Present" if page["meta_description"] else "Missing",
                "meta_description_length": bs.get("meta_description_length", len(page["meta_description"] or "")),
                "h1": page["h1"][0] if page["h1"] else "Missing",
                "h2_count": bs.get("h2_count", len(page["h2"])),
                "canonical": "Present" if page["canonical"] else "Missing",
                "robots": page["meta_robots"] or "Not Declared",
                "schema": "Present" if page["structured_data"] else "Missing",
                "structured_data": len(page["structured_data"]),
                "word_count": page["word_count"],
                "internal_links": len(page["internal_links"]),
                "external_links": len(page["external_links"]),
                "images": bs.get("images", len(page.get("images", []))),
                "images_missing_alt": len(page["images_missing_alt"]),
                "response_time_ms": page["response_time_ms"],
                "page_size_kb": round(page["page_size_bytes"] / 1024, 2),
                "pagespeed_score": ps.get("performance_score") if ps else "Unavailable in Free Mode",
                "seo_score": ps.get("seo_score") if ps else "Unavailable in Free Mode",
                "lcp_ms": crux.get("lcp_ms") if crux else (ps.get("lcp_ms") if ps else "Unavailable in Free Mode"),
                "cls": crux.get("cls") if crux else "Unavailable in Free Mode",
                "inp_ms": crux.get("inp_ms") if crux else "Unavailable in Free Mode",
            })

        if not rows:
            return {"success": False, "agent": self.NAME, "error": "Competitor crawl failed", "message": "No websites returned crawlable data.", "data": {"competitor_comparison_table": [], "warnings": warnings, "data_source": "real_crawl", "api_used": self._processing_stack(False, False, False), "premium_apis": services, "missing_api_keys": missing_premium}}

        primary = rows[0]
        fastest = min(rows, key=lambda item: item["response_time_ms"])
        deepest = max(rows, key=lambda item: item["word_count"])
        strongest = max(rows, key=lambda item: item["internal_links"])
        best_schema = max(rows, key=lambda item: item["structured_data"])
        strengths, weaknesses, opportunities, gap_analysis, opportunity_report, quick_wins = [], [], [], [], [], []
        if primary["response_time_ms"] <= fastest["response_time_ms"]:
            strengths.append(f"{primary['website']} is the fastest page in this benchmark.")
        if primary["internal_links"] >= strongest["internal_links"]:
            strengths.append(f"{primary['website']} has the strongest crawl-visible internal linking.")
        if primary["structured_data"] > 0:
            strengths.append(f"{primary['website']} exposes structured data on the compared page.")
        if primary["word_count"] < deepest["word_count"]:
            weaknesses.append(f"{primary['website']} has thinner visible content than {deepest['website']} ({primary['word_count']} vs {deepest['word_count']} words).")
            gap_analysis.append(f"Content depth trails the benchmark leader by {deepest['word_count'] - primary['word_count']} words.")
        if primary["internal_links"] < strongest["internal_links"]:
            weaknesses.append(f"{primary['website']} has fewer crawl-visible internal links than {strongest['website']}.")
            gap_analysis.append(f"Internal link support is lower than the benchmark leader ({primary['internal_links']} vs {strongest['internal_links']}).")
        if primary["structured_data"] < best_schema["structured_data"]:
            weaknesses.append(f"{primary['website']} exposes less structured data than {best_schema['website']}.")
            gap_analysis.append("Structured data coverage is behind at least one competitor page in the crawl sample.")
        if primary["images_missing_alt"] > 0:
            weaknesses.append(f"{primary['website']} still has {primary['images_missing_alt']} image(s) missing ALT text.")
        if primary["response_time_ms"] > fastest["response_time_ms"]:
            gap_analysis.append(f"Response time is slower than the fastest benchmark page by {round(primary['response_time_ms'] - fastest['response_time_ms'], 2)} ms.")
        if deepest["website"] != primary["website"]:
            opportunities.append(f"Build richer guides and support content to close the content-depth gap with {deepest['website']}.")
            opportunity_report.append(f"{deepest['website']} leads this crawl for visible copy depth.")
        if strongest["website"] != primary["website"]:
            opportunities.append(f"Increase contextual internal links into priority pages to match the discoverability of {strongest['website']}.")
        if best_schema["website"] != primary["website"]:
            opportunities.append(f"Implement more complete schema markup to match the structured data coverage exposed by {best_schema['website']}.")
        if primary["meta"] == "Missing":
            quick_wins.append("Write a meta description for the primary page.")
        if primary["canonical"] == "Missing":
            quick_wins.append("Add a canonical tag to the primary page.")
        if primary["images_missing_alt"] > 0:
            quick_wins.append("Add descriptive ALT text to images missing accessibility metadata.")

        llm_summary, llm_recommendations = self._llm_summary({"rows": rows, "strengths": strengths, "weaknesses": weaknesses, "opportunities": opportunities})
        pagespeed_used = any(isinstance(row["pagespeed_score"], (int, float)) for row in rows)
        crux_used = any(isinstance(row["lcp_ms"], (int, float)) or isinstance(row["inp_ms"], (int, float)) for row in rows)
        openai_used = bool(llm_summary)
        api_used = self._processing_stack(pagespeed_used, crux_used, openai_used)
        data_sources = [
            self._source("Real Crawl Engine", True, "Live crawl of the compared sites."),
            self._source("Requests", True, "HTTP fetching for crawl and API calls."),
            self._source("BeautifulSoup", True, "HTML verification and supplemental parsing."),
            self._source("Internal SEO Scoring", True, "Deterministic comparison logic."),
            self._source("Google PageSpeed Insights", bool(get_env_value("PAGESPEED_API_KEY")), "Connected" if get_env_value("PAGESPEED_API_KEY") else "Not Connected"),
            self._source("Chrome UX Report", bool(get_env_value("CRUX_API_KEY")), "Connected" if get_env_value("CRUX_API_KEY") else "Not Connected"),
            self._source("OpenAI", bool(get_env_value("OPENAI_API_KEY")), "Connected" if get_env_value("OPENAI_API_KEY") else "Not Connected"),
        ]
        crawl_duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return self.build_structured_response(
            input_data,
            llm_summary or f"Competitor analysis completed using live crawl data for {len(rows)} site(s). {primary['website']} was benchmarked against measurable on-page signals rather than a full technical audit.",
            list(dict.fromkeys(quick_wins + opportunities + llm_recommendations)),
            {
                "competitor_comparison_table": rows,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "gap_analysis": gap_analysis,
                "opportunities": opportunities,
                "opportunity_report": opportunity_report,
                "quick_wins": list(dict.fromkeys(quick_wins)),
                "executive_summary": {"market_position": "Leading on crawl-visible signals" if len(weaknesses) <= 1 else "Competitive with measurable gaps", "fastest_site": fastest["website"], "deepest_content_site": deepest["website"], "strongest_internal_linking_site": strongest["website"]},
                "overview": {"pages_crawled": len(rows), "compared_sites": len(rows), "crawl_duration_ms": crawl_duration_ms},
                "data_sources": data_sources,
                "api_used": api_used,
                "premium_apis": services,
                "warnings": warnings,
                "unavailable_metrics": [{"metric": "Organic Traffic", "status": "Unavailable in Free Mode"}, {"metric": "Domain Authority", "status": "Unavailable in Free Mode"}, {"metric": "Backlinks", "status": "Unavailable in Free Mode"}, {"metric": "Referring Domains", "status": "Unavailable in Free Mode"}, {"metric": "Ranking Keywords", "status": "Unavailable in Free Mode"}, {"metric": "Search Volume", "status": "Unavailable in Free Mode"}, {"metric": "Keyword Difficulty", "status": "Unavailable in Free Mode"}],
                "data_source": "real_crawl_and_api" if (pagespeed_used or crux_used or openai_used) else "real_crawl",
                "missing_api_keys": missing_premium + [key for key in ["PAGESPEED_API_KEY", "CRUX_API_KEY", "OPENAI_API_KEY"] if not get_env_value(key)],
            },
        )
