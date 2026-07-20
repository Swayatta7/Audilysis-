from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class SocialPublisherAgent(BaseAgent):
    NAME = "Publisher"
    DESCRIPTION = "Prepare social posts for publishing with platform formatting, hashtags, scheduling notes, and approval checklist."
    ICON = "fa-paper-plane"
    CATEGORY = "Social"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "a social publishing package",
            ["platform", "topic"],
            {"context": ctx, "post_package": {"platform": "", "formatted_post": "", "hashtags": [], "scheduling_notes": "", "approval_checklist": []}},
        )
