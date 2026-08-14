import os
import re
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch
import json
from pathlib import Path

from werkzeug.security import generate_password_hash

import db.storage as storage
from app import app, generate_report_content, run_with_sse_heartbeats
from api.dataforseo import query_platform
from db.storage import DB_PATH, create_run, create_user, get_agent_results_for_run, get_run, get_run_provider_results, get_user_by_email, init_db, insert_mention_result
from services.pdf_generator import generate_pdf_report
from services.report_health import evaluate_report_data_health
from services.tracker_interpretation import generate_tracker_interpretation


def reset_tracker_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM agent_results")
    cursor.execute("DELETE FROM mention_results")
    cursor.execute("DELETE FROM competitor_metrics")
    cursor.execute("DELETE FROM run_provider_results")
    cursor.execute("DELETE FROM runs")
    conn.commit()
    conn.close()


class TrackerReportHealthTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        init_db()
        reset_tracker_tables()
        self.client = app.test_client()
        user = get_user_by_email("tracker-tests@example.com")
        if not user:
            user_id = create_user("tracker-tests@example.com", generate_password_hash("Password123"))
            user = {"id": user_id, "email": "tracker-tests@example.com"}
        self.user_id = int(user["id"])
        with self.client.session_transaction() as flask_session:
            flask_session["auth_user_id"] = user["id"]
            flask_session["csrf_token"] = "tracker-csrf"

    def _create_run_with_results(self, rows):
        run_id = create_run("example.com", "Example", "United States", "en", ["competitor1.com"], user_id=self.user_id)
        for row in rows:
            insert_mention_result(run_id=run_id, **row)
        return run_id

    def _pdf_text(self, pdf_bytes):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp_pdf:
            temp_pdf.write(pdf_bytes)
            temp_path = temp_pdf.name
        try:
            return subprocess.check_output(["pdftotext", temp_path, "-"], text=True)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _pdf_text_normalized(self, pdf_bytes):
        return re.sub(r"\s+", " ", self._pdf_text(pdf_bytes)).strip()

    def test_all_platforms_fail_generate_technical_failure_mode(self):
        rows = [
            {
                "keyword": "example brand",
                "platform": platform,
                "mentioned": None,
                "mention_position": None,
                "sources_cited": [],
                "competitor_mentions": {},
                "ai_response_text": "",
                "response_status": "authentication_error",
                "error_category": "authentication_error",
                "error_message": "The platform request could not be authenticated.",
                "has_valid_data": False,
                "retry_recommendation": "Verify API credentials and access, then rerun the audit.",
            }
            for platform in ["google", "chat_gpt", "perplexity", "gemini", "claude"]
        ]
        run_id = self._create_run_with_results(rows)

        report = generate_report_content(run_id)
        self.assertEqual(report["report_mode"], "technical_failure")
        self.assertIsNone(report["stat_brand_sov"])
        self.assertIsNone(report["stat_brand_mentions"])

        pdf_text = self._pdf_text_normalized(generate_pdf_report(run_id))
        self.assertIn("Technical Failure Report", pdf_text)
        self.assertIn("Data Unavailable", pdf_text)
        self.assertNotIn("Estimated Position", pdf_text)

    def test_partial_data_uses_only_valid_results(self):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 2,
                "sources_cited": ["https://example.com/page"],
                "competitor_mentions": {"competitor1.com": False},
                "ai_response_text": "Example brand was recommended here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            },
            {
                "keyword": "example brand",
                "platform": "chat_gpt",
                "mentioned": None,
                "mention_position": None,
                "sources_cited": [],
                "competitor_mentions": {},
                "ai_response_text": "",
                "response_status": "timeout",
                "error_category": "timeout",
                "error_message": "The platform request timed out before completion.",
                "has_valid_data": False,
                "retry_recommendation": "Check network stability and retry the audit.",
            },
        ])
        report = generate_report_content(run_id)
        self.assertEqual(report["report_mode"], "partial")
        self.assertEqual(report["stat_brand_mentions"], 1)
        self.assertEqual(report["stat_brand_sov"], 100.0)
        self.assertEqual(report["report_health"]["failed_platforms"], 1)
        self.assertIsNone(report["trend_data"][0]["brand"] if report["trend_data"] else None)

    def test_all_success_with_zero_mentions_produces_valid_zero_sov(self):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": platform,
                "mentioned": False,
                "mention_position": None,
                "sources_cited": [],
                "competitor_mentions": {"competitor1.com": False},
                "ai_response_text": "No mention of the tracked brand in this valid response.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
            for platform in ["google", "chat_gpt", "perplexity", "gemini", "claude"]
        ])
        report = generate_report_content(run_id)
        self.assertEqual(report["report_mode"], "full")
        self.assertEqual(report["stat_brand_sov"], 0.0)
        self.assertEqual(report["stat_brand_mentions"], 0)

    def test_invalid_run_id_does_not_fall_back_to_latest_dashboard(self):
        valid_run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com"],
                "competitor_mentions": {},
                "ai_response_text": "Example brand appears here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        with self.client.session_transaction() as flask_session:
            flask_session["last_run_id"] = valid_run_id
        response = self.client.get("/dashboard?run_id=999999")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Run Not Found", response.data)
        self.assertNotIn(b"Example", response.data)

    def test_download_report_requires_explicit_or_session_run_not_global_latest(self):
        self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com"],
                "competitor_mentions": {},
                "ai_response_text": "Example brand appears here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        with self.client.session_transaction() as flask_session:
            flask_session.pop("last_run_id", None)
        response = self.client.get("/download-report")
        self.assertEqual(response.status_code, 404)

    def test_partial_source_run_preserves_unavailable_metrics_in_pdf(self):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com"],
                "competitor_mentions": {},
                "ai_response_text": "Example brand appears here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            },
            {
                "keyword": "example brand",
                "platform": "chat_gpt",
                "mentioned": None,
                "mention_position": None,
                "sources_cited": [],
                "competitor_mentions": {},
                "ai_response_text": "",
                "response_status": "timeout",
                "error_category": "timeout",
                "error_message": "The platform request timed out before completion.",
                "has_valid_data": False,
                "retry_recommendation": "Retry later.",
            },
        ])
        report = generate_report_content(run_id)
        self.assertEqual(report["metric_provenance"]["brand_mentions"]["value"], 1)
        self.assertEqual(report["metric_provenance"]["share_of_voice"]["value"], 100.0)
        self.assertEqual(report["metric_provenance"]["api_health"]["value"], 50.0)

    def test_sqlite_migration_preserves_historical_rows_and_adds_tracker_columns(self):
        original_db_path = storage.DB_PATH
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_db_path = Path(temp_dir) / "legacy_tracker.db"
            storage.DB_PATH = temp_db_path
            try:
                conn = sqlite3.connect(temp_db_path)
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    CREATE TABLE runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        brand_domain TEXT NOT NULL,
                        brand_name TEXT NOT NULL,
                        country TEXT NOT NULL,
                        language TEXT NOT NULL,
                        run_date DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute(
                    "INSERT INTO runs (brand_domain, brand_name, country, language, run_date) VALUES (?, ?, ?, ?, ?)",
                    ("legacy-example.com", "Legacy Example", "United States", "en", "2026-01-01 12:00:00"),
                )
                conn.commit()
                conn.close()

                storage.init_db()

                conn = sqlite3.connect(temp_db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT brand_domain, brand_name FROM runs")
                rows = cursor.fetchall()
                self.assertEqual(rows, [("legacy-example.com", "Legacy Example")])
                cursor.execute("PRAGMA table_info(runs)")
                run_columns = {row[1] for row in cursor.fetchall()}
                self.assertIn("user_id", run_columns)
                self.assertIn("high_volume_keywords", run_columns)
                self.assertIn("brand_keywords", run_columns)
                self.assertIn("use_dataforseo", run_columns)
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='run_provider_results'")
                self.assertIsNotNone(cursor.fetchone())
                cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_run_provider_results_run_id'")
                self.assertIsNotNone(cursor.fetchone())
                cursor.execute("SELECT high_volume_keywords, brand_keywords, use_dataforseo, user_id FROM runs WHERE id = 1")
                migrated_row = cursor.fetchone()
                self.assertEqual(migrated_row, (None, None, 1, None))
                conn.close()

                storage.init_db()
                conn = sqlite3.connect(temp_db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM runs")
                self.assertEqual(cursor.fetchone()[0], 1)
                conn.close()
            finally:
                storage.DB_PATH = original_db_path

    def test_empty_response_is_treated_as_invalid(self):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tasks": [{"status_code": 20000, "result": [{"items": [{"markdown": ""}]}]}]
        }
        with patch("services.dataforseo_client.requests.post", return_value=mock_response):
            result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertFalse(result["has_valid_data"])
        self.assertEqual(result["response_status"], "no_data")

    def test_authentication_and_timeout_errors_are_categorized(self):
        auth_response = MagicMock(status_code=401)
        with patch("services.dataforseo_client.requests.post", return_value=auth_response):
            auth_result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertEqual(auth_result["response_status"], "authentication_error")

        with patch("services.dataforseo_client.requests.post", side_effect=__import__("requests").exceptions.Timeout()), patch("services.dataforseo_client.time.sleep", return_value=None):
            timeout_result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertEqual(timeout_result["response_status"], "timeout")

    def test_rate_limit_errors_are_categorized(self):
        rate_limit_response = MagicMock(status_code=429)
        with patch("services.dataforseo_client.requests.post", return_value=rate_limit_response), patch("services.dataforseo_client.time.sleep", return_value=None):
            result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertEqual(result["response_status"], "rate_limit")
        self.assertFalse(result["has_valid_data"])

    def test_uae_and_saudi_are_accepted_in_setup(self):
        for country in ("UAE", "Saudi Arabia"):
            response = self.client.post("/api/run", json={
                "credentials": {"login": "demo", "password": "demo"},
                "config": {
                    "use_dataforseo": True,
                    "brand_domain": "example.com",
                    "brand_name": "Example",
                    "country": country,
                    "language": "en",
                    "competitors": [],
                    "keywords": ["brand query", "product query", "comparison query"],
                },
                "email_settings": {},
            }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
            self.assertEqual(response.status_code, 200, country)
            self.assertEqual(response.get_json()["status"], "success")

    def test_setup_accepts_missing_dataforseo_credentials_when_disabled(self):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")

    def test_setup_requires_dataforseo_credentials_when_enabled(self):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": True,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("DataForSEO credentials are required", response.get_json()["message"])

    @patch("app.collect_crawl_provider")
    def test_stream_sends_initial_event_and_headers_before_slow_work(self, mocked_crawl):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)

        stream_response = self.client.get("/stream", buffered=False)
        self.assertEqual(stream_response.status_code, 200)
        self.assertIn("text/event-stream", stream_response.headers.get("Content-Type", ""))
        self.assertEqual(stream_response.headers.get("Cache-Control"), "no-cache")
        self.assertEqual(stream_response.headers.get("X-Accel-Buffering"), "no")

        first_chunk = next(stream_response.response).decode("utf-8")
        self.assertIn("Stream connected. Starting tracker execution", first_chunk)
        mocked_crawl.assert_not_called()
        stream_response.close()

    def test_run_with_sse_heartbeats_emits_comment_while_work_runs(self):
        def slow_work():
            time.sleep(0.03)
            return "done"

        generator = run_with_sse_heartbeats(slow_work, heartbeat_message="heartbeat test=slow", interval_seconds=0.01)
        first_chunk = next(generator)
        self.assertTrue(first_chunk.startswith(": heartbeat test=slow"))
        result = None
        while True:
            try:
                next(generator)
            except StopIteration as stopped:
                result = stopped.value
                break
        self.assertEqual(result, "done")

    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "unavailable", "reason": "insufficient_field_data", "payload": None})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 92.0, "seo_score": 88.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1}})
    @patch("app.query_platform")
    def test_stream_skips_dataforseo_calls_when_disabled(self, mocked_query_platform, _crawl, _pagespeed, _crux):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["competitor1.com"],
                "high_volume_keywords": ["brand query", "product query"],
                "brand_keywords": ["comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)

        stream_response = self.client.get("/stream")
        self.assertEqual(stream_response.status_code, 200)
        stream_text = stream_response.get_data(as_text=True)
        self.assertIn("DataForSEO was disabled for this run", stream_text)
        mocked_query_platform.assert_not_called()

        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        report = generate_report_content(run_id)
        self.assertEqual(report["source_provenance"]["dataforseo"]["status"], "skipped_by_user")
        self.assertEqual(report["source_provenance"]["crawl"]["status"], "success")
        self.assertEqual(report["source_provenance"]["pagespeed"]["status"], "success")
        self.assertEqual(report["source_provenance"]["crux"]["status"], "unavailable")
        self.assertEqual(report["report_mode"], "partial")
        self.assertIsNone(report["stat_brand_sov"])
        self.assertIsNone(report["stat_brand_mentions"])
        self.assertEqual(report["metric_provenance"]["pagespeed_performance_score"]["value"], 92.0)
        self.assertEqual(report["metric_provenance"]["crawl_pages_crawled"]["value"], 1)
        self.assertFalse(bool(get_run(run_id)["use_dataforseo"]))
        self.assertEqual(get_run(run_id)["high_volume_keywords"], '["brand query", "product query"]')
        self.assertEqual(get_run(run_id)["brand_keywords"], '["comparison query"]')
        self.assertEqual(len(get_run_provider_results(run_id)), 5)

    @patch("services.agent_orchestrator.get_env_value", return_value="")
    @patch("services.agent_orchestrator.run_agent", return_value={
        "success": True,
        "agent": "test agent",
        "summary": "Agent completed.",
        "recommendations": [],
        "data": {"data_source": "verified_run_context_test", "api_used": []},
    })
    @patch("app.generate_tracker_interpretation", return_value={"provider": "openai", "status": "unavailable", "reason": "OPENAI_API_KEY is not configured.", "role": "interpretation_only", "payload": None})
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "unavailable", "reason": "insufficient_field_data", "payload": None})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "failed", "reason": "PageSpeed returned HTTP 500: Lighthouse returned error: Something went wrong.", "payload": {"diagnostics": {"http_status": 500}}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1, "https": True, "title_present": True, "meta_description_present": True, "h1_count": 1}})
    @patch("app.query_platform")
    def test_pagespeed_500_does_not_kill_tracker_stream(self, mocked_query_platform, _crawl, _pagespeed, _crux, _interpretation, _run_agent, _env):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["competitor1.com"],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        stream_text = self.client.get("/stream").get_data(as_text=True)
        self.assertIn("PageSpeed returned HTTP 500", stream_text)
        self.assertIn("Redirecting to Dashboard", stream_text)
        mocked_query_platform.assert_not_called()

        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        report = generate_report_content(run_id)
        self.assertEqual(report["source_provenance"]["pagespeed"]["status"], "failed")
        self.assertEqual(report["source_provenance"]["crawl"]["status"], "success")
        rows = get_agent_results_for_run(run_id, user_id=self.user_id)
        self.assertTrue(rows)

    @patch("services.agent_orchestrator.get_env_value", return_value="")
    @patch("services.agent_orchestrator.run_agent")
    @patch("app.generate_tracker_interpretation", return_value={"provider": "openai", "status": "unavailable", "reason": "OPENAI_API_KEY is not configured.", "role": "interpretation_only", "payload": None})
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "unavailable", "reason": "insufficient_field_data", "payload": None})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 92.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1, "https": True, "title_present": True, "meta_description_present": True, "h1_count": 1}})
    @patch("app.query_platform")
    def test_one_agent_failure_does_not_kill_tracker_stream(self, mocked_query_platform, _crawl, _pagespeed, _crux, _interpretation, mocked_run_agent, _env):
        def run_agent_side_effect(agent_id, payload):
            if agent_id == "technical_audit":
                raise RuntimeError("boom")
            return {
                "success": True,
                "agent": f"{agent_id} test result",
                "agent_id": agent_id,
                "summary": f"{agent_id} completed.",
                "recommendations": [],
                "data": {"data_source": "verified_run_context_test", "api_used": []},
            }

        mocked_run_agent.side_effect = run_agent_side_effect
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["competitor1.com"],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        stream_text = self.client.get("/stream").get_data(as_text=True)
        self.assertIn("Technical Audit Agent -> failed", stream_text)
        self.assertIn("Redirecting to Dashboard", stream_text)
        mocked_query_platform.assert_not_called()

        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        rows = {row["agent_name"]: row for row in get_agent_results_for_run(run_id, user_id=self.user_id)}
        self.assertEqual(rows["technical_audit"]["status"], "failed")
        self.assertEqual(rows["keyword_research"]["status"], "completed")

    @patch("services.agent_orchestrator.get_env_value", return_value="")
    @patch("services.agent_orchestrator.run_agent")
    @patch("app.generate_tracker_interpretation", return_value={"provider": "openai", "status": "unavailable", "reason": "OPENAI_API_KEY is not configured.", "role": "interpretation_only", "payload": None})
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "unavailable", "reason": "insufficient_field_data", "payload": None})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 92.0, "seo_score": 88.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 2, "indexable_pages": 2, "https": True, "title_present": True, "meta_description_present": True, "h1_count": 1}})
    @patch("app.query_platform")
    def test_run_new_tracker_auto_executes_applicable_agents_without_dataforseo_or_openai(self, mocked_query_platform, _crawl, _pagespeed, _crux, _interpretation, mocked_run_agent, _env):
        def agent_success(agent_id, payload):
            return {
                "success": True,
                "agent": f"{agent_id} test result",
                "agent_id": agent_id,
                "summary": f"{agent_id} completed from run context.",
                "recommendations": [],
                "data": {"data_source": "verified_run_context_test", "api_used": []},
            }

        mocked_run_agent.side_effect = agent_success
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["competitor1.com"],
                "high_volume_keywords": ["brand query", "product query"],
                "brand_keywords": ["comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        stream_text = self.client.get("/stream").get_data(as_text=True)
        self.assertIn("[AGENT] Running Technical Audit Agent", stream_text)
        self.assertIn("[AGENT] SERP Analysis Agent -> not run (provider_unavailable)", stream_text)
        mocked_query_platform.assert_not_called()

        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        rows = {row["agent_name"]: row for row in get_agent_results_for_run(run_id, user_id=self.user_id)}
        self.assertEqual(rows["technical_audit"]["status"], "completed")
        self.assertEqual(rows["keyword_research"]["status"], "completed")
        self.assertEqual(rows["keyword_clustering"]["status"], "completed")
        self.assertEqual(rows["serp_analysis"]["status"], "not_run")
        self.assertEqual(rows["serp_analysis"]["result"]["reason_code"], "provider_unavailable")
        self.assertEqual(rows["rank_tracking"]["status"], "not_run")
        self.assertEqual(rows["strategy"]["status"], "not_run")
        self.assertEqual(rows["outreach"]["status"], "not_run")
        self.assertNotIn("credentials", json.dumps(rows).lower())

        called_agents = [call.args[0] for call in mocked_run_agent.call_args_list]
        self.assertIn("technical_audit", called_agents)
        self.assertIn("content_gap", called_agents)
        self.assertNotIn("serp_analysis", called_agents)
        self.assertNotIn("rank_tracking", called_agents)
        self.assertNotIn("strategy", called_agents)

        report = generate_report_content(run_id)
        self.assertGreaterEqual(report["agent_status_summary"]["completed"], 8)
        self.assertGreaterEqual(report["agent_status_summary"]["not_run"], 5)
        pdf_text = self._pdf_text_normalized(generate_pdf_report(run_id))
        self.assertIn("Completed SEO Agent Outputs", pdf_text)
        self.assertIn("technical_audit completed from run context.", pdf_text)

        agent_page = self.client.get(f"/agents?agent=technical_audit&run_id={run_id}")
        self.assertEqual(agent_page.status_code, 200)
        self.assertIn(b"Saved result from Run #", agent_page.data)
        self.assertIn(b"Re-run / Force Refresh", agent_page.data)
        self.assertIn(b"technical_audit completed from run context.", agent_page.data)

    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "success", "reason": None, "payload": {"largest_contentful_paint_ms": 2400, "interaction_to_next_paint_ms": 170, "cumulative_layout_shift": 0.08}})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "unavailable", "reason": "PageSpeed returned HTTP 403.", "payload": None})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1}})
    @patch("app.verify_dataforseo_credentials", return_value={
        "connected": False,
        "status": "authentication_failed",
        "message": "DataForSEO rejected the credentials (HTTP 401).",
        "provider_payload": {
            "provider": "dataforseo",
            "enabled": True,
            "status": "authentication_failed",
            "authentication": "failed",
        },
    })
    @patch("app.query_platform", return_value={
        "text": "",
        "sources_cited": [],
        "has_valid_data": False,
        "response_status": "platform_unavailable",
        "error_category": "platform_unavailable",
        "error_message": "The provider was unavailable for this request.",
        "retry_recommendation": "Check provider status and rerun the audit later.",
    })
    def test_dataforseo_enabled_failure_is_marked_failed(self, _mocked_query_platform, _mocked_verify, _crawl, _pagespeed, _crux):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "demo", "password": "demo"},
            "config": {
                "use_dataforseo": True,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        self.client.get("/stream").get_data(as_text=True)
        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        report = generate_report_content(run_id)
        self.assertEqual(report["source_provenance"]["dataforseo"]["status"], "authentication_failed")
        self.assertEqual(report["source_provenance"]["dataforseo"]["authentication"], "failed")
        self.assertEqual(report["source_provenance"]["crawl"]["status"], "success")
        self.assertEqual(report["source_provenance"]["pagespeed"]["status"], "unavailable")
        self.assertEqual(report["source_provenance"]["crux"]["status"], "success")
        self.assertEqual(report["report_mode"], "partial")
        self.assertIsNone(report["metric_provenance"]["pagespeed_performance_score"]["value"])
        self.assertEqual(report["metric_provenance"]["crux_lcp_ms"]["value"], 2400)

    @patch("services.agent_orchestrator.get_env_value", return_value="test-openai-key")
    @patch("services.agent_orchestrator.run_agent")
    @patch("app.generate_tracker_interpretation", return_value={"provider": "openai", "status": "success", "reason": None, "role": "interpretation_only", "payload": {"summary": "ok"}})
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "success", "reason": None, "payload": {"largest_contentful_paint_ms": 2100}})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 91.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 4, "indexable_pages": 3, "https": True, "title_present": True, "meta_description_present": True, "h1_count": 1}})
    @patch("app.verify_dataforseo_credentials", return_value={
        "connected": True,
        "status": "connected",
        "message": "DataForSEO credentials verified successfully.",
        "provider_payload": {
            "provider": "dataforseo",
            "enabled": True,
            "status": "connected",
            "authentication": "verified",
            "endpoint": "serp/google/organic/live/advanced",
        },
    })
    @patch("app.query_platform", return_value={
        "text": "Example brand appears in this verified response.",
        "sources_cited": ["https://example.com"],
        "has_valid_data": True,
        "response_status": "success",
        "error_category": "success",
        "error_message": "",
        "retry_recommendation": "No retry needed.",
    })
    def test_run_new_tracker_auto_executes_dataforseo_dependent_agents_when_verified(self, mocked_query_platform, _verify, _crawl, _pagespeed, _crux, _interpretation, mocked_run_agent, _env):
        mocked_run_agent.side_effect = lambda agent_id, payload: {
            "success": True,
            "agent": f"{agent_id} test result",
            "agent_id": agent_id,
            "summary": f"{agent_id} completed from verified providers.",
            "recommendations": [],
            "data": {"data_source": "verified_run_context_test", "api_used": ["DataForSEO"] if agent_id in {"serp_analysis", "rank_tracking"} else []},
        }
        response = self.client.post("/api/run", json={
            "credentials": {"login": "demo", "password": "secret-password"},
            "config": {
                "use_dataforseo": True,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["competitor1.com"],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        stream_text = self.client.get("/stream").get_data(as_text=True)
        self.assertIn("DataForSEO verification", stream_text)
        self.assertIn("connected", stream_text)
        self.assertIn("[AGENT] SERP Analysis Agent -> completed", stream_text)

        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        rows = {row["agent_name"]: row for row in get_agent_results_for_run(run_id, user_id=self.user_id)}
        self.assertEqual(rows["serp_analysis"]["status"], "completed")
        self.assertEqual(rows["rank_tracking"]["status"], "completed")
        self.assertEqual(rows["strategy"]["status"], "completed")
        self.assertEqual(rows["backlink_verification"]["status"], "not_run")
        self.assertEqual(rows["weekly_report"]["status"], "not_run")
        self.assertNotIn("secret-password", json.dumps(rows))
        self.assertEqual(mocked_query_platform.call_count, 15)

        called_agents = [call.args[0] for call in mocked_run_agent.call_args_list]
        self.assertIn("serp_analysis", called_agents)
        self.assertIn("rank_tracking", called_agents)
        self.assertIn("strategy", called_agents)

    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "success", "reason": None, "payload": {"largest_contentful_paint_ms": 2100}})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 91.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 4, "indexable_pages": 3}})
    @patch("app.verify_dataforseo_credentials", return_value={
        "connected": True,
        "status": "connected",
        "message": "DataForSEO credentials verified successfully.",
        "provider_payload": {
            "provider": "dataforseo",
            "enabled": True,
            "status": "connected",
            "authentication": "verified",
            "endpoint": "serp/google/organic/live/advanced",
        },
    })
    @patch("app.query_platform", return_value={
        "text": "Example brand appears in this verified response.",
        "sources_cited": ["https://example.com"],
        "has_valid_data": True,
        "response_status": "success",
        "error_category": "success",
        "error_message": "",
        "retry_recommendation": "No retry needed.",
    })
    def test_dataforseo_valid_credentials_mark_provider_connected_then_success(self, mocked_query_platform, _mocked_verify, _crawl, _pagespeed, _crux):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "demo", "password": "secret-password"},
            "config": {
                "use_dataforseo": True,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        self.client.get("/stream").get_data(as_text=True)
        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        report = generate_report_content(run_id)
        provider_rows = get_run_provider_results(run_id)
        dataforseo_row = next(row for row in provider_rows if row["provider"] == "dataforseo")
        self.assertEqual(dataforseo_row["status"], "success")
        self.assertEqual(report["source_provenance"]["dataforseo"]["status"], "success")
        self.assertEqual(report["source_provenance"]["dataforseo"]["authentication"], "verified")
        self.assertEqual(report["stat_brand_mentions"], 15)
        self.assertEqual(report["stat_brand_sov"], 100.0)
        self.assertNotIn("secret-password", json.dumps(report))
        self.assertEqual(mocked_query_platform.call_count, 15)

    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "success", "reason": None, "payload": {"largest_contentful_paint_ms": 2000}})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 88.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 2, "indexable_pages": 2}})
    @patch("app.verify_dataforseo_credentials", return_value={
        "connected": False,
        "status": "rate_limited",
        "message": "DataForSEO rate limit or quota was reached.",
        "provider_payload": {
            "provider": "dataforseo",
            "enabled": True,
            "status": "rate_limited",
            "authentication": "failed",
        },
    })
    @patch("app.query_platform")
    def test_invalid_dataforseo_verification_stops_follow_up_analysis_calls(self, mocked_query_platform, _mocked_verify, _crawl, _pagespeed, _crux):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "demo", "password": "bad-password"},
            "config": {
                "use_dataforseo": True,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        stream_text = self.client.get("/stream").get_data(as_text=True)
        self.assertIn("DataForSEO authentication failed or the provider was unavailable", stream_text)
        mocked_query_platform.assert_not_called()
        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        report = generate_report_content(run_id)
        self.assertEqual(report["source_provenance"]["dataforseo"]["status"], "rate_limited")
        self.assertIsNone(report["stat_brand_mentions"])
        self.assertIsNone(report["stat_brand_sov"])

    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "unavailable", "reason": "insufficient_field_data", "payload": None})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "unavailable", "reason": "PageSpeed returned HTTP 403.", "payload": None})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "failed", "reason": "Crawl failed", "payload": None})
    @patch("app.query_platform")
    def test_total_factual_collection_failure_remains_technical_failure(self, mocked_query_platform, _crawl, _pagespeed, _crux):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": [],
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        self.client.get("/stream").get_data(as_text=True)
        mocked_query_platform.assert_not_called()
        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]
        report = generate_report_content(run_id)
        self.assertEqual(report["report_mode"], "technical_failure")
        self.assertIsNone(report["metric_provenance"]["pagespeed_performance_score"]["value"])
        self.assertIsNone(report["metric_provenance"]["crux_lcp_ms"]["value"])

    def test_keyword_categories_are_preserved_in_setup_payload(self):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["https://competitor1.com/path"],
                "high_volume_keywords": ["best seo software", "ai tracker"],
                "brand_keywords": ["audilysis review", "audilysis pricing"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as flask_session:
            config = flask_session["tracker_config"]
        self.assertEqual(config["brand_domain"], "example.com")
        self.assertEqual(config["competitors"], ["competitor1.com"])
        self.assertEqual(config["high_volume_keywords"], ["best seo software", "ai tracker"])
        self.assertEqual(config["brand_keywords"], ["audilysis review", "audilysis pricing"])
        self.assertEqual(config["keywords"], ["best seo software", "ai tracker", "audilysis review", "audilysis pricing"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("services.tracker_interpretation.openai_chat_completion", return_value=(
        {"id": "resp"},
        json.dumps({
            "executive_summary": "Verified summary only.",
            "key_findings": ["Finding A"],
            "recommendations": ["Recommendation A"],
            "action_plan": ["Action A"],
        }),
    ))
    def test_openai_interpretation_uses_verified_data_only_prompt(self, mocked_completion):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com"],
                "competitor_mentions": {},
                "ai_response_text": "Example brand appears here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        report = generate_report_content(run_id)
        result = generate_tracker_interpretation(report)
        self.assertEqual(result["status"], "success")
        prompt = mocked_completion.call_args.args[1]
        self.assertIn("Use only the supplied verified data.", prompt)
        self.assertIn("Do not invent, infer or estimate missing factual metrics.", prompt)
        self.assertIn('"share_of_voice": 100.0', prompt)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("services.tracker_interpretation.openai_chat_completion", return_value=(
        None,
        "OpenAI API returned HTTP 500: upstream failure",
    ))
    def test_openai_upstream_failure_is_not_misclassified_as_invalid_json(self, _mocked_completion):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com"],
                "competitor_mentions": {},
                "ai_response_text": "Example brand appears here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        report = generate_report_content(run_id)
        result = generate_tracker_interpretation(report)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "OpenAI API returned HTTP 500: upstream failure")
        self.assertNotEqual(result["reason"], "OpenAI returned invalid JSON.")

    @patch.dict(os.environ, {}, clear=True)
    def test_openai_interpretation_unavailable_without_key(self):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com"],
                "competitor_mentions": {},
                "ai_response_text": "Example brand appears here.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        report = generate_report_content(run_id)
        result = generate_tracker_interpretation(report)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["role"], "interpretation_only")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("services.tracker_interpretation.openai_chat_completion", return_value=(
        None,
        "OpenAI API returned HTTP 500: upstream failure",
    ))
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "unavailable", "reason": "Chrome UX Report returned HTTP 403: API key restrictions blocked this request.", "payload": None})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "unavailable", "reason": "PageSpeed returned HTTP 403: API key restrictions blocked this request.", "payload": None})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1}})
    @patch("app.query_platform")
    def test_dataforseo_disabled_provider_failures_preserve_truthful_unavailable_metrics(self, mocked_query_platform, _crawl, _pagespeed, _crux, _mocked_completion):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "competitors": ["competitor1.com"],
                "high_volume_keywords": ["brand query", "product query"],
                "brand_keywords": ["comparison query"],
            },
            "email_settings": {},
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        self.client.get("/stream").get_data(as_text=True)
        mocked_query_platform.assert_not_called()

        with self.client.session_transaction() as flask_session:
            run_id = flask_session["last_run_id"]

        report = generate_report_content(run_id)
        provider_rows = get_run_provider_results(run_id)
        openai_row = next(row for row in provider_rows if row["provider"] == "openai")

        self.assertEqual(report["report_mode"], "partial")
        self.assertEqual(report["source_provenance"]["dataforseo"]["status"], "skipped_by_user")
        self.assertEqual(report["source_provenance"]["pagespeed"]["status"], "unavailable")
        self.assertEqual(report["source_provenance"]["crux"]["status"], "unavailable")
        self.assertEqual(report["source_provenance"]["openai"]["status"], "failed")
        self.assertEqual(report["source_provenance"]["crawl"]["status"], "success")
        self.assertIsNone(report["stat_total_checks"])
        self.assertIsNone(report["stat_brand_mentions"])
        self.assertIsNone(report["stat_brand_sov"])
        self.assertIsNone(report["stat_api_health"])
        self.assertEqual(report["metric_provenance"]["crawl_pages_crawled"]["value"], 1)
        self.assertIn("disabled for this run", report["visibility_summary_text"])
        self.assertEqual(openai_row["reason"], "OpenAI API returned HTTP 500: upstream failure")

        pdf_text = self._pdf_text_normalized(generate_pdf_report(run_id))
        self.assertIn("Requires DataForSEO", pdf_text)
        self.assertIn("Data Unavailable", pdf_text)
        self.assertIn("Not Run", pdf_text)
        self.assertIn("AI visibility measurement was not performed because DataForSEO was disabled for this run.", pdf_text)
        self.assertNotIn("None%", pdf_text)

    @patch("app.send_report_email")
    @patch("app.generate_tracker_interpretation", return_value={"provider": "openai", "status": "unavailable", "reason": "OPENAI_TRANSCRIPTION", "role": "interpretation_only", "payload": None})
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "success", "reason": None, "payload": {"largest_contentful_paint_ms": 2400}})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 92.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1}})
    def test_auto_email_false_does_not_attempt_smtp(self, _crawl, _pagespeed, _crux, _interpretation, mocked_send_mail):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": "587",
                "sender_email": "sender@example.com",
                "sender_password": "top-secret",
                "recipient_emails": "a@example.com",
                "email_automatically": False,
            },
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        self.client.get("/stream").get_data(as_text=True)
        mocked_send_mail.assert_not_called()

    def test_auto_email_true_requires_backend_fields(self):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {
                "smtp_host": "",
                "smtp_port": "587",
                "sender_email": "sender@example.com",
                "sender_password": "top-secret",
                "recipient_emails": "a@example.com",
                "email_automatically": True,
            },
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Automatic email requires", response.get_json()["message"])

    @patch("app.send_report_email", return_value=(None, "Email authentication failed."))
    @patch("app.generate_tracker_interpretation", return_value={"provider": "openai", "status": "unavailable", "reason": "OPENAI_TRANSCRIPTION", "role": "interpretation_only", "payload": None})
    @patch("app.collect_crux_provider", return_value={"provider": "crux", "status": "success", "reason": None, "payload": {"largest_contentful_paint_ms": 2400}})
    @patch("app.collect_pagespeed_provider", return_value={"provider": "pagespeed", "status": "success", "reason": None, "payload": {"performance_score": 92.0}})
    @patch("app.collect_crawl_provider", return_value={"provider": "crawl", "status": "success", "reason": None, "payload": {"pages_crawled": 1, "indexable_pages": 1}})
    def test_smtp_failure_does_not_corrupt_successful_run(self, _crawl, _pagespeed, _crux, _interpretation, mocked_send_mail):
        response = self.client.post("/api/run", json={
            "credentials": {"login": "", "password": ""},
            "config": {
                "use_dataforseo": False,
                "brand_domain": "example.com",
                "brand_name": "Example",
                "country": "United States",
                "language": "en",
                "keywords": ["brand query", "product query", "comparison query"],
            },
            "email_settings": {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": "587",
                "sender_email": "sender@example.com",
                "sender_password": "top-secret",
                "recipient_emails": "a@example.com, b@example.com",
                "email_automatically": True,
            },
        }, headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        stream_text = self.client.get("/stream").get_data(as_text=True)
        self.assertIn("Auto-email failed", stream_text)
        self.assertIn("Redirecting to Dashboard", stream_text)
        mocked_send_mail.assert_called_once()
        args = mocked_send_mail.call_args.args
        self.assertEqual(args[0], "smtp.gmail.com")
        self.assertEqual(str(args[1]), "587")
        self.assertEqual(args[2], "sender@example.com")
        self.assertEqual(args[4], "a@example.com, b@example.com")
        self.assertNotIn("top-secret", stream_text)

    @patch("app.send_report_email", return_value=("a@example.com, b@example.com", None))
    @patch("app.generate_pdf_report", return_value=b"%PDF-1.4 test report")
    def test_manual_email_route_uses_explicit_run_id(self, _mocked_pdf, mocked_send_mail):
        run_a = self._create_run_with_results([
            {
                "keyword": "brand a",
                "platform": "google",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com/a"],
                "competitor_mentions": {},
                "ai_response_text": "Brand A mention",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        run_b = self._create_run_with_results([
            {
                "keyword": "brand b",
                "platform": "google",
                "mentioned": False,
                "mention_position": None,
                "sources_cited": ["https://example.com/b"],
                "competitor_mentions": {},
                "ai_response_text": "Brand B mention",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            }
        ])
        with self.client.session_transaction() as flask_session:
            flask_session["last_run_id"] = run_b
            flask_session["email_settings"] = {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": "587",
                "sender_email": "sender@example.com",
                "sender_password": "top-secret",
                "recipient_emails": "a@example.com, b@example.com",
            }
        response = self.client.post(
            "/api/email-report",
            json={"run_id": run_a},
            headers={"X-CSRF-Token": "tracker-csrf", "X-Requested-With": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        mocked_send_mail.assert_called_once()
        self.assertIn("Audilysis-2.0-AI-Mention-Report-example.com-", mocked_send_mail.call_args.args[8])

    def test_evaluate_report_data_health_distinguishes_failure_partial_and_full(self):
        technical = evaluate_report_data_health([{"platform": "google", "has_valid_data": False, "response_status": "timeout", "error_category": "timeout", "error_message": "x"}])
        partial = evaluate_report_data_health([
            {"platform": "google", "has_valid_data": True, "response_status": "success", "error_category": "success", "error_message": ""},
            {"platform": "chat_gpt", "has_valid_data": False, "response_status": "timeout", "error_category": "timeout", "error_message": "x"},
        ])
        full = evaluate_report_data_health([{"platform": "google", "has_valid_data": True, "response_status": "success", "error_category": "success", "error_message": ""}])
        self.assertEqual(technical["report_mode"], "technical_failure")
        self.assertEqual(partial["report_mode"], "partial")
        self.assertEqual(full["report_mode"], "full")

    def test_partial_pdf_uses_safe_failure_messages_and_hides_placeholder_metrics(self):
        run_id = self._create_run_with_results([
            {
                "keyword": "example brand",
                "platform": "chat_gpt",
                "mentioned": True,
                "mention_position": 1,
                "sources_cited": ["https://example.com/product"],
                "competitor_mentions": {"competitor1.com": False},
                "ai_response_text": "Example brand is recommended for this search.",
                "response_status": "success",
                "error_category": "success",
                "error_message": "",
                "has_valid_data": True,
                "retry_recommendation": "No retry needed.",
            },
            {
                "keyword": "example brand",
                "platform": "google",
                "mentioned": None,
                "mention_position": None,
                "sources_cited": [],
                "competitor_mentions": {},
                "ai_response_text": "",
                "response_status": "timeout",
                "error_category": "timeout",
                "error_message": "The platform request timed out before completion.",
                "has_valid_data": False,
                "retry_recommendation": "Check network stability and retry the audit.",
            },
        ])
        pdf_text = self._pdf_text_normalized(generate_pdf_report(run_id))
        self.assertIn("The platform request timed out before completion.", pdf_text)
        self.assertIn("Data Unavailable", pdf_text)
        self.assertNotIn("Estimated Position", pdf_text)


if __name__ == "__main__":
    unittest.main()
