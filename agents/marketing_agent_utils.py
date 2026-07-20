def text_value(input_data: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (list, dict)):
            return str(value).strip()
    return default


def list_value(input_data: dict, key: str) -> list:
    raw = input_data.get(key) or []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [item.strip() for item in raw.replace(",", "\n").splitlines() if item.strip()]
    return []


def agent_context(input_data: dict) -> dict:
    return {
        "website_url": text_value(input_data, "website_url"),
        "brand_name": text_value(input_data, "brand_name", default="Brand"),
        "industry": text_value(input_data, "industry", default="general"),
        "target_audience": text_value(input_data, "target_audience", default="target customers"),
        "keyword": text_value(input_data, "keyword", "target_keywords", default="primary keyword"),
        "topic": text_value(input_data, "topic", default="core topic"),
        "platform": text_value(input_data, "platform", default="multi-platform"),
        "tone": text_value(input_data, "tone", default="professional"),
        "language": text_value(input_data, "language", default="en"),
        "country": text_value(input_data, "country", "location", default="United States"),
        "business_goal": text_value(input_data, "business_goal", default="grow qualified demand"),
        "content_type": text_value(input_data, "content_type", default="article"),
        "competitors": list_value(input_data, "competitors"),
    }


MARKETING_INPUT_SCHEMA = [
    {"id": "brand_name", "label": "Brand Name", "type": "text", "placeholder": "Acme Co", "required": False},
    {"id": "industry", "label": "Industry", "type": "text", "placeholder": "SaaS, healthcare, ecommerce", "required": False},
    {"id": "target_audience", "label": "Target Audience", "type": "text", "placeholder": "Marketing leaders at mid-market companies", "required": False},
    {"id": "topic", "label": "Topic", "type": "text", "placeholder": "AI content operations", "required": False},
    {"id": "keyword", "label": "Keyword", "type": "text", "placeholder": "digital marketing agency", "required": False},
    {"id": "platform", "label": "Platform", "type": "text", "placeholder": "LinkedIn, Instagram, YouTube", "required": False},
    {"id": "tone", "label": "Tone", "type": "text", "placeholder": "Helpful, expert, friendly", "required": False},
    {"id": "country", "label": "Country", "type": "text", "placeholder": "United States", "required": False},
    {"id": "content_type", "label": "Content Type", "type": "text", "placeholder": "Blog, carousel, video, newsletter", "required": False},
    {"id": "business_goal", "label": "Business Goal", "type": "text", "placeholder": "Generate qualified leads", "required": False},
]
