from agents.base_agent import BaseAgent
from agents.crawl_utils import audit_single_page


class SchemaAgent(BaseAgent):
    NAME = "Schema Agent"
    DESCRIPTION = "Suggest schema markup opportunities for rich results."
    ICON = "fa-code"
    CATEGORY = "Technical SEO"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "target_page_url", "label": "Target Page URL", "type": "url", "required": False},
        {"id": "crawl_depth", "label": "Crawl Depth", "type": "number", "required": False, "min": 0, "max": 2, "default": 0},
        {"id": "maximum_pages", "label": "Maximum Pages", "type": "number", "required": False, "min": 1, "max": 20, "default": 1},
        {"id": "page_type_override", "label": "Page Type Override", "type": "select", "required": False, "options": [{"value": "Article", "label": "Article"}, {"value": "Product", "label": "Product"}, {"value": "FAQPage", "label": "FAQ Page"}, {"value": "LocalBusiness", "label": "Local Business"}, {"value": "Organization", "label": "Organization"}]},
        {"id": "include_sample_jsonld", "label": "Include Sample JSON-LD", "type": "checkbox", "required": False, "default": True},
        {"id": "force_refresh", "label": "Force Refresh", "type": "checkbox", "required": False, "default": False},
    ]

    def run(self, input_data: dict) -> dict:
        website_url = (input_data.get("website_url") or input_data.get("target_page_url") or "").strip()
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        page = audit_single_page(website_url)
        return self.build_structured_response(
            input_data,
            f"Schema review completed from live structured-data parsing on {page['url']}.",
            ["Add FAQPage schema if FAQ content exists.", "Add Organization or Article markup where absent."],
            {
                "detected_schema": page["structured_data"],
                "recommended_schema_types": ["FAQPage", "Organization", "WebSite"] if not page["structured_data"] else [],
                "priority": "high" if not page["structured_data"] else "medium",
                "data_source": "real_crawl",
                "api_used": [],
                "missing_api_keys": [],
            },
        )
