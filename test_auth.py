import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yojana_api


PROFILE = {
    "age": 32,
    "state": "Maharashtra",
    "annual_income": 180000,
    "gender": "woman",
    "category": "general",
    "occupation": "farmer",
    "owns_land": True,
    "is_student": False,
    "has_lpg": False,
    "has_bank_account": True,
    "is_taxpayer": False,
}


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = yojana_api.DATABASE
        self.original_firebase_auth_configured = yojana_api.FIREBASE_AUTH_CONFIGURED
        self.original_firebase_api_key = yojana_api.FIREBASE_API_KEY
        self.original_gemini_api_key = yojana_api.GEMINI_API_KEY
        self.original_gemini_model = yojana_api.GEMINI_MODEL
        yojana_api.DATABASE = Path(self.temp_dir.name) / "auth-test.sqlite3"
        yojana_api.SESSIONS.clear()
        yojana_api.ASSISTANT_REQUESTS.clear()
        yojana_api.GEMINI_API_KEY = ""
        yojana_api.initialize_database()
        self.server = yojana_api.ThreadingHTTPServer(("127.0.0.1", 0), yojana_api.YojanaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        self.cookie = ""

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        yojana_api.SESSIONS.clear()
        yojana_api.DATABASE = self.original_database
        yojana_api.FIREBASE_AUTH_CONFIGURED = self.original_firebase_auth_configured
        yojana_api.FIREBASE_API_KEY = self.original_firebase_api_key
        yojana_api.GEMINI_API_KEY = self.original_gemini_api_key
        yojana_api.GEMINI_MODEL = self.original_gemini_model
        yojana_api.ASSISTANT_REQUESTS.clear()
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None, *, origin=None, forwarded_proto=None):
        headers = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if origin is not None:
            headers["Origin"] = origin
        if forwarded_proto is not None:
            headers["X-Forwarded-Proto"] = forwarded_proto
        if self.cookie:
            headers["Cookie"] = self.cookie
        self.connection.request(
            method,
            path,
            body=json.dumps(payload) if payload is not None else None,
            headers=headers,
        )
        response = self.connection.getresponse()
        body = response.read()
        set_cookie = response.getheader("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        data = json.loads(body) if body else {}
        return response.status, response.getheaders(), data

    def sign_in_phone(self, local_id="test-phone-user"):
        with patch.object(
            yojana_api,
            "verify_firebase_id_token",
            return_value={
                "id": f"firebase:{local_id}",
                "email": "",
                "phone": "+919876543210",
                "name": "Test phone user",
                "demo": False,
            },
        ):
            status, headers, data = self.request(
                "POST", "/api/auth/firebase", {"id_token": "mock-phone-token"}
            )
        self.assertEqual(status, 200)
        cookie = dict(headers)["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(data["user"]["phone"], "+919876543210")

    def test_scheme_and_helper_apis_require_a_session(self):
        status, _, data = self.request("GET", "/api/states")
        self.assertEqual(status, 401)
        self.assertEqual(data["code"], "authentication_required")
        status, _, data = self.request("GET", "/api/schemes")
        self.assertEqual(status, 401)
        self.assertEqual(data["code"], "authentication_required")
        status, _, _ = self.request("POST", "/api/eligibility/check", PROFILE)
        self.assertEqual(status, 401)

    def test_scheme_catalog_includes_rules_documents_and_application_steps(self):
        self.sign_in_phone()
        status, _, schemes = self.request("GET", "/api/schemes")
        self.assertEqual(status, 200)
        self.assertEqual(len(schemes), 5)
        pm_kisan = next(scheme for scheme in schemes if scheme["id"] == "pm-kisan")
        self.assertTrue(pm_kisan["rules"]["requires_land"])
        self.assertIn("Landholding records", pm_kisan["documents"])
        self.assertTrue(pm_kisan["steps"])
        self.assertTrue(pm_kisan["official_url"].startswith("https://"))
        self.assertEqual(pm_kisan["beneficiary_stat"]["value"], 94_465_646)
        self.assertIn("Apr–Jul 2026–27", pm_kisan["beneficiary_stat"]["label"])
        self.assertEqual(
            pm_kisan["beneficiary_stat"]["source_url"],
            "https://pmkisan.gov.in/",
        )

    def test_auth_config_and_routes_offer_phone_auth_only(self):
        status, _, config = self.request("GET", "/api/auth/config")
        self.assertEqual(status, 200)
        self.assertIn("firebase_configured", config)
        self.assertFalse(config["assistant_configured"])
        self.assertNotIn("api_key", config)
        self.assertNotIn("google_client_id", config)
        status, _, _ = self.request(
            "POST", "/api/auth/google", {"credential": "obsolete-token"}
        )
        self.assertEqual(status, 404)
        status, _, _ = self.request("POST", "/api/auth/demo", {})
        self.assertEqual(status, 404)

    def test_assistant_requires_auth_and_validates_multilingual_messages(self):
        payload = {"language": "hi", "messages": [{"role": "user", "text": "मदद चाहिए"}]}
        status, _, data = self.request("POST", "/api/assistant/chat", payload)
        self.assertEqual(status, 401)
        self.assertEqual(data["code"], "authentication_required")

        self.sign_in_phone()
        status, _, data = self.request("POST", "/api/assistant/chat", payload)
        self.assertEqual(status, 503)
        self.assertIn("not configured", data["error"].lower())

        with patch.object(yojana_api, "generate_assistant_reply", return_value="नमस्ते"):
            yojana_api.GEMINI_API_KEY = "server-only-test-key"
            status, _, data = self.request("POST", "/api/assistant/chat", payload)
        self.assertEqual(status, 200)
        self.assertEqual(data, {"reply": "नमस्ते", "language": "hi"})

        invalid_payloads = [
            {"language": [], "messages": payload["messages"]},
            {"language": "en", "messages": [{"role": "user", "text": "x" * 1_501}]},
            {"language": "en", "messages": [{"role": "assistant", "text": "not a question"}]},
        ]
        for invalid in invalid_payloads:
            with self.subTest(payload=invalid):
                status, _, _ = self.request("POST", "/api/assistant/chat", invalid)
                self.assertEqual(status, 400)

    def test_gemini_request_keeps_api_key_server_side_and_returns_text(self):
        yojana_api.GEMINI_API_KEY = "test-secret"
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": "नमस्ते, मैं मदद कर सकता हूँ।"}]}
            }]
        }).encode()
        with patch("yojana_api.urllib.request.urlopen", return_value=response) as urlopen:
            answer = yojana_api.generate_assistant_reply(
                [{"role": "user", "text": "मुझे योजना के बारे में बताएं"}],
                "hi",
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-secret")
        self.assertNotIn("test-secret", request.full_url)
        self.assertIn("models/gemini-2.5-flash:generateContent", request.full_url)
        self.assertEqual(answer, "नमस्ते, मैं मदद कर सकता हूँ।")
        self.assertIn(
            "Hindi",
            json.loads(request.data)["systemInstruction"]["parts"][0]["text"],
        )

    def test_assistant_chat_is_rate_limited_per_signed_in_account(self):
        self.sign_in_phone()
        yojana_api.GEMINI_API_KEY = "server-only-test-key"
        payload = {"language": "en", "messages": [{"role": "user", "text": "Help me"}]}
        with patch.object(yojana_api, "generate_assistant_reply", return_value="I can help."):
            for _ in range(yojana_api.ASSISTANT_REQUEST_LIMIT):
                status, _, _ = self.request("POST", "/api/assistant/chat", payload)
                self.assertEqual(status, 200)
            status, _, data = self.request("POST", "/api/assistant/chat", payload)
        self.assertEqual(status, 429)
        self.assertIn("wait a minute", data["error"].lower())

    def test_locale_bundle_is_served_as_javascript(self):
        self.connection.request("GET", "/locales.js")
        response = self.connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertIn("text/javascript", response.getheader("Content-Type"))
        self.assertTrue(body)

    def test_secure_cookie_and_https_origin_for_cloud_hosting(self):
        self.assertIn("; Secure;", yojana_api.session_cookie_headers("session", secure=True))
        self.assertNotIn("; Secure;", yojana_api.session_cookie_headers("session", secure=False))
        with patch.object(
            yojana_api,
            "verify_firebase_id_token",
            return_value={
                "id": "firebase:https-user",
                "email": "",
                "phone": "+919876543210",
                "name": "HTTPS user",
                "demo": False,
            },
        ):
            status, headers, _ = self.request(
                "POST",
                "/api/auth/firebase",
                {"id_token": "mock-token"},
                origin=f"https://127.0.0.1:{self.port}",
                forwarded_proto="https",
            )
        self.assertEqual(status, 200)
        self.assertIn("SameSite=Lax", dict(headers)["Set-Cookie"])

    def test_phone_session_cookie_protects_and_logout_revokes_access(self):
        self.sign_in_phone()
        status, _, states = self.request("GET", "/api/states")
        self.assertEqual(status, 200)
        self.assertIn("Maharashtra", states)
        status, _, _ = self.request("POST", "/api/auth/logout", {})
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/states")
        self.assertEqual(status, 401)

    def test_cross_origin_sign_in_is_rejected(self):
        status, _, data = self.request(
            "POST", "/api/auth/firebase", {"id_token":"fake"}, origin="https://attacker.example"
        )
        self.assertEqual(status, 403)
        self.assertIn("origin", data["error"].lower())

    def test_helper_batch_rolls_back_and_is_scoped_to_signed_in_user(self):
        self.sign_in_phone()
        valid_family = {"label": "Test family", **PROFILE}
        invalid_family = {"label": "Invalid family", **PROFILE, "state": "Not a state"}
        status, _, _ = self.request(
            "POST", "/api/helper/batch",
            {"families": [valid_family, invalid_family]},
        )
        self.assertEqual(status, 400)
        status, _, records = self.request("GET", "/api/helper/records")
        self.assertEqual(status, 200)
        self.assertEqual(records, [])

        status, _, result = self.request(
            "POST", "/api/helper/batch", {"families": [valid_family]}
        )
        self.assertEqual(status, 201)
        self.assertEqual(result["count"], 1)
        self.assertGreater(len(result["saved"][0]["matches"]), 0)

        with patch.object(
            yojana_api,
            "verify_firebase_id_token",
            return_value={
                "id": "firebase:another-user",
                "email": "",
                "phone": "+919876543211",
                "name": "Another user",
                "demo": False,
            },
        ):
            self.cookie = ""
            status, _, _ = self.request(
                "POST", "/api/auth/firebase", {"id_token": "mock-token"}
            )
        self.assertEqual(status, 200)
        status, _, other_user_records = self.request("GET", "/api/helper/records")
        self.assertEqual(status, 200)
        self.assertEqual(other_user_records, [])

    def test_firebase_phone_token_is_verified_by_identity_toolkit(self):
        yojana_api.FIREBASE_AUTH_CONFIGURED = True
        yojana_api.FIREBASE_API_KEY = "test-web-api-key"
        claims = {"users": [{
            "localId": "firebase-user-1",
            "phoneNumber": "+919876543210",
        }]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(claims).encode()
        with patch("yojana_api.urllib.request.urlopen", return_value=response) as urlopen:
            user = yojana_api.verify_firebase_id_token("opaque-firebase-token")
        request = urlopen.call_args.args[0]
        self.assertIn("key=test-web-api-key", request.full_url)
        self.assertEqual(json.loads(request.data), {"idToken": "opaque-firebase-token"})
        self.assertEqual(request.method, "POST")
        self.assertEqual(user["phone"], "+919876543210")
        self.assertEqual(user["name"], "+919876543210")
        self.assertEqual(user["id"], "firebase:firebase-user-1")

    def test_firebase_phone_token_and_unverified_email_handling(self):
        yojana_api.FIREBASE_AUTH_CONFIGURED = True
        yojana_api.FIREBASE_API_KEY = "test-web-api-key"
        claims = {"users": [{
            "localId": "firebase-phone-user",
            "phoneNumber": "+919876543210",
        }]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(claims).encode()
        with patch("yojana_api.urllib.request.urlopen", return_value=response):
            user = yojana_api.verify_firebase_id_token("phone-token")
        self.assertEqual(user["phone"], "+919876543210")
        self.assertEqual(user["id"], "firebase:firebase-phone-user")

        claims["users"][0] = {
            "localId": "unverified-email",
            "email": "person@example.com",
            "emailVerified": False,
        }
        response.__enter__.return_value.read.return_value = json.dumps(claims).encode()
        with patch("yojana_api.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "verified mobile number"):
                yojana_api.verify_firebase_id_token("email-only-token")

    def test_legacy_helper_records_keep_their_owners_during_migration(self):
        legacy_database = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_database)
        try:
            connection.execute(
                """CREATE TABLE helper_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    state TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    owner_email TEXT NOT NULL DEFAULT 'local-demo@example.invalid'
                )"""
            )
            connection.executemany(
                """INSERT INTO helper_checks
                   (label,state,age,created_at,result_count,owner_email)
                   VALUES (?,?,?,?,?,?)""",
                [
                    ("Demo family", "Maharashtra", 32, "2025-01-01", 1, "local-demo@example.invalid"),
                    ("Legacy family", "Maharashtra", 32, "2025-01-02", 1, "person@example.com"),
                ],
            )
            connection.commit()
        finally:
            connection.close()

        original_database = yojana_api.DATABASE
        yojana_api.DATABASE = legacy_database
        try:
            yojana_api.initialize_database()
            with yojana_api.database_connection() as connection:
                rows = connection.execute(
                    "SELECT label,owner_id FROM helper_checks ORDER BY id"
                ).fetchall()
            self.assertEqual(
                [(row["label"], row["owner_id"]) for row in rows],
                [
                    ("Demo family", "demo:local-demo"),
                    ("Legacy family", "legacy-email:person@example.com"),
                ],
            )
        finally:
            yojana_api.DATABASE = original_database


if __name__ == "__main__":
    unittest.main()
