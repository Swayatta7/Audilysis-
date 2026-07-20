from agents.base_agent import BaseAgent
from agents.crawl_utils import crawl_site


class InternalLinkingAgent(BaseAgent):
    NAME = "Internal Linking Agent"
    DESCRIPTION = "Recommend internal linking improvements for stronger topical authority."
    ICON = "fa-link"
    CATEGORY = "On-page SEO"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "crawl_depth", "label": "Crawl Depth", "type": "number", "required": False, "min": 0, "max": 2, "default": 1},
        {"id": "maximum_pages", "label": "Maximum Pages", "type": "number", "required": False, "min": 1, "max": 30, "default": 8},
        {"id": "language", "label": "Language", "type": "text", "required": False},
        {"id": "focus_topic", "label": "Focus Topic", "type": "text", "required": False},
        {"id": "include_sitemap", "label": "Include Sitemap", "type": "checkbox", "required": False, "default": True},
        {"id": "include_navigation_links", "label": "Include Navigation Links", "type": "checkbox", "required": False, "default": True},
        {"id": "include_footer_links", "label": "Include Footer Links", "type": "checkbox", "required": False, "default": False},
        {"id": "force_refresh", "label": "Force Refresh", "type": "checkbox", "required": False, "default": False},
    ]

    def run(self, input_data: dict) -> dict:
        website_url = (input_data.get("website_url") or "").strip()
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        raw_crawl_depth = input_data.get("crawl_depth")
        crawl_depth = 1 if raw_crawl_depth in (None, "") else max(0, min(int(raw_crawl_depth), 2))
        raw_maximum_pages = input_data.get("maximum_pages")
        maximum_pages = 8 if raw_maximum_pages in (None, "") else max(1, min(int(raw_maximum_pages), 30))
        crawl = crawl_site(website_url, depth=crawl_depth, limit=maximum_pages)
        pages = [page for page in crawl["pages"] if page.get("url")]
        targets = sorted(pages, key=lambda page: len(page.get("internal_links", [])))[:5]
        return self.build_structured_response(
            input_data,
            f"Internal linking review completed from a live crawl of {len(pages)} page(s).",
            ["Link deeper pages from pages with strong internal link footprints.", "Use relevant anchor text from existing crawl targets."],
            {
                "link_targets": [{"url": page["url"], "internal_links_count": len(page.get("internal_links", []))} for page in targets],
                "priority": "medium",
                "data_source": "real_crawl",
                "api_used": [],
                "missing_api_keys": [],
            },
        )
