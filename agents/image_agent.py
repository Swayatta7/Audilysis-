from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class ImageAgent(BaseAgent):
    NAME = "Image Generator"
    DESCRIPTION = "Generate image concepts, thumbnail prompts, banner ideas, alt text, and design direction."
    ICON = "fa-image"
    CATEGORY = "Social"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "image concepts and design direction",
            ["topic", "platform"],
            {"context": ctx, "creative": {"concept": "", "thumbnail_prompt": "", "banner_idea": "", "alt_text": "", "design_direction": []}},
        )
