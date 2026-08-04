import os
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from app import app
from db.storage import DB_PATH, create_user, get_google_ads_connection, get_user_by_email, list_negative_keyword_audit
from services.google_ads_service import (
    GoogleAdsIntegrationError,
    apply_negative_keywords,
    build_google_ads_connect_url,
    build_oauth_flow,
    disconnect_google_ads,
    get_google_ads_status,
    handle_google_ads_oauth_callback,
    validate_date_range,
)
from services.negative_keyword_service import SearchTermRow
from services.ownership import build_owner_context


def reset_negative_keyword_tables():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM negative_keyword_audit")
    cursor.execute("DELETE FROM google_ads_connections")
    cursor.execute("DELETE FROM negative_keyword_settings_v2")
    cursor.execute("DELETE FROM negative_keyword_rules")
    cursor.execute("DELETE FROM negative_keyword_reports")
    conn.commit()
    conn.close()


def owner_key_for_client(client):
    with client.session_transaction() as flask_session:
        browser_session_id = flask_session.get("browser_session_id")
        user_id = flask_session.get("auth_user_id")
    if not browser_session_id:
        client.get("/api/negative-keywords/google-ads/status")
        with client.session_transaction() as flask_session:
            browser_session_id = flask_session.get("browser_session_id")
            user_id = flask_session.get("auth_user_id")
    return build_owner_context(user_id, browser_session_id, True).owner_key


class GoogleAdsIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        reset_negative_keyword_tables()
        user = get_user_by_email("googleads-tests@example.com")
        if not user:
            user_id = create_user("googleads-tests@example.com", generate_password_hash("Password123"))
            user = {"id": user_id, "email": "googleads-tests@example.com"}
        self.csrf_token = "googleads-csrf"
        with self.client.session_transaction() as flask_session:
            flask_session["auth_user_id"] = user["id"]
            flask_session["csrf_token"] = self.csrf_token
            flask_session["browser_session_id"] = "googleads-tests-session"
        original_open = self.client.open

        def open_with_auth(*args, **kwargs):
            method = str(kwargs.get("method", "GET")).upper()
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                headers = kwargs.setdefault("headers", {})
                headers.setdefault("X-CSRF-Token", self.csrf_token)
                headers.setdefault("X-Requested-With", "XMLHttpRequest")
            return original_open(*args, **kwargs)

        self.client.open = open_with_auth

    @patch("app.get_google_ads_status")
    def test_disconnected_state_is_reported(self, status_mock):
        status_mock.return_value = {
            "configured": False,
            "connected": False,
            "has_stored_token": False,
            "missing_configuration": ["GOOGLE_ADS_CLIENT_ID"],
            "dependency_ready": True,
            "redirect_uri": "",
            "reason": "Missing configuration: GOOGLE_ADS_CLIENT_ID",
        }

        response = self.client.get("/api/negative-keywords/google-ads/status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["google_ads"]["connected"])
        self.assertIn("GOOGLE_ADS_CLIENT_ID", data["google_ads"]["missing_configuration"])
        self.assertTrue(data["ownership"]["development_mode"])
        self.assertFalse(data["ownership"]["show_session_warning"])

    def test_authenticated_user_disables_session_warning(self):
        second_user = get_user_by_email("googleads-tests-2@example.com")
        if not second_user:
            user_id = create_user("googleads-tests-2@example.com", generate_password_hash("Password123"))
            second_user = {"id": user_id, "email": "googleads-tests-2@example.com"}
        with self.client.session_transaction() as flask_session:
            flask_session["auth_user_id"] = second_user["id"]
        response = self.client.get("/api/negative-keywords/google-ads/status")
        self.assertEqual(response.status_code, 200)
        ownership = response.get_json()["ownership"]
        self.assertEqual(ownership["owner_type"], "user")
        self.assertTrue(ownership["secure_auth"])
        self.assertFalse(ownership["show_session_warning"])

    @patch("app.build_google_ads_connect_url", return_value="https://example.test/oauth?state=test-state")
    def test_oauth_connect_route_sets_state_and_redirects(self, _connect_url):
        response = self.client.get("/integrations/google-ads/connect", follow_redirects=False)
        self.assertIn(response.status_code, (302, 308))
        self.assertIn("example.test/oauth", response.location)
        with self.client.session_transaction() as flask_session:
            self.assertTrue(flask_session["google_ads_oauth_state"])
            self.assertNotIn("google_ads_oauth_session_id", flask_session)
            self.assertNotIn("google_ads_oauth_user_id", flask_session)

    @patch("app.build_google_ads_connect_url", side_effect=lambda state: f"https://example.test/oauth?state={state}")
    def test_oauth_connect_generates_fresh_state_per_request(self, _connect_url):
        first = self.client.get("/integrations/google-ads/connect", follow_redirects=False)
        self.assertIn(first.status_code, (302, 308))
        with self.client.session_transaction() as flask_session:
            first_state = flask_session["google_ads_oauth_state"]

        second = self.client.get("/integrations/google-ads/connect", follow_redirects=False)
        self.assertIn(second.status_code, (302, 308))
        with self.client.session_transaction() as flask_session:
            second_state = flask_session["google_ads_oauth_state"]

        self.assertNotEqual(first_state, second_state)
        self.assertIn(f"state={second_state}", second.location)

    @patch("services.google_ads_service.Flow")
    def test_oauth_flow_uses_configured_production_redirect_uri(self, flow_mock):
        flow_instance = MagicMock()
        flow_mock.from_client_config.return_value = flow_instance
        redirect_uri = "http://187.127.185.74/integrations/google-ads/callback"
        with patch.dict(os.environ, {
            "GOOGLE_ADS_CLIENT_ID": "client-id.apps.googleusercontent.com",
            "GOOGLE_ADS_CLIENT_SECRET": "client-secret",
            "GOOGLE_ADS_REDIRECT_URI": redirect_uri,
        }, clear=False):
            build_oauth_flow("state-123")

        client_config = flow_mock.from_client_config.call_args[0][0]
        self.assertEqual(client_config["web"]["redirect_uris"], [redirect_uri])
        self.assertEqual(flow_instance.redirect_uri, redirect_uri)
        self.assertNotIn("127.0.0.1", redirect_uri)
        self.assertNotIn("localhost", redirect_uri)

    @patch("services.google_ads_service.build_oauth_flow")
    @patch("services.google_ads_service.ensure_google_ads_configuration")
    @patch("services.google_ads_service.ensure_google_ads_dependencies")
    def test_auth_url_generation_uses_configured_redirect_uri_exactly(self, _deps_mock, _config_mock, flow_mock):
        flow_instance = MagicMock()
        flow_instance.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?redirect_uri=https%3A%2F%2Fapp.audilysis.com%2Fintegrations%2Fgoogle-ads%2Fcallback", None)
        flow_mock.return_value = flow_instance

        auth_url = build_google_ads_connect_url("state-456")

        self.assertIn("redirect_uri=https%3A%2F%2Fapp.audilysis.com%2Fintegrations%2Fgoogle-ads%2Fcallback", auth_url)
        self.assertNotIn("localhost", auth_url)
        self.assertNotIn("127.0.0.1", auth_url)

    def test_callback_clears_state_after_completion(self):
        with self.client.session_transaction() as flask_session:
            flask_session["google_ads_oauth_state"] = "expected-state"

        with patch("app.handle_google_ads_oauth_callback", return_value={"success": True, "connected": True, "account_count": 1}):
            response = self.client.get("/integrations/google-ads/callback?state=expected-state&code=abc123", follow_redirects=False)

        self.assertIn(response.status_code, (302, 308))
        with self.client.session_transaction() as flask_session:
            self.assertNotIn("google_ads_oauth_state", flask_session)

    def test_oauth_callback_rejects_invalid_state(self):
        with self.client.session_transaction() as flask_session:
            flask_session["google_ads_oauth_state"] = "expected-state"

        response = self.client.get("/integrations/google-ads/callback?state=wrong-state&code=abc123")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "google_ads_invalid_state")

    @patch("app.handle_google_ads_oauth_callback", return_value={"success": True, "connected": True, "account_count": 2})
    def test_oauth_callback_success_redirects_back_to_agent(self, _callback):
        with self.client.session_transaction() as flask_session:
            flask_session["google_ads_oauth_state"] = "expected-state"

        response = self.client.get("/integrations/google-ads/callback?state=expected-state&code=abc123", follow_redirects=False)
        self.assertIn(response.status_code, (302, 308))
        self.assertIn("/agents?agent=negative_keyword", response.location)

    @patch("app.list_google_ads_accounts")
    def test_customer_account_retrieval_is_mocked_and_dynamic(self, accounts_mock):
        accounts_mock.return_value = [
            {"customer_id": "1234567890", "name": "Real Customer A", "currency_code": "USD", "time_zone": "America/New_York", "manager": False},
            {"customer_id": "2345678901", "name": "Real Customer B", "currency_code": "CAD", "time_zone": "America/Toronto", "manager": False},
        ]

        response = self.client.get("/api/negative-keywords/google-ads/accounts")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["accounts"]), 2)
        self.assertEqual(data["accounts"][0]["name"], "Real Customer A")

    def test_status_reports_ready_when_env_is_complete(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography is not installed")
        with patch.dict(os.environ, {
            "GOOGLE_ADS_DEVELOPER_TOKEN": "dev-token",
            "GOOGLE_ADS_CLIENT_ID": "client-id.apps.googleusercontent.com",
            "GOOGLE_ADS_CLIENT_SECRET": "client-secret",
            "GOOGLE_ADS_REDIRECT_URI": "http://127.0.0.1:5000/integrations/google-ads/callback",
            "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        }, clear=False):
            status = get_google_ads_status(get_user_by_email("googleads-tests@example.com")["id"], owner_key_for_client(self.client))
        self.assertTrue(status["configured"])
        self.assertEqual(status["missing_configuration"], [])
        self.assertEqual(status["reason"], "Ready")

    @patch("services.google_ads_service.list_google_ads_accounts", return_value=[{"customer_id": "1234567890", "name": "Account", "currency_code": "USD", "time_zone": "UTC", "manager": False}])
    @patch("services.google_ads_service.build_oauth_flow")
    def test_oauth_callback_persists_encrypted_token_per_user(self, build_flow_mock, _accounts_mock):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography is not installed")
        fake_flow = MagicMock()
        fake_flow.credentials = SimpleNamespace(
            refresh_token="refresh-token-123",
            expiry=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
            scopes=["https://www.googleapis.com/auth/adwords"],
        )
        build_flow_mock.return_value = fake_flow
        user = get_user_by_email("googleads-tests@example.com")
        with patch.dict(os.environ, {
            "GOOGLE_ADS_DEVELOPER_TOKEN": "dev-token",
            "GOOGLE_ADS_CLIENT_ID": "client-id.apps.googleusercontent.com",
            "GOOGLE_ADS_CLIENT_SECRET": "client-secret",
            "GOOGLE_ADS_REDIRECT_URI": "http://127.0.0.1:5000/integrations/google-ads/callback",
            "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        }, clear=False):
            result = handle_google_ads_oauth_callback(
                user["id"],
                owner_key_for_client(self.client),
                "oauth-state",
                "http://127.0.0.1:5000/integrations/google-ads/callback?state=oauth-state&code=abc123",
            )
            connection = get_google_ads_connection(user["id"], owner_key_for_client(self.client))

        self.assertTrue(result["success"])
        self.assertTrue(result["connected"])
        self.assertIsNotNone(connection)
        self.assertNotEqual(connection["refresh_token_encrypted"], "refresh-token-123")
        self.assertEqual(connection["user_id"], user["id"])
        self.assertIsNone(connection["token_expiry"])
        self.assertIsNone(connection["scopes"])

    @patch("app.fetch_google_ads_campaigns")
    def test_campaign_retrieval_uses_selected_account(self, campaigns_mock):
        campaigns_mock.return_value = [
            {"campaign_id": "1111111111", "campaign_name": "Dynamic Campaign", "campaign_status": "ENABLED", "campaign_type": "SEARCH"}
        ]

        response = self.client.post("/api/negative-keywords/google-ads/campaigns", json={
            "customer_id": "123-456-7890",
            "search": "Dynamic",
        })
        self.assertEqual(response.status_code, 200)
        campaigns_mock.assert_called_once()
        args = campaigns_mock.call_args[0]
        self.assertEqual(args[2], "123-456-7890")
        self.assertEqual(args[3], "Dynamic")

    @patch("app.fetch_google_ads_search_terms")
    def test_selected_campaign_validation_errors_cleanly(self, search_terms_mock):
        search_terms_mock.side_effect = GoogleAdsIntegrationError(
            "Select at least one campaign.",
            status_code=400,
            error_code="google_ads_missing_campaigns",
        )

        response = self.client.post("/api/negative-keywords/analyse", json={
            "company_name": "Example",
            "customer_id": "1234567890",
            "campaign_ids": [],
            "start_date": "2026-07-01",
            "end_date": "2026-07-07",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "google_ads_missing_campaigns")

    @patch("app.fetch_google_ads_search_terms")
    def test_live_search_term_retrieval_flows_into_analysis(self, search_terms_mock):
        search_terms_mock.return_value = (
            [
                SearchTermRow(
                    search_term="seo jobs",
                    campaign="Real Campaign",
                    ad_group="Core",
                    clicks=5,
                    impressions=50,
                    cost=25.0,
                    conversions=0.0,
                    ctr=0.1,
                    source_row=1,
                    raw={"campaign_id": "1111111111"},
                )
            ],
            {
                "customer_id": "1234567890",
                "campaign_ids": ["1111111111"],
                "date_start": "2026-07-01",
                "date_end": "2026-07-07",
                "parsed_rows": 1,
                "source_rows": 1,
                "file_type": "google_ads_api",
                "filename": "live_google_ads",
            }
        )

        response = self.client.post("/api/negative-keywords/analyse", json={
            "company_name": "Example",
            "customer_id": "1234567890",
            "campaign_ids": ["1111111111"],
            "start_date": "2026-07-01",
            "end_date": "2026-07-07",
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["data_source"], "google_ads_api")
        self.assertEqual(data["data"]["summary"]["negative_count"], 1)

    @patch("app.fetch_google_ads_campaigns")
    def test_one_browser_session_cannot_reuse_another_sessions_connection(self, campaigns_mock):
        seen_session_ids = []

        def capture_session(user_id, owner_key, customer_id, search):
            seen_session_ids.append(owner_key)
            return []

        campaigns_mock.side_effect = capture_session
        second_client = app.test_client()
        with second_client.session_transaction() as flask_session:
            flask_session["auth_user_id"] = 3
            flask_session["csrf_token"] = self.csrf_token
            flask_session["browser_session_id"] = "googleads-tests-session-2"
        second_original_open = second_client.open

        def second_open(*args, **kwargs):
            method = str(kwargs.get("method", "GET")).upper()
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                headers = kwargs.setdefault("headers", {})
                headers.setdefault("X-CSRF-Token", self.csrf_token)
                headers.setdefault("X-Requested-With", "XMLHttpRequest")
            return second_original_open(*args, **kwargs)

        second_client.open = second_open

        self.client.post("/api/negative-keywords/google-ads/campaigns", json={"customer_id": "1234567890", "search": ""})
        second_client.post("/api/negative-keywords/google-ads/campaigns", json={"customer_id": "1234567890", "search": ""})

        self.assertEqual(len(seen_session_ids), 2)
        self.assertNotEqual(seen_session_ids[0], seen_session_ids[1])

    def test_agent_page_contains_no_hardcoded_example_campaign_names(self):
        response = self.client.get("/agents")
        html = response.data.decode("utf-8")
        for campaign_name in (
            "Australia-SEO",
            "Canada Search Campaign",
            "NZ Search Campaign",
            "USA Search Campaign",
        ):
            self.assertNotIn(campaign_name, html)

    def test_refresh_token_encryption_round_trip(self):
        try:
            from cryptography.fernet import Fernet
            from services import google_ads_service as service
        except ImportError:
            self.skipTest("Google Ads crypto dependencies are not installed")

        with patch.dict(os.environ, {"GOOGLE_ADS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8")}, clear=False):
            encrypted = service.encrypt_refresh_token("refresh-token-123")
            decrypted = service.decrypt_refresh_token(encrypted)

        self.assertNotEqual(encrypted, "refresh-token-123")
        self.assertEqual(decrypted, "refresh-token-123")

    @patch("app.disconnect_google_ads", return_value={"success": True, "connected": False})
    def test_disconnect_route_works(self, disconnect_mock):
        response = self.client.post("/api/negative-keywords/google-ads/disconnect")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["connected"])
        disconnect_mock.assert_called_once()

    def test_disconnect_service_creates_audit_record(self):
        from db.storage import upsert_google_ads_connection
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography is not installed")
        user = get_user_by_email("googleads-tests@example.com")
        with patch.dict(os.environ, {
            "GOOGLE_ADS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("utf-8"),
        }, clear=False):
            upsert_google_ads_connection(
                user_id=user["id"],
                owner_key=owner_key_for_client(self.client),
                refresh_token_encrypted="encrypted-token",
                token_expiry=None,
                scopes=["https://www.googleapis.com/auth/adwords"],
                owner_type="user",
            )
            result = disconnect_google_ads(user["id"], owner_key_for_client(self.client))

        self.assertTrue(result["success"])
        audit = list_negative_keyword_audit(user["id"], owner_key_for_client(self.client))
        self.assertEqual(audit[-1]["action_status"], "disconnected")

    def test_future_dates_are_rejected(self):
        with self.assertRaises(GoogleAdsIntegrationError) as ctx:
            validate_date_range("2026-08-04", "2026-08-05")
        self.assertEqual(ctx.exception.error_code, "google_ads_invalid_date_range")

    @patch("app.apply_negative_keywords", return_value={"success": True, "applied_count": 1, "failed_count": 0, "applied": [], "failed": []})
    def test_apply_requires_explicit_confirmation_route(self, apply_mock):
        response = self.client.post("/api/negative-keywords/google-ads/apply", json={
            "customer_id": "1234567890",
            "confirm": True,
            "recommendations": [{"classification": "NEGATIVE", "campaign_id": "1111111111", "negative_keyword": "jobs", "match_type": "PHRASE"}],
        })
        self.assertEqual(response.status_code, 200)
        apply_mock.assert_called_once()

    def test_apply_service_rejects_missing_confirmation(self):
        with self.assertRaises(GoogleAdsIntegrationError) as ctx:
            apply_negative_keywords(None, "session-a", "1234567890", [{"classification": "NEGATIVE"}], False)
        self.assertEqual(ctx.exception.error_code, "google_ads_apply_confirmation_required")

    @patch("services.google_ads_service.assert_customer_access", return_value="1234567890")
    @patch("services.google_ads_service.build_google_ads_client_for_session")
    def test_only_selected_negative_recommendations_are_applied_and_audited(self, build_client_mock, _access_mock):
        campaign_service = SimpleNamespace(campaign_path=lambda customer_id, campaign_id: f"customers/{customer_id}/campaigns/{campaign_id}")
        criterion_service = MagicMock()
        criterion_service.mutate_campaign_criteria.return_value = SimpleNamespace(results=[SimpleNamespace(resource_name="customers/123/campaignCriteria/456")])

        fake_client = MagicMock()
        fake_client.get_service.side_effect = lambda name: criterion_service if name == "CampaignCriterionService" else campaign_service
        fake_client.get_type.return_value = SimpleNamespace(create=SimpleNamespace(keyword=SimpleNamespace()))
        fake_client.enums.KeywordMatchTypeEnum.PHRASE = "PHRASE"
        build_client_mock.return_value = fake_client

        result = apply_negative_keywords(
            None,
            "session-a",
            "1234567890",
            [
                {"classification": "NEGATIVE", "campaign_id": "1111111111", "campaign": "Campaign A", "negative_keyword": "jobs", "match_type": "PHRASE"},
                {"classification": "REVIEW", "campaign_id": "1111111111", "campaign": "Campaign A", "negative_keyword": "review", "match_type": "PHRASE"},
            ],
            True,
        )

        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        audit = list_negative_keyword_audit(None, "session-a")
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["action_status"], "applied")
        self.assertEqual(audit[0]["negative_keyword"], "jobs")

    def test_audit_route_returns_records(self):
        from db.storage import create_negative_keyword_audit

        user_id = get_user_by_email("googleads-tests@example.com")["id"]
        create_negative_keyword_audit({
            "user_id": user_id,
            "owner_key": owner_key_for_client(self.client),
            "session_id": "session-a",
            "customer_id": "1234567890",
            "campaign_id": "1111111111",
            "campaign_name": "Campaign A",
            "negative_keyword": "jobs",
            "match_type": "PHRASE",
            "action_status": "applied",
            "action_message": "Applied to Google Ads.",
            "recommendation_snapshot": {"search_term": "seo jobs"},
            "upstream_response": {"resource_names": ["x"]},
        })
        response = self.client.get("/api/negative-keywords/google-ads/audit")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["audit"]), 1)

    def test_agent_template_uses_connect_button_script_not_nested_form_markup(self):
        with open("templates/_agent_studio.html", "r", encoding="utf-8") as handle:
            template = handle.read()
        self.assertIn("submitNegativeKeywordGoogleAdsConnect()", template)
        self.assertIn("window.location.href = '/integrations/google-ads/connect';", template)
        self.assertNotIn('action="/integrations/google-ads/connect"', template)


if __name__ == "__main__":
    unittest.main()
