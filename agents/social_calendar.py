from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class SocialCalendarAgent(BaseAgent):
    NAME = "Social Calendar"
    DESCRIPTION = "Generate weekly or monthly social media calendars for Facebook, Instagram, LinkedIn, X, YouTube, and TikTok."
    ICON = "fa-calendar-check"
    CATEGORY = "Social"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "a social media calendar",
            ["topic", "business_goal"],
            {"context": ctx, "calendar": []},
        )
