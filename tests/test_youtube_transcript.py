import os
import unittest
from unittest.mock import Mock, patch

from app import app
from services import youtube_transcript_service as service


class FakeTranscript:
    def __init__(self, language_code="en", language="English", is_generated=False, segments=None):
        self.language_code = language_code
        self.language = language
        self.is_generated = is_generated
        self._segments = segments or [
            {"start": 0.0, "duration": 2.5, "text": "Hello world"},
            {"start": 2.5, "duration": 3.0, "text": "This is a real mocked transcript"},
        ]

    def fetch(self):
        return self._segments


class FakeFailingThenWorkingTranscript(FakeTranscript):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.calls < 3:
            raise service.requests.exceptions.Timeout("temporary timeout")
        return self._segments


class FakeAlwaysIpBlockedTranscript(FakeTranscript):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def fetch(self):
        self.calls += 1
        raise service.IpBlocked("abcDEF123_4")


class FakeTranscriptList:
    def __init__(self, transcripts):
        self._transcripts = transcripts

    def __iter__(self):
        return iter(self._transcripts)


class FakeNewYouTubeTranscriptApi:
    instances = []

    def __init__(self, proxy_config=None):
        self.proxy_config = proxy_config
        FakeNewYouTubeTranscriptApi.instances.append(self)

    def list(self, video_id):
        return FakeTranscriptList([FakeTranscript()])


class YouTubeTranscriptFeatureTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_extract_video_id_accepts_supported_urls(self):
        expected = "abcDEF123_4"
        self.assertEqual(service.extract_video_id(f"https://www.youtube.com/watch?v={expected}&t=10"), expected)
        self.assertEqual(service.extract_video_id(f"https://youtu.be/{expected}?si=share"), expected)
        self.assertEqual(service.extract_video_id(f"https://youtube.com/shorts/{expected}"), expected)
        self.assertEqual(service.extract_video_id(f"https://youtube.com/embed/{expected}"), expected)
        self.assertEqual(service.extract_video_id(expected), expected)

    def test_extract_video_id_rejects_invalid_inputs(self):
        for value in [
            "https://notyoutube.com/watch?v=abcDEF123_4",
            "https://youtube.example.com/watch?v=abcDEF123_4",
            "https://youtube.com/watch?v=bad",
            "",
        ]:
            with self.assertRaises(service.ValidationError):
                service.extract_video_id(value)

    @patch.object(service, "YouTubeTranscriptApi", FakeNewYouTubeTranscriptApi)
    def test_direct_connection_without_proxy(self):
        FakeNewYouTubeTranscriptApi.instances = []
        with patch.dict(os.environ, {
            "WEBSHARE_PROXY_USERNAME": "",
            "WEBSHARE_PROXY_PASSWORD": "",
            "YOUTUBE_PROXY_HTTP_URL": "",
            "YOUTUBE_PROXY_HTTPS_URL": "",
        }, clear=True):
            result = service.fetch_transcript("abcDEF123_4", "original")
        self.assertEqual(result["segment_count"], 2)
        self.assertIsNone(FakeNewYouTubeTranscriptApi.instances[0].proxy_config)

    def test_webshare_proxy_configuration(self):
        with patch.dict(os.environ, {
            "WEBSHARE_PROXY_USERNAME": "webshare-user",
            "WEBSHARE_PROXY_PASSWORD": "webshare-pass",
            "YOUTUBE_PROXY_HTTP_URL": "",
            "YOUTUBE_PROXY_HTTPS_URL": "",
        }, clear=True):
            proxy_config = service.build_proxy_config()
        self.assertIsInstance(proxy_config, service.WebshareProxyConfig)
        self.assertEqual(proxy_config.proxy_username, "webshare-user")
        self.assertEqual(proxy_config.proxy_password, "webshare-pass")

    @patch.object(service, "YouTubeTranscriptApi")
    def test_webshare_proxy_is_passed_to_youtube_api(self, transcript_api):
        transcript_api.return_value.list.return_value = FakeTranscriptList([FakeTranscript()])
        with patch.dict(os.environ, {
            "WEBSHARE_PROXY_USERNAME": "webshare-user",
            "WEBSHARE_PROXY_PASSWORD": "webshare-pass",
            "YOUTUBE_PROXY_HTTP_URL": "",
            "YOUTUBE_PROXY_HTTPS_URL": "",
        }, clear=True):
            service.fetch_transcript("abcDEF123_4", "original")
        proxy_config = transcript_api.call_args.kwargs["proxy_config"]
        self.assertIsInstance(proxy_config, service.WebshareProxyConfig)

    def test_generic_proxy_configuration(self):
        with patch.dict(os.environ, {
            "WEBSHARE_PROXY_USERNAME": "",
            "WEBSHARE_PROXY_PASSWORD": "",
            "YOUTUBE_PROXY_HTTP_URL": "http://proxy.local:8080",
            "YOUTUBE_PROXY_HTTPS_URL": "http://secure-proxy.local:8080",
        }, clear=True):
            proxy_config = service.build_proxy_config()
        self.assertIsInstance(proxy_config, service.GenericProxyConfig)
        self.assertEqual(proxy_config.to_requests_dict()["http"], "http://proxy.local:8080")
        self.assertEqual(proxy_config.to_requests_dict()["https"], "http://secure-proxy.local:8080")

    def test_missing_optional_proxy_credentials_do_not_break_direct_mode(self):
        with patch.dict(os.environ, {
            "WEBSHARE_PROXY_USERNAME": "webshare-user",
            "WEBSHARE_PROXY_PASSWORD": "",
            "YOUTUBE_PROXY_HTTP_URL": "",
            "YOUTUBE_PROXY_HTTPS_URL": "",
        }, clear=True):
            self.assertIsNone(service.build_proxy_config())
            self.assertEqual(service.get_proxy_mode(), "direct")

    @patch.object(service, "YouTubeTranscriptApi")
    def test_generic_proxy_is_passed_to_youtube_api(self, transcript_api):
        transcript_api.return_value.list.return_value = FakeTranscriptList([FakeTranscript()])
        with patch.dict(os.environ, {
            "WEBSHARE_PROXY_USERNAME": "",
            "WEBSHARE_PROXY_PASSWORD": "",
            "YOUTUBE_PROXY_HTTP_URL": "http://proxy.local:8080",
            "YOUTUBE_PROXY_HTTPS_URL": "http://secure-proxy.local:8080",
        }, clear=True):
            service.fetch_transcript("abcDEF123_4", "original")
        proxy_config = transcript_api.call_args.kwargs["proxy_config"]
        self.assertIsInstance(proxy_config, service.GenericProxyConfig)

    @patch.object(service, "YouTubeTranscriptApi")
    def test_generate_original_transcript_without_google_key(self, transcript_api):
        transcript_api.return_value.list.return_value = FakeTranscriptList([FakeTranscript()])
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/youtube-transcript/generate", json={
                "url": "https://www.youtube.com/watch?v=abcDEF123_4",
                "target_language": "original",
            })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["transcript"]["translated"])
        self.assertEqual(data["transcript"]["video_id"], "abcDEF123_4")
        self.assertEqual(data["transcript"]["segment_count"], 2)

    @patch.object(service, "YouTubeTranscriptApi")
    def test_missing_google_key_only_blocks_translation(self, transcript_api):
        transcript_api.return_value.list.return_value = FakeTranscriptList([FakeTranscript()])
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/youtube-transcript/generate", json={
                "url": "https://www.youtube.com/watch?v=abcDEF123_4",
                "target_language": "es",
            })
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "missing_google_translate_key")

    @patch.object(service.requests, "post")
    @patch.object(service, "YouTubeTranscriptApi")
    def test_translation_preserves_timestamps_and_order(self, transcript_api, post):
        transcript_api.return_value.list.return_value = FakeTranscriptList([FakeTranscript()])
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {
            "data": {"translations": [{"translatedText": "Hola mundo"}, {"translatedText": "Transcripcion real"}]}
        }
        with patch.dict(os.environ, {"GOOGLE_TRANSLATE_API_KEY": "test-key"}, clear=True):
            response = self.client.post("/api/youtube-transcript/generate", json={
                "url": "https://youtu.be/abcDEF123_4",
                "target_language": "es",
            })
        self.assertEqual(response.status_code, 200)
        segments = response.get_json()["transcript"]["segments"]
        self.assertEqual(segments[0]["start"], 0.0)
        self.assertEqual(segments[0]["duration"], 2.5)
        self.assertEqual(segments[0]["text"], "Hola mundo")
        self.assertEqual(segments[1]["start"], 2.5)

    @patch.object(service.requests, "post")
    def test_translation_failure_does_not_return_success(self, post):
        post.return_value = Mock(status_code=500)
        post.return_value.json.return_value = {}
        with patch.dict(os.environ, {"GOOGLE_TRANSLATE_API_KEY": "test-key"}, clear=True):
            response = self.client.post("/api/youtube-transcript/translate", json={
                "target_language": "es",
                "source_language": "en",
                "segments": [{"start": 0, "duration": 1, "text": "Hello"}],
            })
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["success"])

    @patch.object(service, "YouTubeTranscriptApi")
    def test_transcript_error_mapping(self, transcript_api):
        transcript_api.return_value.list.side_effect = service.TranscriptsDisabled("abcDEF123_4")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "transcripts_disabled")

        transcript_api.return_value.list.side_effect = service.NoTranscriptFound("abcDEF123_4", [], [])
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "no_transcript")

        transcript_api.return_value.list.side_effect = service.VideoUnavailable("abcDEF123_4")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "video_unavailable")

        transcript_api.return_value.list.side_effect = service.RequestBlocked("abcDEF123_4")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "youtube_request_blocked")

        transcript_api.return_value.list.side_effect = service.IpBlocked("abcDEF123_4")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "youtube_ip_blocked")

        transcript_api.return_value.list.side_effect = service.TooManyRequests("abcDEF123_4")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "youtube_rate_limited")

        transcript_api.return_value.list.side_effect = service.CouldNotRetrieveTranscript("abcDEF123_4")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "youtube_unavailable")

    @patch.object(service.time, "sleep")
    @patch.object(service, "YouTubeTranscriptApi")
    def test_transient_fetch_errors_retry_at_most_twice(self, transcript_api, sleep):
        transcript = FakeFailingThenWorkingTranscript()
        transcript_api.return_value.list.return_value = FakeTranscriptList([transcript])
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transcript.calls, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch.object(service.time, "sleep")
    @patch.object(service, "YouTubeTranscriptApi")
    def test_ip_blocked_is_not_retried(self, transcript_api, sleep):
        transcript = FakeAlwaysIpBlockedTranscript()
        transcript_api.return_value.list.return_value = FakeTranscriptList([transcript])
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["error"], "youtube_ip_blocked")
        self.assertEqual(transcript.calls, 1)
        sleep.assert_not_called()

    @patch.object(service, "YouTubeTranscriptApi")
    def test_blocked_request_logs_safe_diagnostics(self, transcript_api):
        transcript_api.return_value.list.side_effect = service.IpBlocked("abcDEF123_4")
        with self.assertLogs("services.youtube_transcript_service", level="WARNING") as logs:
            response = self.client.post("/api/youtube-transcript/generate", json={
                "url": "https://youtube.com/watch?v=abcDEF123_4",
                "target_language": "original",
            })
        self.assertEqual(response.status_code, 429)
        joined = "\n".join(logs.output)
        self.assertIn("youtube_transcript_upstream_error", joined)
        self.assertNotIn("GOOGLE_TRANSLATE_API_KEY", joined)
        self.assertNotIn("Hello world", joined)

    @patch.object(service, "YouTubeTranscriptApi")
    def test_youtube_connection_error_is_not_reported_as_missing_dependency(self, transcript_api):
        import requests

        transcript_api.return_value.list.side_effect = requests.exceptions.ConnectionError("DNS failure")
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://youtube.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        data = response.get_json()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(data["error"], "youtube_connection_failed")
        self.assertNotEqual(data["error"], "missing_dependency")
        self.assertIn("Could not connect to YouTube", data["message"])

    def test_download_formatters(self):
        segments = [{"start": 3661.234, "duration": 2.5, "text": "Long video text"}]
        self.assertIn("[01:01:01] Long video text", service.format_transcript_download({
            "video_id": "abcDEF123_4",
            "target_language": "en",
            "segments": segments,
        }, "txt")[0])
        self.assertIn("01:01:01,234 --> 01:01:03,734", service.format_srt(segments))
        self.assertTrue(service.format_vtt(segments).startswith("WEBVTT"))
        with self.assertRaises(service.ValidationError):
            service.format_transcript_download({"video_id": "abcDEF123_4", "segments": segments}, "exe")

    @patch.object(service, "YouTubeTranscriptApi")
    def test_download_endpoint_validates_format_and_returns_file(self, transcript_api):
        transcript_api.return_value.list.return_value = FakeTranscriptList([FakeTranscript()])
        response = self.client.get("/api/youtube-transcript/download/abcDEF123_4?format=srt&lang=original")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.headers["Content-Disposition"])
        self.assertIn("1\n00:00:00,000 --> 00:00:02,500", response.data.decode("utf-8"))

        response = self.client.get("/api/youtube-transcript/download/abcDEF123_4?format=zip&lang=original")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unsupported_format")

    def test_page_routes_and_sidebar_link_load(self):
        response = self.client.get("/youtube-multilingual-transcripter")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Free YouTube Multilingual Transcripter", response.data)
        self.assertIn(b"/api/youtube-transcript/generate", response.data)

        content_response = self.client.get("/content-agents")
        self.assertEqual(content_response.status_code, 200)
        self.assertIn(b"/youtube-multilingual-transcripter", content_response.data)

    def test_health_languages_and_regression_routes(self):
        self.assertEqual(self.client.get("/api/youtube-transcript/health").status_code, 200)
        languages = self.client.get("/api/youtube-transcript/languages")
        self.assertEqual(languages.status_code, 200)
        self.assertTrue(any(item["code"] == "original" for item in languages.get_json()["languages"]))
        self.assertNotIn(b"GOOGLE_TRANSLATE_API_KEY", self.client.get("/youtube-multilingual-transcripter").data)
        self.assertNotIn(self.client.get("/dashboard").status_code, (404, 500))
        self.assertNotIn(self.client.get("/agents").status_code, (404, 500))

    def test_payload_validation(self):
        response = self.client.post("/api/youtube-transcript/generate", json={
            "url": "https://example.com/watch?v=abcDEF123_4",
            "target_language": "original",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_domain")

        response = self.client.post("/api/youtube-transcript/translate", json={
            "target_language": "xx",
            "segments": [{"start": 0, "duration": 1, "text": "Hello"}],
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unsupported_language")


if __name__ == "__main__":
    unittest.main()
