import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

import requests

from agents.runtime_config import get_env_value
from services.diarization import (
    build_audio_download_strategies,
    get_audio_download_timeout_seconds,
    normalize_audio_download_error,
)


logger = logging.getLogger(__name__)

OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS = 120.0


class AudioTranscriptionError(Exception):
    status_code = 502
    error_code = "audio_transcription_failed"

    def __init__(self, message: str, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code
        if error_code is not None:
            self.error_code = error_code


@dataclass(frozen=True)
class AudioTranscriptionResult:
    language_code: str
    language_name: str
    segments: list[dict]
    status: dict


def get_audio_transcription_diagnostics() -> dict:
    provider = selected_transcription_provider()
    deno_path = shutil.which("deno")
    return {
        "available": provider != "unavailable",
        "provider": provider,
        "openai_api_key": "Configured" if get_env_value("OPENAI_API_KEY") else "Missing",
        "openai_model": get_openai_transcription_model(),
        "faster_whisper": "Installed" if find_spec("faster_whisper") else "Missing",
        "whisper": "Installed" if find_spec("whisper") else "Missing",
        "yt_dlp": "Installed" if find_spec("yt_dlp") or shutil.which("yt-dlp") else "Missing",
        "ffmpeg": "Installed" if shutil.which("ffmpeg") else "Missing",
        "deno": "Installed" if deno_path else "Missing",
    }


def selected_transcription_provider() -> str:
    requested = get_env_value("YOUTUBE_AUDIO_TRANSCRIPTION_PROVIDER").lower()
    if requested in {"openai", "faster_whisper", "whisper", "disabled"}:
        if requested == "disabled":
            return "unavailable"
        if requested == "openai":
            return "openai" if get_env_value("OPENAI_API_KEY") else "unavailable"
        if requested == "faster_whisper":
            return "faster_whisper" if find_spec("faster_whisper") else "unavailable"
        return "whisper" if find_spec("whisper") else "unavailable"
    if get_env_value("OPENAI_API_KEY"):
        return "openai"
    if find_spec("faster_whisper"):
        return "faster_whisper"
    if find_spec("whisper"):
        return "whisper"
    return "unavailable"


def transcribe_youtube_audio(video_id: str) -> AudioTranscriptionResult:
    total_start = time.perf_counter()
    provider = selected_transcription_provider()
    if provider == "unavailable":
        raise AudioTranscriptionError(
            "Audio transcription is not configured. Add OPENAI_API_KEY or install a supported local Whisper provider.",
            status_code=503,
            error_code="audio_transcription_unavailable",
        )
    if not shutil.which("ffmpeg"):
        raise AudioTranscriptionError("ffmpeg is required for audio transcription fallback.", 503, "ffmpeg_missing")

    logger.info(
        "youtube_transcript_audio_fallback_started video_id=%s provider=%s deno=%s",
        video_id,
        provider,
        "yes" if shutil.which("deno") else "no",
    )
    with tempfile.TemporaryDirectory(prefix="audilysis-audio-transcription-") as temp_dir:
        audio_path = Path(temp_dir) / f"{video_id}.wav"
        download_start = time.perf_counter()
        _download_audio_for_transcription(video_id, audio_path)
        audio_size = audio_path.stat().st_size if audio_path.exists() else 0
        logger.info(
            "youtube_transcript_audio_fallback_audio_downloaded video_id=%s elapsed_ms=%s audio_file_size=%s",
            video_id,
            elapsed_ms(download_start),
            audio_size,
        )
        transcription_start = time.perf_counter()
        if provider == "openai":
            segments, language_code = _transcribe_with_openai(audio_path)
        elif provider == "faster_whisper":
            segments, language_code = _transcribe_with_faster_whisper(audio_path)
        else:
            segments, language_code = _transcribe_with_whisper(audio_path)
        logger.info(
            "youtube_transcript_audio_fallback_transcription_completed video_id=%s provider=%s elapsed_ms=%s segment_count=%s",
            video_id,
            provider,
            elapsed_ms(transcription_start),
            len(segments),
        )
    if not segments:
        raise AudioTranscriptionError("Audio transcription produced no transcript segments.", 404, "empty_audio_transcript")
    total_elapsed = elapsed_ms(total_start)
    logger.info(
        "youtube_transcript_audio_fallback_completed video_id=%s provider=%s segment_count=%s total_elapsed_ms=%s",
        video_id,
        provider,
        len(segments),
        total_elapsed,
    )
    language_code = language_code or "und"
    return AudioTranscriptionResult(
        language_code=language_code,
        language_name=language_code,
        segments=segments,
        status={
            "status": "Completed",
            "strategy": "audio_transcription",
            "proxy_used": None,
            "proxy_index": None,
            "fallback_used": True,
            "audio_fallback_used": True,
            "provider": provider,
            "reason": "Transcript generated from accessible YouTube audio because captions were unavailable.",
            "processing_time_ms": total_elapsed,
        },
    )


def _download_audio_for_transcription(video_id: str, audio_path: Path) -> None:
    url = f"https://www.youtube.com/watch?v={video_id}"
    last_error = None
    for index, strategy in enumerate(build_audio_download_strategies(), start=1):
        try:
            logger.info(
                "youtube_transcript_audio_download_attempt video_id=%s strategy=%s proxy_index=%s proxy_host=%s proxy_port=%s cookies=%s",
                video_id,
                strategy["name"],
                index,
                strategy["proxy_host"] or "-",
                strategy["proxy_port"] or "-",
                strategy["cookies_enabled"],
            )
            _download_audio_with_ytdlp(url, audio_path, strategy)
            logger.info(
                "youtube_transcript_audio_download_success video_id=%s strategy=%s proxy_index=%s",
                video_id,
                strategy["name"],
                index,
            )
            return
        except Exception as exc:
            last_error = exc
            error_code, safe_message = normalize_audio_download_error(exc)
            logger.warning(
                "youtube_transcript_audio_download_failure video_id=%s strategy=%s proxy_index=%s error_code=%s",
                video_id,
                strategy["name"],
                index,
                error_code,
            )
            if error_code in {"youtube_private_video", "youtube_members_only", "youtube_age_restricted", "youtube_geo_blocked"}:
                raise AudioTranscriptionError(safe_message, 403 if error_code != "youtube_private_video" else 404, error_code) from exc
            continue
    if last_error is None:
        raise AudioTranscriptionError("No audio download strategy was available.", 503, "audio_download_unavailable")
    error_code, safe_message = normalize_audio_download_error(last_error)
    status = 504 if error_code == "youtube_audio_download_timeout" else 502
    raise AudioTranscriptionError(safe_message, status, error_code) from last_error


def _download_audio_with_ytdlp(url: str, audio_path: Path, strategy: dict) -> None:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise AudioTranscriptionError("yt-dlp is required for audio transcription fallback.", 503, "ytdlp_not_installed") from exc

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(audio_path.with_suffix(".%(ext)s")),
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": get_audio_download_timeout_seconds(),
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 1,
        "ignore_no_formats_error": False,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        "proxy": strategy["proxy_url"] or "",
    }
    deno_path = shutil.which("deno")
    if deno_path:
        options["js_runtimes"] = {"deno": {"path": deno_path}}
    if strategy["cookies_file"]:
        options["cookiefile"] = strategy["cookies_file"]
    with YoutubeDL(options) as downloader:
        downloader.download([url])
    if not audio_path.exists():
        raise RuntimeError("yt-dlp did not produce a WAV file for transcription.")


def _transcribe_with_openai(audio_path: Path) -> tuple[list[dict], str]:
    api_key = get_env_value("OPENAI_API_KEY")
    if not api_key:
        raise AudioTranscriptionError("OPENAI_API_KEY is required for OpenAI audio transcription.", 503, "openai_transcription_key_missing")
    data = {
        "model": get_openai_transcription_model(),
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    try:
        with audio_path.open("rb") as audio_file:
            response = requests.post(
                OPENAI_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": (audio_path.name, audio_file, "audio/wav")},
                timeout=get_transcription_timeout_seconds(),
            )
    except requests.exceptions.Timeout as exc:
        raise AudioTranscriptionError("Audio transcription request timed out.", 504, "audio_transcription_timeout") from exc
    except requests.RequestException as exc:
        raise AudioTranscriptionError("Audio transcription request failed.", 502, "audio_transcription_request_failed") from exc
    if response.status_code == 401:
        raise AudioTranscriptionError("Audio transcription authentication failed.", 502, "audio_transcription_auth_failed")
    if response.status_code >= 400:
        raise AudioTranscriptionError("Audio transcription provider rejected the request.", 502, "audio_transcription_provider_failed")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AudioTranscriptionError("Audio transcription provider returned an invalid response.", 502, "audio_transcription_bad_response") from exc
    return parse_openai_verbose_segments(payload), payload.get("language") or "und"


def parse_openai_verbose_segments(payload: dict) -> list[dict]:
    segments = []
    for item in payload.get("segments") or []:
        start = safe_float(item.get("start"), 0.0)
        end = safe_float(item.get("end"), start + 1.0)
        text = str(item.get("text") or "").strip()
        if text and end > start:
            segments.append({"start": start, "duration": round(end - start, 3), "text": text})
    if segments:
        return segments
    text = str(payload.get("text") or "").strip()
    return [{"start": 0.0, "duration": 3.0, "text": text}] if text else []


def _transcribe_with_faster_whisper(audio_path: Path) -> tuple[list[dict], str]:
    from faster_whisper import WhisperModel

    model = WhisperModel(get_env_value("WHISPER_MODEL_SIZE") or "base", device=get_env_value("WHISPER_DEVICE") or "cpu")
    raw_segments, info = model.transcribe(str(audio_path), vad_filter=True)
    segments = []
    for item in raw_segments:
        text = str(getattr(item, "text", "") or "").strip()
        start = float(getattr(item, "start", 0.0))
        end = float(getattr(item, "end", start + 1.0))
        if text and end > start:
            segments.append({"start": start, "duration": round(end - start, 3), "text": text})
    return segments, getattr(info, "language", "") or "und"


def _transcribe_with_whisper(audio_path: Path) -> tuple[list[dict], str]:
    import whisper

    model = whisper.load_model(get_env_value("WHISPER_MODEL_SIZE") or "base")
    payload = model.transcribe(str(audio_path))
    segments = []
    for item in payload.get("segments") or []:
        start = safe_float(item.get("start"), 0.0)
        end = safe_float(item.get("end"), start + 1.0)
        text = str(item.get("text") or "").strip()
        if text and end > start:
            segments.append({"start": start, "duration": round(end - start, 3), "text": text})
    return segments, payload.get("language") or "und"


def get_openai_transcription_model() -> str:
    return get_env_value("OPENAI_TRANSCRIPTION_MODEL") or DEFAULT_OPENAI_TRANSCRIPTION_MODEL


def get_transcription_timeout_seconds() -> float:
    raw = get_env_value("YOUTUBE_AUDIO_TRANSCRIPTION_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS
    return min(240.0, max(20.0, value))


def safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def elapsed_ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))
