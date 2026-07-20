from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class PublisherAgent(BaseAgent):
    NAME = "Publisher"
    DESCRIPTION = "Prepare final content for publishing with title, slug, metadata, schema, excerpt, category, tags, and checklist."
    ICON = "fa-upload"
    CATEGORY = "Content"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "a publishing package",
            ["topic", "content_type"],
            {"context": ctx, "publishing_package": {"title": "", "slug": "", "meta_title": "", "meta_description": "", "schema": "", "excerpt": "", "category": "", "tags": [], "checklist": []}},
        )
