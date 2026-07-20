import requests


def openai_chat_completion(api_key: str, prompt: str, model: str = "gpt-4o-mini") -> tuple[dict | None, str | None]:
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=90,
        )
        if response.status_code != 200:
            return None, f"OpenAI API returned HTTP {response.status_code}: {response.text}"
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return response.json(), content
    except requests.RequestException as exc:
        return None, f"OpenAI API request failed: {exc}"
