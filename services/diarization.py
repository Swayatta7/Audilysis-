import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

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
    detected_speakers: int = 0
    confidence: int = 0
    timings_ms: dict | None = None
    reason: str = ""


class BaseDiarizer:
    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        raise NotImplementedError


class NoOpDiarizer(BaseDiarizer):
    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        return DiarizationResult(
            enabled=False,
            speaker_labels_available=False,
            segments=segments,
            message="Speaker detection disabled.",
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
            status="Disabled",
            model=PyannoteDiarizer.model_name,
            detected_speakers=0,
            confidence=0,
            timings_ms={"audio_download": 0, "model_loading": 0, "diarization": 0, "total": 0},
            reason=self.reason,
        )


class PyannoteDiarizer(BaseDiarizer):
    model_name = "pyannote/speaker-diarization-3.1"

    def __init__(self, token: str | None = None):
        self.token = token or get_env_value("HUGGINGFACE_TOKEN")

    def apply(self, segments: list[dict], video_id: str | None = None) -> DiarizationResult:
        total_start = time.perf_counter()
        timings = {"audio_download": 0, "model_loading": 0, "diarization": 0, "total": 0}
        logger.info("[DIARIZATION] Enabled")
        if not self.token:
            message = "HUGGINGFACE_TOKEN is not configured."
            logger.error("[DIARIZATION] %s", message)
            timings["total"] = elapsed_ms(total_start)
            return DiarizationResult(True, False, segments, message, "Failed", self.model_name, 0, 0, timings, message)
        if not video_id:
            message = "A validated YouTube video ID is required for diarization."
            logger.error("[DIARIZATION] %s", message)
            timings["total"] = elapsed_ms(total_start)
            return DiarizationResult(True, False, segments, message, "Failed", self.model_name, 0, 0, timings, message)

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
            confidence = estimate_confidence(turns, segments)
            logger.info("[DIARIZATION] Detected %s speaker(s)", speaker_count)

            logger.info("[DIARIZATION] Assigning speaker labels...")
            labeled = merge_speaker_runs(assign_speakers_to_segments(segments, turns))
            labels_available = speaker_count > 1 and any(item.get("speaker") for item in labeled)
            if speaker_count <= 1:
                labeled = [strip_speaker_label(item) for item in labeled]
            logger.info("[DIARIZATION] Speaker labels assigned")

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
                confidence,
                timings,
                reason,
            )
        except Exception as exc:
            timings["total"] = elapsed_ms(total_start)
            logger.exception(
                "[DIARIZATION] Speaker detection failed",
                extra={"exception_class": exc.__class__.__name__, "video_id": video_id, "timings_ms": timings},
            )
            return DiarizationResult(
                True,
                False,
                segments,
                f"{exc.__class__.__name__}: {exc}",
                "Failed",
                self.model_name,
                0,
                0,
                timings,
                f"{exc.__class__.__name__}: {exc}",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _download_audio(self, video_id: str, audio_path: Path) -> None:
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            self._download_audio_with_binary(url, audio_path)
            return

        output_template = str(audio_path.with_suffix(".%(ext)s"))
        options = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        }
        with YoutubeDL(options) as downloader:
            downloader.download([url])
        if not audio_path.exists():
            raise RuntimeError("Audio download did not produce a WAV file.")

    def _download_audio_with_binary(self, url: str, audio_path: Path) -> None:
        command = [
            "yt-dlp",
            "-f",
            "bestaudio/best",
            "--extract-audio",
            "--audio-format",
            "wav",
            "-o",
            str(audio_path.with_suffix(".%(ext)s")),
            url,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def estimate_confidence(turns: list[dict], segments: list[dict]) -> int:
    if not turns or not segments:
        return 0
    labeled = 0
    for segment in segments:
        if best_speaker_for_segment(segment, turns):
            labeled += 1
    return int(round((labeled / max(1, len(segments))) * 100))


def strip_speaker_label(segment: dict) -> dict:
    item = dict(segment)
    item.pop("speaker", None)
    return item


def get_speaker_detection_diagnostics() -> dict:
    pyannote_installed = is_python_module_installed("pyannote.audio")
    ytdlp_installed = is_python_module_installed("yt_dlp") or shutil.which("yt-dlp") is not None
    ffmpeg_installed = shutil.which("ffmpeg") is not None
    token_configured = bool(get_env_value("HUGGINGFACE_TOKEN"))
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
