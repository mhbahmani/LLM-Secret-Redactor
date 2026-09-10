import unittest
import os
import sys
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from secret_redactor.vault import mask_text, unmask_text, mask_recursive, unmask_recursive

class TestVaultCore(unittest.TestCase):
    def setUp(self):
        self.session_id = "test-core-session"
        self.vault_dir = "/tmp/claude_secret_vault"
        shutil.rmtree(self.vault_dir, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.vault_dir, ignore_errors=True)

    def test_mask_and_unmask_openai_key(self):
        secret = "sk-proj-1234567890abcdef1234567890"
        text = f"API_KEY={secret}"
        masked, _ = mask_text(text, self.session_id)
        self.assertNotIn(secret, masked)
        self.assertIn("__MASKED_OPENAI_KEY_", masked)

        unmasked = unmask_text(masked, self.session_id)
        self.assertEqual(unmasked, text)

    def test_mask_and_unmask_github_token(self):
        secret = "ghp_123456789012345678901234567890123456"
        text = f"token: {secret}"
        masked, _ = mask_text(text, self.session_id)
        self.assertNotIn(secret, masked)
        self.assertIn("__MASKED_GITHUB_TOKEN_", masked)

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

if __name__ == "__main__":
    unittest.main()
