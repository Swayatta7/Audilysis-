from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent
from agents.marketing_agent_utils import MARKETING_INPUT_SCHEMA, agent_context


class ContentCalendarAgent(BaseAgent):
    NAME = "Content Calendar"
    DESCRIPTION = "Generate monthly and weekly content calendar ideas by niche, keywords, audience, and goal."
    ICON = "fa-calendar-days"
    CATEGORY = "Content"
    INPUT_SCHEMA = MARKETING_INPUT_SCHEMA

    def run(self, input_data: dict) -> dict:
        ctx = agent_context(input_data)
        return run_openai_json_agent(
            self,
            {**input_data, "context": ctx},
            "a content calendar",
            ["topic", "target_audience", "business_goal"],
            {"context": ctx, "calendar": []},
        )
