import logging
import math
import re
from html import unescape
from typing import Iterable


logger = logging.getLogger(__name__)

MIN_CAPTION_DURATION = 1.0
MAX_CAPTION_DURATION = 7.0
MAX_CAPTION_CHARS = 84
TARGET_LINE_CHARS = 42
MAX_LINE_CHARS = 42
MAX_GAP_FOR_MERGE = 0.9
OVERLAP_TOLERANCE = 0.02
WEAK_LINE_ENDINGS = {"and", "or", "but", "the", "a", "an", "to", "of", "in", "for", "with"}
DANGLING_PHRASE_ENDINGS = {"with", "with over", "over", "more than", "less than", "at least", "around", "about", "under"}
ARTICLES = {"a", "an", "the"}
PREPOSITIONS = {"to", "of", "in", "on", "at", "for", "from", "with", "without", "inside", "into", "over", "under"}
VERBS_THAT_NEED_OBJECTS = {"connect", "run", "execute", "use", "open", "create", "build", "generate", "translate", "download", "copy"}
COMMON_ADJECTIVES = {"own", "paid", "free", "limited", "single", "technical", "generative", "open", "source", "offline"}
SENTENCE_END_RE = re.compile(r"[.!?。！？]$|[\]\)]$")
SPEAKER_PREFIX_RE = re.compile(r"^speaker\s+\d+:\s+", re.IGNORECASE)


def process_transcript_segments(raw_segments: Iterable[dict], max_segments: int = 2000) -> list[dict]:
    timed = normalize_timestamps(raw_segments)
    captions = build_sentence_aware_captions(timed)
    captions = [format_caption_text(item) for item in captions]
    captions = repair_caption_timing(captions)
    validate_captions(captions)
    if len(captions) > max_segments:
        raise ValueError("too_many_segments")
    return captions


def normalize_timestamps(raw_segments: Iterable[dict]) -> list[dict]:
    cleaned = []
    for item in raw_segments or []:
        text = clean_text(get_value(item, "text", ""))
        if not text:
            continue
        start = safe_float(get_value(item, "start", 0.0), 0.0)
        duration = safe_float(get_value(item, "duration", None), None)
        if start < 0:
            logger.warning("subtitle_timestamp_repaired", extra={"reason": "negative_start", "start": start})
            start = 0.0
        end = start + duration if duration and duration > 0 else None
        cleaned_item = {
            "start": round(start, 3),
            "end": round(end, 3) if end else None,
            "text": text,
            "timing_source": get_value(item, "timing_source", "youtube_caption") or "youtube_caption",
        }
        speaker = get_value(item, "speaker", None)
        if speaker:
            cleaned_item["speaker"] = str(speaker)
        cleaned.append(cleaned_item)

    cleaned.sort(key=lambda item: item["start"])
    normalized = []
    for index, item in enumerate(cleaned):
        start = item["start"]
        next_start = cleaned[index + 1]["start"] if index + 1 < len(cleaned) else None
        end = item["end"]
        if end is None or end <= start:
            fallback = next_start if next_start and next_start > start else start + 3.0
            end = min(start + MAX_CAPTION_DURATION, max(start + MIN_CAPTION_DURATION, fallback))
        if next_start is not None:
            end = min(end, next_start)
        if end <= start:
            end = start + MIN_CAPTION_DURATION
        if normalized and start < normalized[-1]["end"] - OVERLAP_TOLERANCE:
            start = normalized[-1]["end"]
            if end <= start:
                end = start + MIN_CAPTION_DURATION
        normalized_item = {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "text": item["text"],
            "timing_source": item["timing_source"],
        }
        if item.get("speaker"):
            normalized_item["speaker"] = item["speaker"]
        normalized.append(normalized_item)
    return normalized


def build_sentence_aware_captions(segments: list[dict]) -> list[dict]:
    captions = []
    current = []
    for segment in segments:
        if not current:
            current = [segment]
            continue
        gap = segment["start"] - current[-1]["end"]
        merged_text = " ".join(item["text"] for item in current + [segment])
        merged_duration = segment["end"] - current[0]["start"]
        current_text = " ".join(item["text"] for item in current)
        dangling = ends_with_dangling_phrase(current_text)
        should_merge = (
            gap <= MAX_GAP_FOR_MERGE
            and merged_duration <= MAX_CAPTION_DURATION
            and (len(merged_text) <= MAX_CAPTION_CHARS or dangling)
            and (not is_clear_sentence_end(current[-1]["text"]) or dangling)
        )
        if should_merge:
            current.append(segment)
        else:
            captions.extend(split_caption_block(current))
            current = [segment]
    if current:
        captions.extend(split_caption_block(current))
    return captions


def split_caption_block(block: list[dict]) -> list[dict]:
    start = block[0]["start"]
    end = block[-1]["end"]
    text = optimize_punctuation(" ".join(item["text"] for item in block))
    pieces = split_text_for_subtitles(text)
    if len(pieces) <= 1:
        caption = {"start": start, "end": end, "duration": round(end - start, 3), "text": text, "timing_source": block[0].get("timing_source", "youtube_caption")}
        if block[0].get("speaker"):
            caption["speaker"] = block[0]["speaker"]
        return [caption]

    total_chars = max(1, sum(len(piece) for piece in pieces))
    captions = []
    cursor = start
    for index, piece in enumerate(pieces):
        if index == len(pieces) - 1:
            piece_end = end
        else:
            share = len(piece) / total_chars
            piece_end = cursor + max(MIN_CAPTION_DURATION, min(MAX_CAPTION_DURATION, (end - start) * share))
        caption = {
            "start": round(cursor, 3),
            "end": round(piece_end, 3),
            "duration": round(piece_end - cursor, 3),
            "text": piece,
            "timing_source": block[0].get("timing_source", "youtube_caption"),
        }
        if block[0].get("speaker"):
            caption["speaker"] = block[0]["speaker"]
        captions.append(caption)
        cursor = piece_end
    return captions


def split_text_for_subtitles(text: str) -> list[str]:
    if len(text) <= MAX_CAPTION_CHARS:
        return [text] if text else []
    words = text.split()
    if not words:
        return []
    split_index = choose_subtitle_split(words)
    if split_index:
        first = " ".join(words[:split_index])
        second = " ".join(words[split_index:])
        return [first] + split_text_for_subtitles(second)
    pieces = []
    current = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and len(candidate) > MAX_CAPTION_CHARS:
            pieces.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def choose_subtitle_split(words: list[str]) -> int | None:
    candidates = []
    for index in range(1, len(words)):
        first = " ".join(words[:index])
        second = " ".join(words[index:])
        if len(first) > MAX_CAPTION_CHARS or not second:
            continue
        if ends_with_dangling_phrase(first):
            continue
        if is_bad_phrase_split(words, index):
            continue
        last = words[index - 1].strip()
        next_word = words[index].strip()
        if not last or not next_word:
            continue
        punctuation_bonus = 0 if last[-1:] in {",", ";", ":", ".", "?", "!"} else 8
        weak_bonus = 20 if last.lower().strip(".,:;!?") in WEAK_LINE_ENDINGS else 0
        target_score = abs(len(first) - min(MAX_CAPTION_CHARS, len(text_from_words(words)) // 2))
        candidates.append((punctuation_bonus + weak_bonus + target_score, index))
    if not candidates:
        return None
    return min(candidates)[1]


def format_caption_text(caption: dict) -> dict:
    text = optimize_punctuation(caption["text"])
    caption = {**caption, "text": balance_caption_lines(text)}
    return caption


def balance_caption_lines(text: str) -> str:
    text = clean_text(text)
    if len(text) <= MAX_LINE_CHARS:
        return text
    words = text.split()
    best = None
    best_score = None
    for index in range(1, len(words)):
        first = " ".join(words[:index])
        second = " ".join(words[index:])
        if len(first) > MAX_LINE_CHARS or len(second) > MAX_LINE_CHARS:
            continue
        if first.split()[-1].lower().strip(".,:;!?") in WEAK_LINE_ENDINGS:
            continue
        if is_bad_phrase_split(words, index):
            continue
        score = abs(len(first) - len(second))
        if best_score is None or score < best_score:
            best = (first, second)
            best_score = score
    if best:
        return "\n".join(best)
    return text


def repair_caption_timing(captions: list[dict]) -> list[dict]:
    repaired = []
    previous_end = 0.0
    for index, caption in enumerate(captions):
        start = max(0.0, safe_float(caption.get("start"), 0.0))
        end = safe_float(caption.get("end"), None)
        if end is None:
            duration = safe_float(caption.get("duration"), MIN_CAPTION_DURATION)
            end = start + max(MIN_CAPTION_DURATION, min(MAX_CAPTION_DURATION, duration))
        if start < previous_end - OVERLAP_TOLERANCE:
            start = previous_end
        if end <= start:
            end = start + MIN_CAPTION_DURATION
        if end - start > MAX_CAPTION_DURATION:
            end = start + MAX_CAPTION_DURATION
        repaired.append({
            **caption,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        })
        previous_end = repaired[-1]["end"]
    return repaired


def validate_captions(captions: list[dict]) -> None:
    seen = set()
    previous_end = -math.inf
    for caption in captions:
        text = caption.get("text", "")
        if not text.strip():
            raise ValueError("empty_caption")
        start = safe_float(caption.get("start"), None)
        end = safe_float(caption.get("end"), None)
        if start is None or end is None or start < 0 or end <= start:
            raise ValueError("invalid_timestamp")
        if start < previous_end - OVERLAP_TOLERANCE:
            raise ValueError("overlapping_caption")
        lines = text.splitlines()
        if len(lines) > 2:
            raise ValueError("too_many_lines")
        if any(len(line) > MAX_LINE_CHARS + 12 for line in lines):
            logger.warning("subtitle_line_length_exceeded", extra={"line_length": max(len(line) for line in lines)})
        key = (round(start, 3), round(end, 3), text)
        if key in seen:
            raise ValueError("duplicate_caption")
        seen.add(key)
        if SPEAKER_PREFIX_RE.match(text) and len(captions) < 3:
            raise ValueError("random_speaker_label")
        previous_end = end


def optimize_punctuation(text: str) -> str:
    text = clean_text(text)
    text = re.sub(
        r"\b(Option\s+(?:one|two|three|four|five|\d+)),\s+([A-Za-z])",
        lambda m: f"{m.group(1)}: {m.group(2).upper()}",
        text,
        flags=re.IGNORECASE,
    )
    return text


def is_clear_sentence_end(text: str) -> bool:
    stripped = text.strip()
    return bool(SENTENCE_END_RE.search(stripped)) or stripped.startswith("[") and stripped.endswith("]")


def ends_with_dangling_phrase(text: str) -> bool:
    words = [word.lower().strip(".,:;!?") for word in text.split()]
    tail = " ".join(words[-2:])
    return bool(words and (words[-1] in DANGLING_PHRASE_ENDINGS or tail in DANGLING_PHRASE_ENDINGS))


def is_bad_phrase_split(words: list[str], index: int) -> bool:
    previous = words[index - 1].lower().strip(".,:;!?")
    current = words[index].lower().strip(".,:;!?")
    next_word = words[index + 1].lower().strip(".,:;!?") if index + 1 < len(words) else ""
    if previous in ARTICLES or previous in PREPOSITIONS:
        return True
    if previous in VERBS_THAT_NEED_OBJECTS and current:
        return True
    if previous in COMMON_ADJECTIVES and current:
        return True
    if current in {"key", "keys"} and previous == "api":
        return True
    if current in {"image", "video"} and previous == "to":
        return True
    if previous.isdigit() and current:
        return True
    if current.isdigit() and previous in {"over", "under", "around", "about"}:
        return True
    if previous in {"open"} and current == "source":
        return True
    if previous in {"text"} and current == "to" and next_word in {"image", "video"}:
        return True
    return False


def text_from_words(words: list[str]) -> str:
    return " ".join(words)


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", unescape(str(value or "").replace("\n", " "))).strip()


def get_value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def safe_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
