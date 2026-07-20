from datetime import date

from agents.base_agent import BaseAgent
from db.storage import get_latest_run, get_mention_results, get_competitor_metrics


SEO_REPORT_AGENT_OPTIONS = [
    {"value": "technical_audit", "label": "Technical Audit"},
    {"value": "competitor_analysis", "label": "Competitor Analysis"},
    {"value": "keyword_research", "label": "Keyword Research"},
    {"value": "keyword_clustering", "label": "Keyword Clustering"},
    {"value": "content_gap", "label": "Content Gap"},
    {"value": "serp_analysis", "label": "SERP Analysis"},
    {"value": "rank_tracking", "label": "Rank Tracking"},
    {"value": "on_page_optimizer", "label": "On-Page Optimizer"},
    {"value": "schema_agent", "label": "Schema Markup"},
    {"value": "internal_linking", "label": "Internal Linking"},
    {"value": "backlink_prospecting", "label": "Backlink Prospecting"},
    {"value": "outreach", "label": "Outreach"},
    {"value": "backlink_verification", "label": "Backlink Verification"},
]


class WeeklyReportAgent(BaseAgent):
    NAME = "Weekly Report Agent"
    DESCRIPTION = "Summarize weekly SEO performance and recommendations."
    ICON = "fa-calendar-week"
    CATEGORY = "Reporting"
    INPUT_SCHEMA = [
        {"id": "website_url", "label": "Website URL", "type": "url", "required": False, "placeholder": "https://example.com"},
        {"id": "project_name", "label": "Project Name", "type": "text", "required": False},
        {"id": "start_date", "label": "Start Date", "type": "date", "required": False},
        {"id": "end_date", "label": "End Date", "type": "date", "required": False},
        {"id": "comparison_period", "label": "Comparison Period", "type": "select", "required": False, "default": "previous_week", "options": [{"value": "previous_week", "label": "Previous Week"}, {"value": "previous_month", "label": "Previous Month"}, {"value": "custom", "label": "Custom"}]},
        {"id": "selected_agents", "label": "Selected Agents", "type": "multi_select", "required": False, "options": SEO_REPORT_AGENT_OPTIONS},
        {"id": "completed_tasks", "label": "Completed Tasks", "type": "textarea", "required": False},
        {"id": "planned_tasks", "label": "Planned Tasks", "type": "textarea", "required": False},
        {"id": "notes", "label": "Notes", "type": "textarea", "required": False},
        {"id": "search_console_property", "label": "Search Console Property", "type": "text", "required": False},
    ]

    def _date_range_warning(self, input_data: dict) -> str | None:
        raw_start = (input_data.get("start_date") or "").strip()
        raw_end = (input_data.get("end_date") or "").strip()
        if not raw_start or not raw_end:
            return None
        try:
            start = date.fromisoformat(raw_start)
            end = date.fromisoformat(raw_end)
        except ValueError:
            return "start_date/end_date could not be parsed; expected YYYY-MM-DD."
        days = (end - start).days
        if days < 5 or days > 9:
            return f"The selected date range spans {days} day(s); a weekly report is typically about 7 days."
        return None

    def run(self, input_data: dict) -> dict:
        latest = get_latest_run()
        if not latest:
            return {
                "success": False,
                "agent": self.NAME,
                "error": "Missing data",
                "message": "No tracker runs exist in the database. Run the tracker first to generate a weekly report.",
            }
        results = get_mention_results(latest["id"])
        metrics = get_competitor_metrics(latest["id"])
        date_range_warning = self._date_range_warning(input_data)
        return self.build_structured_response(
            input_data,
            f"Weekly report compiled from the latest real tracker run #{latest['id']}.",
            ["Review the weakest platform coverage.", "Prioritize pages tied to keywords without mentions."],
            {
                "run_id": latest["id"],
                "results_count": len(results),
                "competitor_metrics_count": len(metrics),
                "focus": ["content", "technical", "links"],
                "week": "current",
                "date_range_warning": date_range_warning,
                "data_source": "real_database_data",
                "api_used": [],
                "missing_api_keys": [],
            },
        )
