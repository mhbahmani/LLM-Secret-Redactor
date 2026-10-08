import unittest
import os
import sys
import shutil
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="llm-redactor-python-")
os.environ["SECRET_REDACTOR_RUNTIME_DIR"] = TEST_RUNTIME_DIR

from secret_redactor.vault import (
    mask_text,
    unmask_text,
    mask_recursive,
    unmask_recursive,
    clear_session,
    broker_stats,
    shutdown_broker,
)

class TestVaultCore(unittest.TestCase):
    def setUp(self):
        self.session_id = "test-core-session"
        clear_session(self.session_id)

    def tearDown(self):
        clear_session(self.session_id)

    @classmethod
    def tearDownClass(cls):
        shutdown_broker()
        shutil.rmtree(TEST_RUNTIME_DIR, ignore_errors=True)

    def test_key_prefixes_inside_words_are_not_masked(self):
        names = [
            "src/task-management-service-handler.ts",
            "/var/log/disk-usage-monitoring-report.log",
            "https://example.com/blog/risk-assessment-framework-guide",
            "docs/desk-booking-integration-notes.md",
            "lib/high_gho_1234567890123456789012345678901234567/x",
            "id=XAKIAIOSFODNN7EXAMPLE1",
        ]
        for name in names:
            with self.subTest(name=name):
                masked, _ = mask_text(name, self.session_id)
                self.assertEqual(masked, name)

    def test_key_prefixes_at_word_start_are_masked(self):
        secrets = [
            "sk-proj-1234567890abcdef1234567890",
            "sk-ant-1234567890abcdef1234567890",
            "ghp_123456789012345678901234567890123456",
            "AKIAIOSFODNN7EXAMPLE",
        ]
        for secret in secrets:
            for text in (secret, f"key={secret}", f"s3://bucket/{secret}/report.csv", f"'{secret}'"):
                with self.subTest(text=text):
                    masked, _ = mask_text(text, self.session_id)
                    self.assertNotIn(secret, masked)

    def test_mask_and_unmask_openai_key(self):
        secret = "sk-proj-1234567890abcdef1234567890"
        text = f"API_KEY={secret}"
        masked, _ = mask_text(text, self.session_id)
        self.assertNotIn(secret, masked)
        self.assertIn("__MASKED_TOKEN_", masked)

        unmasked = unmask_text(masked, self.session_id)
        self.assertEqual(unmasked, text)

    def test_mask_and_unmask_github_token(self):
        secret = "ghp_123456789012345678901234567890123456"
        text = f"token: {secret}"
        masked, _ = mask_text(text, self.session_id)
        self.assertNotIn(secret, masked)
        self.assertIn("__MASKED_TOKEN_", masked)

        unmasked = unmask_text(masked, self.session_id)
        self.assertEqual(unmasked, text)

    def test_mask_uri_password(self):
        url = "postgres://admin:SuperSecretPass123!@localhost:5432/mydb"
        masked, _ = mask_text(url, self.session_id)
        self.assertNotIn("SuperSecretPass123!", masked)
        self.assertIn("__MASKED_URIPASS_", masked)

        unmasked = unmask_text(masked, self.session_id)
        self.assertEqual(unmasked, url)

    def test_mask_recursive_dict_and_list(self):
        data = {
            "auth": "Bearer secrettoken1234567890123456",
            "db": ["mongodb://user:dbpass12345@localhost/test"]
        }
        masked_data, changed = mask_recursive(data, self.session_id)
        self.assertTrue(changed)
        self.assertNotIn("secrettoken1234567890123456", str(masked_data))
        self.assertNotIn("dbpass12345", str(masked_data))

        unmasked_data, changed_back = unmask_recursive(masked_data, self.session_id)
        self.assertTrue(changed_back)
        self.assertEqual(unmasked_data, data)

    def test_mask_recursive_covers_dict_keys(self):
        secret = "sk-proj-1234567890abcdef1234567890"
        data = {"keys": {secret: "primary"}}
        masked_data, changed = mask_recursive(data, self.session_id)
        self.assertTrue(changed)
        self.assertNotIn(secret, str(masked_data))
        self.assertEqual(unmask_recursive(masked_data, self.session_id)[0], data)

    def test_single_broker_process_is_reused(self):
        first = broker_stats()["pid"]
        second = broker_stats()["pid"]
        self.assertEqual(first, second)
        self.assertEqual(os.listdir(TEST_RUNTIME_DIR), ["broker.sock"])

if __name__ == "__main__":
    unittest.main()
