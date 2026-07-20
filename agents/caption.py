from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class CaptionAgent(BaseAgent):
    NAME = "Caption Generator"
    DESCRIPTION = "Generate platform-specific captions with hooks, CTAs, hashtags, and engagement triggers."
    ICON = "fa-comment-dots"
    CATEGORY = "Social"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "platform-specific captions",
            ["platform", "topic"],
            {"context": ctx, "captions": []},
        )
