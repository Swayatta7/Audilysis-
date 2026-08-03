TECHNICAL_GLOSSARY = [
    "API Key",
    "GitHub",
    "Text to Image",
    "Text to Video",
    "AI News Desk",
    "Prompt",
    "Workflow",
    "Open Source",
    "open source",
    "Repository",
    "Offline",
    "offline",
    "Model",
    "model",
    "models",
    "Dataset",
    "dataset",
    "Embedding",
    "embedding",
    "LLM",
    "Fine-tuning",
    "Token",
    "token",
    "Nano Banana",
    "Lip Sync Studio",
    "ChatGPT",
    "Gemini",
    "Claude",
    "DeepSeek",
    "OpenAI",
    "AI",
    "API",
    "UI",
    "URL",
    "SRT",
    "VTT",
    "machine",
    "prompt",
    "repository",
    "workflow",
]

HINDI_STYLE_REPLACEMENTS = {
    "एपीआई कुंजी": "API Key",
    "एपीआई की": "API Key",
    "गिटहब": "GitHub",
    "ओपनएआई": "OpenAI",
    "चैटजीपीटी": "ChatGPT",
    "जेमिनी": "Gemini",
    "क्लॉड": "Claude",
    "डीपसीक": "DeepSeek",
}


def get_technical_glossary() -> list[str]:
    return list(TECHNICAL_GLOSSARY)


def get_hindi_style_replacements() -> dict[str, str]:
    return dict(HINDI_STYLE_REPLACEMENTS)
