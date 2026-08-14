from agents.base_agent import BaseAgent
from agents.weekly_report import SEO_REPORT_AGENT_OPTIONS
from services.run_context import load_run_analysis_context


class MonthlyReportAgent(BaseAgent):
    NAME = "Monthly Report Agent"
    DESCRIPTION = "Create a structured monthly SEO progress report."
    ICON = "fa-calendar-days"
    CATEGORY = "Reporting"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "required": False, "placeholder": "https://example.com"},
        {"id": "project_name", "label": "Project Name", "type": "text", "required": False},
        {"id": "month", "label": "Month", "type": "month", "required": False},
        {"id": "year", "label": "Year", "type": "number", "required": False, "min": 2020, "max": 2100},
        {"id": "comparison_month", "label": "Comparison Month", "type": "month", "required": False},
        {"id": "business_goals", "label": "Business Goals", "type": "textarea", "required": False},
        {"id": "selected_agents", "label": "Selected Agents", "type": "multi_select", "required": False, "options": SEO_REPORT_AGENT_OPTIONS},
        {"id": "notes", "label": "Notes", "type": "textarea", "required": False},
        {"id": "search_console_property", "label": "Search Console Property", "type": "text", "required": False},
    ]

    def run(self, input_data: dict) -> dict:
        user_id = input_data.get("_user_id")
        run_context = (
            load_run_analysis_context(input_data.get("run_id"), user_id=user_id)
            if user_id
            else load_run_analysis_context(input_data.get("run_id"))
        )
        if not run_context:
            return {
                "success": False,
                "agent": self.NAME,
                "error": "Missing data",
                "message": "A valid run_id is required to generate a monthly report from verified tracker data.",
            }
        return self.build_structured_response(
            input_data,
            f"Monthly report compiled from verified tracker run #{run_context['run_id']}.",
            ["Review trend changes across repeated runs.", "Use the database output to set next-month targets."],
            {
                "run_id": run_context["run_id"],
                "results_count": len(run_context["results"]),
                "competitor_metrics_count": len(run_context["metrics"]),
                "monthly_goals": ["traffic", "rankings", "links"],
                "status": "database_snapshot",
                "data_source": "real_database_data",
                "api_used": [],
                "missing_api_keys": [],
                "verified_run_summary": {
                    "brand_name": run_context["run"]["brand_name"],
                    "brand_domain": run_context["run"]["brand_domain"],
                    "report_mode": run_context["report_mode"],
                    "brand_mentions": run_context["brand_mentions_metric"],
                    "share_of_voice": run_context["share_of_voice_metric"],
                    "api_health": run_context["api_health_metric"],
                },
            },
        )
