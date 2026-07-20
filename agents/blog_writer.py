from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class BlogWriterAgent(BaseAgent):
    NAME = "Blog Writer"
    DESCRIPTION = "Generate blog drafts from an SEO brief, tone, keywords, audience, and brand voice."
    ICON = "fa-pen-nib"
    CATEGORY = "Content"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "a blog draft",
            ["topic", "target_audience"],
            {"draft": {"title": "", "intro": "", "sections": [], "conclusion": "", "cta": ""}, "context": ctx},
        )
