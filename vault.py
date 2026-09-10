import os
import json
import re
import hashlib
from typing import Tuple, Dict, Any

# Simple regexes for common secrets/tokens
SECRET_PATTERNS = [
    # OpenAI key
    (re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"), "OPENAI_KEY"),
    # GitHub Token
    (re.compile(r"gh[pousr]_[a-zA-Z0-9]{36,}"), "GITHUB_TOKEN"),
    # Anthropic key
    (re.compile(r"sk-ant-[a-zA-Z0-9_\-]{20,}"), "ANTHROPIC_KEY"),
    # AWS Access Key ID
    (re.compile(r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}"), "AWS_KEY"),
    # Generic bearer token
    (re.compile(r"(?i)bearer\s+([a-zA-Z0-9_\-\.]{20,})"), "BEARER_TOKEN"),
    # URI with credentials (e.g. postgres://user:pass@host)
    (re.compile(r"""(?i)([a-z0-9+.\-]+://[^:\s@/]+:)([^@\s/]+)(@)"""), "URI_PASS"),
    # Key-value secrets (password, secret, token, api_key)
    (re.compile(r"""(?i)(["']?(?:password|passwd|secret|api[_-]?key|token|auth_token)["']?\s*[:=]\s*["']?)([^\s"',;}{]{6,})(["']?)"""), "KV_SECRET"),
    # JWT token pattern
    (re.compile(r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}"), "JWT_TOKEN"),
]

VAULT_DIR = "/tmp/claude_secret_vault"

def get_vault_path(session_id: str) -> str:
    os.makedirs(VAULT_DIR, exist_ok=True)
    safe_session = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id or "default")
    return os.path.join(VAULT_DIR, f"vault_{safe_session}.json")

def load_vault(session_id: str) -> Dict[str, str]:
    path = get_vault_path(session_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_vault(session_id: str, vault: Dict[str, str]):
    path = get_vault_path(session_id)
    try:
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(vault, f, indent=2)
        os.replace(temp_path, path)
    except Exception:
        pass

def _make_token(prefix: str, secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8].upper()
    return f"__MASKED_{prefix}_{digest}__"

def mask_text(text: str, session_id: str) -> Tuple[str, Dict[str, str]]:
    if not isinstance(text, str) or not text:
        return text, {}

    vault = load_vault(session_id)
    updated = False

    # 1. Check KV secrets
    def kv_sub(m: re.Match) -> str:
        nonlocal updated
        prefix_chars, secret, suffix_chars = m.group(1), m.group(2), m.group(3)
        # Avoid double masking existing masked tokens
        if secret.startswith("__MASKED_") and secret.endswith("__"):
            return m.group(0)
        token = _make_token("SECRET", secret)
        if vault.get(token) != secret:
            vault[token] = secret
            updated = True
        return f"{prefix_chars}{token}{suffix_chars}"

    # 2. Check URI password
    def uri_sub(m: re.Match) -> str:
        nonlocal updated
        prefix, secret, suffix = m.group(1), m.group(2), m.group(3)
        token = _make_token("URIPASS", secret)
        if vault.get(token) != secret:
            vault[token] = secret
            updated = True
        return f"{prefix}{token}{suffix}"

    # 3. Check Bearer token
    def bearer_sub(m: re.Match) -> str:
        nonlocal updated
        secret = m.group(1)
        token = _make_token("BEARER", secret)
        if vault.get(token) != secret:
            vault[token] = secret
            updated = True
        return f"Bearer {token}"

    # Standard patterns
    for regex, kind in SECRET_PATTERNS:
        if kind == "KV_SECRET":
            text = regex.sub(kv_sub, text)
        elif kind == "URI_PASS":
            text = regex.sub(uri_sub, text)
        elif kind == "BEARER_TOKEN":
            text = regex.sub(bearer_sub, text)
        else:
            def std_sub(m: re.Match, kind=kind) -> str:
                nonlocal updated
                secret = m.group(0)
                if secret.startswith("__MASKED_") and secret.endswith("__"):
                    return secret
                token = _make_token(kind, secret)
                if vault.get(token) != secret:
                    vault[token] = secret
                    updated = True
                return token
            text = regex.sub(std_sub, text)

    if updated:
        save_vault(session_id, vault)

    return text, vault

def unmask_text(text: str, session_id: str) -> str:
    if not isinstance(text, str) or not text:
        return text

    vault = load_vault(session_id)
    if not vault:
        return text

    # Sort tokens longest first to avoid partial collision
    for token, secret in sorted(vault.items(), key=lambda x: len(x[0]), reverse=True):
        if token in text:
            text = text.replace(token, secret)

    return text

def mask_recursive(obj: Any, session_id: str) -> Tuple[Any, bool]:
    if isinstance(obj, str):
        masked, _ = mask_text(obj, session_id)
        return masked, masked != obj
    elif isinstance(obj, dict):
        new_dict = {}
        changed = False
        for k, v in obj.items():
            new_v, ch = mask_recursive(v, session_id)
            new_dict[k] = new_v
            if ch:
                changed = True
        return new_dict, changed
    elif isinstance(obj, list):
        new_list = []
        changed = False
        for item in obj:
            new_item, ch = mask_recursive(item, session_id)
            new_list.append(new_item)
            if ch:
                changed = True
        return new_list, changed
    return obj, False

def unmask_recursive(obj: Any, session_id: str) -> Tuple[Any, bool]:
    if isinstance(obj, str):
        unmasked = unmask_text(obj, session_id)
        return unmasked, unmasked != obj
    elif isinstance(obj, dict):
        new_dict = {}
        changed = False
        for k, v in obj.items():
            new_v, ch = unmask_recursive(v, session_id)
            new_dict[k] = new_v
            if ch:
                changed = True
        return new_dict, changed
    elif isinstance(obj, list):
        new_list = []
        changed = False
        for item in obj:
            new_item, ch = unmask_recursive(item, session_id)
            new_list.append(new_item)
            if ch:
                changed = True
        return new_list, changed
    return obj, False
