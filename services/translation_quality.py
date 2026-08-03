import logging
import re
from dataclasses import dataclass

from services.glossary import get_hindi_style_replacements, get_technical_glossary


logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://\S+|www\.\S+")
FILE_RE = re.compile(r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|json|md|txt|srt|vtt|html|css|yml|yaml)\b")
CODE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\([^)]*\)|`[^`]+`")
NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\b")
PLACEHOLDER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass
class ProtectedText:
    text: str
    entities: dict[str, str]
    numbers: list[str]


def protect_text(text: str) -> ProtectedText:
    entities = {}
    protected = text
    for pattern in _entity_patterns():
        protected = pattern.sub(lambda match: _replace(match.group(0), entities), protected)
    return ProtectedText(text=protected, entities=entities, numbers=NUMBER_RE.findall(text))


def restore_text(text: str, entities: dict[str, str]) -> str:
    restored = text
    for placeholder, original in entities.items():
        restored = restored.replace(placeholder, original)
    return restored


def validate_translation(source: str, translated: str, entities: dict[str, str]) -> list[str]:
    failures = []
    for placeholder in entities:
        if placeholder in translated:
            failures.append(f"placeholder_not_restored:{placeholder}")
    for original in entities.values():
        if original not in translated:
            failures.append(f"protected_entity_changed:{original}")
    source_numbers = NUMBER_RE.findall(source)
    translated_numbers = NUMBER_RE.findall(translated)
    if sorted(source_numbers) != sorted(translated_numbers):
        failures.append("numbers_changed")
    if failures:
        logger.warning("translation_validation_failed", extra={"reasons": failures})
    return failures


def protect_segments_for_translation(segments: list[dict]) -> tuple[list[dict], list[ProtectedText]]:
    protected = []
    metadata = []
    for segment in segments:
        item = protect_text(prepare_contextual_translation_text(segment.get("text", "")))
        metadata.append(item)
        protected.append({**segment, "text": item.text})
    return protected, metadata


def prepare_contextual_translation_text(text: str) -> str:
    software_terms = r"\b(?:models?|API|Prompt|Workflow|Repository|GitHub|OpenAI|LLM|Dataset|Embedding|offline|machine)\b"
    if re.search(software_terms, text, re.IGNORECASE):
        text = re.sub(r"\bRun\b(?=\s+(?:certain\s+)?models?\b)", "Execute", text)
        text = re.sub(r"\brun\b(?=\s+(?:certain\s+)?models?\b)", "execute", text)
    return text


def restore_translated_segments(original_segments: list[dict], translated_segments: list[dict], metadata: list[ProtectedText], target_language: str = "") -> list[dict]:
    restored = []
    for original, translated, item in zip(original_segments, translated_segments, metadata):
        text = post_process_translation(restore_text(translated.get("text", ""), item.entities), target_language)
        failures = validate_translation(original.get("text", ""), text, item.entities)
        if failures:
            raise ValueError(",".join(failures))
        restored.append({**translated, "text": text})
    return restored


def post_process_translation(text: str, target_language: str) -> str:
    if target_language != "hi":
        return text
    processed = text
    for source, replacement in get_hindi_style_replacements().items():
        processed = processed.replace(source, replacement)
    return processed


def _entity_patterns():
    escaped_terms = sorted((re.escape(term) for term in get_technical_glossary()), key=len, reverse=True)
    return [
        URL_RE,
        FILE_RE,
        CODE_RE,
        NUMBER_RE,
        re.compile(r"\b(?:" + "|".join(escaped_terms) + r")\b", re.IGNORECASE),
    ]


def _replace(value: str, entities: dict[str, str]) -> str:
    for placeholder, original in entities.items():
        if original == value:
            return placeholder
    placeholder = f"ZXQ{_placeholder_suffix(len(entities))}QXZ"
    entities[placeholder] = value
    return placeholder


def _placeholder_suffix(index: int) -> str:
    first = PLACEHOLDER_ALPHABET[(index // 26) % 26]
    second = PLACEHOLDER_ALPHABET[index % 26]
    return f"{first}{second}"
