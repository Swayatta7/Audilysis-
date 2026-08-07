import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from app import app, generate_report_content
from api.dataforseo import query_platform
from db.storage import DB_PATH, create_run, create_user, get_user_by_email, init_db, insert_mention_result
from services.pdf_generator import generate_pdf_report
from services.report_health import evaluate_report_data_health


def reset_tracker_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM mention_results")
    cursor.execute("DELETE FROM competitor_metrics")
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
        with self.client.session_transaction() as flask_session:
            flask_session["auth_user_id"] = user["id"]
            flask_session["csrf_token"] = "tracker-csrf"

    def _create_run_with_results(self, rows):
        run_id = create_run("example.com", "Example", "United States", "en", ["competitor1.com"])
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

    def test_empty_response_is_treated_as_invalid(self):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {
            "tasks": [{"status_code": 20000, "result": [{"items": [{"markdown": ""}]}]}]
        }
        with patch("api.dataforseo.requests.post", return_value=mock_response):
            result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertFalse(result["has_valid_data"])
        self.assertEqual(result["response_status"], "no_data")

    def test_authentication_and_timeout_errors_are_categorized(self):
        auth_response = MagicMock(status_code=401)
        with patch("api.dataforseo.requests.post", return_value=auth_response):
            auth_result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertEqual(auth_result["response_status"], "authentication_error")

        with patch("api.dataforseo.requests.post", side_effect=__import__("requests").exceptions.Timeout()):
            timeout_result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertEqual(timeout_result["response_status"], "timeout")

    def test_rate_limit_errors_are_categorized(self):
        rate_limit_response = MagicMock(status_code=429)
        with patch("api.dataforseo.requests.post", return_value=rate_limit_response), patch("api.dataforseo.time.sleep", return_value=None):
            result = query_platform("google", "example brand", {"login": "u", "password": "p"}, "example.com", "Example", [], "United States", "en")
        self.assertEqual(result["response_status"], "rate_limit")
        self.assertFalse(result["has_valid_data"])

    def test_uae_and_saudi_are_accepted_in_setup(self):
        for country in ("UAE", "Saudi Arabia"):
            response = self.client.post("/api/run", json={
                "credentials": {"login": "demo", "password": "demo"},
                "config": {
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
