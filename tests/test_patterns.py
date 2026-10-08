import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from secret_redactor.redaction import load_patterns, redact

PATTERNS = load_patterns()

SECRETS = [
    ("DB_PASSWORD=hunter2hunter2", "hunter2hunter2"),
    ("api_key='SecretApiKey123'", "SecretApiKey123"),
    ('{"token": "abc123def456ghi"}', "abc123def456ghi"),
    ("postgres://admin:SuperSecretPass123!@localhost:5432/db", "SuperSecretPass123!"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123", "abcdefghijklmnopqrstuvwxyz123"),
    ("key sk-proj-1234567890abcdef1234567890", "sk-proj-1234567890abcdef1234567890"),
    ("ghp_123456789012345678901234567890123456", "ghp_123456789012345678901234567890123456"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("secret: s3cr3t.v4lu3", "s3cr3t.v4lu3"),
]

NOT_SECRETS = [
    'token = vault.token_for("SECRET", secret)',
    "access_token = request.headers.get('Authorization')",
    'secret = os.environ["APP_SECRET"]',
    "password = getpass()",
    "api_key = settings.API_KEY",
    "Set the api_key: <your-key-here>",
    "token: ${{ secrets.GITHUB_TOKEN }}",
    "password=${DB_PASSWORD}",
    "max_tokens = 1000000",
    "tokenizer = AutoTokenizer.from_pretrained(name)",
    "token = undefined",
    r'"pattern": "([a-z0-9+.\\-]+://[^:\\s@/]+:)([^@\\s/]+)(@)"',
    "postgres://${DB_USER}:${DB_PASS}@db/app",
]


def mask(text):
    return redact(text, PATTERNS, lambda kind, secret: f"<{kind}>")


class TestPatterns(unittest.TestCase):
    def test_secrets_are_masked(self):
        for text, secret in SECRETS:
            with self.subTest(text=text):
                self.assertNotIn(secret, mask(text))

    def test_code_references_are_left_alone(self):
        for text in NOT_SECRETS:
            with self.subTest(text=text):
                self.assertEqual(mask(text), text)

    def test_bearer_keeps_original_casing(self):
        self.assertEqual(mask("authorization: bearer abcdefghijklmnopqrstuvwxyz123"),
                         "authorization: bearer <BEARER>")


if __name__ == "__main__":
    unittest.main()
