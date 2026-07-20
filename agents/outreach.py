from agents.base_agent import BaseAgent
from agents.ai_marketing_agent import run_openai_json_agent


class OutreachAgent(BaseAgent):
    NAME = "Outreach Agent"
    DESCRIPTION = "Draft a concise outreach plan for link acquisition and partnerships."
    ICON = "fa-paper-plane"
    CATEGORY = "Link Building"
    INPUT_SCHEMA = [
        {"id": "brand_name", "label": "Brand Name", "type": "text", "required": False},
        {"id": "brand_website", "label": "Brand Website", "type": "url", "placeholder": "https://example.com", "required": True},
        {"id": "prospect_name", "label": "Prospect Name", "type": "text", "required": False},
        {"id": "prospect_website", "label": "Prospect Website", "type": "url", "required": False},
        {"id": "outreach_goal", "label": "Outreach Goal", "type": "text", "placeholder": "Earn a contextual backlink", "required": True},
        {"id": "offer_type", "label": "Offer Type", "type": "select", "required": False, "options": [{"value": "guest_post", "label": "Guest Post"}, {"value": "link_insertion", "label": "Link Insertion"}, {"value": "resource_mention", "label": "Resource Mention"}, {"value": "partnership", "label": "Partnership"}, {"value": "broken_link_fix", "label": "Broken Link Fix"}]},
        {"id": "tone", "label": "Tone", "type": "text", "required": False},
        {"id": "contact_name", "label": "Contact Name", "type": "text", "required": False},
        {"id": "target_page", "label": "Target Page", "type": "url", "required": False},
        {"id": "source_page", "label": "Source Page", "type": "url", "required": False},
        {"id": "article_topic", "label": "Article Topic", "type": "text", "required": False},
        {"id": "value_proposition", "label": "Value Proposition", "type": "textarea", "required": False},
        {"id": "previous_contact_notes", "label": "Previous Contact Notes", "type": "textarea", "required": False},
        {"id": "language", "label": "Language", "type": "text", "required": False},
        {"id": "sender_name", "label": "Sender Name", "type": "text", "required": False},
        {"id": "sender_role", "label": "Sender Role", "type": "text", "required": False},
    ]

    def run(self, input_data: dict) -> dict:
        merged = {
            **input_data,
            "website_url": input_data.get("website_url") or input_data.get("brand_website"),
            "business_goal": input_data.get("business_goal") or input_data.get("outreach_goal"),
        }
        return run_openai_json_agent(
            self,
            merged,
            "a link outreach plan",
            ["website_url", "business_goal"],
            {"sequence": [], "email_subject": "", "data_source": "real_user_input_and_openai"},
        )
