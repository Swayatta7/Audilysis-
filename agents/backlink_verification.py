from agents.base_agent import BaseAgent
from agents.crawl_utils import audit_single_page


class BacklinkVerificationAgent(BaseAgent):
    NAME = "Backlink Verification Agent"
    DESCRIPTION = "Validate whether a backlink or link opportunity looks trustworthy."
    ICON = "fa-check-double"
    CATEGORY = "Link Building"
    INPUT_SCHEMA = [
        {"id": "backlink_url", "label": "Backlink URL", "type": "url", "placeholder": "https://referring-site.com/article", "required": True},
        {"id": "target_url", "label": "Target URL", "type": "url", "placeholder": "https://example.com/landing-page", "required": False},
        {"id": "expected_anchor_text", "label": "Expected Anchor Text", "type": "text", "required": False},
        {"id": "project_name", "label": "Project Name", "type": "text", "required": False},
        {"id": "verification_frequency", "label": "Verification Frequency", "type": "select", "required": False, "default": "once", "options": [{"value": "once", "label": "Once"}, {"value": "daily", "label": "Daily"}, {"value": "weekly", "label": "Weekly"}, {"value": "monthly", "label": "Monthly"}]},
        {"id": "force_javascript_rendering", "label": "Force JavaScript Rendering", "type": "checkbox", "required": False, "default": False},
    ]

    def run(self, input_data: dict) -> dict:
        backlink_url = (input_data.get("backlink_url") or input_data.get("website_url") or "").strip()
        if not backlink_url:
            return self.missing_input_response("backlink_url", input_data)
        page = audit_single_page(backlink_url)
        return self.build_structured_response(
            input_data,
            f"Backlink verification checked the live page at {page['url']}.",
            ["Confirm the page still links to your site if this is an existing backlink.", "Review topical relevance manually if you need a qualitative judgement."],
            {
                "status": "verified" if page["http_status"] == 200 else "unreachable",
                "http_status": page["http_status"],
                "https": page["https"],
                "title": page["title"],
                "meta_description": page["meta_description"],
                "external_links_count": len(page["external_links"]),
                "data_source": "real_crawl",
                "api_used": [],
                "missing_api_keys": [],
            },
        )
