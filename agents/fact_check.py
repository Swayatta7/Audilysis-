from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context, text_value


class FactCheckAgent(BaseAgent):
    NAME = "Fact Check"
    DESCRIPTION = "Check content for factual accuracy, risky claims, missing citations, outdated statements, and unsupported claims."
    ICON = "fa-shield-halved"
    CATEGORY = "Content"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA + [
        {"id": "content", "label": "Content / Draft Text", "type": "textarea", "placeholder": "Paste the content to fact-check.", "required": True},
    ]

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        content = text_value(input_data, "content", "draft", "text", default=f"Content about {ctx['topic']}.")
        return run_openai_json_agent(
            self,
            {**input_data, "content": content, "context": ctx},
            "a fact check review using only provided content",
            ["content"],
            {"context": ctx, "fact_check": {"risk_level": "", "checked_excerpt": "", "issues": [], "citation_needed": True, "approval_status": ""}},
        )
