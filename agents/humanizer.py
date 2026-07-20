from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context, text_value


class HumanizerAgent(BaseAgent):
    NAME = "Humanizer"
    DESCRIPTION = "Rewrite AI-generated content to sound natural, polished, human, and brand-friendly."
    ICON = "fa-wand-magic-sparkles"
    CATEGORY = "Content"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA + [
        {"id": "content", "label": "Content / Draft Text", "type": "textarea", "placeholder": "Paste the AI-generated draft to humanize.", "required": True},
    ]

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        source = text_value(input_data, "content", "draft", "text", default=f"Draft content about {ctx['topic']}.")
        return run_openai_json_agent(
            self,
            {**input_data, "content": source, "context": ctx},
            "a humanized content rewrite",
            ["content"],
            {"context": ctx, "humanized": {"original_excerpt": "", "rewritten_excerpt": "", "editing_notes": []}},
        )
