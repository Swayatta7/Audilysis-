from agents.base_agent import BaseAgent
from agents.crawl_utils import audit_single_page


class OnPageOptimizerAgent(BaseAgent):
    NAME = "On-page Optimizer Agent"
    DESCRIPTION = "Recommend on-page SEO improvements for a target page or keyword."
    ICON = "fa-sitemap"
    CATEGORY = "On-page SEO"
    INPUT_SCHEMA = [
        {"id": "target_page_url", "label": "Target Page URL", "type": "url", "placeholder": "https://example.com/page", "required": True},
        {"id": "target_keyword", "label": "Target Keyword", "type": "text", "required": False},
        {"id": "secondary_keywords", "label": "Secondary Keywords", "type": "keyword_list", "required": False},
        {"id": "country", "label": "Country", "type": "text", "required": False},
        {"id": "language", "label": "Language", "type": "text", "required": False},
        {"id": "device", "label": "Device", "type": "select", "required": False, "options": [{"value": "desktop", "label": "Desktop"}, {"value": "mobile", "label": "Mobile"}]},
        {"id": "target_audience", "label": "Target Audience", "type": "text", "required": False},
        {"id": "business_goal", "label": "Business Goal", "type": "text", "required": False},
        {"id": "competitor_page_urls", "label": "Competitor Page URLs", "type": "url_list", "required": False},
        {"id": "desired_content_type", "label": "Desired Content Type", "type": "select", "required": False, "options": [{"value": "blog", "label": "Blog Post"}, {"value": "landing_page", "label": "Landing Page"}, {"value": "product_page", "label": "Product Page"}, {"value": "category_page", "label": "Category Page"}]},
    ]

    def run(self, input_data: dict) -> dict:
        website_url = (input_data.get("target_page_url") or input_data.get("website_url") or "").strip()
        if not website_url:
            return self.missing_input_response("target_page_url", input_data)
        page = audit_single_page(website_url)
        priorities = []
        if not page["title"]:
            priorities.append("title_tag")
        if not page["meta_description"]:
            priorities.append("meta_description")
        if not page["h1"]:
            priorities.append("h1")
        if page["images_missing_alt"]:
            priorities.append("image_alt")
        if not page["canonical"]:
            priorities.append("canonical")
        return self.build_structured_response(
            input_data,
            f"On-page review completed from a live page parse of {page['url']}.",
            ["Refine missing on-page elements found in the crawl.", "Improve internal anchors based on live page structure."],
            {
                "priorities": priorities,
                "page_snapshot": {
                    "title": page["title"],
                    "meta_description": page["meta_description"],
                    "h1": page["h1"],
                    "canonical": page["canonical"],
                    "internal_links_count": len(page["internal_links"]),
                    "images_missing_alt": page["images_missing_alt"],
                },
                "data_source": "real_crawl",
                "api_used": [],
                "missing_api_keys": [],
            },
        )
