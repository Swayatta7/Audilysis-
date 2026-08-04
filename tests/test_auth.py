import sqlite3
import unittest
from unittest.mock import patch

from app import app
from db.storage import DB_PATH, get_user_by_email
from services.auth import authenticate_user


def delete_user(email: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE lower(email) = lower(?)", (email,))
    conn.commit()
    conn.close()


class AuthenticationTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.email = "auth-tests@example.com"
        self.password = "Password123"
        delete_user(self.email)
        delete_user("auth-tests-2@example.com")

    def _csrf_token(self, path="/login"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as flask_session:
            return flask_session["csrf_token"]

    def test_register_creates_user_with_hashed_password(self):
        csrf_token = self._csrf_token("/register")
        response = self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        }, follow_redirects=False)

        self.assertIn(response.status_code, (302, 308))
        user = get_user_by_email(self.email)
        self.assertIsNotNone(user)
        self.assertNotEqual(user["password_hash"], self.password)
        self.assertTrue(user["password_hash"].startswith(("scrypt:", "pbkdf2:")))

    def test_duplicate_email_is_rejected(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        with self.client.session_transaction() as flask_session:
            logout_token = flask_session["csrf_token"]
        self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=False)

        csrf_token = self._csrf_token("/register")
        response = self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already exists", response.data)

    def test_login_authenticates_existing_user(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        with self.client.session_transaction() as flask_session:
            logout_token = flask_session["csrf_token"]
        self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=False)

        csrf_token = self._csrf_token("/login")
        response = self.client.post("/login", data={
            "email": self.email,
            "password": self.password,
            "csrf_token": csrf_token,
        }, follow_redirects=False)

        self.assertIn(response.status_code, (302, 308))
        with self.client.session_transaction() as flask_session:
            self.assertTrue(flask_session.get("auth_user_id"))

    def test_invalid_credentials_are_rejected(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        with self.client.session_transaction() as flask_session:
            logout_token = flask_session["csrf_token"]
        self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=False)

        csrf_token = self._csrf_token("/login")
        response = self.client.post("/login", data={
            "email": self.email,
            "password": "WrongPassword123",
            "csrf_token": csrf_token,
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid email or password", response.data)

    def test_logout_clears_authenticated_session(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        with self.client.session_transaction() as flask_session:
            logout_token = flask_session["csrf_token"]

        response = self.client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=False)
        self.assertIn(response.status_code, (302, 308))
        with self.client.session_transaction() as flask_session:
            self.assertIsNone(flask_session.get("auth_user_id"))

    def test_protected_page_redirects_to_login(self):
        response = self.client.get("/dashboard", follow_redirects=False)
        self.assertIn(response.status_code, (302, 308))
        self.assertIn("/login", response.location)

    def test_protected_api_requires_authentication(self):
        response = self.client.post("/api/negative-keywords/rules", json={
            "name": "Rule",
            "terms": "jobs",
            "classification": "NEGATIVE",
            "reason": "test",
            "confidence": "HIGH",
            "risk": "LOW",
            "match_type": "PHRASE",
        })
        self.assertEqual(response.status_code, 401)
        data = response.get_json()
        self.assertEqual(data["error"], "Authentication required")
        self.assertEqual(data["error_code"], "authentication_required")
        self.assertFalse(data["ok"])
        self.assertEqual(response.mimetype, "application/json")

    def test_run_agent_requires_authentication_and_returns_json(self):
        response = self.client.post("/run-agent", json={"agent": "keyword_clustering", "keyword_list": "seo audit"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.mimetype, "application/json")
        data = response.get_json()
        self.assertEqual(data["error"], "Authentication required")
        self.assertEqual(data["error_code"], "authentication_required")
        self.assertFalse(data["ok"])

    def test_logout_requires_csrf(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        response = self.client.post("/logout", data={})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"CSRF validation failed", response.data)

    def test_mutation_api_requires_csrf(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        response = self.client.post("/api/negative-keywords/rules", json={
            "name": "Rule",
            "terms": "jobs",
            "classification": "NEGATIVE",
            "reason": "test",
            "confidence": "HIGH",
            "risk": "LOW",
            "match_type": "PHRASE",
        }, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "CSRF validation failed.")
        self.assertEqual(response.get_json()["error_code"], "csrf_failed")

    def test_missing_api_route_returns_json_not_html(self):
        response = self.client.get("/api/does-not-exist", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.mimetype, "application/json")
        self.assertEqual(response.get_json()["error_code"], "not_found")

    def test_run_agent_server_failure_returns_json(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        with self.client.session_transaction() as flask_session:
            api_csrf_token = flask_session["csrf_token"]
        with patch("app.run_agent", side_effect=RuntimeError("boom")):
            response = self.client.post(
                "/run-agent",
                json={"agent": "keyword_clustering", "keyword_list": "seo audit"},
                headers={"X-CSRF-Token": api_csrf_token, "X-Requested-With": "XMLHttpRequest"},
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.mimetype, "application/json")
        data = response.get_json()
        self.assertEqual(data["error"], "Server error")
        self.assertEqual(data["error_code"], "server_error")

    def test_password_hash_authenticates_through_service(self):
        csrf_token = self._csrf_token("/register")
        self.client.post("/register", data={
            "email": self.email,
            "password": self.password,
            "confirm_password": self.password,
            "csrf_token": csrf_token,
        })
        user = authenticate_user(self.email, self.password)
        self.assertEqual(user["email"], self.email)


if __name__ == "__main__":
    unittest.main()
