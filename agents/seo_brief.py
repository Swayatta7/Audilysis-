from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class SEOBriefAgent(BaseAgent):
    NAME = "SEO Brief"
    DESCRIPTION = "Create SEO content briefs with metadata, headings, keywords, FAQs, intent, links, and word count."
    ICON = "fa-clipboard-list"
    CATEGORY = "Content"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "an SEO content brief",
            ["topic", "keyword"],
            {"context": ctx, "brief": {"title": "", "meta_title": "", "meta_description": "", "headings": [], "keywords": [], "faqs": [], "internal_links": [], "target_intent": "", "target_word_count": 0}},
        )
