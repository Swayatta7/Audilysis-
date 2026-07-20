from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class VideoAgent(BaseAgent):
    NAME = "Video Generator"
    DESCRIPTION = "Generate short-form and long-form video scripts, hooks, shot ideas, titles, descriptions, and CTAs."
    ICON = "fa-video"
    CATEGORY = "Social"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "video scripts and production notes",
            ["topic", "platform"],
            {"context": ctx, "video": {"short_form_hook": "", "short_script": [], "long_form_title": "", "description": "", "shot_ideas": [], "cta": ""}},
        )
