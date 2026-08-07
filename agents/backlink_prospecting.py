from agents.base_agent import BaseAgent
from agents.crawl_utils import audit_single_page
import requests


class BacklinkProspectingAgent(BaseAgent):
    NAME = "Backlink Prospecting Agent"
    DESCRIPTION = "Find and score link-building opportunities."
    ICON = "fa-network-wired"
    CATEGORY = "Link Building"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "target_keyword", "label": "Target Keyword", "type": "text", "required": False},
        {"id": "industry", "label": "Industry", "type": "text", "required": False},
        {"id": "country", "label": "Country", "type": "text", "required": False},
        {"id": "language", "label": "Language", "type": "text", "required": False},
        {"id": "competitor_urls", "label": "Competitor URLs", "type": "url_list", "required": True, "help_text": "One competitor URL per line."},
        {"id": "target_audience", "label": "Target Audience", "type": "text", "required": False},
        {"id": "business_goal", "label": "Business Goal", "type": "text", "required": False},
        {"id": "prospect_types", "label": "Prospect Types", "type": "multi_select", "required": False, "options": [{"value": "guest_post", "label": "Guest Post"}, {"value": "directory", "label": "Directory"}, {"value": "resource_page", "label": "Resource Page"}, {"value": "broken_link", "label": "Broken Link"}, {"value": "competitor_backlink", "label": "Competitor Backlink"}]},
        {"id": "maximum_prospects", "label": "Maximum Prospects", "type": "number", "required": False, "min": 1, "max": 20, "default": 5},
    ]

    def run(self, input_data: dict) -> dict:
        website_url = (input_data.get("website_url") or "").strip()
        competitors = self.parse_competitors(input_data.get("competitor_urls") or input_data.get("competitors") or [])
        if not website_url:
            return self.missing_input_response("website_url", input_data)
        if not competitors:
            return self.missing_input_response("competitor_urls", input_data)
        raw_maximum_prospects = input_data.get("maximum_prospects")
        maximum_prospects = 5 if raw_maximum_prospects in (None, "") else max(1, min(int(raw_maximum_prospects), 20))

        prospects = []
        warnings = []
        for competitor in competitors[:maximum_prospects]:
            try:
                page = audit_single_page(competitor)
            except requests.RequestException as exc:
                warnings.append({"website": competitor, "status": "crawl_failed", "message": str(exc)})
                continue
            prospects.append({
                "site": page["url"],
                "https": page["https"],
                "external_links_count": len(page["external_links"]),
                "has_contact_or_about_signals": any(token in page["url"].lower() for token in ["about", "contact"]),
            })

        if not prospects:
            return self.build_structured_response(
                input_data,
                "No competitor pages could be crawled for backlink prospecting.",
                ["Verify the competitor URLs are reachable from the server and try again."],
                {
                    "prospects": [],
                    "warnings": warnings,
                    "data_source": "real_user_input_and_real_crawl",
                    "api_used": [],
                    "missing_api_keys": [],
                    "unavailable_metrics": ["backlink_authority", "referring_domains"],
                },
                success=False,
                message="Competitor crawl failed.",
            )
        return self.build_structured_response(
            input_data,
            "Backlink prospecting used real competitor URLs as source domains for outreach review.",
            ["Prioritize competitor domains with clear editorial pages and HTTPS.", "Add a backlink API if you want genuine referring-domain metrics."],
            {
                "prospects": prospects,
                "warnings": warnings,
                "data_source": "real_user_input_and_real_crawl",
                "api_used": [],
                "missing_api_keys": [],
                "unavailable_metrics": ["backlink_authority", "referring_domains"],
            },
        )
