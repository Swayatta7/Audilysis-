import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlparse

from agents.runtime_config import get_env_value


logger = logging.getLogger(__name__)

MIN_SPEAKER_OVERLAP = 0.55
MIN_SPEAKER_RUN_GAP = 0.35
MAX_MERGED_SPEAKER_DURATION = 7.0
MAX_MERGED_SPEAKER_CHARS = 84
REQUIRED_HUGGINGFACE_MODELS = (
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-community-1",
)
REQUIRED_HUGGINGFACE_FILES = (
    ("pyannote/speaker-diarization-3.1", "config.yaml"),
    ("pyannote/segmentation-3.0", "pytorch_model.bin"),
    ("pyannote/speaker-diarization-community-1", "plda/xvec_transform.npz"),
)


@dataclass
class DiarizationResult:
    enabled: bool
    speaker_labels_available: bool
    segments: list[dict]
    message: str = ""
    status: str = "Disabled"
    model: str = ""
    detected_speakers: int | None = None
    confidence: int | None = None
    timings_ms: dict | None = None
    reason: str = ""
    error_code: str | None = None
    confidence_available: bool = False


class BaseDiarizer:
    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        raise NotImplementedError


class NoOpDiarizer(BaseDiarizer):
    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        return DiarizationResult(
            enabled=False,
            speaker_labels_available=False,
            segments=segments,
            message="Speaker detection was not requested.",
            status="Not Run",
            model=PyannoteDiarizer.model_name,
            detected_speakers=None,
            confidence=None,
            timings_ms={"audio_download": 0, "model_loading": 0, "diarization": 0, "total": 0},
            reason="Speaker detection was not requested.",
            error_code=None,
            confidence_available=False,
        )


class UnavailableDiarizer(BaseDiarizer):
    def __init__(self, reason: str, diagnostics: dict):
        self.reason = reason
        self.diagnostics = diagnostics

    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        logger.warning("[DIARIZATION] Speaker detection unavailable: %s", self.reason)
        return DiarizationResult(
            enabled=True,
            speaker_labels_available=False,
            segments=segments,
            message=self.reason,
            status="Failed",
            model=PyannoteDiarizer.model_name,
            detected_speakers=None,
            confidence=None,
            timings_ms={"audio_download": 0, "model_loading": 0, "diarization": 0, "total": 0},
            reason=self.reason,
            error_code="speaker_detection_unavailable",
            confidence_available=False,
        )


class PyannoteDiarizer(BaseDiarizer):
    model_name = "pyannote/speaker-diarization-3.1"

    def __init__(self, token: str | None = None):
        self.token = token or get_env_value("HUGGINGFACE_TOKEN")

    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        total_start = time.perf_counter()
        timings = {"audio_download": 0, "model_loading": 0, "diarization": 0, "total": 0}
        logger.info("[DIARIZATION] Speaker detection enabled")
        if not self.token:
            message = "HUGGINGFACE_TOKEN is not configured."
            logger.error("[DIARIZATION] %s", message)
            timings["total"] = elapsed_ms(total_start)
            return DiarizationResult(True, False, segments, message, "Failed", self.model_name, None, None, timings, message, "huggingface_token_missing", False)
        if not video_id:
            message = "A validated YouTube video ID is required for diarization."
            logger.error("[DIARIZATION] %s", message)
            timings["total"] = elapsed_ms(total_start)
            return DiarizationResult(True, False, segments, message, "Failed", self.model_name, None, None, timings, message, "missing_video_id", False)

        temp_dir = tempfile.mkdtemp(prefix="audilysis-diarization-")
        try:
            audio_path = Path(temp_dir) / f"{video_id}.wav"
            logger.info("[DIARIZATION] Downloading audio...")
            step_start = time.perf_counter()
            self._download_audio(video_id, audio_path)
            timings["audio_download"] = elapsed_ms(step_start)
            logger.info("[DIARIZATION] Audio downloaded")

            logger.info("[DIARIZATION] Loading pyannote model...")
            step_start = time.perf_counter()
            pipeline = self._load_pyannote_model()
            timings["model_loading"] = elapsed_ms(step_start)

            logger.info("[DIARIZATION] Running diarization...")
            step_start = time.perf_counter()
            turns = self._run_pyannote(audio_path, pipeline)
            timings["diarization"] = elapsed_ms(step_start)
            speaker_count = len({turn["speaker"] for turn in turns})
            logger.info("[DIARIZATION] Detected %s speaker(s)", speaker_count)

            logger.info("[DIARIZATION] Assigning speaker labels...")
            labeled = merge_speaker_runs(assign_speakers_to_segments(segments, turns))
            labels_available = speaker_count > 1 and any(item.get("speaker") for item in labeled)
            if speaker_count <= 1:
                labeled = [strip_speaker_label(item) for item in labeled]
            logger.info("[DIARIZATION] Speaker labeling completed")

            timings["total"] = elapsed_ms(total_start)
            logger.info("[DIARIZATION] Completed successfully")
            logger.info("[DIARIZATION] Processing time: %s ms", timings["total"])
            reason = (
                "Only one speaker was detected, so speaker labels were intentionally not added because no speaker changes exist."
                if speaker_count <= 1
                else "Speaker labels were assigned from pyannote diarization turns."
            )
            return DiarizationResult(
                True,
                labels_available,
                labeled,
                "Speaker detection completed.",
                "Completed",
                self.model_name,
                speaker_count,
                None,
                timings,
                reason,
                None,
                False,
            )
        except Exception as exc:
            timings["total"] = elapsed_ms(total_start)
            logger.exception(
                "[DIARIZATION] Speaker detection failed",
                extra={"exception_class": exc.__class__.__name__, "video_id": video_id, "timings_ms": timings},
            )
            error_code, safe_message = normalize_audio_download_error(exc)
            return DiarizationResult(
                True,
                False,
                segments,
                safe_message,
                "Failed",
                self.model_name,
                None,
                None,
                timings,
                safe_message,
                error_code,
                False,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _download_audio(self, video_id: str, audio_path: Path) -> None:
        url = f"https://www.youtube.com/watch?v={video_id}"
        last_error = None
        for index, strategy in enumerate(build_audio_download_strategies(), start=1):
            try:
                logger.info("[DIARIZATION] Audio proxy attempt strategy=%s proxy_index=%s proxy_host=%s proxy_port=%s", strategy["name"], index, strategy["proxy_host"] or "-", strategy["proxy_port"] or "-")
                if strategy["cookies_enabled"]:
                    logger.info("[DIARIZATION] Audio cookie auth enabled")
                self._download_audio_with_strategy(url, audio_path, strategy)
                logger.info("[DIARIZATION] Audio downloaded successfully")
                return
            except Exception as exc:
                last_error = exc
                error_code, safe_message = normalize_audio_download_error(exc)
                logger.warning(
                    "[DIARIZATION] Audio download failure strategy=%s proxy_index=%s error_code=%s",
                    strategy["name"],
                    index,
                    error_code,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("Audio download strategy list was empty.")

    def _download_audio_with_strategy(self, url: str, audio_path: Path, strategy: dict) -> None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            self._download_audio_with_binary(url, audio_path, strategy)
            return

        output_template = str(audio_path.with_suffix(".%(ext)s"))
        options = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": get_audio_download_timeout_seconds(),
            "retries": 1,
            "fragment_retries": 1,
            "extractor_retries": 1,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        }
        if strategy["proxy_url"]:
            options["proxy"] = strategy["proxy_url"]
        if strategy["cookies_file"]:
            options["cookiefile"] = strategy["cookies_file"]
        with YoutubeDL(options) as downloader:
            downloader.download([url])
        if not audio_path.exists():
            raise RuntimeError("Audio download did not produce a WAV file.")

    def _download_audio_with_binary(self, url: str, audio_path: Path, strategy: dict) -> None:
        command = [
            "yt-dlp",
            "-f",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "wav",
            "--socket-timeout",
            str(int(get_audio_download_timeout_seconds())),
            "--retries",
            "1",
            "--fragment-retries",
            "1",
            "--extractor-retries",
            "1",
            "-o",
            str(audio_path.with_suffix(".%(ext)s")),
        ]
        if strategy["proxy_url"]:
            command.extend(["--proxy", strategy["proxy_url"]])
        if strategy["cookies_file"]:
            command.extend(["--cookies", strategy["cookies_file"]])
        command.append(url)
        result = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "yt-dlp failed to download audio.")
        if not audio_path.exists():
            raise RuntimeError("yt-dlp did not produce a WAV file.")

    def _load_pyannote_model(self):
        from pyannote.audio import Pipeline

        return Pipeline.from_pretrained(self.model_name, token=self.token)

    def _run_pyannote(self, audio_path: Path, pipeline=None) -> list[dict]:
        if pipeline is None:
            pipeline = self._load_pyannote_model()
        output = pipeline(str(audio_path))
        annotation = getattr(output, "speaker_diarization", output)
        turns = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            turns.append({"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)})
        return turns


def get_diarizer(enabled: bool = False) -> BaseDiarizer:
    if not enabled:
        return NoOpDiarizer()
    diagnostics = get_speaker_detection_diagnostics()
    if not diagnostics["available"]:
        return UnavailableDiarizer(diagnostics["reason"], diagnostics)
    logger.info("[DIARIZATION] get_diarizer() selected PyannoteDiarizer")
    return PyannoteDiarizer()


def assign_speakers_to_segments(segments: list[dict], turns: list[dict]) -> list[dict]:
    if not turns:
        return segments
    speaker_map = stable_speaker_map(turns)
    labeled = []
    previous_speaker = None
    previous_end = None
    for segment in segments:
        speaker = best_speaker_for_segment(segment, turns)
        if previous_speaker and speaker == previous_speaker and previous_end is not None:
            if segment["start"] - previous_end <= MIN_SPEAKER_RUN_GAP:
                speaker = previous_speaker
        item = dict(segment)
        if speaker:
            item["speaker"] = speaker_map[speaker]
        labeled.append(item)
        previous_speaker = speaker
        previous_end = segment.get("end")
    return labeled


def best_speaker_for_segment(segment: dict, turns: list[dict]) -> str | None:
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    duration = max(0.001, end - start)
    overlaps = {}
    for turn in turns:
        overlap = max(0.0, min(end, turn["end"]) - max(start, turn["start"]))
        if overlap > 0:
            overlaps[turn["speaker"]] = overlaps.get(turn["speaker"], 0.0) + overlap
    if not overlaps:
        return None
    speaker, overlap = max(overlaps.items(), key=lambda item: item[1])
    return speaker if overlap / duration >= MIN_SPEAKER_OVERLAP else None


def stable_speaker_map(turns: list[dict]) -> dict[str, str]:
    ordered = []
    for turn in sorted(turns, key=lambda item: item["start"]):
        if turn["speaker"] not in ordered:
            ordered.append(turn["speaker"])
    return {speaker: f"Speaker {index}" for index, speaker in enumerate(ordered, start=1)}


def merge_speaker_runs(segments: list[dict]) -> list[dict]:
    merged = []
    for segment in segments:
        if not merged:
            merged.append(dict(segment))
            continue
        previous = merged[-1]
        same_speaker = previous.get("speaker") and previous.get("speaker") == segment.get("speaker")
        gap = float(segment.get("start", 0.0)) - float(previous.get("end", 0.0))
        candidate_text = f"{previous.get('text', '')} {segment.get('text', '')}".strip()
        candidate_duration = float(segment.get("end", segment.get("start", 0.0))) - float(previous.get("start", 0.0))
        if same_speaker and gap <= MIN_SPEAKER_RUN_GAP and candidate_duration <= MAX_MERGED_SPEAKER_DURATION and len(candidate_text) <= MAX_MERGED_SPEAKER_CHARS:
            previous["text"] = candidate_text
            previous["end"] = segment["end"]
            previous["duration"] = round(previous["end"] - previous["start"], 3)
        else:
            merged.append(dict(segment))
    return merged


def elapsed_ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


def get_audio_download_timeout_seconds() -> float:
    raw = get_env_value("YOUTUBE_AUDIO_DOWNLOAD_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw else 15.0
    except ValueError:
        value = 15.0
    return min(60.0, max(5.0, value))


def get_youtube_cookies_file() -> str:
    return get_env_value("YOUTUBE_COOKIES_FILE").strip()


def get_cookie_file_diagnostics() -> dict:
    cookie_path = get_youtube_cookies_file()
    if not cookie_path:
        return {"configured": False, "exists": False, "readable": False, "path": ""}
    path = Path(cookie_path)
    return {
        "configured": True,
        "exists": path.exists(),
        "readable": os.access(path, os.R_OK),
        "path": str(path),
    }


def build_audio_download_strategies() -> list[dict]:
    cookie_file = resolve_cookie_file_for_runtime()
    strategies = []
    seen = set()

    def add_strategy(name: str, proxy_url: str | None, cookies_file: str | None) -> None:
        key = (proxy_url or "", cookies_file or "")
        if key in seen:
            return
        seen.add(key)
        parsed = urlparse(proxy_url) if proxy_url else None
        strategies.append({
            "name": name,
            "proxy_url": proxy_url,
            "proxy_host": parsed.hostname if parsed else "",
            "proxy_port": parsed.port if parsed else None,
            "cookies_file": cookies_file,
            "cookies_enabled": bool(cookies_file),
        })

    for index, proxy_url in enumerate(load_audio_proxy_urls(), start=1):
        add_strategy(f"proxy_{index}", proxy_url, cookie_file)
    if not strategies or direct_audio_fallback_enabled():
        add_strategy("direct", None, cookie_file)
    return strategies


def load_audio_proxy_urls() -> list[str]:
    proxy_urls = []
    raw_list = get_env_value("WEBSHARE_PROXY_LIST")
    if raw_list:
        proxy_urls.extend(item.strip() for item in raw_list.split(",") if item.strip())

    list_file = get_env_value("WEBSHARE_PROXY_LIST_FILE")
    if list_file:
        path = Path(list_file)
        try:
            if path.exists() and path.is_file():
                proxy_urls.extend(
                    line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                )
        except OSError:
            logger.warning("[DIARIZATION] Proxy list file unreadable: %s", path)

    single_proxy = get_env_value("WEBSHARE_PROXY").strip()
    if single_proxy:
        proxy_urls.append(single_proxy)

    legacy_username = get_env_value("WEBSHARE_PROXY_USERNAME").strip()
    legacy_password = get_env_value("WEBSHARE_PROXY_PASSWORD").strip()
    if legacy_username and legacy_password:
        legacy_host = get_env_value("WEBSHARE_PROXY_HOST").strip() or "p.webshare.io"
        legacy_port = get_env_value("WEBSHARE_PROXY_PORT").strip() or "80"
        proxy_urls.append(f"http://{legacy_username}:{legacy_password}@{legacy_host}:{legacy_port}")

    generic_http = get_env_value("YOUTUBE_PROXY_HTTP_URL").strip()
    generic_https = get_env_value("YOUTUBE_PROXY_HTTPS_URL").strip()
    if generic_https:
        proxy_urls.append(generic_https)
    elif generic_http:
        proxy_urls.append(generic_http)

    deduped = []
    seen = set()
    for proxy_url in proxy_urls:
        if proxy_url and proxy_url not in seen:
            deduped.append(proxy_url)
            seen.add(proxy_url)
    return deduped


def resolve_cookie_file_for_runtime() -> str | None:
    details = get_cookie_file_diagnostics()
    if not details["configured"]:
        return None
    if not details["exists"]:
        raise RuntimeError("Configured YouTube cookies file was not found.")
    if not details["readable"]:
        raise RuntimeError("Configured YouTube cookies file is not readable.")
    return details["path"]


def direct_audio_fallback_enabled() -> bool:
    raw = get_env_value("YOUTUBE_DIRECT_FALLBACK_ENABLED").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def normalize_audio_download_error(exc: Exception) -> tuple[str, str]:
    text = str(exc or "").strip()
    lowered = text.lower()
    if "not a bot" in lowered or "sign in to confirm" in lowered:
        return "youtube_auth_required", "YouTube requires authentication before the audio can be downloaded for speaker detection."
    if "cookies file was not found" in lowered or "configured youtube cookies file was not found" in lowered:
        return "youtube_cookies_invalid", "The configured YouTube cookies file could not be found."
    if "cookies file is not readable" in lowered:
        return "youtube_cookies_invalid", "The configured YouTube cookies file is not readable."
    if "members-only" in lowered or "members only" in lowered:
        return "youtube_members_only", "This video is members-only, so audio could not be downloaded for speaker detection."
    if "age-restricted" in lowered or "age restricted" in lowered:
        return "youtube_age_restricted", "This video is age-restricted, so audio could not be downloaded for speaker detection."
    if "private video" in lowered or "private" in lowered:
        return "youtube_private_video", "This private video could not be downloaded for speaker detection."
    if "geo" in lowered and "blocked" in lowered:
        return "youtube_geo_blocked", "This video is geo-blocked for audio download."
    if "timed out" in lowered or "timeout" in lowered:
        return "youtube_audio_download_timeout", "The audio download timed out before speaker detection could run."
    if "proxy" in lowered and "407" in lowered:
        return "youtube_proxy_auth_failed", "Proxy authentication failed while downloading audio for speaker detection."
    if "proxy" in lowered:
        return "youtube_proxy_connection_failed", "The configured proxy failed while downloading audio for speaker detection."
    if "unavailable" in lowered:
        return "youtube_unavailable", "The YouTube audio download is unavailable for this video."
    return "youtube_audio_download_failed", "Speaker detection could not run because the YouTube audio download failed."


def strip_speaker_label(segment: dict) -> dict:
    item = dict(segment)
    item.pop("speaker", None)
    return item


def get_speaker_detection_diagnostics() -> dict:
    pyannote_installed = is_python_module_installed("pyannote.audio")
    ytdlp_installed = is_python_module_installed("yt_dlp") or shutil.which("yt-dlp") is not None
    ffmpeg_installed = shutil.which("ffmpeg") is not None
    token_configured = bool(get_env_value("HUGGINGFACE_TOKEN"))
    cookie_diagnostics = get_cookie_file_diagnostics()
    model_authentication, model_authentication_reason = check_model_authentication() if token_configured else ("Failed", "Missing HUGGINGFACE_TOKEN")

    missing = []
    if not pyannote_installed:
        missing.append("Missing pyannote.audio")
    if not ytdlp_installed:
        missing.append("Missing yt-dlp")
    if not ffmpeg_installed:
        missing.append("Missing ffmpeg")
    if not token_configured:
        missing.append("Missing HUGGINGFACE_TOKEN")
    if token_configured and model_authentication != "Success":
        missing.append("Model authentication failed")

    available = not missing
    return {
        "pyannote_audio": "Installed" if pyannote_installed else "Missing",
        "yt_dlp": "Installed" if ytdlp_installed else "Missing",
        "ffmpeg": "Installed" if ffmpeg_installed else "Missing",
        "huggingface_token": "Configured" if token_configured else "Missing",
        "model_authentication": model_authentication,
        "model_authentication_reason": model_authentication_reason,
        "available": available,
        "status": "Enabled" if available else "Disabled",
        "reason": "Ready" if available else ", ".join(missing),
        "youtube_cookies_file": "Configured" if cookie_diagnostics["configured"] else "Missing",
        "youtube_cookies_exists": "Yes" if cookie_diagnostics["exists"] else "No",
        "youtube_cookies_readable": "Yes" if cookie_diagnostics["readable"] else "No",
        "proxy_count": len(load_audio_proxy_urls()),
        "audio_timeout_seconds": get_audio_download_timeout_seconds(),
        "install_commands": [
            "pip install -r requirements-diarization.txt",
            "sudo apt install ffmpeg -y",
        ],
        "environment_example": "HUGGINGFACE_TOKEN=hf_xxxxxxxxx",
        "required_models": list(REQUIRED_HUGGINGFACE_MODELS),
    }


def log_speaker_detection_diagnostics() -> dict:
    diagnostics = get_speaker_detection_diagnostics()
    logger.warning("========== Speaker Detection Diagnostics ==========")
    logger.warning("pyannote.audio: %s", diagnostics["pyannote_audio"])
    logger.warning("yt-dlp: %s", diagnostics["yt_dlp"])
    logger.warning("ffmpeg: %s", diagnostics["ffmpeg"])
    logger.warning("HUGGINGFACE_TOKEN: %s", diagnostics["huggingface_token"])
    logger.warning("YOUTUBE_COOKIES_FILE: %s", diagnostics["youtube_cookies_file"])
    logger.warning("Model Authentication: %s", diagnostics["model_authentication"])
    logger.warning("Speaker Detection Available: %s", "Yes" if diagnostics["available"] else "No")
    logger.warning("==================================================")
    if diagnostics["huggingface_token"] == "Missing":
        logger.warning("[DIARIZATION] HUGGINGFACE_TOKEN is not configured.")
    if not diagnostics["available"]:
        logger.warning("[DIARIZATION] Speaker detection disabled: %s", diagnostics["reason"])
    return diagnostics


def is_python_module_installed(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def check_model_authentication() -> tuple[str, str]:
    token = get_env_value("HUGGINGFACE_TOKEN")
    if not token:
        return "Failed", "Missing HUGGINGFACE_TOKEN"
    try:
        from huggingface_hub import HfApi, hf_hub_url
        from huggingface_hub.file_download import get_hf_file_metadata

        api = HfApi()
        for model_name in REQUIRED_HUGGINGFACE_MODELS:
            api.model_info(model_name, token=token)
        for model_name, filename in REQUIRED_HUGGINGFACE_FILES:
            get_hf_file_metadata(hf_hub_url(model_name, filename), token=token, timeout=10)
        return "Success", "Authenticated for required pyannote models"
    except Exception as exc:
        logger.warning(
            "[DIARIZATION] Model authentication failed: %s: %s",
            exc.__class__.__name__,
            exc,
        )
        return "Failed", f"{exc.__class__.__name__}: {exc}"
