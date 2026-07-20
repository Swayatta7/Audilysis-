from agents.base_agent import BaseAgent
from agents.weekly_report import SEO_REPORT_AGENT_OPTIONS
from db.storage import get_latest_run, get_mention_results, get_competitor_metrics


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
        latest = get_latest_run()
        if not latest:
            return {
                "success": False,
                "agent": self.NAME,
                "error": "Missing data",
                "message": "No tracker runs exist in the database. Run the tracker first to generate a monthly report.",
            }
        results = get_mention_results(latest["id"])
        metrics = get_competitor_metrics(latest["id"])
        return self.build_structured_response(
            input_data,
            f"Monthly report compiled from the latest real tracker run #{latest['id']}.",
            ["Review trend changes across repeated runs.", "Use the database output to set next-month targets."],
            {
                "run_id": latest["id"],
                "results_count": len(results),
                "competitor_metrics_count": len(metrics),
                "monthly_goals": ["traffic", "rankings", "links"],
                "status": "database_snapshot",
                "data_source": "real_database_data",
                "api_used": [],
                "missing_api_keys": [],
            },
        )
