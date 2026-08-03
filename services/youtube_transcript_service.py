import json
import logging
import math
import os
import re
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from importlib import metadata
from typing import Iterable
from urllib.parse import parse_qs, urlparse

import requests

from agents.runtime_config import get_env_value
from services.subtitle_quality import (
    format_caption_text,
    process_transcript_segments,
    repair_caption_timing,
    validate_captions,
)
from services.diarization import get_diarizer
from services.translation_quality import (
    protect_segments_for_translation,
    restore_translated_segments,
)

YOUTUBE_TRANSCRIPT_IMPORT_ERROR = None
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
    import youtube_transcript_api._errors as transcript_errors
    CouldNotRetrieveTranscript = transcript_errors.CouldNotRetrieveTranscript
    NoTranscriptFound = transcript_errors.NoTranscriptFound
    TranscriptsDisabled = transcript_errors.TranscriptsDisabled
    VideoUnavailable = transcript_errors.VideoUnavailable
    RequestBlocked = getattr(transcript_errors, "RequestBlocked", None)
    IpBlocked = getattr(transcript_errors, "IpBlocked", None)
    PoTokenRequired = getattr(transcript_errors, "PoTokenRequired", None)
    TooManyRequests = getattr(transcript_errors, "TooManyRequests", None)
except ImportError as exc:
    YOUTUBE_TRANSCRIPT_IMPORT_ERROR = exc
    YouTubeTranscriptApi = None
    GenericProxyConfig = None
    WebshareProxyConfig = None
    class TranscriptsDisabled(Exception):
        pass
    class NoTranscriptFound(Exception):
        pass
    class TooManyRequests(Exception):
        pass
    class VideoUnavailable(Exception):
        pass
    class CouldNotRetrieveTranscript(Exception):
        pass
    RequestBlocked = None
    IpBlocked = None
    PoTokenRequired = None

if RequestBlocked is None:
    class RequestBlocked(Exception):
        pass
if IpBlocked is None:
    class IpBlocked(Exception):
        pass
if PoTokenRequired is None:
    class PoTokenRequired(Exception):
        pass
if TooManyRequests is None:
    class TooManyRequests(Exception):
        pass

logger = logging.getLogger(__name__)


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
MAX_URL_LENGTH = 512
MAX_SEGMENTS = 2000
MAX_TRANSLATION_CHARS = 90000
MAX_TRANSCRIPT_FETCH_RETRIES = 2
GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"


LANGUAGES = [
    {"code": "original", "name": "Original Language", "flag": ""},
    {"code": "en", "name": "English", "flag": ""},
    {"code": "es", "name": "Spanish", "flag": ""},
    {"code": "fr", "name": "French", "flag": ""},
    {"code": "de", "name": "German", "flag": ""},
    {"code": "it", "name": "Italian", "flag": ""},
    {"code": "pt", "name": "Portuguese", "flag": ""},
    {"code": "ru", "name": "Russian", "flag": ""},
    {"code": "zh", "name": "Chinese (Simplified)", "flag": ""},
    {"code": "ja", "name": "Japanese", "flag": ""},
    {"code": "ko", "name": "Korean", "flag": ""},
    {"code": "ar", "name": "Arabic", "flag": ""},
    {"code": "hi", "name": "Hindi", "flag": ""},
    {"code": "nl", "name": "Dutch", "flag": ""},
    {"code": "pl", "name": "Polish", "flag": ""},
    {"code": "tr", "name": "Turkish", "flag": ""},
    {"code": "sv", "name": "Swedish", "flag": ""},
    {"code": "da", "name": "Danish", "flag": ""},
    {"code": "no", "name": "Norwegian", "flag": ""},
    {"code": "fi", "name": "Finnish", "flag": ""},
    {"code": "cs", "name": "Czech", "flag": ""},
    {"code": "el", "name": "Greek", "flag": ""},
    {"code": "hu", "name": "Hungarian", "flag": ""},
    {"code": "ro", "name": "Romanian", "flag": ""},
    {"code": "uk", "name": "Ukrainian", "flag": ""},
    {"code": "th", "name": "Thai", "flag": ""},
    {"code": "vi", "name": "Vietnamese", "flag": ""},
    {"code": "id", "name": "Indonesian", "flag": ""},
    {"code": "ms", "name": "Malay", "flag": ""},
    {"code": "tl", "name": "Filipino", "flag": ""},
    {"code": "ta", "name": "Tamil", "flag": ""},
    {"code": "te", "name": "Telugu", "flag": ""},
    {"code": "bn", "name": "Bengali", "flag": ""},
    {"code": "ur", "name": "Urdu", "flag": ""},
    {"code": "fa", "name": "Persian", "flag": ""},
    {"code": "he", "name": "Hebrew", "flag": ""},
    {"code": "sw", "name": "Swahili", "flag": ""},
    {"code": "af", "name": "Afrikaans", "flag": ""},
    {"code": "sq", "name": "Albanian", "flag": ""},
    {"code": "bg", "name": "Bulgarian", "flag": ""},
    {"code": "hr", "name": "Croatian", "flag": ""},
    {"code": "et", "name": "Estonian", "flag": ""},
    {"code": "lv", "name": "Latvian", "flag": ""},
    {"code": "lt", "name": "Lithuanian", "flag": ""},
    {"code": "mk", "name": "Macedonian", "flag": ""},
    {"code": "sr", "name": "Serbian", "flag": ""},
    {"code": "sk", "name": "Slovak", "flag": ""},
    {"code": "sl", "name": "Slovenian", "flag": ""},
]
LANGUAGE_NAMES = {item["code"]: item["name"] for item in LANGUAGES}
SUPPORTED_LANGUAGE_CODES = set(LANGUAGE_NAMES)
DOWNLOAD_FORMATS = {
    "txt": ("text/plain; charset=utf-8", "txt"),
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "json": ("application/json; charset=utf-8", "json"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
}


class YouTubeTranscriptError(Exception):
    status_code = 400
    error_code = "transcript_error"

    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code


class ValidationError(YouTubeTranscriptError):
    status_code = 400
    error_code = "validation_error"


class ConfigurationError(YouTubeTranscriptError):
    status_code = 500
    error_code = "configuration_error"


class UpstreamError(YouTubeTranscriptError):
    status_code = 502
    error_code = "upstream_error"


@dataclass
class TranscriptPayload:
    video_id: str
    source_language: str
    source_language_name: str
    target_language: str
    target_language_name: str
    translated: bool
    is_generated: bool
    speaker_detection: dict
    segments: list[dict]
    thumbnail_url: str

    def to_dict(self) -> dict:
        duration = calculate_duration(self.segments)
        return {
            "video_id": self.video_id,
            "video_title": "YouTube Video",
            "thumbnail_url": self.thumbnail_url,
            "source_language": self.source_language,
            "source_language_name": self.source_language_name,
            "target_language": self.target_language,
            "target_language_name": self.target_language_name,
            "translated": self.translated,
            "is_generated": self.is_generated,
            "speaker_detection": self.speaker_detection,
            "segment_count": len(self.segments),
            "word_count": word_count(self.segments),
            "duration": duration,
            "segments": self.segments,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }


def get_youtube_transcript_api_version() -> str | None:
    try:
        return metadata.version("youtube-transcript-api")
    except metadata.PackageNotFoundError:
        return None


def get_languages() -> list[dict]:
    return LANGUAGES


def validate_language_code(code: str, allow_original: bool = True) -> str:
    code = (code or "original").strip()
    if code == "original" and allow_original:
        return code
    if code not in SUPPORTED_LANGUAGE_CODES or code == "original" or not LANGUAGE_CODE_RE.match(code):
        raise ValidationError("Unsupported target language.", error_code="unsupported_language")
    return code


def extract_video_id(value: str, allow_raw_id: bool = True) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValidationError("YouTube URL is required.", error_code="missing_url")
    if len(raw) > MAX_URL_LENGTH:
        raise ValidationError("YouTube URL is too long.", error_code="url_too_long")
    if allow_raw_id and VIDEO_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "m.youtube.com":
        host = "youtube.com"

    video_id = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com":
        if parsed.path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        elif parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else ""
    else:
        raise ValidationError("Enter a valid YouTube URL.", error_code="invalid_domain")

    if not video_id or not VIDEO_ID_RE.match(video_id):
        raise ValidationError("The YouTube video ID is invalid.", error_code="invalid_video_id")
    return video_id


def fetch_transcript(url_or_id: str, target_language: str = "original", enable_speaker_detection: bool = False) -> dict:
    video_id = extract_video_id(url_or_id)
    target_language = validate_language_code(target_language)
    transcript = _fetch_source_transcript(video_id, enable_speaker_detection=enable_speaker_detection)
    source_segments = transcript["segments"]
    source_language = transcript["source_language"]
    source_language_name = transcript["source_language_name"]
    translated = False
    segments = source_segments

    if target_language != "original" and target_language != source_language:
        segments = translate_segments(source_segments, target_language, source_language)
        translated = True

    effective_language = target_language if translated else source_language
    payload = TranscriptPayload(
        video_id=video_id,
        source_language=source_language,
        source_language_name=source_language_name,
        target_language=effective_language,
        target_language_name=LANGUAGE_NAMES.get(effective_language, effective_language),
        translated=translated,
        is_generated=transcript["is_generated"],
        speaker_detection=transcript.get("speaker_detection") or {"enabled": False, "speaker_labels_available": False, "message": ""},
        segments=segments,
        thumbnail_url=get_thumbnail_url(video_id),
    )
    return payload.to_dict()


def _fetch_source_transcript(video_id: str, enable_speaker_detection: bool = False) -> dict:
    if not VIDEO_ID_RE.match(video_id):
        raise ValidationError("The YouTube video ID is invalid.", error_code="invalid_video_id")

    if YouTubeTranscriptApi is None:
        installed_version = get_youtube_transcript_api_version()
        if installed_version:
            raise ConfigurationError(
                "Transcript package is installed but could not be imported correctly. "
                f"Import error: {YOUTUBE_TRANSCRIPT_IMPORT_ERROR}",
                error_code="transcript_dependency_import_failed",
            )
        raise ConfigurationError(
            "Transcript dependency is not installed. Install youtube-transcript-api to use this feature.",
            error_code="missing_dependency",
        )

    try:
        transcript_list = _list_transcripts(video_id)
        transcript = _select_transcript(transcript_list)
        raw_segments = _fetch_raw_segments_with_retry(transcript, video_id)
    except TranscriptsDisabled as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError("Transcripts are disabled for this video.", 403, "transcripts_disabled") from exc
    except NoTranscriptFound as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError("No transcript is available for this video.", 404, "no_transcript") from exc
    except VideoUnavailable as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError("This video is private, removed, or unavailable.", 404, "video_unavailable") from exc
    except IpBlocked as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError(
            "YouTube blocked requests from this server IP. Configure a supported proxy or try again later.",
            429,
            "youtube_ip_blocked",
        ) from exc
    except RequestBlocked as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError(
            "YouTube blocked the transcript request. Configure a supported proxy or try again later.",
            429,
            "youtube_request_blocked",
        ) from exc
    except TooManyRequests as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError(
            "YouTube rate-limited transcript requests. Please try again later.",
            429,
            "youtube_rate_limited",
        ) from exc
    except PoTokenRequired as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise YouTubeTranscriptError(
            "YouTube requires additional verification for this transcript request.",
            403,
            "youtube_po_token_required",
        ) from exc
    except requests.exceptions.ProxyError as exc:
        log_transcript_exception(exc, video_id, "fetch")
        if is_proxy_authentication_error(exc):
            raise UpstreamError(
                "Proxy authentication failed. Check the configured Webshare proxy credentials in the environment.",
                error_code="youtube_proxy_auth_failed",
            ) from exc
        raise UpstreamError(
            "Could not connect through the configured YouTube proxy.",
            error_code="youtube_proxy_connection_failed",
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise UpstreamError(
            "Could not connect to YouTube to retrieve the transcript. Check server network/DNS access and try again.",
            error_code="youtube_connection_failed",
        ) from exc
    except CouldNotRetrieveTranscript as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise UpstreamError(
            "YouTube could not return transcript data for this video.",
            error_code="youtube_unavailable",
        ) from exc
    except requests.exceptions.Timeout as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise UpstreamError("The YouTube transcript request timed out.", error_code="youtube_timeout") from exc
    except requests.exceptions.RequestException as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise UpstreamError("YouTube transcript request failed.", error_code="youtube_request_failed") from exc
    except ElementTree.ParseError as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise UpstreamError(
            "YouTube returned an empty or invalid transcript response. Please try again later.",
            error_code="youtube_bad_transcript_response",
        ) from exc
    except Exception as exc:
        log_transcript_exception(exc, video_id, "fetch")
        raise UpstreamError("Transcript retrieval failed. Please try another video or try again later.") from exc

    diarization = get_diarizer(enabled=enable_speaker_detection).apply(normalize_segments(raw_segments), video_id=video_id)
    segments = diarization.segments
    if not segments:
        raise YouTubeTranscriptError("No transcript segments were found for this video.", 404, "empty_transcript")

    source_language = getattr(transcript, "language_code", "") or "und"
    return {
        "source_language": source_language,
        "source_language_name": getattr(transcript, "language", "") or LANGUAGE_NAMES.get(source_language, source_language),
        "is_generated": bool(getattr(transcript, "is_generated", False)),
        "speaker_detection": {
            "enabled": diarization.enabled,
            "speaker_labels_available": diarization.speaker_labels_available,
            "message": diarization.message,
            "status": diarization.status,
            "model": diarization.model,
            "detected_speakers": diarization.detected_speakers,
            "confidence": diarization.confidence,
            "reason": diarization.reason,
            "timings_ms": diarization.timings_ms or {},
        },
        "segments": segments,
    }


def _fetch_raw_segments_with_retry(transcript, video_id: str):
    last_exc = None
    for attempt in range(MAX_TRANSCRIPT_FETCH_RETRIES + 1):
        try:
            return transcript.fetch()
        except (IpBlocked, RequestBlocked, TooManyRequests, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, PoTokenRequired, CouldNotRetrieveTranscript):
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.RequestException) as exc:
            last_exc = exc
            if attempt >= MAX_TRANSCRIPT_FETCH_RETRIES:
                raise
            logger.warning(
                "youtube_transcript_fetch_retry",
                extra={
                    "video_id": video_id,
                    "attempt": attempt + 1,
                    "max_retries": MAX_TRANSCRIPT_FETCH_RETRIES,
                    "exception_class": exc.__class__.__name__,
                    "proxy_mode": get_proxy_mode(),
                },
            )
            time.sleep(0.25 * (2 ** attempt))
    raise last_exc


def _list_transcripts(video_id: str):
    proxy_config = build_proxy_config()
    try:
        return YouTubeTranscriptApi(proxy_config=proxy_config).list(video_id)
    except TypeError:
        if proxy_config is not None:
            raise ConfigurationError(
                "The installed youtube-transcript-api version does not support proxy configuration.",
                error_code="transcript_proxy_not_supported",
            )
    if hasattr(YouTubeTranscriptApi, "list_transcripts"):
        return YouTubeTranscriptApi.list_transcripts(video_id)
    return YouTubeTranscriptApi().list(video_id)


def log_transcript_exception(exc: Exception, video_id: str, stage: str) -> None:
    logger.warning(
        "youtube_transcript_upstream_error",
        extra={
            "video_id": video_id,
            "stage": stage,
            "exception_class": exc.__class__.__name__,
            "youtube_transcript_api_version": get_youtube_transcript_api_version(),
            "proxy_mode": get_proxy_mode(),
            "diagnostic": safe_exception_diagnostic(exc),
        },
    )
    if should_log_full_transcript_traceback():
        logger.exception(
            "youtube_transcript_original_exception_traceback class=%s video_id=%s stage=%s",
            exc.__class__.__name__,
            video_id,
            stage,
        )
    logger.debug(
        "youtube_transcript_original_exception_traceback class=%s video_id=%s stage=%s",
        exc.__class__.__name__,
        video_id,
        stage,
        exc_info=True,
    )


def should_log_full_transcript_traceback() -> bool:
    return get_env_value("AUDILYSIS_TRANSCRIPT_DEBUG").lower() in {"1", "true", "yes", "on"}


def safe_exception_diagnostic(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    for line in text:
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return exc.__class__.__name__


def is_proxy_authentication_error(exc: Exception) -> bool:
    return "407" in str(exc) or "Proxy Authentication Required" in str(exc)


def get_proxy_mode() -> str:
    if get_env_value("WEBSHARE_PROXY"):
        return "webshare_url"
    if get_env_value("WEBSHARE_PROXY_USERNAME") and get_env_value("WEBSHARE_PROXY_PASSWORD"):
        return "webshare"
    if get_env_value("YOUTUBE_PROXY_HTTP_URL") or get_env_value("YOUTUBE_PROXY_HTTPS_URL"):
        return "generic"
    return "direct"


def get_proxy_diagnostics() -> dict:
    webshare_url = bool(get_env_value("WEBSHARE_PROXY"))
    webshare_username = bool(get_env_value("WEBSHARE_PROXY_USERNAME"))
    webshare_password = bool(get_env_value("WEBSHARE_PROXY_PASSWORD"))
    webshare_host = bool(get_env_value("WEBSHARE_PROXY_HOST"))
    webshare_port = bool(get_env_value("WEBSHARE_PROXY_PORT"))
    generic_http = bool(get_env_value("YOUTUBE_PROXY_HTTP_URL"))
    generic_https = bool(get_env_value("YOUTUBE_PROXY_HTTPS_URL"))
    mode = get_proxy_mode()
    if mode == "webshare_url":
        reason = "Webshare proxy URL configured."
    elif mode == "webshare":
        reason = "Webshare proxy configured."
    elif mode == "generic":
        reason = "Generic proxy configured."
    elif webshare_username != webshare_password:
        reason = "Incomplete Webshare proxy credentials."
    else:
        reason = "No proxy configured; YouTube transcript requests use direct mode."
    return {
        "mode": mode,
        "webshare_proxy_url": "Configured" if webshare_url else "Missing",
        "webshare_username": "Configured" if webshare_username else "Missing",
        "webshare_password": "Configured" if webshare_password else "Missing",
        "webshare_host": "Configured" if webshare_host else "Default",
        "webshare_port": "Configured" if webshare_port else "Default",
        "generic_http_url": "Configured" if generic_http else "Missing",
        "generic_https_url": "Configured" if generic_https else "Missing",
        "available": mode != "direct",
        "reason": reason,
    }


def build_proxy_config():
    webshare_proxy_url = get_env_value("WEBSHARE_PROXY")
    if webshare_proxy_url:
        return GenericProxyConfig(http_url=webshare_proxy_url, https_url=webshare_proxy_url)

    webshare_username = get_env_value("WEBSHARE_PROXY_USERNAME")
    webshare_password = get_env_value("WEBSHARE_PROXY_PASSWORD")
    if webshare_username and webshare_password:
        kwargs = {
            "proxy_username": webshare_username,
            "proxy_password": webshare_password,
        }
        webshare_host = get_env_value("WEBSHARE_PROXY_HOST")
        webshare_port = parse_proxy_port(get_env_value("WEBSHARE_PROXY_PORT"))
        if webshare_host:
            kwargs["domain_name"] = webshare_host
        if webshare_port:
            kwargs["proxy_port"] = webshare_port
        return WebshareProxyConfig(**kwargs)

    http_url = get_env_value("YOUTUBE_PROXY_HTTP_URL")
    https_url = get_env_value("YOUTUBE_PROXY_HTTPS_URL")
    if http_url or https_url:
        return GenericProxyConfig(http_url=http_url or None, https_url=https_url or None)

    return None


def parse_proxy_port(value: str) -> int | None:
    if not value:
        return None
    try:
        port = int(value)
    except ValueError:
        logger.warning("youtube_transcript_invalid_proxy_port")
        return None
    if 1 <= port <= 65535:
        return port
    logger.warning("youtube_transcript_invalid_proxy_port")
    return None


def _select_transcript(transcript_list):
    transcripts = list(transcript_list)
    if not transcripts:
        raise YouTubeTranscriptError("No transcript is available for this video.", 404, "no_transcript")

    manual = [item for item in transcripts if not getattr(item, "is_generated", False)]
    generated = [item for item in transcripts if getattr(item, "is_generated", False)]
    preferred = manual or generated

    for code in ("en", "en-US", "en-GB"):
        for item in preferred:
            if getattr(item, "language_code", "") == code:
                return item
    return preferred[0]


def normalize_segments(raw_segments: Iterable[dict]) -> list[dict]:
    try:
        normalized = process_transcript_segments(raw_segments, max_segments=MAX_SEGMENTS)
    except ValueError as exc:
        if str(exc) == "too_many_segments":
            raise ValidationError("Transcript is too large to process safely.", error_code="too_many_segments") from exc
        logger.warning("subtitle_quality_validation_failed", extra={"reason": str(exc)})
        raise UpstreamError("Transcript timing or subtitle formatting could not be validated.", error_code="subtitle_quality_failed") from exc
    if len(normalized) > MAX_SEGMENTS:
        raise ValidationError("Transcript is too large to process safely.", error_code="too_many_segments")
    return normalized


def get_segment_value(item, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def translate_existing_payload(data: dict) -> dict:
    target_language = validate_language_code(data.get("target_language"), allow_original=False)
    source_language = (data.get("source_language") or "auto").strip()
    segments = normalize_segments(data.get("segments") or [])
    if not segments:
        raise ValidationError("Transcript segments are required.", error_code="missing_segments")
    translated = translate_segments(segments, target_language, source_language)
    return {
        "source_language": source_language,
        "target_language": target_language,
        "target_language_name": LANGUAGE_NAMES.get(target_language, target_language),
        "translated": True,
        "segment_count": len(translated),
        "word_count": word_count(translated),
        "segments": translated,
    }


def translate_segments(segments: list[dict], target_language: str, source_language: str = "auto") -> list[dict]:
    api_key = get_env_value("GOOGLE_TRANSLATE_API_KEY")
    if not api_key:
        raise ConfigurationError(
            "Google Translation is not configured. Add the server-side Google Translation API key to translate transcripts.",
            error_code="missing_google_translate_key",
        )
    target_language = validate_language_code(target_language, allow_original=False)
    if len(segments) > MAX_SEGMENTS:
        raise ValidationError("Transcript is too large to translate safely.", error_code="too_many_segments")

    total_chars = sum(len(item.get("text", "")) for item in segments)
    if total_chars > MAX_TRANSLATION_CHARS:
        raise ValidationError("Transcript text is too large to translate in one request.", error_code="translation_too_large")

    protected_segments, protection_metadata = protect_segments_for_translation(segments)

    translated_texts = []
    for batch in _translation_batches(protected_segments):
        payload = {
            "q": [item["text"] for item in batch],
            "target": target_language,
            "format": "text",
            "key": api_key,
        }
        if source_language and source_language != "auto" and source_language != "und":
            payload["source"] = source_language
        try:
            response = requests.post(GOOGLE_TRANSLATE_URL, data=payload, timeout=20)
        except requests.exceptions.Timeout as exc:
            raise UpstreamError("Google Translation request timed out.", error_code="google_translate_timeout") from exc
        except requests.RequestException as exc:
            raise UpstreamError("Google Translation request failed.", error_code="google_translate_request_failed") from exc

        if response.status_code != 200:
            raise UpstreamError("Google Translation failed for this transcript.", error_code="google_translate_failed")
        try:
            body = response.json()
            translations = body["data"]["translations"]
        except Exception as exc:
            raise UpstreamError("Google Translation returned an unexpected response.", error_code="google_translate_bad_response") from exc
        if len(translations) != len(batch):
            raise UpstreamError("Google Translation response did not match transcript segments.", error_code="translation_mismatch")
        translated_texts.extend(unescape(item.get("translatedText", "")).strip() for item in translations)

    translated_segments = []
    for segment, translated_text in zip(segments, translated_texts):
        translated_segments.append({
            "start": segment["start"],
            "end": segment["end"],
            "duration": segment["duration"],
            "timing_source": segment.get("timing_source", "youtube_caption"),
            "speaker": segment.get("speaker"),
            "text": translated_text,
        })
    try:
        translated_segments = restore_translated_segments(segments, translated_segments, protection_metadata, target_language=target_language)
    except ValueError as exc:
        raise UpstreamError("Google Translation changed protected terms or facts.", error_code="translation_validation_failed") from exc
    translated_segments = repair_caption_timing([format_caption_text(item) for item in translated_segments])
    try:
        validate_captions(translated_segments)
    except ValueError as exc:
        raise UpstreamError("Translated subtitles failed quality validation.", error_code="translated_subtitle_quality_failed") from exc
    return translated_segments


def _translation_batches(segments: list[dict], max_chars: int = 4500) -> Iterable[list[dict]]:
    batch = []
    size = 0
    for segment in segments:
        text_size = len(segment.get("text", ""))
        if batch and size + text_size > max_chars:
            yield batch
            batch = []
            size = 0
        batch.append(segment)
        size += text_size
    if batch:
        yield batch


def format_transcript_download(transcript: dict, fmt: str) -> tuple[str, str, str]:
    fmt = (fmt or "txt").strip().lower()
    if fmt not in DOWNLOAD_FORMATS:
        raise ValidationError("Unsupported download format.", error_code="unsupported_format")
    segments = normalize_segments(transcript.get("segments") or [])
    if fmt == "txt":
        content = "\n".join(f"[{format_plain_timestamp(item['start'])} - {format_plain_timestamp(segment_end(item))}] {format_segment_text(item)}" for item in segments)
    elif fmt == "srt":
        content = format_srt(segments)
    elif fmt == "vtt":
        content = format_vtt(segments)
    else:
        content = json.dumps({**transcript, "segments": segments}, ensure_ascii=False, indent=2)
    mime, extension = DOWNLOAD_FORMATS[fmt]
    video_id = extract_video_id(transcript.get("video_id") or "")
    language = validate_filename_language(transcript.get("target_language") or transcript.get("source_language") or "original")
    return content, mime, f"audilysis-youtube-transcript-{video_id}-{language}.{extension}"


def format_srt(segments: list[dict]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        start = format_subtitle_timestamp(segment["start"], separator=",")
        end = format_subtitle_timestamp(segment_end(segment), separator=",")
        blocks.append(f"{index}\n{start} --> {end}\n{format_segment_text(segment)}\n")
    return "\n".join(blocks).strip() + "\n"


def format_vtt(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        start = format_subtitle_timestamp(segment["start"], separator=".")
        end = format_subtitle_timestamp(segment_end(segment), separator=".")
        lines.extend([f"{start} --> {end}", format_segment_text(segment), ""])
    return "\n".join(lines)


def format_plain_timestamp(seconds: float) -> str:
    seconds = max(0.0, safe_float(seconds, 0.0))
    total = int(math.floor(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_subtitle_timestamp(seconds: float, separator: str = ",") -> str:
    seconds = max(0.0, safe_float(seconds, 0.0))
    total_ms = int(round(seconds * 1000))
    hours = total_ms // 3600000
    total_ms %= 3600000
    minutes = total_ms // 60000
    total_ms %= 60000
    secs = total_ms // 1000
    ms = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{ms:03d}"


def build_download_from_url(url: str, target_language: str, fmt: str) -> tuple[str, str, str]:
    transcript = fetch_transcript(url, target_language)
    return format_transcript_download(transcript, fmt)


def format_segment_text(segment: dict) -> str:
    speaker = (segment.get("speaker") or "").strip()
    text = segment.get("text", "")
    return f"{speaker}: {text}" if speaker else text


def safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_duration(segment: dict) -> float:
    duration = safe_float(segment.get("duration"), 0.0)
    return duration if duration > 0 else 1.0


def segment_end(segment: dict) -> float:
    end = safe_float(segment.get("end"), None)
    if end is not None and end > safe_float(segment.get("start"), 0.0):
        return end
    return safe_float(segment.get("start"), 0.0) + safe_duration(segment)


def calculate_duration(segments: list[dict]) -> float:
    if not segments:
        return 0.0
    return round(max(segment_end(item) for item in segments), 3)


def word_count(segments: list[dict]) -> int:
    return sum(len(item.get("text", "").split()) for item in segments)


def get_thumbnail_url(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"


def validate_filename_language(value: str) -> str:
    value = (value or "original").strip()
    if value == "original":
        return value
    if not LANGUAGE_CODE_RE.match(value):
        return "original"
    return value
