import os
import unittest
from unittest.mock import Mock, patch

from services import youtube_transcript_service as service
from services.diarization import (
    NoOpDiarizer,
    PyannoteDiarizer,
    UnavailableDiarizer,
    assign_speakers_to_segments,
    get_diarizer,
    get_speaker_detection_diagnostics,
    log_speaker_detection_diagnostics,
    merge_speaker_runs,
)
from services.subtitle_quality import process_transcript_segments
from services.technical_glossary import get_technical_glossary
from services.translation_quality import protect_text, restore_text, validate_translation


class SubtitleQualityTestCase(unittest.TestCase):
    def test_timestamp_normalization_generates_end_times_and_prevents_overlap(self):
        captions = process_transcript_segments([
            {"start": -0.5, "duration": 0.2, "text": "Hello"},
            {"start": 0.4, "duration": 0.0, "text": "world."},
            {"start": 1.0, "duration": 2.0, "text": "Next sentence."},
        ])
        for caption in captions:
            self.assertIn("end", caption)
            self.assertEqual(caption["timing_source"], "youtube_caption")
            self.assertGreater(caption["end"], caption["start"])
            self.assertGreaterEqual(caption["start"], 0)
        for first, second in zip(captions, captions[1:]):
            self.assertLessEqual(first["end"], second["start"] + 0.02)

    def test_sentence_reconstruction_merges_raw_chunks(self):
        captions = process_transcript_segments([
            {"start": 5.1, "duration": 1.2, "text": "This one is on our"},
            {"start": 6.3, "duration": 1.5, "text": "production evaluation list right now."},
        ])
        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0]["start"], 5.1)
        self.assertEqual(captions[0]["end"], 7.8)
        self.assertIn("production", captions[0]["text"])

    def test_two_line_subtitle_formatting_avoids_weak_line_endings(self):
        captions = process_transcript_segments([
            {"start": 0.0, "duration": 5.0, "text": "This one is on our production evaluation list right now."},
        ])
        lines = captions[0]["text"].splitlines()
        self.assertLessEqual(len(lines), 2)
        self.assertTrue(all(len(line) <= 42 for line in lines))
        self.assertNotIn(lines[0].split()[-1].lower().strip(".,:;!?"), {"and", "or", "but", "the", "a", "to", "of", "in"})

    def test_punctuation_optimized_for_option_labels(self):
        captions = process_transcript_segments([
            {"start": 0.0, "duration": 2.0, "text": "Option one, connect your own API Key."},
        ])
        self.assertIn("Option one:", captions[0]["text"])

    def test_grammar_aware_line_breaks_keep_api_key_and_objects_together(self):
        captions = process_transcript_segments([
            {"start": 0.0, "duration": 5.0, "text": "Option one, connect your own API Key and run models offline on your machine."},
        ])
        text = captions[0]["text"]
        self.assertNotIn("API\nKey", text)
        self.assertNotIn("connect\nyour", text)
        self.assertNotIn("your\nown", text)

    def test_srt_and_vtt_use_stored_end_timestamps(self):
        captions = process_transcript_segments([
            {"start": 5.1, "duration": 2.7, "text": "Caption text"},
        ])
        self.assertIn("00:00:05,100 --> 00:00:07,800", service.format_srt(captions))
        self.assertIn("00:00:05.100 --> 00:00:07.800", service.format_vtt(captions))

    def test_speaker_labels_are_not_fabricated(self):
        captions = process_transcript_segments([
            {"start": 0.0, "duration": 2.0, "text": "Welcome to the podcast"},
            {"start": 2.1, "duration": 2.0, "text": "thanks for having me here."},
        ])
        self.assertFalse(any("Speaker 1:" in caption["text"] or "Speaker 2:" in caption["text"] for caption in captions))

    def test_diarization_interface_is_disabled_by_default(self):
        diarizer = get_diarizer(enabled=False)
        result = diarizer.apply([{"start": 0.0, "end": 1.0, "text": "Hello"}])
        self.assertIsInstance(diarizer, NoOpDiarizer)
        self.assertFalse(result.enabled)
        self.assertFalse(result.speaker_labels_available)
        self.assertNotIn("Speaker 1:", result.segments[0]["text"])

    @patch("services.diarization.check_model_authentication", return_value=("Success", "ok"))
    @patch("services.diarization.shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("services.diarization.is_python_module_installed", return_value=True)
    def test_diarization_diagnostics_reports_ready(self, _modules, _which, _auth):
        with patch.dict(os.environ, {"HUGGINGFACE_TOKEN": "token"}, clear=True):
            diagnostics = get_speaker_detection_diagnostics()
        self.assertTrue(diagnostics["available"])
        self.assertEqual(diagnostics["status"], "Enabled")
        self.assertEqual(diagnostics["reason"], "Ready")
        self.assertEqual(diagnostics["model_authentication"], "Success")
        self.assertIn("pyannote/segmentation-3.0", diagnostics["required_models"])

    @patch("services.diarization.shutil.which", return_value=None)
    @patch("services.diarization.is_python_module_installed", return_value=False)
    def test_diarization_diagnostics_reports_missing_requirements(self, _modules, _which):
        with patch.dict(os.environ, {}, clear=True):
            diagnostics = get_speaker_detection_diagnostics()
        self.assertFalse(diagnostics["available"])
        self.assertEqual(diagnostics["status"], "Disabled")
        self.assertIn("Missing HUGGINGFACE_TOKEN", diagnostics["reason"])
        self.assertIn("pip install -r requirements-diarization.txt", diagnostics["install_commands"])

    @patch("services.diarization.shutil.which", return_value=None)
    @patch("services.diarization.is_python_module_installed", return_value=False)
    def test_enabled_diarization_uses_unavailable_diarizer_when_setup_is_missing(self, _modules, _which):
        with patch.dict(os.environ, {}, clear=True):
            diarizer = get_diarizer(enabled=True)
            result = diarizer.apply([{"start": 0.0, "end": 1.0, "text": "Hello"}])
        self.assertIsInstance(diarizer, UnavailableDiarizer)
        self.assertEqual(result.status, "Disabled")
        self.assertIn("Missing HUGGINGFACE_TOKEN", result.reason)

    @patch("services.diarization.shutil.which", return_value=None)
    @patch("services.diarization.is_python_module_installed", return_value=False)
    def test_startup_diagnostics_logs_report(self, _modules, _which):
        with patch.dict(os.environ, {}, clear=True), self.assertLogs("services.diarization", level="WARNING") as logs:
            diagnostics = log_speaker_detection_diagnostics()
        self.assertFalse(diagnostics["available"])
        joined_logs = "\n".join(logs.output)
        self.assertIn("========== Speaker Detection Diagnostics ==========", joined_logs)
        self.assertIn("pyannote.audio: Missing", joined_logs)
        self.assertIn("HUGGINGFACE_TOKEN: Missing", joined_logs)

    def test_speaker_mapping_uses_audio_turn_overlap_only(self):
        segments = [
            {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "Hello"},
            {"start": 2.1, "end": 4.0, "duration": 1.9, "text": "Thanks"},
        ]
        turns = [
            {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_09"},
            {"start": 2.1, "end": 4.0, "speaker": "SPEAKER_02"},
        ]
        labeled = assign_speakers_to_segments(segments, turns)
        self.assertEqual(labeled[0]["speaker"], "Speaker 1")
        self.assertEqual(labeled[1]["speaker"], "Speaker 2")

    def test_consecutive_same_speaker_segments_merge_safely(self):
        merged = merge_speaker_runs([
            {"start": 0.0, "end": 1.5, "duration": 1.5, "speaker": "Speaker 1", "text": "Hello"},
            {"start": 1.6, "end": 3.0, "duration": 1.4, "speaker": "Speaker 1", "text": "there"},
            {"start": 3.5, "end": 5.0, "duration": 1.5, "speaker": "Speaker 2", "text": "Welcome"},
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["text"], "Hello there")

    @patch("services.diarization.shutil.rmtree")
    @patch("services.diarization.tempfile.mkdtemp", return_value="/tmp/audilysis-diarization-test")
    def test_enabled_diarization_cleans_up_temp_audio(self, _mkdtemp, rmtree):
        segments = [
            {"start": 0.0, "end": 1.0, "duration": 1.0, "text": "Hello"},
            {"start": 1.1, "end": 2.0, "duration": 0.9, "text": "Welcome"},
        ]
        diarizer = PyannoteDiarizer(token="token")
        with patch.object(diarizer, "_download_audio"), \
                patch.object(diarizer, "_load_pyannote_model", return_value=object()), \
                patch.object(diarizer, "_run_pyannote", return_value=[
                    {"start": 0.0, "end": 1.0, "speaker": "A"},
                    {"start": 1.1, "end": 2.0, "speaker": "B"},
                ]):
            result = diarizer.apply(segments, video_id="abcDEF123_4")
        self.assertTrue(result.enabled)
        self.assertTrue(result.speaker_labels_available)
        self.assertEqual(result.segments[0]["speaker"], "Speaker 1")
        rmtree.assert_called_once_with("/tmp/audilysis-diarization-test", ignore_errors=True)

    @patch("services.diarization.shutil.rmtree")
    @patch("services.diarization.tempfile.mkdtemp", return_value="/tmp/audilysis-diarization-test")
    def test_enabled_diarization_logs_execution_path_and_timings(self, _mkdtemp, _rmtree):
        segments = [
            {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "Hello"},
            {"start": 2.2, "end": 4.0, "duration": 1.8, "text": "Welcome"},
        ]
        calls = []
        diarizer = PyannoteDiarizer(token="token")

        def download(_video_id, _audio_path):
            calls.append("_download_audio")

        def load_model():
            calls.append("_load_pyannote_model")
            return object()

        def run(_audio_path, _pipeline):
            calls.append("_run_pyannote")
            return [
                {"start": 0.0, "end": 2.0, "speaker": "A"},
                {"start": 2.2, "end": 4.0, "speaker": "B"},
            ]

        with patch.object(diarizer, "_download_audio", side_effect=download), \
                patch.object(diarizer, "_load_pyannote_model", side_effect=load_model), \
                patch.object(diarizer, "_run_pyannote", side_effect=run), \
                self.assertLogs("services.diarization", level="INFO") as logs:
            result = diarizer.apply(segments, video_id="abcDEF123_4")

        self.assertEqual(calls, ["_download_audio", "_load_pyannote_model", "_run_pyannote"])
        self.assertEqual(result.status, "Completed")
        self.assertEqual(result.detected_speakers, 2)
        self.assertEqual(result.confidence, 100)
        self.assertTrue(result.speaker_labels_available)
        self.assertIn("audio_download", result.timings_ms)
        joined_logs = "\n".join(logs.output)
        for message in (
            "[DIARIZATION] Enabled",
            "[DIARIZATION] Downloading audio...",
            "[DIARIZATION] Audio downloaded",
            "[DIARIZATION] Loading pyannote model...",
            "[DIARIZATION] Running diarization...",
            "[DIARIZATION] Detected 2 speaker(s)",
            "[DIARIZATION] Assigning speaker labels...",
            "[DIARIZATION] Speaker labels assigned",
            "[DIARIZATION] Completed successfully",
        ):
            self.assertIn(message, joined_logs)

    @patch("services.diarization.shutil.rmtree")
    @patch("services.diarization.tempfile.mkdtemp", return_value="/tmp/audilysis-diarization-test")
    def test_single_speaker_diarization_reports_reason_without_fake_second_speaker(self, _mkdtemp, _rmtree):
        segments = [{"start": 0.0, "end": 2.0, "duration": 2.0, "text": "Hello"}]
        diarizer = PyannoteDiarizer(token="token")
        with patch.object(diarizer, "_download_audio"), \
                patch.object(diarizer, "_load_pyannote_model", return_value=object()), \
                patch.object(diarizer, "_run_pyannote", return_value=[{"start": 0.0, "end": 2.0, "speaker": "A"}]):
            result = diarizer.apply(segments, video_id="abcDEF123_4")
        self.assertEqual(result.detected_speakers, 1)
        self.assertFalse(result.speaker_labels_available)
        self.assertNotIn("speaker", result.segments[0])
        self.assertIn("Only one speaker was detected", result.reason)

    @patch("services.diarization.shutil.rmtree")
    @patch("services.diarization.tempfile.mkdtemp", return_value="/tmp/audilysis-diarization-test")
    def test_diarization_failure_logs_full_exception_and_returns_reason(self, _mkdtemp, _rmtree):
        segments = [{"start": 0.0, "end": 2.0, "duration": 2.0, "text": "Hello"}]
        diarizer = PyannoteDiarizer(token="token")
        with patch.object(diarizer, "_download_audio", side_effect=RuntimeError("download failed")), \
                self.assertLogs("services.diarization", level="ERROR") as logs:
            result = diarizer.apply(segments, video_id="abcDEF123_4")
        self.assertEqual(result.status, "Failed")
        self.assertIn("RuntimeError: download failed", result.reason)
        self.assertIsNotNone(logs.records[0].exc_info)

    def test_entity_protection_restores_product_names_and_api_key(self):
        protected = protect_text("Connect your own API Key to Nano Banana on GitHub.")
        self.assertNotIn("API Key", protected.text)
        restored = restore_text(protected.text, protected.entities)
        self.assertEqual(restored, "Connect your own API Key to Nano Banana on GitHub.")

    def test_technical_glossary_is_centralized_and_extensible(self):
        glossary = get_technical_glossary()
        for term in ("API Key", "GitHub", "Text to Image", "Text to Video", "AI News Desk", "Dataset", "Embedding", "LLM", "Fine-tuning", "Token"):
            self.assertIn(term, glossary)

    def test_translation_validation_preserves_numbers_and_entities(self):
        protected = protect_text("Nano Banana has 22,000 stars on GitHub.")
        good = restore_text("ZXQAAQXZ has ZXQABQXZ stars on ZXQACQXZ.", protected.entities)
        self.assertEqual(validate_translation("Nano Banana has 22,000 stars on GitHub.", good, protected.entities), [])
        bad = "Nano Banana has 100 members and 22,000 stars on GitHub."
        self.assertIn("numbers_changed", validate_translation("Nano Banana has 22,000 stars on GitHub.", bad, protected.entities))

    @patch.object(service.requests, "post")
    def test_hindi_translation_keeps_technical_terms_in_english(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "data": {"translations": [{"translatedText": "अपना ZXQAAQXZ कनेक्ट करें।"}]}
        }
        with patch.dict(os.environ, {"GOOGLE_TRANSLATE_API_KEY": "test-key"}, clear=True):
            translated = service.translate_segments([
                {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "Connect your own API Key."}
            ], "hi", "en")
        self.assertEqual(translated[0]["text"], "अपना API Key कनेक्ट करें।")

    @patch.object(service.requests, "post")
    def test_context_aware_translation_sends_software_run_as_execute(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "data": {"translations": [{"translatedText": "कुछ ZXQAAQXZ को अपनी ZXQABQXZ पर पूरी तरह ZXQACQXZ चलाइए।"}]}
        }
        with patch.dict(os.environ, {"GOOGLE_TRANSLATE_API_KEY": "test-key"}, clear=True):
            translated = service.translate_segments([
                {"start": 0.0, "end": 3.0, "duration": 3.0, "text": "Run models completely offline on your machine."}
            ], "hi", "en")
        sent_text = post.call_args.kwargs["data"]["q"][0]
        self.assertIn("Execute", sent_text)
        self.assertIn("models", translated[0]["text"])
        self.assertIn("offline", translated[0]["text"])

    @patch.object(service.requests, "post")
    def test_protected_terms_survive_multiple_languages(self, post):
        for language in ("es", "fr", "de", "ar"):
            post.return_value = Mock(status_code=200)
            post.return_value.json.return_value = {
                "data": {"translations": [{"translatedText": f"{language} ZXQAAQXZ ZXQABQXZ ZXQACQXZ"}]}
            }
            with patch.dict(os.environ, {"GOOGLE_TRANSLATE_API_KEY": "test-key"}, clear=True):
                translated = service.translate_segments([
                    {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "OpenAI GitHub 42"}
                ], language, "en")
            self.assertIn("OpenAI", translated[0]["text"])
            self.assertIn("GitHub", translated[0]["text"])
            self.assertIn("42", translated[0]["text"])

    @patch.object(service.requests, "post")
    def test_placeholder_corruption_rejects_translation(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "data": {"translations": [{"translatedText": "अनुवादित API कुंजी"}]}
        }
        with patch.dict(os.environ, {"GOOGLE_TRANSLATE_API_KEY": "test-key"}, clear=True):
            with self.assertRaises(service.UpstreamError):
                service.translate_segments([
                    {"start": 0.0, "end": 2.0, "duration": 2.0, "text": "API Key"}
                ], "hi", "en")


if __name__ == "__main__":
    unittest.main()
