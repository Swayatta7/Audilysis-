from agents.base_agent import BaseAgent
import json

from agents.ai_marketing_agent import run_openai_json_agent
from agents.llm_client import openai_chat_completion
from agents.runtime_config import get_env_value
from agents.weekly_report import SEO_REPORT_AGENT_OPTIONS
from services.run_context import load_run_analysis_context


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
        user_id = input_data.get("_user_id")
        run_context = (
            load_run_analysis_context(input_data.get("run_id"), user_id=user_id)
            if user_id
            else load_run_analysis_context(input_data.get("run_id"))
        )
        if run_context:
            api_key = get_env_value("OPENAI_API_KEY")
            if not api_key:
                return self.missing_key_response("OPENAI_API_KEY", input_data)

            verified_snapshot = {
                "run_id": run_context["run_id"],
                "brand_name": run_context["run"]["brand_name"],
                "brand_domain": run_context["run"]["brand_domain"],
                "country": run_context["run"]["country"],
                "language": run_context["run"]["language"],
                "report_mode": run_context["report_mode"],
                "brand_mentions": run_context["brand_mentions_metric"],
                "share_of_voice": run_context["share_of_voice_metric"],
                "api_health": run_context["api_health_metric"],
                "platform_health": run_context["platform_summaries"],
                "top_competitor": run_context["top_competitor"],
            }
            prompt = (
                "You are preparing an SEO strategy using only verified run data and explicit user input. "
                "Do not invent traffic, rankings, volumes, percentages, performance metrics, backlinks, or competitor facts. "
                "If a metric is unavailable, keep it unavailable. "
                "Return JSON with keys: summary, recommendations, data. "
                "The data object must include: business_goal, phases, focus, verified_metrics_used, measured_facts, ai_recommendations_note. "
                f"Verified run data: {json.dumps(verified_snapshot)}. "
                f"User input: {json.dumps(input_data)}"
            )
            _, content = openai_chat_completion(api_key, prompt)
            if content is None:
                return {
                    "success": False,
                    "agent": self.NAME,
                    "error": "OpenAI API error",
                    "message": "Unable to generate strategy guidance from verified run data.",
                }
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "agent": self.NAME,
                    "error": "Invalid API response",
                    "message": "OpenAI returned non-JSON content.",
                    "raw_response": content,
                }
            return self.build_structured_response(
                input_data,
                parsed.get("summary", f"Strategy generated from verified tracker run #{run_context['run_id']}."),
                parsed.get("recommendations", []),
                {
                    **parsed.get("data", {}),
                    "data_source": "verified_run_data_and_openai",
                    "api_used": ["OpenAI Chat Completions API"],
                    "missing_api_keys": [],
                    "verified_run_context": verified_snapshot,
                    "ai_recommendations_note": "Recommendations are AI-generated interpretations of verified run data and explicit user input.",
                },
                True,
                parsed.get("message", "Completed successfully."),
            )

        return run_openai_json_agent(
            self,
            input_data,
            "an SEO strategy",
            ["business_goal", "website_url"],
            {"business_goal": input_data.get("business_goal"), "phases": [], "focus": [], "data_source": "real_user_input_and_openai"},
        )
