from agents.base_agent import BaseAgent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class AnalyticsAgent(BaseAgent):
    NAME = "Analytics"
    DESCRIPTION = "Analyze social performance metrics and recommend improvements for engagement, impressions, CTR, saves, shares, and conversions."
    ICON = "fa-chart-simple"
    CATEGORY = "Social"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA + [
        {"id": "impressions", "label": "Impressions", "type": "number", "placeholder": "1000", "required": True, "min": 0},
        {"id": "clicks", "label": "Clicks", "type": "number", "placeholder": "80", "required": True, "min": 0},
        {"id": "engagement", "label": "Engagement", "type": "number", "placeholder": "120", "required": True, "min": 0},
        {"id": "conversions", "label": "Conversions", "type": "number", "placeholder": "10", "required": True, "min": 0},
    ]

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        try:
            impressions = float(input_data.get("impressions"))
            clicks = float(input_data.get("clicks"))
            engagement = float(input_data.get("engagement"))
            conversions = float(input_data.get("conversions"))
        except (TypeError, ValueError):
            return self.missing_input_response(
                "impressions/clicks/engagement/conversions",
                input_data,
                "Provide real impressions, clicks, engagement, and conversions to run analytics.",
            )
        if impressions <= 0 or clicks < 0 or engagement < 0 or conversions < 0:
            return {
                "success": False,
                "agent": self.NAME,
                "error": "Invalid analytics input",
                "message": "Impressions must be greater than zero and other metrics cannot be negative.",
            }
        ctr = round((clicks / impressions) * 100, 2)
        engagement_rate = round((engagement / impressions) * 100, 2)
        conversion_rate = round((conversions / clicks) * 100, 2) if clicks else 0.0
        return self.build_structured_response(
            input_data,
            f"Calculated social analytics from real user-entered metrics for {ctx['platform']}.",
            ["Improve CTR with stronger hooks if CTR is low.", "Improve conversion rate if clicks are not turning into conversions."],
            {
                "context": ctx,
                "analysis": {
                    "impressions": impressions,
                    "clicks": clicks,
                    "engagement": engagement,
                    "conversions": conversions,
                    "ctr": ctr,
                    "engagement_rate": engagement_rate,
                    "conversion_rate": conversion_rate,
                },
                "data_source": "real_user_input",
                "api_used": [],
                "missing_api_keys": [],
            }
        )
