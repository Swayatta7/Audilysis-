from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.weekly_report import SEO_REPORT_AGENT_OPTIONS


class StrategyAgent(BaseAgent):
    NAME = "Strategy Agent"
    DESCRIPTION = "Build a practical SEO strategy aligned with the business goal."
    ICON = "fa-bullseye"
    CATEGORY = "Strategy"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "business_goal", "label": "Business Goal", "type": "text", "placeholder": "Increase organic traffic by 30%", "required": True},
        {"id": "target_country", "label": "Target Country", "type": "text", "required": False},
        {"id": "target_audience", "label": "Target Audience", "type": "text", "required": False},
        {"id": "target_keywords", "label": "Target Keywords", "type": "keyword_list", "required": False},
        {"id": "competitor_urls", "label": "Competitor URLs", "type": "url_list", "required": False},
        {"id": "timeframe", "label": "Timeframe", "type": "select", "required": False, "default": "6_months", "options": [{"value": "3_months", "label": "3 Months"}, {"value": "6_months", "label": "6 Months"}, {"value": "12_months", "label": "12 Months"}]},
        {"id": "budget_level", "label": "Budget Level", "type": "select", "required": False, "options": [{"value": "low", "label": "Low"}, {"value": "medium", "label": "Medium"}, {"value": "high", "label": "High"}]},
        {"id": "team_capacity", "label": "Team Capacity", "type": "select", "required": False, "options": [{"value": "solo", "label": "Solo"}, {"value": "small_team", "label": "Small Team"}, {"value": "agency", "label": "Agency"}]},
        {"id": "project_name", "label": "Project Name", "type": "text", "required": False},
        {"id": "selected_agent_results", "label": "Selected Agent Results", "type": "multi_select", "required": False, "options": SEO_REPORT_AGENT_OPTIONS},
    ]

    def run(self, input_data: dict) -> dict:
        return run_openai_json_agent(
            self,
            input_data,
            "an SEO strategy",
            ["business_goal", "website_url"],
            {"business_goal": input_data.get("business_goal"), "phases": [], "focus": [], "data_source": "real_user_input_and_openai"},
        )
