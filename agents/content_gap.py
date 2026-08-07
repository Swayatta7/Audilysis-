from agents.base_agent import BaseAgent
from agents.crawl_utils import audit_single_page, ensure_url
from urllib.parse import urljoin
import requests


class ContentGapAgent(BaseAgent):
    NAME = "Content Gap Agent"
    DESCRIPTION = "Identify content opportunities missing from the site and competitors."
    ICON = "fa-file-lines"
    CATEGORY = "Content Strategy"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "competitor_urls", "label": "Competitor URLs", "type": "url_list", "placeholder": "https://competitor1.com\nhttps://competitor2.com", "required": True, "help_text": "Provide at least one valid competitor URL."},
        {"id": "industry", "label": "Industry", "type": "text", "required": False},
        {"id": "target_audience", "label": "Target Audience", "type": "text", "required": False},
        {"id": "focus_keyword", "label": "Focus Keyword", "type": "text", "required": False},
        {"id": "focus_topic", "label": "Focus Topic", "type": "text", "required": False},
        {"id": "country", "label": "Country", "type": "text", "required": False},
        {"id": "language", "label": "Language", "type": "text", "required": False},
        {"id": "business_goal", "label": "Business Goal", "type": "text", "required": False},
        {"id": "crawl_depth", "label": "Crawl Depth", "type": "number", "required": False, "min": 0, "max": 2, "default": 1},
        {"id": "maximum_pages_per_site", "label": "Maximum Pages per Site", "type": "number", "required": False, "min": 1, "max": 20, "default": 5},
    ]

    def run(self, input_data: dict) -> dict:
        website_url = (input_data.get("website_url") or "").strip()
        competitors = self.parse_competitors(input_data.get("competitor_urls") or input_data.get("competitors") or [])
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        if not competitors:
            return self.missing_input_response("competitor_urls", input_data, "Provide at least one valid competitor URL.")

        try:
            site_page = audit_single_page(website_url)
        except requests.RequestException as exc:
            return self.build_structured_response(
                input_data,
                "The primary site could not be crawled for content-gap analysis.",
                ["Verify the website URL is reachable from the server and try again."],
                {
                    "missing_topics": [],
                    "missing_pages": [],
                    "missing_faqs": [],
                    "competitor_topics": [],
                    "recommended_articles": [],
                    "recommended_landing_pages": [],
                    "warnings": [{"website": website_url, "status": "crawl_failed", "message": str(exc)}],
                    "unavailable_metrics": ["keyword_gap"],
                    "data_source": "real_crawl",
                    "api_used": [],
                    "missing_api_keys": [],
                },
                success=False,
                message=f"Unable to crawl {website_url}.",
            )

        competitor_pages = []
        warnings = []
        for url in competitors:
            try:
                competitor_pages.append(audit_single_page(url))
            except requests.RequestException as exc:
                warnings.append({"website": url, "status": "crawl_failed", "message": str(exc)})

        if not competitor_pages:
            return self.build_structured_response(
                input_data,
                "No competitor pages could be crawled for content-gap analysis.",
                ["Verify the competitor URLs are reachable from the server and try again."],
                {
                    "missing_topics": [],
                    "missing_pages": [],
                    "missing_faqs": [],
                    "competitor_topics": [],
                    "recommended_articles": [],
                    "recommended_landing_pages": [],
                    "warnings": warnings,
                    "unavailable_metrics": ["keyword_gap"],
                    "data_source": "real_crawl",
                    "api_used": [],
                    "missing_api_keys": [],
                },
                success=False,
                message="Competitor crawl failed.",
            )

        site_topics = set(filter(None, [site_page["title"], *site_page["h1"], *site_page["h2"]]))
        competitor_topics = []
        for page in competitor_pages:
            competitor_topics.extend(filter(None, [page["title"], *page["h1"], *page["h2"]]))
        missing_topics = [topic for topic in competitor_topics if topic not in site_topics]

        return self.build_structured_response(
            input_data,
            f"Content gap analysis compared live page content between {website_url} and {len(competitors)} competitor URL(s).",
            ["Create missing FAQ and topic coverage identified in competitor pages."],
            {
                "missing_topics": missing_topics[:20],
                "missing_pages": [urljoin(ensure_url(website_url), f"/{topic.lower().replace(' ', '-')}") for topic in missing_topics[:10]],
                "missing_faqs": [topic for topic in competitor_topics if "faq" in topic.lower() or "?" in topic][:10],
                "competitor_topics": competitor_topics[:30],
                "recommended_articles": missing_topics[:10],
                "recommended_landing_pages": [topic for topic in missing_topics if any(word in topic.lower() for word in ["service", "solution", "pricing"])][:10],
                "warnings": warnings,
                "unavailable_metrics": ["keyword_gap"] ,
                "data_source": "real_crawl",
                "api_used": [],
                "missing_api_keys": [],
            }
        )
