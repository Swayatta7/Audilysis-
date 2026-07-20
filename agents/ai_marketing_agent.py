import json

from agents.llm_client import openai_chat_completion
from agents.runtime_config import get_env_value


def run_openai_json_agent(agent, input_data: dict, task_label: str, required_inputs: list[str], output_schema: dict) -> dict:
    for field in required_inputs:
        value = input_data.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            return agent.missing_input_response(field, input_data)

    api_key = get_env_value("OPENAI_API_KEY")
    if not api_key:
        return agent.missing_key_response("OPENAI_API_KEY", input_data)

    prompt = (
        f"You are generating {task_label} using only the user's real input. "
        "Do not invent analytics, search volumes, rankings, or external facts. "
        "Return JSON with keys: summary, recommendations, data. "
        f"The data object should follow this shape: {json.dumps(output_schema)}. "
        f"User input: {json.dumps(input_data)}"
    )
    _, content = openai_chat_completion(api_key, prompt)
    if content is None:
        return {
            "success": False,
            "agent": agent.NAME,
            "error": "OpenAI API error",
            "message": "Unable to generate output from OpenAI using genuine input.",
        }

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {
            "success": False,
            "agent": agent.NAME,
            "error": "Invalid API response",
            "message": "OpenAI returned non-JSON content.",
            "raw_response": content,
        }

    return agent.build_structured_response(
        input_data,
        parsed.get("summary", f"{task_label} generated from real user input."),
        parsed.get("recommendations", []),
        {
            **parsed.get("data", {}),
            "data_source": "real_user_input_and_openai",
            "api_used": "OpenAI Chat Completions API",
            "missing_api_keys": [],
        },
        True,
        parsed.get("message", "Completed successfully."),
    )
